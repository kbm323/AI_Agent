"""Tests for the opencode-go CLI Qwen invocation wrapper.

Sub-AC 2c-2: Independently testable by mocking the CLI subprocess call.

All tests inject a mock ``SubprocessRunner`` so no real ``opencode-go``
binary or network access is required.  The mock returns controlled
(exit_code, stdout, stderr) tuples per test scenario.

Coverage:
- Command construction (model, context_file, ordering)
- Successful invocation (exit_code=0, stdout captured)
- Non-zero exit code handling
- Timeout handling (exit_code=-1)
- OSError / internal error handling (exit_code=-1)
- Empty/invalid config validation
- Runner injection and restoration
- Stderr content handling (warnings on success, errors on failure)
- Duration tracking
- Immutability of result dataclass
- Deterministic command construction
"""

from __future__ import annotations

import json
import re
import textwrap
from typing import Any

import pytest

from src.opencode_qwen_wrapper import (
    OpencodeCallConfig,
    OpencodeCallResult,
    SubprocessRunner,
    _default_subprocess_runner,
    build_opencode_command,
    get_runner,
    inject_runner,
    invoke_qwen,
)


# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def qwen_classification_json() -> str:
    """A realistic Qwen classification JSON response."""
    return json.dumps({
        "agenda_type": "creative_production",
        "tags": ["character-design", "visual-concept", "sns-strategy"],
        "risk_tags": ["brand"],
        "required_roles": ["coordinator", "art-director", "marketing-lead"],
        "optional_roles": ["concept-artist", "sns-strategist"],
        "validator_required": True,
        "codex_required": False,
        "confidence": 0.92,
        "reasoning": "Visual design + SNS strategy spans art and marketing.",
    })


@pytest.fixture
def qwen_json_with_markdown_fence(qwen_classification_json: str) -> str:
    """Qwen response with markdown code fence (common LLM artefact)."""
    return f"```json\n{qwen_classification_json}\n```\n\nLet me know if anything else."


@pytest.fixture
def default_config() -> OpencodeCallConfig:
    """A standard configuration for Qwen Qwen-Max invocation."""
    return OpencodeCallConfig(
        model="qwen-max",
        context_file="/tmp/meetings/meeting_20260610_test/packet_r1.json",
        timeout_seconds=60,
    )


# ═════════════════════════════════════════════════════════════════════════
# Mock subprocess runner helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_mock_runner(
    exit_code: int,
    stdout: str,
    stderr: str = "",
) -> SubprocessRunner:
    """Return a mock runner that returns fixed values.

    Also records the received arguments for assertion via closure.
    """

    calls: list[dict[str, Any]] = []

    def _runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        calls.append({
            "command": command,
            "timeout_seconds": timeout_seconds,
            "env": env,
            "workdir": workdir,
        })
        return (exit_code, stdout, stderr)

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


def _make_tracking_runner() -> SubprocessRunner:
    """Return a mock runner that succeeds and tracks all arguments."""
    calls: list[dict[str, Any]] = []

    def _runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        calls.append({
            "command": command,
            "timeout_seconds": timeout_seconds,
            "env": env,
            "workdir": workdir,
        })
        return (0, '{"ok": true}', "")

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


# ═════════════════════════════════════════════════════════════════════════
# Command construction tests
# ═════════════════════════════════════════════════════════════════════════


