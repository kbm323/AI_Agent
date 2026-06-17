"""Tests for the opencode-go CLI GLM-5.1 invocation wrapper.

Sub-AC 7.1a: Independently testable by mocking the CLI subprocess call.

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
- GLM-5.1 validator-specific defaults (180s timeout, 20k token context)
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from src.opencode_glm_wrapper import (
    GlmCallConfig,
    GlmCallResult,
    SubprocessRunner,
    _default_subprocess_runner,
    build_glm_command,
    get_runner,
    inject_runner,
    invoke_glm,
)

# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def glm_validation_json() -> str:
    """A realistic GLM-5.1 validation verdict JSON response."""
    return json.dumps({
        "verdict": "conditional_pass",
        "overall_score": 0.82,
        "areas": {
            "requirements_fit": {"score": 0.85, "notes": "Addresses agenda well"},
            "logical_consistency": {
                "score": 0.80,
                "notes": "Minor tension between roles",
            },
            "factual_grounding": {"score": 0.90, "notes": "Claims well-sourced"},
            "feasibility": {"score": 0.75, "notes": "Timeline optimistic"},
            "risk_policy": {"score": 0.80, "notes": "Mitigation partially addressed"},
        },
        "required_fixes": ["Clarify timeline feasibility", "Resolve role tension R2"],
        "escalation_triggers": [],
    })


@pytest.fixture
def glm_json_with_markdown_fence(glm_validation_json: str) -> str:
    """GLM response with markdown code fence (common LLM artefact)."""
    return f"```json\n{glm_validation_json}\n```\n\nValidation complete."


@pytest.fixture
def default_config() -> GlmCallConfig:
    """A standard configuration for GLM-5.1 validation invocation."""
    return GlmCallConfig(
        model="glm-5.1",
        context_file="/tmp/meetings/meeting_20260610_test/validation_packet_r3.json",
        timeout_seconds=180,
    )


@pytest.fixture
def worker_config() -> GlmCallConfig:
    """GLM-5.1 configured as a worker (shorter timeout, 12k context)."""
    return GlmCallConfig(
        model="glm-5.1",
        context_file="/tmp/meetings/meeting_20260610_test/packet_r1.json",
        timeout_seconds=120,
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
        return (0, '{"verdict": "pass", "overall_score": 1.0}', "")

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


# ═════════════════════════════════════════════════════════════════════════
# Command construction tests
# ═════════════════════════════════════════════════════════════════════════


class TestBuildGlmCommand:
    """Verify command list construction is deterministic and correct."""

    def test_basic_command_structure(self) -> None:
        """Command must start with 'opencode-go' and include --model, --context-file."""
        cmd = build_glm_command("glm-5.1", "/tmp/packet.json")
        assert cmd[0] == "opencode-go"
        assert "--model" in cmd
        assert "--context-file" in cmd
        assert cmd == [
            "opencode-go",
            "--model",
            "glm-5.1",
            "--context-file",
            "/tmp/packet.json",
        ]

    def test_model_appears_after_model_flag(self) -> None:
        """--model must be immediately followed by the model name."""
        cmd = build_glm_command("glm-5.1", "/tmp/c.json")
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "glm-5.1"

    def test_context_file_appears_after_context_file_flag(self) -> None:
        """--context-file must be immediately followed by the path."""
        cmd = build_glm_command("glm-5.1", "/tmp/ctx.json")
        ctx_idx = cmd.index("--context-file")
        assert cmd[ctx_idx + 1] == "/tmp/ctx.json"

    def test_different_models_produce_different_commands(self) -> None:
        """Different model names produce different command lists."""
        cmd_a = build_glm_command("glm-5.1", "/tmp/a.json")
        cmd_b = build_glm_command("glm-4", "/tmp/a.json")
        assert cmd_a != cmd_b

    def test_different_files_produce_different_commands(self) -> None:
        """Different context files produce different command lists."""
        cmd_a = build_glm_command("glm-5.1", "/tmp/a.json")
        cmd_b = build_glm_command("glm-5.1", "/tmp/b.json")
        assert cmd_a != cmd_b

    def test_same_inputs_produce_identical_commands(self) -> None:
        """Deterministic: same inputs -> byte-identical command list."""
        cmd_1 = build_glm_command("glm-5.1", "/tmp/p.json")
        cmd_2 = build_glm_command("glm-5.1", "/tmp/p.json")
        assert cmd_1 == cmd_2

    def test_whitespace_model_trimmed(self) -> None:
        """Leading/trailing whitespace in model name is stripped."""
        cmd = build_glm_command("  glm-5.1  ", "/tmp/p.json")
        assert cmd[2] == "glm-5.1"

    def test_whitespace_context_file_trimmed(self) -> None:
        """Leading/trailing whitespace in context file is stripped."""
        cmd = build_glm_command("glm-5.1", "  /tmp/p.json  ")
        assert cmd[4] == "/tmp/p.json"

    def test_empty_model_raises_value_error(self) -> None:
        """Empty model name must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            build_glm_command("", "/tmp/p.json")

    def test_whitespace_only_model_raises_value_error(self) -> None:
        """Whitespace-only model must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            build_glm_command("   \n\t ", "/tmp/p.json")

    def test_empty_context_file_raises_value_error(self) -> None:
        """Empty context file path must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            build_glm_command("glm-5.1", "")

    def test_whitespace_only_context_file_raises_value_error(self) -> None:
        """Whitespace-only context file path must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            build_glm_command("glm-5.1", "   \n\t ")

    def test_command_length_is_exactly_5(self) -> None:
        """Command should have exactly 5 elements: binary + 2 flags + 2 values."""
        cmd = build_glm_command("glm-5.1", "/tmp/p.json")
        assert len(cmd) == 5

    def test_no_prompt_flag_in_command(self) -> None:
        """--prompt flag is FORBIDDEN per Track 5 design."""
        cmd = build_glm_command("glm-5.1", "/tmp/p.json")
        assert "--prompt" not in cmd

    def test_glm_specific_model_name(self) -> None:
        """Default model name for GLM-5.1 is passed correctly."""
        cmd = build_glm_command("glm-5.1", "/tmp/p.json")
        assert cmd[2] == "glm-5.1"


# ═════════════════════════════════════════════════════════════════════════
# GlmCallConfig validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestGlmCallConfig:
    """Verify GlmCallConfig input validation and defaults."""

    def test_valid_config_constructed(self) -> None:
        """A valid config should construct without errors."""
        config = GlmCallConfig(model="glm-5.1", context_file="/tmp/p.json")
        assert config.model == "glm-5.1"
        assert config.context_file == "/tmp/p.json"
        assert config.timeout_seconds == 180.0  # validator default

    def test_validator_default_timeout_is_180(self) -> None:
        """GLM-5.1 default timeout = 180s (validator workload)."""
        config = GlmCallConfig(model="glm-5.1", context_file="/tmp/p.json")
        assert config.timeout_seconds == 180.0

    def test_custom_timeout_accepted(self) -> None:
        """Custom timeout for worker-configured GLM-5.1."""
        config = GlmCallConfig(
            model="glm-5.1", context_file="/tmp/p.json", timeout_seconds=120
        )
        assert config.timeout_seconds == 120.0

    def test_empty_model_raises_value_error(self) -> None:
        """Empty model name must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            GlmCallConfig(model="", context_file="/tmp/p.json")

    def test_whitespace_model_raises_value_error(self) -> None:
        """Whitespace-only model must raise ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty"):
            GlmCallConfig(model="   ", context_file="/tmp/p.json")

    def test_empty_context_file_raises_value_error(self) -> None:
        """Empty context file must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be a non-empty"):
            GlmCallConfig(model="glm-5.1", context_file="")

    def test_timeout_below_1_raises_value_error(self) -> None:
        """Timeout must be >= 1 second."""
        with pytest.raises(ValueError, match="timeout_seconds must be >= 1"):
            GlmCallConfig(
                model="glm-5.1", context_file="/tmp/p.json", timeout_seconds=0.5
            )

    def test_timeout_boundary_one_is_valid(self) -> None:
        """Timeout of exactly 1 second is valid."""
        config = GlmCallConfig(
            model="glm-5.1", context_file="/tmp/p.json", timeout_seconds=1
        )
        assert config.timeout_seconds == 1.0

    def test_custom_env_preserved(self) -> None:
        """Custom env dict should be preserved in config."""
        env = {"GLM_API_KEY": "test-key"}
        config = GlmCallConfig(
            model="glm-5.1", context_file="/tmp/p.json", env=env
        )
        assert config.env is env

    def test_custom_workdir_preserved(self) -> None:
        """Custom workdir should be preserved in config."""
        config = GlmCallConfig(
            model="glm-5.1", context_file="/tmp/p.json", workdir="/meetings/active"
        )
        assert config.workdir == "/meetings/active"

    def test_config_is_frozen(self) -> None:
        """GlmCallConfig must be immutable."""
        config = GlmCallConfig(model="glm-5.1", context_file="/tmp/p.json")
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.model = "changed"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Successful invocation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeGlmSuccess:
    """Verify invoke_glm with successful subprocess outcomes."""

    def test_success_with_clean_json(
        self, default_config: GlmCallConfig, glm_validation_json: str
    ) -> None:
        """stdout is captured verbatim on exit_code=0."""
        runner = _make_mock_runner(0, glm_validation_json)
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == glm_validation_json
        assert result.stderr == ""
        assert result.timeout_occurred is False
        assert result.duration_seconds >= 0.0
        assert result.model == "glm-5.1"

    def test_success_with_markdown_fence(
        self, default_config: GlmCallConfig, glm_json_with_markdown_fence: str
    ) -> None:
        """stdout with markdown fences is captured as-is (caller parses it)."""
        runner = _make_mock_runner(0, glm_json_with_markdown_fence)
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is True
        assert "```json" in result.stdout
        assert result.exit_code == 0

    def test_success_preserves_stderr_warnings(
        self, default_config: GlmCallConfig
    ) -> None:
        """Stderr content on success is preserved (caller may log warnings)."""
        stderr_warning = "Warning: token limit approaching 20k"
        runner = _make_mock_runner(0, '{"verdict": "pass"}', stderr=stderr_warning)
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.stderr == stderr_warning
        assert result.has_stderr_output is True

    def test_success_empty_stdout_still_success(
        self, default_config: GlmCallConfig
    ) -> None:
        """Empty stdout with exit_code=0 is still success (caller handles it)."""
        runner = _make_mock_runner(0, "")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is True
        assert result.stdout == ""

    def test_success_duration_tracked(
        self, default_config: GlmCallConfig
    ) -> None:
        """Duration should be non-negative float."""
        runner = _make_mock_runner(0, "{}")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert isinstance(result.duration_seconds, float)
        assert result.duration_seconds >= 0.0

    def test_success_error_message_empty(
        self, default_config: GlmCallConfig
    ) -> None:
        """error_message must be empty on success."""
        runner = _make_mock_runner(0, "{}")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.error_message == ""

    def test_runner_receives_correct_command(
        self, default_config: GlmCallConfig
    ) -> None:
        """The mock runner must receive the exact command built from config."""
        runner = _make_tracking_runner()
        invoke_glm(default_config, _injected_runner=runner)

        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        assert runner.calls[0]["command"] == [  # type: ignore[attr-defined]
            "opencode-go", "--model", "glm-5.1",
            "--context-file", default_config.context_file,
        ]

    def test_runner_receives_correct_timeout(
        self, default_config: GlmCallConfig
    ) -> None:
        """The mock runner must receive the timeout from config."""
        runner = _make_tracking_runner()
        invoke_glm(default_config, _injected_runner=runner)

        assert runner.calls[0]["timeout_seconds"] == 180.0  # type: ignore[attr-defined]

    def test_runner_receives_workdir(self) -> None:
        """workdir from config must be forwarded to the runner."""
        config = GlmCallConfig(
            model="glm-5.1",
            context_file="/tmp/p.json",
            workdir="/meetings/active",
        )
        runner = _make_tracking_runner()
        invoke_glm(config, _injected_runner=runner)

        assert runner.calls[0]["workdir"] == "/meetings/active"  # type: ignore[attr-defined]

    def test_runner_receives_env(self) -> None:
        """Custom env from config must be forwarded to the runner."""
        env = {"GLM_API_KEY": "test-key"}
        config = GlmCallConfig(
            model="glm-5.1",
            context_file="/tmp/p.json",
            env=env,
        )
        runner = _make_tracking_runner()
        invoke_glm(config, _injected_runner=runner)

        assert runner.calls[0]["env"] is env  # type: ignore[attr-defined]

    def test_worker_configured_timeout_forwarded(
        self, worker_config: GlmCallConfig
    ) -> None:
        """GLM-5.1 in worker mode with 120s timeout forwards correctly."""
        runner = _make_tracking_runner()
        invoke_glm(worker_config, _injected_runner=runner)

        assert runner.calls[0]["timeout_seconds"] == 120.0  # type: ignore[attr-defined]


