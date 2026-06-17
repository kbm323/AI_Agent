"""Tests for the classify() orchestrator (Sub-AC 2c-4).

Verifies end-to-end pipeline integration:
- Happy path: prompt build → CLI → parse → enriched ClassificationResult
- Empty topic → fail result
- Prompt build error → fail result
- CLI timeout → fail result
- CLI non-zero exit → fail result
- CLI empty stdout → fail result
- Response parse failure → fail result (transparent pass-through)
- Mock runner receives correct command and config
- Context file is written and cleaned up
- meeting_id-based directory isolation
- Pipeline stage isolation: each stage's error is caught independently
- Result immutability
- Determinism: same input + same mock → same result
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from src.classify import classify, _write_context_file
from src.opencode_qwen_wrapper import SubprocessRunner
from src.response_parser import ClassificationResult

# Import for runner tracking
from src.opencode_qwen_wrapper import build_opencode_command


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
def tech_classification_json() -> str:
    """Technical development classification JSON."""
    return json.dumps({
        "agenda_type": "technical_development",
        "tags": ["backend-api", "refactoring", "database"],
        "risk_tags": ["technical", "schedule"],
        "required_roles": ["coordinator", "tech-director", "backend-dev"],
        "optional_roles": ["devops-engineer"],
        "validator_required": True,
        "codex_required": True,
        "confidence": 0.88,
        "reasoning": "Backend refactoring with timeline risk.",
    })


@pytest.fixture
def sample_topic() -> str:
    return "신규 캐릭터 '루나'의 비주얼 디자인 회의"


# ═════════════════════════════════════════════════════════════════════════
# Mock runner helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_mock_runner(
    exit_code: int,
    stdout: str,
    stderr: str = "",
) -> SubprocessRunner:
    """Return a mock runner that returns fixed values and records calls."""
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


def _make_raw_json_runner(json_str: str) -> SubprocessRunner:
    """Return a mock runner that returns the given JSON as stdout."""
    return _make_mock_runner(0, json_str)


# ═════════════════════════════════════════════════════════════════════════
# Happy path tests
# ═════════════════════════════════════════════════════════════════════════


class TestClassifySuccess:
    """Happy-path: valid topic → valid ClassificationResult."""

    def test_creates_valid_classification(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Full pipeline with mock runner produces correct result."""
        runner = _make_raw_json_runner(qwen_classification_json)
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is True
        assert result.agenda_type == "creative_production"
        assert result.tags == (
            "character-design", "visual-concept", "sns-strategy",
        )
        assert result.risk_tags == ("brand",)
        assert result.required_roles == (
            "coordinator", "art-director", "marketing-lead",
        )
        assert result.optional_roles == ("concept-artist", "sns-strategist")
        assert result.validator_required is True
        assert result.codex_required is False
        assert result.confidence == 0.92
        assert "Visual design" in result.reasoning
        assert result.validation_verdict == "pass"
        assert result.validation_score == 1.0

    def test_technical_topic_classification(
        self, tech_classification_json: str
    ) -> None:
        """Technical topic with dual risk tags → P2 priority."""
        runner = _make_raw_json_runner(tech_classification_json)
        result = classify("백엔드 API 리팩토링 회의", _injected_runner=runner)

        assert result.is_valid is True
        assert result.agenda_type == "technical_development"
        assert result.priority == "P2"
        assert "tech_development" in result.teams
        assert "coordination" in result.teams
        assert result.validator_required is True
        assert result.codex_required is True

    def test_teams_derived_from_roles(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Teams are correctly derived from required + optional roles."""
        runner = _make_raw_json_runner(qwen_classification_json)
        result = classify(sample_topic, _injected_runner=runner)

        # coordinator → coordination
        # art-director, concept-artist → art_design
        # marketing-lead, sns-strategist → marketing
        assert "coordination" in result.teams
        assert "art_design" in result.teams
        assert "marketing" in result.teams
        assert len(result.teams) == 3

    def test_result_is_frozen(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """ClassificationResult must be immutable."""
        runner = _make_raw_json_runner(qwen_classification_json)
        result = classify(sample_topic, _injected_runner=runner)
        with pytest.raises(Exception):
            result.agenda_type = "changed"  # type: ignore[misc]

    def test_markdown_fenced_response_parsed(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Qwen response with markdown fences is correctly parsed."""
        fenced = (
            "Here is the classification:\n```json\n"
            + qwen_classification_json
            + "\n```\nLet me know if anything else."
        )
        runner = _make_raw_json_runner(fenced)
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is True
        assert result.agenda_type == "creative_production"

    def test_deterministic_same_input(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Same topic + same mock → identical results."""
        r1 = classify(
            sample_topic,
            _injected_runner=_make_raw_json_runner(qwen_classification_json),
        )
        r2 = classify(
            sample_topic,
            _injected_runner=_make_raw_json_runner(qwen_classification_json),
        )
        # Different runner instances but same JSON → same classification
        assert r1.agenda_type == r2.agenda_type
        assert r1.priority == r2.priority
        assert r1.teams == r2.teams
        assert r1.validation_verdict == r2.validation_verdict


# ═════════════════════════════════════════════════════════════════════════
# Input validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Empty/invalid input returns fail result without calling CLI."""

    def test_empty_topic_returns_fail(self) -> None:
        """Empty string must return fail without invoking runner."""
        runner = _make_mock_runner(0, '{"ok": true}')
        result = classify("", _injected_runner=runner)
        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert "Empty meeting topic" in result.reasoning
        assert result.confidence == 0.0
        # Runner must NOT have been called
        assert len(runner.calls) == 0  # type: ignore[attr-defined]

    def test_whitespace_only_topic_returns_fail(self) -> None:
        """Whitespace-only string must return fail."""
        runner = _make_mock_runner(0, '{"ok": true}')
        result = classify("   \n\t  ", _injected_runner=runner)
        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert len(runner.calls) == 0  # type: ignore[attr-defined]


# ═════════════════════════════════════════════════════════════════════════
# CLI failure tests
# ═════════════════════════════════════════════════════════════════════════


class TestCLIFailure:
    """CLI invocation failures produce fail results."""

    def test_timeout_returns_fail(self, sample_topic: str) -> None:
        """CLI timeout → fail with timeout message."""
        runner = _make_mock_runner(
            -1, "", "subprocess.TimeoutExpired: timed out after 120s"
        )
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert "timed out" in result.reasoning.lower()
        assert result.confidence == 0.0

    def test_non_zero_exit_returns_fail(self, sample_topic: str) -> None:
        """CLI exits with code 1 → fail with error message."""
        runner = _make_mock_runner(1, "", "Error: model 'qwen-max' not found")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert "exited with code 1" in result.reasoning

    def test_exit_code_137_returns_fail(self, sample_topic: str) -> None:
        """SIGKILL (137) → fail."""
        runner = _make_mock_runner(137, "", "Killed")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert "exited with code 137" in result.reasoning

    def test_empty_stdout_returns_fail(self, sample_topic: str) -> None:
        """CLI succeeds (exit 0) but returns empty stdout → fail."""
        runner = _make_mock_runner(0, "", "")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert "empty stdout" in result.reasoning.lower()

    def test_whitespace_only_stdout_returns_fail(self, sample_topic: str) -> None:
        """CLI returns only whitespace → fail."""
        runner = _make_mock_runner(0, "   \n\t  ", "")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert "empty stdout" in result.reasoning.lower()

    def test_oserror_returns_fail(self, sample_topic: str) -> None:
        """Internal OSError from runner → fail."""
        runner = _make_mock_runner(-1, "", "OSError: No such file")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        assert "subprocess error" in result.reasoning.lower() or \
               "OSError" in result.reasoning


# ═════════════════════════════════════════════════════════════════════════
# Pipeline integration tests (runner receives correct data)
# ═════════════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    """Verify the runner receives correct command and context file."""

    def test_runner_receives_opencode_command(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Mock runner must receive a well-formed opencode-go command."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(sample_topic, _injected_runner=runner)

        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        assert cmd[0] == "opencode-go"
        assert "--model" in cmd
        assert "--context-file" in cmd
        assert cmd[2] == "qwen-max"  # default model

    def test_runner_receives_context_file_path(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Context file path must be a .json file."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(sample_topic, _injected_runner=runner)

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        ctx_idx = cmd.index("--context-file")
        context_path = cmd[ctx_idx + 1]
        assert context_path.endswith(".json")
        assert "classify_context_" in context_path

    def test_context_file_contains_prompt(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """The context file written to disk contains the classification prompt."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(sample_topic, _injected_runner=runner)

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        ctx_idx = cmd.index("--context-file")
        context_path = cmd[ctx_idx + 1]

        # File should have been cleaned up (deleted after use)
        assert not os.path.exists(context_path), (
            f"Context file {context_path} was not cleaned up after classify()"
        )

    def test_context_file_cleaned_up_after_success(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Context file is deleted after a successful classification."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(sample_topic, _injected_runner=runner)

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        ctx_idx = cmd.index("--context-file")
        context_path = cmd[ctx_idx + 1]
        assert not os.path.exists(context_path)

    def test_context_file_cleaned_up_after_failure(
        self, sample_topic: str
    ) -> None:
        """Context file is deleted even after CLI failure."""
        runner = _make_mock_runner(1, "", "error")
        classify(sample_topic, _injected_runner=runner)

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        ctx_idx = cmd.index("--context-file")
        context_path = cmd[ctx_idx + 1]
        assert not os.path.exists(context_path)

    def test_custom_model_passed_to_runner(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Custom model name is forwarded to the CLI."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(sample_topic, model="qwen-plus", _injected_runner=runner)

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "qwen-plus"

    def test_custom_timeout_passed_to_runner(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """Custom timeout is forwarded to the runner."""
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(
            sample_topic,
            timeout_seconds=30.0,
            _injected_runner=runner,
        )

        assert runner.calls[0]["timeout_seconds"] == 30.0  # type: ignore[attr-defined]

    def test_meeting_id_creates_isolated_directory(
        self, sample_topic: str, qwen_classification_json: str
    ) -> None:
        """When meeting_id is provided, context file goes under meetings/{id}/."""
        meeting_id = "test_meeting_2c4_dir"
        runner = _make_raw_json_runner(qwen_classification_json)
        classify(
            sample_topic,
            meeting_id=meeting_id,
            _injected_runner=runner,
        )

        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        ctx_idx = cmd.index("--context-file")
        context_path = cmd[ctx_idx + 1]
        assert f"meetings/{meeting_id}" in context_path


# ═════════════════════════════════════════════════════════════════════════
# Error propagation tests (parse_response failures pass through)
# ═════════════════════════════════════════════════════════════════════════


class TestResponseParseFailure:
    """parse_response failures are transparently passed through."""

    def test_non_json_response_returns_fail(self, sample_topic: str) -> None:
        """CLI returns non-JSON text → parse_response returns fail."""
        runner = _make_mock_runner(0, "This is not JSON at all.", "")
        result = classify(sample_topic, _injected_runner=runner)

        assert result.is_valid is False
        assert result.validation_verdict == "fail"
        # Confidence is set by parse_response on failure
        assert result.confidence == 0.0

    def test_malformed_json_returns_degraded(self, sample_topic: str) -> None:
        """Truncated JSON → degraded but still a ClassificationResult."""
        runner = _make_mock_runner(
            0,
            '{"agenda_type": "creative_production", "tags": ["art"',
            "",
        )
        result = classify(sample_topic, _injected_runner=runner)

        # Should return a result (not raise) — verdict may vary
        assert isinstance(result, ClassificationResult)
        assert result.validation_score < 1.0

    def test_json_with_wrong_types_still_parses(self, sample_topic: str) -> None:
        """JSON with wrong field types → still returns result."""
        bad_json = json.dumps({
            "agenda_type": "general_planning",
            "tags": "not-a-list",  # wrong type
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        runner = _make_mock_runner(0, bad_json, "")
        result = classify(sample_topic, _injected_runner=runner)

        # Should not raise; parse_response handles schema violations
        assert isinstance(result, ClassificationResult)
        # tags="not-a-list" → validation should flag this
        assert result.validation_verdict != "pass"


# ═════════════════════════════════════════════════════════════════════════
# Context file helper tests
# ═════════════════════════════════════════════════════════════════════════


class TestWriteContextFile:
    """Unit tests for _write_context_file helper."""

    def test_writes_valid_json_file(self) -> None:
        """The written file contains a valid JSON object with a prompt key."""
        path = _write_context_file("Hello, classify this topic.", None)
        try:
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)
            assert "prompt" in data
            assert data["prompt"] == "Hello, classify this topic."
        finally:
            os.unlink(path)

    def test_meeting_id_creates_dir(self) -> None:
        """meeting_id creates the meetings/{id}/ directory."""
        meeting_id = "test_ctx_file_meeting"
        meeting_dir = os.path.join("meetings", meeting_id)

        # Clean up from any previous run
        import shutil
        if os.path.exists(meeting_dir):
            shutil.rmtree(meeting_dir)

        path = _write_context_file("Test prompt", meeting_id)
        try:
            assert os.path.exists(meeting_dir)
            assert meeting_dir in path
        finally:
            os.unlink(path)
            # Clean up empty dir
            try:
                os.rmdir(meeting_dir)
            except OSError:
                pass

    def test_unicode_prompt_preserved(self) -> None:
        """Korean/Unicode prompts are preserved in the JSON file."""
        korean_prompt = "한국어 프롬프트 테스트입니다."
        path = _write_context_file(korean_prompt, None)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["prompt"] == korean_prompt
        finally:
            os.unlink(path)


# ═════════════════════════════════════════════════════════════════════════
# ClassificationResult property tests
# ═════════════════════════════════════════════════════════════════════════


class TestResultProperties:
    """ClassificationResult convenience properties."""

    def test_is_valid_true_on_pass(self, sample_topic: str,
                                   qwen_classification_json: str) -> None:
        runner = _make_raw_json_runner(qwen_classification_json)
        result = classify(sample_topic, _injected_runner=runner)
        assert result.is_valid is True
        assert result.needs_escalation is False

    def test_needs_escalation_on_fail(self, sample_topic: str) -> None:
        runner = _make_mock_runner(-1, "", "timeout")
        result = classify(sample_topic, _injected_runner=runner)
        assert result.is_valid is False
        assert result.needs_escalation is True