class TestBuildOpencodeCommand:
    """Verify command list construction is deterministic and correct."""

    def test_basic_command_structure(self) -> None:
        """Command must start with 'opencode-go' and include --model, --context-file."""
        cmd = build_opencode_command("qwen-max", "/tmp/packet.json")
        assert cmd[0] == "opencode-go"
        assert "--model" in cmd
        assert "--context-file" in cmd
        assert cmd == [
            "opencode-go",
            "--model",
            "qwen-max",
            "--context-file",
            "/tmp/packet.json",
        ]

    def test_model_appears_after_model_flag(self) -> None:
        """--model must be immediately followed by the model name."""
        cmd = build_opencode_command("glm-5.1", "/tmp/c.json")
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "glm-5.1"

    def test_context_file_appears_after_context_file_flag(self) -> None:
        """--context-file must be immediately followed by the path."""
        cmd = build_opencode_command("qwen-max", "/tmp/ctx.json")
        ctx_idx = cmd.index("--context-file")
        assert cmd[ctx_idx + 1] == "/tmp/ctx.json"

    def test_different_models_produce_different_commands(self) -> None:
        """Different model names produce different command lists."""
        cmd_a = build_opencode_command("qwen-max", "/tmp/a.json")
        cmd_b = build_opencode_command("deepseek-v4-pro", "/tmp/a.json")
        assert cmd_a != cmd_b

    def test_different_files_produce_different_commands(self) -> None:
        """Different context files produce different command lists."""
        cmd_a = build_opencode_command("qwen-max", "/tmp/a.json")
        cmd_b = build_opencode_command("qwen-max", "/tmp/b.json")
        assert cmd_a != cmd_b

    def test_same_inputs_produce_identical_commands(self) -> None:
        """Deterministic: same inputs → byte-identical command list."""
        cmd_1 = build_opencode_command("qwen-max", "/tmp/p.json")
        cmd_2 = build_opencode_command("qwen-max", "/tmp/p.json")
        assert cmd_1 == cmd_2

    def test_whitespace_model_trimmed(self) -> None:
        """Leading/trailing whitespace in model name is stripped."""
        cmd = build_opencode_command("  qwen-max  ", "/tmp/p.json")
        assert cmd[2] == "qwen-max"

    def test_whitespace_context_file_trimmed(self) -> None:
        """Leading/trailing whitespace in context file is stripped."""
        cmd = build_opencode_command("qwen-max", "  /tmp/p.json  ")
        assert cmd[4] == "/tmp/p.json"

    def test_empty_model_raises_value_error(self) -> None:
        """Empty model name must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            build_opencode_command("", "/tmp/p.json")

    def test_whitespace_only_model_raises_value_error(self) -> None:
        """Whitespace-only model must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            build_opencode_command("   \n\t ", "/tmp/p.json")

    def test_empty_context_file_raises_value_error(self) -> None:
        """Empty context file path must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            build_opencode_command("qwen-max", "")

    def test_whitespace_only_context_file_raises_value_error(self) -> None:
        """Whitespace-only context file path must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            build_opencode_command("qwen-max", "   \n\t ")

    def test_command_length_is_exactly_5(self) -> None:
        """Command should have exactly 5 elements: binary + 2 flags + 2 values."""
        cmd = build_opencode_command("qwen-max", "/tmp/p.json")
        assert len(cmd) == 5

    def test_no_prompt_flag_in_command(self) -> None:
        """--prompt flag is FORBIDDEN per Track 5 design."""
        cmd = build_opencode_command("qwen-max", "/tmp/p.json")
        assert "--prompt" not in cmd