# ═════════════════════════════════════════════════════════════════════════
# Failure: non-zero exit code tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeGlmNonZeroExit:
    """Verify invoke_glm behaviour with non-zero exit codes."""

    def test_exit_code_1_is_failure(self, default_config: GlmCallConfig) -> None:
        """Non-zero exit code must produce success=False."""
        stderr_msg = "Error: model 'glm-5.1' not available"
        runner = _make_mock_runner(1, "", stderr_msg)
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 1
        assert result.timeout_occurred is False
        assert "exited with code 1" in result.error_message

    def test_exit_code_2_is_failure(self, default_config: GlmCallConfig) -> None:
        """Exit code 2 (misuse) must produce success=False."""
        runner = _make_mock_runner(2, "", "invalid argument")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 2

    def test_non_zero_exit_error_message_contains_stderr(
        self, default_config: GlmCallConfig
    ) -> None:
        """Error message should include stderr content for diagnostics."""
        stderr_msg = "Fatal: GLM-5.1 API endpoint unreachable"
        runner = _make_mock_runner(3, "", stderr_msg)
        result = invoke_glm(default_config, _injected_runner=runner)

        assert stderr_msg[:50] in result.error_message

    def test_non_zero_exit_no_stderr_fallback(
        self, default_config: GlmCallConfig
    ) -> None:
        """When stderr is empty, error message provides a fallback."""
        runner = _make_mock_runner(1, "", "")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert "no stderr output" in result.error_message

    def test_non_zero_exit_stdout_preserved(
        self, default_config: GlmCallConfig
    ) -> None:
        """Partial stdout is preserved even on failure (for debugging)."""
        partial_output = '{"verdict": "incomplete", "areas":'
        runner = _make_mock_runner(1, partial_output, "error occurred")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.stdout == partial_output

    def test_exit_code_137_sigkill(self, default_config: GlmCallConfig) -> None:
        """Exit code 137 (SIGKILL, OOM) should be reported correctly."""
        runner = _make_mock_runner(137, "", "Killed")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == 137
        assert not result.timeout_occurred  # not our timeout


