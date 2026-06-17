"""Tests for the validation wrapper orchestrator.

Sub-AC 7.1c: Comprehensive test coverage for composing GLM-5.1 CLI
invocation with structured output parsing and handling all error modes.

Coverage:
- Full pipeline: CLI success → parse success → passed result
- Full pipeline: CLI success → parse success → failed verdict
- CLI error: non-zero exit code
- CLI error: timeout
- CLI error: OSError (exit_code=-1)
- Parse error: empty output
- Parse error: malformed JSON
- Parse error: missing verdict
- Config validation: empty model, empty context_file, bad timeout, wrong type
- Duration tracking across pipeline
- Standardised result properties: passed_clean, passed_conditional
- requires_codex_escalation heuristics
- run_glm_validation_from_stdout: happy path and parse failure
- Immutability of result dataclass
- Immutability of config dataclass
- Integration: config.to_glm_call_config() mapping
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from src.glm_output_parser import (
    GlmParseResult,
)
from src.opencode_glm_wrapper import (
    GlmCallConfig,
    GlmCallResult,
    SubprocessRunner,
    _default_subprocess_runner,
    inject_runner,
)
from src.validation_wrapper import (
    GlmValidationConfig,
    ValidationResult,
    resolve_dual_validation_conflict,
    run_glm_validation,
    run_glm_validation_from_stdout,
)

# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def pass_stdout() -> str:
    """GLM-5.1 pass verdict JSON."""
    return json.dumps({
        "verdict": "pass",
        "overall_score": 0.95,
        "areas": {
            "requirements_fit": {"score": 0.96},
            "logical_consistency": {"score": 0.94},
            "factual_grounding": {"score": 0.93},
            "feasibility": {"score": 0.92},
            "risk_policy": {"score": 0.95},
        },
        "required_fixes": [],
        "escalation_triggers": [],
        "risk_level": "low",
        "summary": "All areas pass with high confidence.",
    })


@pytest.fixture
def conditional_pass_stdout() -> str:
    """GLM-5.1 conditional_pass verdict JSON."""
    return json.dumps({
        "verdict": "conditional_pass",
        "overall_score": 0.82,
        "areas": {
            "requirements_fit": {"score": 0.85},
            "feasibility": {"score": 0.75, "notes": "Timeline optimistic"},
        },
        "required_fixes": ["Clarify timeline"],
        "escalation_triggers": [],
    })


@pytest.fixture
def fail_stdout() -> str:
    """GLM-5.1 fail verdict JSON."""
    return json.dumps({
        "verdict": "fail",
        "overall_score": 0.42,
    })


@pytest.fixture
def escalate_stdout() -> str:
    """GLM-5.1 escalate verdict JSON."""
    return json.dumps({
        "verdict": "escalate",
        "overall_score": 0.35,
        "escalation_triggers": ["legal_concern", "budget_risk"],
    })


@pytest.fixture
def revision_required_stdout() -> str:
    """GLM-5.1 revision_required verdict JSON."""
    return json.dumps({
        "verdict": "revision_required",
        "overall_score": 0.68,
        "required_fixes": ["Address missing risk assessment"],
    })


@pytest.fixture
def default_config() -> GlmValidationConfig:
    """Standard GLM-5.1 validation configuration."""
    return GlmValidationConfig(
        model="glm-5.1",
        context_file="/tmp/meetings/test/validation_packet.json",
    )


# ── Mock subprocess runners ────────────────────────────────────────────


def _mock_runner_success(stdout: str) -> SubprocessRunner:
    """Return a mock runner that always succeeds with the given stdout."""

    def runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        return (0, stdout, "")

    return runner


def _mock_runner_failure(
    exit_code: int, stderr: str = ""
) -> SubprocessRunner:
    """Return a mock runner that fails with the given exit code."""

    def runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        return (exit_code, "", stderr)

    return runner


def _mock_runner_timeout() -> SubprocessRunner:
    """Return a mock runner that simulates a timeout (exit_code=-1)."""

    def runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        return (-1, "", "subprocess timed out")

    return runner


def _mock_runner_os_error() -> SubprocessRunner:
    """Return a mock runner that simulates an OSError."""

    def runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        return (-1, "", "OSError: opencode-go not found")

    return runner


# ═════════════════════════════════════════════════════════════════════════
# Config validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestGlmValidationConfig:
    """Tests for GlmValidationConfig construction and validation."""

    def test_valid_config(self) -> None:
        """A valid config should construct without error."""
        config = GlmValidationConfig(
            model="glm-5.1",
            context_file="/tmp/packet.json",
        )
        assert config.model == "glm-5.1"
        assert config.context_file == "/tmp/packet.json"
        assert config.timeout_seconds == 180.0

    def test_empty_model_raises(self) -> None:
        """Empty model should raise ValueError."""
        with pytest.raises(ValueError, match="model must be"):
            GlmValidationConfig(model="", context_file="/tmp/packet.json")

    def test_whitespace_model_raises(self) -> None:
        """Whitespace-only model should raise ValueError."""
        with pytest.raises(ValueError, match="model must be"):
            GlmValidationConfig(model="   ", context_file="/tmp/packet.json")

    def test_empty_context_file_raises(self) -> None:
        """Empty context_file should raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be"):
            GlmValidationConfig(model="glm-5.1", context_file="")

    def test_whitespace_context_file_raises(self) -> None:
        """Whitespace-only context_file should raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be"):
            GlmValidationConfig(model="glm-5.1", context_file="   ")

    def test_zero_timeout_raises(self) -> None:
        """Timeout < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be"):
            GlmValidationConfig(
                model="glm-5.1",
                context_file="/tmp/packet.json",
                timeout_seconds=0,
            )

    def test_negative_timeout_raises(self) -> None:
        """Negative timeout should raise ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be"):
            GlmValidationConfig(
                model="glm-5.1",
                context_file="/tmp/packet.json",
                timeout_seconds=-1,
            )

    def test_default_timeout_is_180(self) -> None:
        """Default timeout should be 180s (validator tier)."""
        config = GlmValidationConfig(
            model="glm-5.1",
            context_file="/tmp/packet.json",
        )
        assert config.timeout_seconds == 180.0

    def test_to_glm_call_config(self) -> None:
        """Converting to GlmCallConfig should preserve all fields."""
        config = GlmValidationConfig(
            model="glm-5.1",
            context_file="/tmp/packet.json",
            timeout_seconds=200.0,
            env={"GLM_API_KEY": "test-key"},
            workdir="/tmp/work",
        )
        glm_config = config.to_glm_call_config()
        assert isinstance(glm_config, GlmCallConfig)
        assert glm_config.model == "glm-5.1"
        assert glm_config.context_file == "/tmp/packet.json"
        assert glm_config.timeout_seconds == 200.0
        assert glm_config.env == {"GLM_API_KEY": "test-key"}
        assert glm_config.workdir == "/tmp/work"

    def test_config_is_immutable(self) -> None:
        """GlmValidationConfig should be frozen (immutable)."""
        config = GlmValidationConfig(
            model="glm-5.1",
            context_file="/tmp/packet.json",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.model = "glm-5.2"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Full pipeline — success paths
# ═════════════════════════════════════════════════════════════════════════


class TestFullPipelineSuccess:
    """Tests where the full pipeline (CLI + parse) succeeds."""

    def test_pass_verdict(
        self, default_config: GlmValidationConfig, pass_stdout: str
    ) -> None:
        """CLI returns pass verdict → ValidationResult.passed=True."""
        runner = _mock_runner_success(pass_stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is True
        assert result.confidence == 0.95
        assert result.error is None
        assert result.verdict_raw == "pass"
        assert result.error_category == ""
        assert result.duration_seconds > 0

    def test_conditional_pass_verdict(
        self,
        default_config: GlmValidationConfig,
        conditional_pass_stdout: str,
    ) -> None:
        """CLI returns conditional_pass → ValidationResult.passed=True."""
        runner = _mock_runner_success(conditional_pass_stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is True
        assert result.confidence == 0.82
        assert result.error is None
        assert result.verdict_raw == "conditional_pass"

    def test_fail_verdict(
        self, default_config: GlmValidationConfig, fail_stdout: str
    ) -> None:
        """CLI returns fail → ValidationResult.passed=False, no error."""
        runner = _mock_runner_success(fail_stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.confidence == 0.42
        assert result.error is None  # Not a system error — valid output
        assert result.verdict_raw == "fail"

    def test_escalate_verdict(
        self, default_config: GlmValidationConfig, escalate_stdout: str
    ) -> None:
        """CLI returns escalate → passed=False."""
        runner = _mock_runner_success(escalate_stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.verdict_raw == "escalate"
        assert result.error is None

    def test_revision_required_verdict(
        self,
        default_config: GlmValidationConfig,
        revision_required_stdout: str,
    ) -> None:
        """CLI returns revision_required → passed=False."""
        runner = _mock_runner_success(revision_required_stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.verdict_raw == "revision_required"
        assert result.error is None

    def test_duration_tracked(self, default_config: GlmValidationConfig) -> None:
        """Duration should be >= 0 for a successful pipeline (mock may be instant)."""
        stdout = json.dumps({"verdict": "pass", "overall_score": 0.90})
        runner = _mock_runner_success(stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.duration_seconds >= 0

    def test_markdown_fenced_json(
        self, default_config: GlmValidationConfig
    ) -> None:
        """GLM response inside markdown code fences should parse correctly."""
        raw_json = json.dumps({"verdict": "pass", "overall_score": 0.88})
        fenced = f"```json\n{raw_json}\n```\n\nValidation complete."
        runner = _mock_runner_success(fenced)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is True
        assert result.confidence == 0.88

    def test_confidence_defaulted_to_zero(
        self, default_config: GlmValidationConfig
    ) -> None:
        """Missing overall_score → confidence 0.0 with passed still from verdict."""
        stdout = json.dumps({"verdict": "pass"})
        runner = _mock_runner_success(stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is True
        assert result.confidence == 0.0


# ═════════════════════════════════════════════════════════════════════════
# Full pipeline — CLI error paths
# ═════════════════════════════════════════════════════════════════════════


class TestFullPipelineCliErrors:
    """Tests where the CLI invocation itself fails."""

    def test_non_zero_exit_code(
        self, default_config: GlmValidationConfig
    ) -> None:
        """Non-zero exit code → passed=False with cli_error category."""
        runner = _mock_runner_failure(1, "GLM API error: rate limited")
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is not None
        assert "exited with code 1" in result.error
        assert result.error_category == "cli_error"
        assert result.verdict_raw == ""

    def test_timeout(self, default_config: GlmValidationConfig) -> None:
        """Timeout → passed=False with cli_error category."""
        runner = _mock_runner_timeout()
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is not None
        assert "timed out" in result.error.lower()
        assert result.error_category == "cli_error"
        assert result.verdict_raw == ""

    def test_os_error(self, default_config: GlmValidationConfig) -> None:
        """OSError (binary not found) → passed=False with cli_error category."""
        runner = _mock_runner_os_error()
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is not None
        assert "opencode-go not found" in result.error
        assert result.error_category == "cli_error"

    def test_exit_code_2(
        self, default_config: GlmValidationConfig
    ) -> None:
        """Exit code 2 (usage error) → passed=False."""
        runner = _mock_runner_failure(2, "unknown flag: --bad-flag")
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.error_category == "cli_error"


# ═════════════════════════════════════════════════════════════════════════
# Full pipeline — parse error paths
# ═════════════════════════════════════════════════════════════════════════


class TestFullPipelineParseErrors:
    """Tests where CLI succeeds but output parsing fails."""

    def test_empty_output(
        self, default_config: GlmValidationConfig
    ) -> None:
        """CLI returns empty stdout → parse error."""
        runner = _mock_runner_success("")
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is not None
        assert "parse failed" in result.error.lower()
        assert result.error_category == "parse_error"

    def test_whitespace_only_output(
        self, default_config: GlmValidationConfig
    ) -> None:
        """CLI returns whitespace-only stdout → parse error."""
        runner = _mock_runner_success("   \n\t\n   ")
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.error_category == "parse_error"

    def test_garbled_output(
        self, default_config: GlmValidationConfig
    ) -> None:
        """CLI returns non-JSON, non-delimited garbage → parse error."""
        runner = _mock_runner_success(
            "I'm sorry, I cannot process this request at the moment."
        )
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.error_category == "parse_error"

    def test_malformed_json(
        self, default_config: GlmValidationConfig
    ) -> None:
        """CLI returns truly unparseable text → parse error."""
        # Text that is neither valid JSON nor delimited format
        runner = _mock_runner_success(
            "The model returned unstructured prose without any JSON block."
        )
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.error_category == "parse_error"

    def test_missing_verdict_field(
        self, default_config: GlmValidationConfig
    ) -> None:
        """CLI returns valid JSON without verdict field → parse error."""
        stdout = json.dumps({"overall_score": 0.95, "notes": "looks good"})
        runner = _mock_runner_success(stdout)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is False
        assert result.error_category == "parse_error"


# ═════════════════════════════════════════════════════════════════════════
# Config type validation for run_glm_validation
# ═════════════════════════════════════════════════════════════════════════


class TestRunGlmValidationConfigErrors:
    """Tests for invalid config types passed to run_glm_validation."""

    def test_none_config_raises(self) -> None:
        """None config should raise TypeError."""
        with pytest.raises(TypeError, match="GlmValidationConfig"):
            run_glm_validation(None)  # type: ignore[arg-type]

    def test_dict_config_raises(self) -> None:
        """dict config should raise TypeError."""
        with pytest.raises(TypeError, match="GlmValidationConfig"):
            run_glm_validation({"model": "glm-5.1"})  # type: ignore[arg-type]

    def test_string_config_raises(self) -> None:
        """string config should raise TypeError."""
        with pytest.raises(TypeError, match="GlmValidationConfig"):
            run_glm_validation("glm-5.1")  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════
# ValidationResult properties
# ═════════════════════════════════════════════════════════════════════════


class TestValidationResultProperties:
    """Tests for ValidationResult convenience properties."""

    def test_passed_clean_true(self) -> None:
        """passed_clean=True when verdict is 'pass'."""
        result = ValidationResult(
            passed=True,
            confidence=0.95,
            error=None,
            verdict_raw="pass",
            duration_seconds=1.0,
        )
        assert result.passed_clean is True

    def test_passed_clean_false_for_conditional(self) -> None:
        """passed_clean=False when verdict is 'conditional_pass'."""
        result = ValidationResult(
            passed=True,
            confidence=0.82,
            error=None,
            verdict_raw="conditional_pass",
            duration_seconds=1.0,
        )
        assert result.passed_clean is False
        assert result.passed_conditional is True

    def test_passed_conditional_false_for_pass(self) -> None:
        """passed_conditional=False when verdict is 'pass'."""
        result = ValidationResult(
            passed=True,
            confidence=0.95,
            error=None,
            verdict_raw="pass",
            duration_seconds=1.0,
        )
        assert result.passed_conditional is False

    def test_passed_conditional_true(self) -> None:
        """passed_conditional=True when verdict is 'conditional_pass'."""
        result = ValidationResult(
            passed=True,
            confidence=0.82,
            error=None,
            verdict_raw="conditional_pass",
            duration_seconds=1.0,
        )
        assert result.passed_conditional is True

    def test_requires_codex_escalation_on_error(self) -> None:
        """Any error → requires_codex_escalation=True."""
        result = ValidationResult(
            passed=False,
            error="GLM timed out",
            verdict_raw="",
            error_category="cli_error",
        )
        assert result.requires_codex_escalation is True

    def test_requires_codex_escalation_on_escalate_verdict(self) -> None:
        """escalate verdict → requires_codex_escalation=True."""
        result = ValidationResult(
            passed=False,
            confidence=0.35,
            verdict_raw="escalate",
        )
        assert result.requires_codex_escalation is True

    def test_requires_codex_escalation_on_fail_verdict(self) -> None:
        """fail verdict → requires_codex_escalation=True."""
        result = ValidationResult(
            passed=False,
            confidence=0.42,
            verdict_raw="fail",
        )
        assert result.requires_codex_escalation is True

    def test_requires_codex_escalation_on_low_confidence(self) -> None:
        """confidence < 0.75 → requires_codex_escalation=True."""
        result = ValidationResult(
            passed=True,
            confidence=0.70,
            verdict_raw="pass",
        )
        assert result.requires_codex_escalation is True

    def test_no_codex_escalation_on_clean_pass(self) -> None:
        """Clean pass with high confidence → requires_codex_escalation=False."""
        result = ValidationResult(
            passed=True,
            confidence=0.95,
            verdict_raw="pass",
        )
        assert result.requires_codex_escalation is False

    def test_no_codex_escalation_on_conditional_pass_high_confidence(
        self,
    ) -> None:
        """Conditional pass with confidence >= 0.75 should not escalate."""
        result = ValidationResult(
            passed=True,
            confidence=0.82,
            verdict_raw="conditional_pass",
        )
        assert result.requires_codex_escalation is False

    def test_dual_validation_conflict_uses_glm_for_technical_domains(self) -> None:
        """Tech/security/data conflicts should prefer GLM validator output."""
        glm = ValidationResult(passed=True, confidence=0.91, verdict_raw="pass")
        codex = ValidationResult(passed=False, confidence=0.88, verdict_raw="fail")

        decision = resolve_dual_validation_conflict(glm, codex, domain="security")

        assert decision.winner == "glm"
        assert decision.passed is True
        assert decision.policy == "technical_domain_glm_authoritative"

    def test_dual_validation_conflict_uses_codex_for_policy_domains(self) -> None:
        """Legal/budget/brand conflicts should prefer Codex validator output."""
        glm = ValidationResult(passed=True, confidence=0.90, verdict_raw="pass")
        codex = ValidationResult(passed=False, confidence=0.86, verdict_raw="fail")

        decision = resolve_dual_validation_conflict(glm, codex, domain="legal")

        assert decision.winner == "codex"
        assert decision.passed is False
        assert decision.policy == "policy_domain_codex_authoritative"

    def test_dual_validation_conflict_factual_uses_higher_confidence(self) -> None:
        """Factual conflicts should choose the higher-confidence validator."""
        glm = ValidationResult(passed=False, confidence=0.61, verdict_raw="fail")
        codex = ValidationResult(passed=True, confidence=0.84, verdict_raw="pass")

        decision = resolve_dual_validation_conflict(glm, codex, domain="factual")

        assert decision.winner == "codex"
        assert decision.passed is True
        assert decision.policy == "factual_domain_confidence_tiebreak"

    def test_dual_validation_agreement_has_no_conflict(self) -> None:
        """Matching verdicts should report agreement without domain override."""
        glm = ValidationResult(passed=True, confidence=0.91, verdict_raw="pass")
        codex = ValidationResult(passed=True, confidence=0.86, verdict_raw="pass")

        decision = resolve_dual_validation_conflict(glm, codex, domain="brand")

        assert decision.conflict is False
        assert decision.winner == "agreement"
        assert decision.passed is True

    def test_default_values(self) -> None:
        """Default ValidationResult should have safe defaults."""
        result = ValidationResult()
        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is None
        assert result.verdict_raw == ""
        assert result.duration_seconds == 0.0
        assert result.error_category == ""


# ═════════════════════════════════════════════════════════════════════════
# ValidationResult immutability
# ═════════════════════════════════════════════════════════════════════════


class TestValidationResultImmutability:
    """Tests that ValidationResult is frozen."""

    def test_cannot_mutate_passed(self) -> None:
        result = ValidationResult(passed=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.passed = False  # type: ignore[misc]

    def test_cannot_mutate_confidence(self) -> None:
        result = ValidationResult(confidence=0.5)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.confidence = 0.9  # type: ignore[misc]

    def test_cannot_mutate_error(self) -> None:
        result = ValidationResult()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.error = "something"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# run_glm_validation_from_stdout
# ═════════════════════════════════════════════════════════════════════════


class TestRunGlmValidationFromStdout:
    """Tests for run_glm_validation_from_stdout (parser-only path)."""

    def test_happy_path(self, pass_stdout: str) -> None:
        """Valid GLM pass output should parse correctly."""
        result = run_glm_validation_from_stdout(pass_stdout)

        assert result.passed is True
        assert result.confidence == 0.95
        assert result.error is None
        assert result.verdict_raw == "pass"

    def test_conditional_pass(self, conditional_pass_stdout: str) -> None:
        """Conditional pass output should parse correctly."""
        result = run_glm_validation_from_stdout(conditional_pass_stdout)

        assert result.passed is True
        assert result.confidence == 0.82
        assert result.verdict_raw == "conditional_pass"

    def test_fail_verdict(self, fail_stdout: str) -> None:
        """Fail verdict should parse correctly with passed=False."""
        result = run_glm_validation_from_stdout(fail_stdout)

        assert result.passed is False
        assert result.confidence == 0.42
        assert result.verdict_raw == "fail"
        assert result.error is None  # Not a system error

    def test_parse_failure(self) -> None:
        """Garbled output → parse error."""
        result = run_glm_validation_from_stdout("not valid json at all")

        assert result.passed is False
        assert result.confidence == 0.0
        assert result.error is not None
        assert result.error_category == "parse_error"

    def test_empty_string(self) -> None:
        """Empty string → parse error."""
        result = run_glm_validation_from_stdout("")

        assert result.passed is False
        assert result.error_category == "parse_error"

    def test_duration_propagated(self) -> None:
        """When duration_seconds is provided, it should be set."""
        stdout = json.dumps({"verdict": "pass", "overall_score": 0.90})
        result = run_glm_validation_from_stdout(
            stdout, duration_seconds=5.5
        )

        assert result.passed is True
        assert result.duration_seconds == 5.5

    def test_duration_default_zero(self) -> None:
        """When duration_seconds is 0, it should be the actual elapsed time."""
        stdout = json.dumps({"verdict": "pass", "overall_score": 0.90})
        result = run_glm_validation_from_stdout(stdout)

        # With default 0, the function measures elapsed time
        # which should be > 0
        assert result.duration_seconds >= 0

    def test_delimited_format(self) -> None:
        """Key-value delimited format should be parsed."""
        stdout = "verdict: conditional_pass\noverall_score: 0.78"
        result = run_glm_validation_from_stdout(stdout)

        assert result.passed is True
        assert result.confidence == 0.78
        assert result.verdict_raw == "conditional_pass"


# ═════════════════════════════════════════════════════════════════════════
# Integration smoke test
# ═════════════════════════════════════════════════════════════════════════


class TestIntegrationSmoke:
    """Smoke tests that exercise the full pipeline with realistic data."""

    def test_realistic_full_validation_payload(
        self, default_config: GlmValidationConfig
    ) -> None:
        """A realistic GLM-5.1 validation response should parse correctly."""
        realistic_json = json.dumps({
            "verdict": "conditional_pass",
            "overall_score": 0.83,
            "confidence": 0.85,
            "areas": {
                "requirements_fit": {
                    "score": 0.90,
                    "notes": "Addresses all core user requirements",
                },
                "logical_consistency": {
                    "score": 0.80,
                    "notes": "Minor tension between roles R3 and R5",
                },
                "factual_grounding": {
                    "score": 0.88,
                    "notes": "Claims are well-sourced with citations",
                },
                "feasibility": {
                    "score": 0.75,
                    "notes": "Timeline is optimistic; needs buffer",
                },
                "risk_policy": {
                    "score": 0.82,
                    "notes": "Most risks identified; missing legal review",
                },
            },
            "risk_level": "medium",
            "issues": [
                {
                    "area": "feasibility",
                    "severity": "minor",
                    "description": "Timeline optimistic",
                },
                {
                    "area": "risk_policy",
                    "severity": "minor",
                    "description": "Missing legal review step",
                },
            ],
            "required_fixes": [
                "Add 2-week buffer to timeline",
                "Include legal review in risk assessment",
            ],
            "missing_requirements": [],
            "codex_escalation": {"required": False},
            "user_escalation": {"required": False},
            "recommended_next_state": "finalizing",
            "summary": "Meeting output is strong with two minor fixes needed.",
        })

        runner = _mock_runner_success(realistic_json)
        result = run_glm_validation(default_config, _injected_runner=runner)

        assert result.passed is True
        assert result.confidence == 0.83
        assert result.verdict_raw == "conditional_pass"
        assert result.error is None
        assert result.passed_conditional is True
        assert result.passed_clean is False
        # confidence 0.83 >= 0.75, so no automatic escalation
        assert result.requires_codex_escalation is False

    def test_stderr_warnings_on_success_do_not_affect_result(
        self, default_config: GlmValidationConfig
    ) -> None:
        """stderr warnings during a successful CLI call should not affect parsing."""
        stdout = json.dumps({"verdict": "pass", "overall_score": 0.92})

        def runner_with_stderr(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, stdout, "Warning: deprecated flag --legacy-format")

        result = run_glm_validation(
            default_config, _injected_runner=runner_with_stderr
        )

        # stderr warnings don't affect the result when exit_code=0
        assert result.passed is True
        assert result.confidence == 0.92

    def test_runner_injection_isolation(self) -> None:
        """The _injected_runner should not affect the module-level runner."""
        original_runner = _default_subprocess_runner

        # Inject a mock at module level
        def mock_runner(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
            return (0, '{"verdict": "pass", "overall_score": 0.99}', "")

        inject_runner(mock_runner)
        try:
            # Call with _injected_runner override — this should take precedence
            config = GlmValidationConfig(
                model="glm-5.1",
                context_file="/tmp/test.json",
            )
            result = run_glm_validation(
                config,
                _injected_runner=lambda *a, **kw: (
                    0,
                    '{"verdict": "conditional_pass", "overall_score": 0.77}',
                    "",
                ),
            )

            # The _injected_runner should be used, not the module-level one
            assert result.confidence == 0.77
            assert result.verdict_raw == "conditional_pass"
        finally:
            inject_runner(None)  # Restore default