# ═════════════════════════════════════════════════════════════════════════
# OpencodeCallConfig validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestOpencodeCallConfig:
    """Verify OpencodeCallConfig input validation and defaults."""

    def test_valid_config_constructed(self) -> None:
        """A valid config should construct without errors."""
        config = OpencodeCallConfig(model="qwen-max", context_file="/tmp/p.json")
        assert config.model == "qwen-max"
        assert config.context_file == "/tmp/p.json"
        assert config.timeout_seconds == 120.0  # default

    def test_empty_model_raises_value_error(self) -> None:
        """Empty model name must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            OpencodeCallConfig(model="", context_file="/tmp/p.json")

    def test_whitespace_model_raises_value_error(self) -> None:
        """Whitespace-only model must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            OpencodeCallConfig(model="   ", context_file="/tmp/p.json")

    def test_empty_context_file_raises_value_error(self) -> None:
        """Empty context file must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            OpencodeCallConfig(model="qwen-max", context_file="")

    def test_timeout_below_1_raises_value_error(self) -> None:
        """Timeout must be >= 1 second."""
        with pytest.raises(ValueError, match="timeout_seconds must be >= 1"):
            OpencodeCallConfig(
                model="qwen-max", context_file="/tmp/p.json", timeout_seconds=0.5
            )

    def test_timeout_boundary_one_is_valid(self) -> None:
        """Timeout of exactly 1 second is valid."""
        config = OpencodeCallConfig(
            model="qwen-max", context_file="/tmp/p.json", timeout_seconds=1
        )
        assert config.timeout_seconds == 1.0

    def test_custom_env_preserved(self) -> None:
        """Custom env dict should be preserved in config."""
        env = {"EXTRA_VAR": "value"}
        config = OpencodeCallConfig(
            model="qwen-max", context_file="/tmp/p.json", env=env
        )
        assert config.env is env

    def test_custom_workdir_preserved(self) -> None:
        """Custom workdir should be preserved in config."""
        config = OpencodeCallConfig(
            model="qwen-max", context_file="/tmp/p.json", workdir="/custom/wd"
        )
        assert config.workdir == "/custom/wd"

    def test_config_is_frozen(self) -> None:
        """OpencodeCallConfig must be immutable."""
        config = OpencodeCallConfig(model="qwen-max", context_file="/tmp/p.json")
        with pytest.raises(Exception):
            config.model = "changed"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Successful invocation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeQwenSuccess:
    """Verify invoke_qwen with successful subprocess outcomes."""

    def test_success_with_clean_json(
        self, default_config: OpencodeCallConfig, qwen_classification_json: str
    ) -> None:
        """stdout is captured verbatim on exit_code=0."""
        runner = _make_mock_runner(0, qwen_classification_json)
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == qwen_classification_json
        assert result.stderr == ""
        assert result.timeout_occurred is False
        assert result.duration_seconds >= 0.0
        assert result.model == "qwen-max"

    def test_success_with_markdown_fence(
        self, default_config: OpencodeCallConfig, qwen_json_with_markdown_fence: str
    ) -> None:
        """stdout with markdown fences is captured as-is (caller parses it)."""
        runner = _make_mock_runner(0, qwen_json_with_markdown_fence)
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is True
        assert "```json" in result.stdout
        assert result.exit_code == 0

    def test_success_preserves_stderr_warnings(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """Stderr content on success is preserved (caller may log warnings)."""
        stderr_warning = "Warning: rate limit approaching"
        runner = _make_mock_runner(0, '{"ok": true}', stderr=stderr_warning)
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.stderr == stderr_warning
        assert result.has_stderr_output is True

    def test_success_empty_stdout_still_success(self, default_config: OpencodeCallConfig) -> None:
        """Empty stdout with exit_code=0 is still success (caller handles it)."""
        runner = _make_mock_runner(0, "")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.stdout == ""

    def test_success_duration_tracked(self, default_config: OpencodeCallConfig) -> None:
        """Duration should be non-negative float."""
        runner = _make_mock_runner(0, "{}")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert isinstance(result.duration_seconds, float)
        assert result.duration_seconds >= 0.0

    def test_success_error_message_empty(self, default_config: OpencodeCallConfig) -> None:
        """error_message must be empty on success."""
        runner = _make_mock_runner(0, "{}")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.error_message == ""

    def test_runner_receives_correct_command(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """The mock runner must receive the exact command built from config."""
        runner = _make_tracking_runner()
        invoke_qwen(default_config, _injected_runner=runner)

        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        assert runner.calls[0]["command"] == [  # type: ignore[attr-defined]
            "opencode-go", "--model", "qwen-max",
            "--context-file", default_config.context_file,
        ]

    def test_runner_receives_correct_timeout(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """The mock runner must receive the timeout from config."""
        runner = _make_tracking_runner()
        invoke_qwen(default_config, _injected_runner=runner)

        assert runner.calls[0]["timeout_seconds"] == 60.0  # type: ignore[attr-defined]

    def test_runner_receives_workdir(self) -> None:
        """workdir from config must be forwarded to the runner."""
        config = OpencodeCallConfig(
            model="qwen-max",
            context_file="/tmp/p.json",
            workdir="/custom/workdir",
        )
        runner = _make_tracking_runner()
        invoke_qwen(config, _injected_runner=runner)

        assert runner.calls[0]["workdir"] == "/custom/workdir"  # type: ignore[attr-defined]

    def test_runner_receives_env(self) -> None:
        """Custom env from config must be forwarded to the runner."""
        env = {"CUSTOM_VAR": "custom_val"}
        config = OpencodeCallConfig(
            model="qwen-max",
            context_file="/tmp/p.json",
            env=env,
        )
        runner = _make_tracking_runner()
        invoke_qwen(config, _injected_runner=runner)

        assert runner.calls[0]["env"] is env  # type: ignore[attr-defined]


# ═════════════════════════════════════════════════════════════════════════
# Failure: non-zero exit code tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeQwenNonZeroExit:
    """Verify invoke_qwen behaviour with non-zero exit codes."""

    def test_exit_code_1_is_failure(self, default_config: OpencodeCallConfig) -> None:
        """Non-zero exit code must produce success=False."""
        stderr_msg = "Error: model 'qwen-max' not found"
        runner = _make_mock_runner(1, "", stderr_msg)
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 1
        assert result.timeout_occurred is False
        assert "exited with code 1" in result.error_message

    def test_exit_code_2_is_failure(self, default_config: OpencodeCallConfig) -> None:
        """Exit code 2 (misuse) must produce success=False."""
        runner = _make_mock_runner(2, "", "invalid argument")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 2

    def test_non_zero_exit_error_message_contains_stderr(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """Error message should include stderr content for diagnostics."""
        stderr_msg = "Fatal: network unreachable for provider qwen"
        runner = _make_mock_runner(3, "", stderr_msg)
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert stderr_msg[:50] in result.error_message

    def test_non_zero_exit_no_stderr_fallback(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """When stderr is empty, error message provides a fallback."""
        runner = _make_mock_runner(1, "", "")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert "no stderr output" in result.error_message

    def test_non_zero_exit_stdout_preserved(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """Partial stdout is preserved even on failure (for debugging)."""
        partial_output = "partial JSON output before crash"
        runner = _make_mock_runner(1, partial_output, "error occurred")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.stdout == partial_output

    def test_exit_code_137_sigkill(self, default_config: OpencodeCallConfig) -> None:
        """Exit code 137 (SIGKILL, OOM) should be reported correctly."""
        runner = _make_mock_runner(137, "", "Killed")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 137
        assert not result.timeout_occurred  # not our timeout


# ═════════════════════════════════════════════════════════════════════════
# Timeout tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeQwenTimeout:
    """Verify invoke_qwen timeout handling (exit_code=-1)."""

    def test_exit_code_minus_1_with_timeout_stderr(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """exit_code=-1 + timeout stderr → timeout_occurred=True."""
        runner = _make_mock_runner(-1, "", "subprocess.TimeoutExpired: timed out")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == -1
        assert result.timeout_occurred is True
        assert "timed out" in result.error_message

    def test_exit_code_minus_1_with_generic_oserror(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """exit_code=-1 + OSError stderr → timeout_occurred=True, error in message."""
        runner = _make_mock_runner(-1, "", "OSError: [Errno 2] No such file")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == -1
        assert result.timeout_occurred is True
        assert "OSError" in result.error_message or "opencode-go" in result.error_message

    def test_exit_code_minus_1_empty_stderr_fallback(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """exit_code=-1 with empty stderr still reports timeout."""
        runner = _make_mock_runner(-1, "", "")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.timeout_occurred is True
        assert "timed out" in result.error_message.lower()

    def test_timeout_partial_stdout_preserved(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """Partial stdout before timeout is preserved."""
        partial = "partial output before timeout"
        runner = _make_mock_runner(-1, partial, "timeout")
        result = invoke_qwen(default_config, _injected_runner=runner)

        assert result.stdout == partial


# ═════════════════════════════════════════════════════════════════════════
# Input validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeQwenInputValidation:
    """Verify invoke_qwen validates its inputs before executing."""

    def test_non_opencode_call_config_raises_type_error(self) -> None:
        """invoke_qwen must reject non-OpencodeCallConfig input."""
        with pytest.raises(TypeError, match="must be OpencodeCallConfig"):
            invoke_qwen("not a config")  # type: ignore[arg-type]

    def test_none_config_raises_type_error(self) -> None:
        """invoke_qwen(None) must raise TypeError."""
        with pytest.raises(TypeError, match="must be OpencodeCallConfig"):
            invoke_qwen(None)  # type: ignore[arg-type]

    def test_invalid_config_validated_by_post_init(self) -> None:
        """Config validation is handled by __post_init__, not invoke_qwen."""
        # invoke_qwen never sees an invalid config because the dataclass
        # raises ValueError in __post_init__.
        with pytest.raises(ValueError):
            OpencodeCallConfig(model="", context_file="/tmp/p.json")


# ═════════════════════════════════════════════════════════════════════════
# Runner injection tests
# ═════════════════════════════════════════════════════════════════════════


class TestRunnerInjection:
    """Verify inject_runner / get_runner mechanism."""

    def test_default_runner_is_production_runner(self) -> None:
        """Before any injection, get_runner returns the default."""
        inject_runner(None)  # ensure clean state
        assert get_runner() is _default_subprocess_runner

    def test_inject_custom_runner(self) -> None:
        """inject_runner replaces the active runner."""
        original = get_runner()

        def custom_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "custom output", "")

        inject_runner(custom_runner)
        try:
            assert get_runner() is custom_runner
        finally:
            inject_runner(original)

    def test_inject_none_restores_default(self) -> None:
        """inject_runner(None) restores the production runner."""
        original = get_runner()

        def dummy(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "", "")

        inject_runner(dummy)
        assert get_runner() is dummy

        inject_runner(None)
        assert get_runner() is _default_subprocess_runner

    def test_injected_runner_used_by_invoke(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """invoke_qwen uses the globally injected runner."""
        original = get_runner()

        def tagged_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "injected-output", "")

        inject_runner(tagged_runner)
        try:
            result = invoke_qwen(default_config)
            assert result.stdout == "injected-output"
        finally:
            inject_runner(original)

    def test_per_call_injected_runner_overrides_global(
        self, default_config: OpencodeCallConfig
    ) -> None:
        """_injected_runner parameter overrides global injection for one call."""
        def per_call_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "per-call-output", "")

        result = invoke_qwen(default_config, _injected_runner=per_call_runner)
        assert result.stdout == "per-call-output"


# ═════════════════════════════════════════════════════════════════════════
# OpencodeCallResult immutability and properties
# ═════════════════════════════════════════════════════════════════════════


class TestOpencodeCallResult:
    """Verify OpencodeCallResult dataclass behaviour."""

    def test_result_is_frozen(self) -> None:
        """Results must be immutable."""
        result = OpencodeCallResult(
            success=True, exit_code=0, stdout="x", stderr="",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
        )
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]

    def test_has_stderr_output_true(self) -> None:
        """has_stderr_output property reflects stderr content."""
        result = OpencodeCallResult(
            success=True, exit_code=0, stdout="", stderr="warning text",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is True

    def test_has_stderr_output_false_empty(self) -> None:
        """has_stderr_output is False for empty stderr."""
        result = OpencodeCallResult(
            success=True, exit_code=0, stdout="", stderr="",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is False

    def test_has_stderr_output_false_whitespace_only(self) -> None:
        """has_stderr_output is False for whitespace-only stderr."""
        result = OpencodeCallResult(
            success=True, exit_code=0, stdout="", stderr="   \n\t  ",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is False

    def test_defaults_on_success(self) -> None:
        """On success, timeout_occurred=False and error_message=''."""
        result = OpencodeCallResult(
            success=True, exit_code=0, stdout="ok", stderr="",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
        )
        assert result.timeout_occurred is False
        assert result.error_message == ""

    def test_error_fields_populated_on_failure(self) -> None:
        """On failure, error fields carry diagnostic information."""
        result = OpencodeCallResult(
            success=False, exit_code=1, stdout="", stderr="fail",
            duration_seconds=1.0, model="qwen-max", context_file="/tmp/p.json",
            error_message="something went wrong",
        )
        assert result.success is False
        assert result.error_message == "something went wrong"


# ═════════════════════════════════════════════════════════════════════════
# Real default runner smoke test (only runs if opencode-go is on PATH)
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultRunnerSmoke:
    """Minimal smoke test for the production runner (no real call)."""

    def test_default_runner_is_callable(self) -> None:
        """The default runner must be a callable."""
        assert callable(_default_subprocess_runner)

    def test_default_runner_handles_nonexistent_binary(self) -> None:
        """Default runner should return exit_code=-1 for missing binary."""
        # Run a command that definitely doesn't exist
        code, stdout, stderr = _default_subprocess_runner(
            ["nonexistent_binary_xyz_123", "--flag"],
            5.0,
            None,
            None,
        )
        # Should either fail with non-zero or -1
        assert code != 0