# ═════════════════════════════════════════════════════════════════════════
# Timeout tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeGlmTimeout:
    """Verify invoke_glm timeout handling (exit_code=-1)."""

    def test_exit_code_minus_1_with_timeout_stderr(
        self, default_config: GlmCallConfig
    ) -> None:
        """exit_code=-1 + timeout stderr -> timeout_occurred=True."""
        runner = _make_mock_runner(
            -1, "", "subprocess.TimeoutExpired: GLM-5.1 timed out after 180s"
        )
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == -1
        assert result.timeout_occurred is True
        assert "timed out" in result.error_message
        assert "180s" in result.error_message

    def test_exit_code_minus_1_with_generic_oserror(
        self, default_config: GlmCallConfig
    ) -> None:
        """exit_code=-1 + OSError stderr -> timeout_occurred=True, error in message."""
        runner = _make_mock_runner(
            -1, "", "OSError: [Errno 2] No such file: opencode-go"
        )
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.exit_code == -1
        assert result.timeout_occurred is True
        assert "OSError" in result.error_message or "GLM-5.1" in result.error_message

    def test_exit_code_minus_1_empty_stderr_fallback(
        self, default_config: GlmCallConfig
    ) -> None:
        """exit_code=-1 with empty stderr still reports timeout."""
        runner = _make_mock_runner(-1, "", "")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.success is False
        assert result.timeout_occurred is True
        assert "timed out" in result.error_message.lower()

    def test_timeout_partial_stdout_preserved(
        self, default_config: GlmCallConfig
    ) -> None:
        """Partial stdout before timeout is preserved."""
        partial = '{"verdict": "pending", "overall_score": 0.'
        runner = _make_mock_runner(-1, partial, "timeout")
        result = invoke_glm(default_config, _injected_runner=runner)

        assert result.stdout == partial


# ═════════════════════════════════════════════════════════════════════════
# Input validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInvokeGlmInputValidation:
    """Verify invoke_glm validates its inputs before executing."""

    def test_non_glm_call_config_raises_type_error(self) -> None:
        """invoke_glm must reject non-GlmCallConfig input."""
        with pytest.raises(TypeError, match="must be GlmCallConfig"):
            invoke_glm("not a config")  # type: ignore[arg-type]

    def test_none_config_raises_type_error(self) -> None:
        """invoke_glm(None) must raise TypeError."""
        with pytest.raises(TypeError, match="must be GlmCallConfig"):
            invoke_glm(None)  # type: ignore[arg-type]

    def test_invalid_config_validated_by_post_init(self) -> None:
        """Config validation is handled by __post_init__, not invoke_glm."""
        with pytest.raises(ValueError):
            GlmCallConfig(model="", context_file="/tmp/p.json")


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
            return (0, "custom glm output", "")

        inject_runner(custom_runner)
        try:
            assert get_runner() is custom_runner
        finally:
            inject_runner(original)

    def test_inject_none_restores_default(self) -> None:
        """inject_runner(None) restores the production runner."""

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
        self, default_config: GlmCallConfig
    ) -> None:
        """invoke_glm uses the globally injected runner."""
        original = get_runner()

        def tagged_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "injected-glm-output", "")

        inject_runner(tagged_runner)
        try:
            result = invoke_glm(default_config)
            assert result.stdout == "injected-glm-output"
        finally:
            inject_runner(original)

    def test_per_call_injected_runner_overrides_global(
        self, default_config: GlmCallConfig
    ) -> None:
        """_injected_runner parameter overrides global injection for one call."""

        def per_call_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, "per-call-glm-output", "")

        result = invoke_glm(default_config, _injected_runner=per_call_runner)
        assert result.stdout == "per-call-glm-output"


# ═════════════════════════════════════════════════════════════════════════
# GlmCallResult immutability and properties
# ═════════════════════════════════════════════════════════════════════════


class TestGlmCallResult:
    """Verify GlmCallResult dataclass behaviour."""

    def test_result_is_frozen(self) -> None:
        """Results must be immutable."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="x",
            stderr="",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.success = False  # type: ignore[misc]

    def test_has_stderr_output_true(self) -> None:
        """has_stderr_output property reflects stderr content."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr="warning: high token usage",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is True

    def test_has_stderr_output_false_empty(self) -> None:
        """has_stderr_output is False for empty stderr."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is False

    def test_has_stderr_output_false_whitespace_only(self) -> None:
        """has_stderr_output is False for whitespace-only stderr."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr="   \n\t  ",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
        )
        assert result.has_stderr_output is False

    def test_defaults_on_success(self) -> None:
        """On success, timeout_occurred=False and error_message=''."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
        )
        assert result.timeout_occurred is False
        assert result.error_message == ""

    def test_error_fields_populated_on_failure(self) -> None:
        """On failure, error fields carry diagnostic information."""
        result = GlmCallResult(
            success=False,
            exit_code=1,
            stdout="",
            stderr="fail",
            duration_seconds=1.0,
            model="glm-5.1",
            context_file="/tmp/p.json",
            error_message="GLM-5.1 validation failed",
        )
        assert result.success is False
        assert result.error_message == "GLM-5.1 validation failed"

    def test_model_field_reflects_config(self) -> None:
        """model field must reflect the config used."""
        result = GlmCallResult(
            success=True,
            exit_code=0,
            stdout="{}",
            stderr="",
            duration_seconds=2.5,
            model="glm-5.1",
            context_file="/tmp/validation.json",
        )
        assert result.model == "glm-5.1"


# ═════════════════════════════════════════════════════════════════════════
# Real default runner smoke test (only runs if opencode-go is on PATH)
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultRunnerSmoke:
    """Minimal smoke test for the production runner (no real call)."""

    def test_default_runner_is_callable(self) -> None:
        """The default runner must be a callable."""
        assert callable(_default_subprocess_runner)

    def test_default_runner_handles_nonexistent_binary(self) -> None:
        """Default runner should return exit_code != 0 for missing binary."""
        code, stdout, stderr = _default_subprocess_runner(
            ["nonexistent_binary_xyz_123", "--flag"],
            5.0,
            None,
            None,
        )
        # Should either fail with non-zero or -1
        assert code != 0
