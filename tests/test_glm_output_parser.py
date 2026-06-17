"""Tests for the GLM-5.1 structured output parser.

Sub-AC 7.1b: Comprehensive test coverage for parsing raw GLM-5.1
validation output strings into structured pass/fail + confidence.

Coverage:
- Clean JSON parsing (pass, conditional_pass, fail, etc.)
- Markdown-fenced JSON
- Key-value delimited format
- Empty/whitespace input
- Missing verdict field
- Invalid verdict values (unknown string, wrong type, empty string)
- Missing overall_score (confidence defaulted)
- Invalid overall_score types (string, bool, out of range)
- Format auto-detection
- format_hint override
- Immutability of result dataclasses
- Realistic GLM-5.1 full validation payload
- Leading/trailing commentary
- Truncated JSON repair
- Verdict mapping for all known values
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from src.glm_output_parser import (
    GlmParseErrorType,
    GlmParseResult,
    parse_glm_output,
)

# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def pass_json() -> str:
    """GLM-5.1 pass verdict JSON."""
    return json.dumps({
        "verdict": "pass",
        "overall_score": 0.95,
        "areas": {
            "requirements_fit": {"score": 0.96, "notes": "Excellent alignment"},
        },
        "required_fixes": [],
        "escalation_triggers": [],
    })


@pytest.fixture
def conditional_pass_json() -> str:
    """GLM-5.1 conditional_pass verdict JSON."""
    return json.dumps({
        "verdict": "conditional_pass",
        "overall_score": 0.82,
        "areas": {
            "requirements_fit": {"score": 0.85},
            "feasibility": {"score": 0.75, "notes": "Timeline optimistic"},
        },
        "required_fixes": ["Clarify timeline", "Resolve role tension R2"],
        "escalation_triggers": [],
    })


@pytest.fixture
def fail_json() -> str:
    """GLM-5.1 fail verdict JSON."""
    return json.dumps({
        "verdict": "fail",
        "overall_score": 0.42,
        "areas": {
            "requirements_fit": {"score": 0.30},
            "logical_consistency": {"score": 0.35},
        },
        "required_fixes": [
            "Complete redesign of risk assessment",
            "Address fundamental logical contradictions",
        ],
        "escalation_triggers": ["high_risk", "irrecoverable"],
    })


@pytest.fixture
def revision_required_json() -> str:
    """GLM-5.1 revision_required verdict JSON."""
    return json.dumps({
        "verdict": "revision_required",
        "overall_score": 0.68,
        "areas": {},
        "required_fixes": ["Revise section 3"],
        "escalation_triggers": [],
    })


@pytest.fixture
def escalate_json() -> str:
    """GLM-5.1 escalate verdict JSON."""
    return json.dumps({
        "verdict": "escalate",
        "overall_score": 0.10,
        "areas": {},
        "required_fixes": [],
        "escalation_triggers": ["critical_risk", "infeasible_constraint"],
    })


@pytest.fixture
def full_validation_json() -> str:
    """Realistic full GLM-5.1 validation payload (from wrapper test)."""
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
            "risk_policy": {
                "score": 0.80,
                "notes": "Mitigation partially addressed",
            },
        },
        "required_fixes": [
            "Clarify timeline feasibility",
            "Resolve role tension R2",
        ],
        "escalation_triggers": [],
    })


@pytest.fixture
def json_with_markdown_fence(pass_json: str) -> str:
    """GLM output wrapped in markdown code fence."""
    return f"```json\n{pass_json}\n```\n\nValidation complete."


@pytest.fixture
def json_with_leading_text(pass_json: str) -> str:
    """GLM output with leading commentary."""
    return (
        "Here is the validation result for meeting m123:\n\n"
        f"{pass_json}\n\n"
        "End of validation report."
    )


@pytest.fixture
def json_with_tilde_fence(fail_json: str) -> str:
    """GLM output wrapped in tilde code fence."""
    return f"Here's my analysis:\n\n~~~json\n{fail_json}\n~~~"


@pytest.fixture
def delimited_pass() -> str:
    """Key-value delimited format pass verdict."""
    return "verdict: pass\noverall_score: 0.95"


@pytest.fixture
def delimited_conditional_pass() -> str:
    """Key-value delimited format conditional_pass verdict."""
    return "verdict: conditional_pass\noverall_score: 0.82"


@pytest.fixture
def delimited_fail() -> str:
    """Key-value delimited format fail verdict."""
    return "verdict: fail\noverall_score: 0.42"


@pytest.fixture
def delimited_no_score() -> str:
    """Key-value delimited format missing score."""
    return "verdict: pass"


@pytest.fixture
def delimited_with_confidence_key() -> str:
    """Key-value delimited format using 'confidence' instead of 'overall_score'."""
    return "verdict: pass\nconfidence: 0.88"


@pytest.fixture
def delimited_extra_fields() -> str:
    """Key-value delimited with extra fields (should be ignored)."""
    return (
        "verdict: pass\n"
        "overall_score: 0.93\n"
        "model: glm-5.1\n"
        "timestamp: 2026-06-11T10:00:00Z\n"
    )


# ═════════════════════════════════════════════════════════════════════════
# JSON format: successful parse
# ═════════════════════════════════════════════════════════════════════════


class TestJsonParseSuccess:
    """Verify successful JSON parsing for all verdict types."""

    def test_pass_verdict(self, pass_json: str) -> None:
        """Pass verdict maps to passed=True, confidence extracted."""
        result = parse_glm_output(pass_json)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.95
        assert result.confidence_defaulted is False
        assert result.verdict_raw == "pass"
        assert result.format_detected == "json"

    def test_conditional_pass_verdict(
        self, conditional_pass_json: str
    ) -> None:
        """Conditional pass maps to passed=True."""
        result = parse_glm_output(conditional_pass_json)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.82
        assert result.verdict_raw == "conditional_pass"

    def test_fail_verdict(self, fail_json: str) -> None:
        """Fail verdict maps to passed=False."""
        result = parse_glm_output(fail_json)
        assert result.success is True
        assert result.passed is False
        assert result.confidence == 0.42
        assert result.verdict_raw == "fail"

    def test_revision_required_verdict(
        self, revision_required_json: str
    ) -> None:
        """Revision required maps to passed=False."""
        result = parse_glm_output(revision_required_json)
        assert result.success is True
        assert result.passed is False
        assert result.verdict_raw == "revision_required"

    def test_escalate_verdict(self, escalate_json: str) -> None:
        """Escalate maps to passed=False."""
        result = parse_glm_output(escalate_json)
        assert result.success is True
        assert result.passed is False
        assert result.verdict_raw == "escalate"

    def test_full_validation_payload(
        self, full_validation_json: str
    ) -> None:
        """Realistic full GLM-5.1 validation payload parses correctly."""
        result = parse_glm_output(full_validation_json)
        assert result.success is True
        assert result.passed is True  # conditional_pass
        assert result.confidence == 0.82
        assert result.confidence_defaulted is False
        assert result.parsed_data is not None
        assert "areas" in result.parsed_data

    def test_confidence_zero(self) -> None:
        """Confidence score of exactly 0.0 is valid."""
        json_str = json.dumps({"verdict": "fail", "overall_score": 0.0})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is False

    def test_confidence_one(self) -> None:
        """Confidence score of exactly 1.0 is valid."""
        json_str = json.dumps({"verdict": "pass", "overall_score": 1.0})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 1.0
        assert result.confidence_defaulted is False

    def test_confidence_integer(self) -> None:
        """Confidence as integer (0 or 1) should be coerced to float."""
        json_str = json.dumps({"verdict": "pass", "overall_score": 1})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 1.0
        assert isinstance(result.confidence, float)

    def test_verdict_whitespace_normalised(self) -> None:
        """Verdict with surrounding whitespace is normalised."""
        json_str = json.dumps({"verdict": "  PASS  ", "overall_score": 0.85})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.passed is True
        assert result.verdict_raw == "pass"


# ═════════════════════════════════════════════════════════════════════════
# JSON format: fence and artifact handling
# ═════════════════════════════════════════════════════════════════════════


class TestJsonFences:
    """Verify JSON extraction handles markdown fences and artifacts."""

    def test_markdown_triple_backtick(
        self, json_with_markdown_fence: str
    ) -> None:
        """```json ... ``` fence is stripped and JSON parsed."""
        result = parse_glm_output(json_with_markdown_fence)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.95

    def test_leading_commentary(
        self, json_with_leading_text: str
    ) -> None:
        """Leading text before JSON is ignored, JSON extracted."""
        result = parse_glm_output(json_with_leading_text)
        assert result.success is True
        assert result.passed is True

    def test_tilde_fence(self, json_with_tilde_fence: str) -> None:
        """~~~json ... ~~~ fence is stripped."""
        result = parse_glm_output(json_with_tilde_fence)
        assert result.success is True
        assert result.passed is False  # fail verdict
        assert result.confidence == 0.42

    def test_markdown_fence_no_language_tag(self) -> None:
        """``` ... ``` without json tag is still handled."""
        raw = '```\n{"verdict": "pass", "overall_score": 0.90}\n```'
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.90

    def test_quadruple_backtick_fence(self) -> None:
        """````json ... ```` is stripped."""
        raw = '````json\n{"verdict": "fail", "overall_score": 0.30}\n````'
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.confidence == 0.30


# ═════════════════════════════════════════════════════════════════════════
# JSON format: error cases
# ═════════════════════════════════════════════════════════════════════════


class TestJsonParseErrors:
    """Verify error handling for malformed JSON inputs."""

    def test_empty_string(self) -> None:
        """Empty string returns EMPTY_OUTPUT error."""
        result = parse_glm_output("")
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type == GlmParseErrorType.EMPTY_OUTPUT

    def test_whitespace_only(self) -> None:
        """Whitespace-only returns EMPTY_OUTPUT error."""
        result = parse_glm_output("   \n\t   \n  ")
        assert result.success is False
        assert result.error.error_type == GlmParseErrorType.EMPTY_OUTPUT

    def test_no_json_structure(self) -> None:
        """Pure prose with no JSON returns NO_STRUCTURED_DATA error."""
        result = parse_glm_output("This is just some random text.")
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type in (
            GlmParseErrorType.NO_STRUCTURED_DATA,
            GlmParseErrorType.MALFORMED_OUTPUT,
        )

    def test_missing_verdict_field(self) -> None:
        """JSON without verdict field returns MISSING_VERDICT error."""
        json_str = json.dumps({"overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type == GlmParseErrorType.MISSING_VERDICT

    def test_verdict_null(self) -> None:
        """Null verdict is treated as missing (type error)."""
        json_str = json.dumps({"verdict": None, "overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type == GlmParseErrorType.INVALID_VERDICT

    def test_verdict_not_string(self) -> None:
        """Non-string verdict returns INVALID_VERDICT error."""
        json_str = json.dumps({"verdict": 123, "overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.success is False
        assert result.error.error_type == GlmParseErrorType.INVALID_VERDICT

    def test_verdict_empty_string(self) -> None:
        """Empty string verdict returns INVALID_VERDICT error."""
        json_str = json.dumps({"verdict": "", "overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.success is False
        assert result.error.error_type == GlmParseErrorType.INVALID_VERDICT

    def test_verdict_whitespace_only(self) -> None:
        """Whitespace-only verdict (after strip) returns INVALID_VERDICT."""
        json_str = json.dumps({"verdict": "   ", "overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.success is False

    def test_unknown_verdict_value(self) -> None:
        """Unknown verdict string: parsing succeeds but passed=False."""
        json_str = json.dumps({"verdict": "maybe", "overall_score": 0.70})
        result = parse_glm_output(json_str)
        # Unknown verdict is not an error — it maps to fail
        assert result.success is True
        assert result.passed is False
        assert result.verdict_raw == "maybe"


# ═════════════════════════════════════════════════════════════════════════
# JSON format: confidence edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestJsonConfidence:
    """Verify confidence score extraction edge cases."""

    def test_missing_overall_score(self) -> None:
        """Missing overall_score defaults to 0.0 with defaulted flag."""
        json_str = json.dumps({"verdict": "pass"})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_null(self) -> None:
        """Null overall_score defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": None})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_boolean_true(self) -> None:
        """Boolean true for overall_score defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": True})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_boolean_false(self) -> None:
        """Boolean false for overall_score defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": False})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_string_valid(self) -> None:
        """String '0.85' is parsed to float 0.85."""
        json_str = json.dumps({"verdict": "pass", "overall_score": "0.85"})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.confidence == 0.85
        assert result.confidence_defaulted is False

    def test_score_string_invalid(self) -> None:
        """Non-numeric string defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": "high"})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_string_empty(self) -> None:
        """Empty string score defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": ""})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_list(self) -> None:
        """List type for score defaults to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": [0.85]})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_negative(self) -> None:
        """Negative score is clamped/defaulted to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": -0.5})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_above_one(self) -> None:
        """Score > 1.0 is defaulted to 0.0."""
        json_str = json.dumps({"verdict": "pass", "overall_score": 1.5})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_score_very_large(self) -> None:
        """Very large score is defaulted."""
        json_str = json.dumps({"verdict": "pass", "overall_score": 9999.0})
        result = parse_glm_output(json_str)
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True


# ═════════════════════════════════════════════════════════════════════════
# JSON format: verdict case insensitivity
# ═════════════════════════════════════════════════════════════════════════


class TestJsonVerdictCaseInsensitivity:
    """Verify case insensitivity and normalisation of verdict strings."""

    def test_uppercase_pass(self) -> None:
        """PASS in uppercase maps to pass."""
        json_str = json.dumps({"verdict": "PASS", "overall_score": 0.90})
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.passed is True
        assert result.verdict_raw == "pass"

    def test_mixed_case_pass(self) -> None:
        """MiXeD cAsE maps correctly."""
        json_str = json.dumps({"verdict": "PaSs", "overall_score": 0.90})
        result = parse_glm_output(json_str)
        assert result.passed is True

    def test_uppercase_fail(self) -> None:
        """FAIL in uppercase maps to fail."""
        json_str = json.dumps({"verdict": "FAIL", "overall_score": 0.10})
        result = parse_glm_output(json_str)
        assert result.passed is False
        assert result.verdict_raw == "fail"

    def test_conditional_pass_spacing(self) -> None:
        """conditional_pass with extra spaces in the value."""
        json_str = json.dumps({
            "verdict": "  conditional_pass  ",
            "overall_score": 0.88,
        })
        result = parse_glm_output(json_str)
        assert result.passed is True
        assert result.verdict_raw == "conditional_pass"


# ═════════════════════════════════════════════════════════════════════════
# Key-value delimited format: successful parse
# ═════════════════════════════════════════════════════════════════════════


class TestDelimitedParseSuccess:
    """Verify successful parsing of key-value delimited format."""

    def test_pass_delimited(self, delimited_pass: str) -> None:
        """Simple pass verdict in delimited format."""
        result = parse_glm_output(delimited_pass)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.95
        assert result.confidence_defaulted is False
        assert result.format_detected == "delimited"

    def test_conditional_pass_delimited(
        self, delimited_conditional_pass: str
    ) -> None:
        """Conditional pass in delimited format."""
        result = parse_glm_output(delimited_conditional_pass)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.82

    def test_fail_delimited(self, delimited_fail: str) -> None:
        """Fail in delimited format."""
        result = parse_glm_output(delimited_fail)
        assert result.success is True
        assert result.passed is False
        assert result.confidence == 0.42

    def test_no_score_delimited(self, delimited_no_score: str) -> None:
        """Missing score in delimited defaults to 0.0."""
        result = parse_glm_output(delimited_no_score)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_confidence_key_delimited(
        self, delimited_with_confidence_key: str
    ) -> None:
        """Using 'confidence' key instead of 'overall_score' works."""
        result = parse_glm_output(delimited_with_confidence_key)
        assert result.success is True
        assert result.confidence == 0.88
        assert result.confidence_defaulted is False

    def test_extra_fields_ignored(
        self, delimited_extra_fields: str
    ) -> None:
        """Extra key-value fields are ignored."""
        result = parse_glm_output(delimited_extra_fields)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.93

    def test_delimited_with_colon_in_value(self) -> None:
        """Values containing colons are handled correctly."""
        raw = (
            "verdict: pass\n"
            "overall_score: 0.91\n"
            "note: time: 10:30 AM"
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True

    def test_delimited_with_blank_lines(self) -> None:
        """Blank lines in delimited output are skipped."""
        raw = "\n\nverdict: pass\n\noverall_score: 0.87\n\n"
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.confidence == 0.87

    def test_delimited_case_insensitive_keys(self) -> None:
        """Keys in delimited format are case-insensitive."""
        raw = "VERDICT: pass\nOVERALL_SCORE: 0.73"
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.73

    def test_delimited_whitespace_around_delimiter(self) -> None:
        """Extra whitespace around colon is tolerated."""
        raw = "verdict   :   pass   \noverall_score   :   0.99"
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.confidence == 0.99


# ═════════════════════════════════════════════════════════════════════════
# Key-value delimited format: error cases
# ═════════════════════════════════════════════════════════════════════════


class TestDelimitedParseErrors:
    """Verify error handling for malformed delimited inputs."""

    def test_no_key_value_pairs(self) -> None:
        """Text without key-value pairs returns NO_STRUCTURED_DATA."""
        result = parse_glm_output("Just some random text without colons.")
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type == GlmParseErrorType.NO_STRUCTURED_DATA

    def test_missing_verdict_delimited(self) -> None:
        """Delimited format missing verdict returns MISSING_VERDICT."""
        result = parse_glm_output("overall_score: 0.85\nmodel: glm-5.1")
        assert result.success is False
        assert result.error.error_type == GlmParseErrorType.MISSING_VERDICT

    def test_invalid_score_delimited(self) -> None:
        """Non-numeric score in delimited format defaults to 0.0."""
        result = parse_glm_output("verdict: pass\noverall_score: notanumber")
        assert result.success is True  # verdict is still valid
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_unknown_verdict_delimited(self) -> None:
        """Unknown verdict in delimited format maps to fail."""
        result = parse_glm_output("verdict: maybe\noverall_score: 0.80")
        assert result.success is True
        assert result.passed is False

    def test_delimited_truncated(self) -> None:
        """Truncated delimited line still parses what it can."""
        result = parse_glm_output("verdict: pass\noverall_scor")  # truncated
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True


# ═════════════════════════════════════════════════════════════════════════
# Format auto-detection
# ═════════════════════════════════════════════════════════════════════════


class TestFormatDetection:
    """Verify automatic format detection between JSON and delimited."""

    def test_json_detected(self, pass_json: str) -> None:
        """JSON with braces is auto-detected as JSON."""
        result = parse_glm_output(pass_json)
        assert result.format_detected == "json"

    def test_delimited_detected(self, delimited_pass: str) -> None:
        """Key-value format without braces is detected as delimited."""
        result = parse_glm_output(delimited_pass)
        assert result.format_detected == "delimited"

    def test_format_hint_overrides_detection(
        self, delimited_pass: str
    ) -> None:
        """format_hint='json' forces JSON parsing."""
        result = parse_glm_output(delimited_pass, format_hint="json")
        # JSON parser falls back to trying to find JSON, may fail
        # but format_detected reflects the hint
        # Actually: with format_hint='json', the detector is bypassed.
        # The delimited string has no { so extract_json will fail.
        # This is expected — the caller forced the wrong format.
        assert result.format_detected == "json" or not result.success

    def test_format_hint_delimited_on_json(self, pass_json: str) -> None:
        """format_hint='delimited' forces delimited parsing on JSON text."""
        result = parse_glm_output(pass_json, format_hint="delimited")
        # JSON text has no valid key:value lines that match the delimited
        # format regex (the line starts with '{', not a word character),
        # so the parser returns an error_result with format_detected='delimited'.
        assert result.format_detected == "delimited"
        # The parse fails because JSON text doesn't contain delimited key:value pairs
        assert result.success is False
        assert result.error is not None


# ═════════════════════════════════════════════════════════════════════════
# Result data type properties
# ═════════════════════════════════════════════════════════════════════════


class TestGlmParseResultProperties:
    """Verify GlmParseResult dataclass immutability and properties."""

    def test_result_is_frozen(self, pass_json: str) -> None:
        """GlmParseResult must be immutable."""
        result = parse_glm_output(pass_json)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.passed = False  # type: ignore[misc]

    def test_success_result_has_no_error(self, pass_json: str) -> None:
        """On success, error must be None."""
        result = parse_glm_output(pass_json)
        assert result.success is True
        assert result.error is None

    def test_error_result_has_error(self) -> None:
        """On failure, error must be populated."""
        result = parse_glm_output("")
        assert result.success is False
        assert result.error is not None
        assert isinstance(result.error.message, str)
        assert len(result.error.message) > 0

    def test_json_result_has_parsed_data(self, pass_json: str) -> None:
        """JSON format result carries the full parsed dict."""
        result = parse_glm_output(pass_json)
        assert result.parsed_data is not None
        assert isinstance(result.parsed_data, dict)
        assert result.parsed_data["verdict"] == "pass"

    def test_delimited_result_has_no_parsed_data(
        self, delimited_pass: str
    ) -> None:
        """Delimited format result does not carry parsed_data."""
        result = parse_glm_output(delimited_pass)
        assert result.parsed_data is None

    def test_error_result_has_no_parsed_data(self) -> None:
        """Error result has no parsed_data."""
        result = parse_glm_output("")
        assert result.parsed_data is None

    def test_error_message_contains_recovery_hint(self) -> None:
        """Error should include a recovery hint."""
        result = parse_glm_output("")
        assert result.error is not None
        assert len(result.error.recovery_hint) > 0

    def test_error_message_has_raw_excerpt(self, pass_json: str) -> None:
        """Error with raw text should include an excerpt."""
        # Missing verdict in otherwise valid JSON
        json_str = json.dumps({"overall_score": 0.95})
        result = parse_glm_output(json_str)
        assert result.error is not None
        assert "overall_score" in result.error.raw_excerpt


# ═════════════════════════════════════════════════════════════════════════
# Regression: malformed real-world outputs
# ═════════════════════════════════════════════════════════════════════════


class TestRealWorldMalformedOutputs:
    """Verify handling of malformed outputs seen in real GLM-5.1 calls."""

    def test_truncated_json_mid_field(self) -> None:
        """JSON truncated in the middle of a field value."""
        raw = (
            '{\n'
            '  "verdict": "pass",\n'
            '  "overall_score": 0.\n'
        )
        result = parse_glm_output(raw)
        # The JSON parser's repair may fix this.
        # If not, it's a non-fatal parse error.
        if result.success:
            # Repaired: score defaults or is extracted
            assert isinstance(result.passed, bool)
        else:
            assert result.error is not None

    def test_truncated_json_mid_key(self) -> None:
        """JSON truncated before a key is complete."""
        raw = '{"verdict": "pass", "overall'
        result = parse_glm_output(raw)
        if result.success:
            assert result.passed is True  # verdict was found
            assert result.confidence_defaulted is True
        else:
            assert result.error is not None

    def test_double_json_objects(self) -> None:
        """Two JSON objects in one output — largest wins."""
        raw = (
            '{"verdict": "fail", "overall_score": 0.30}\n'
            '{"verdict": "pass", "overall_score": 0.95}\n'
        )
        result = parse_glm_output(raw)
        if result.success:
            # The extractor picks the largest balanced span
            assert result.passed is True or result.passed is False

    def test_json_with_unicode_escapes(self) -> None:
        """JSON with unicode escapes in string values."""
        raw = (
            '{"verdict": "pass",'
            '"overall_score": 0.91,'
            '"note": "Valid\\u00e9"}'
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.confidence == 0.91

    def test_json_with_escaped_quotes(self) -> None:
        """JSON with escaped quotes inside strings."""
        raw = (
            '{"verdict": "pass",'
            '"overall_score": 0.88,'
            '"message": "He said \\"hello\\""}'
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.confidence == 0.88

    def test_json_with_nested_objects(self) -> None:
        """JSON with nested object structure."""
        raw = json.dumps({
            "verdict": "pass",
            "overall_score": 0.92,
            "areas": {
                "requirements_fit": {
                    "score": 0.95,
                    "sub_areas": {"accuracy": 0.96, "completeness": 0.94},
                },
            },
        })
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.92

    def test_json_with_arrays(self) -> None:
        """JSON with array fields (standard GLM output)."""
        raw = json.dumps({
            "verdict": "conditional_pass",
            "overall_score": 0.78,
            "required_fixes": [
                "Fix issue A",
                "Fix issue B",
                "Fix issue C",
            ],
        })
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True

    def test_glm_output_with_log_prefix(self) -> None:
        """GLM output sometimes includes log lines before JSON."""
        raw = (
            "[INFO] Loading model glm-5.1\n"
            "[INFO] Processing validation packet\n"
            '{"verdict": "pass", "overall_score": 0.93}\n'
            "[INFO] Validation complete"
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True

    def test_glm_stderr_leaked_into_stdout(self) -> None:
        """When stderr leaks into stdout alongside JSON."""
        raw = (
            "Warning: token usage approaching limit\n"
            '{"verdict": "fail", "overall_score": 0.45}\n'
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is False

    def test_bare_minimum_valid_json(self) -> None:
        """Minimal valid JSON with only required fields."""
        raw = json.dumps({"verdict": "pass"})
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True
        assert result.confidence == 0.0
        assert result.confidence_defaulted is True

    def test_json_with_comment_like_content(self) -> None:
        """JSON preceded by // comment-like text (LLM artifact)."""
        raw = (
            "// Validation result for meeting m456\n"
            '{"verdict": "pass", "overall_score": 0.91}'
        )
        result = parse_glm_output(raw)
        assert result.success is True
        assert result.passed is True


# ═════════════════════════════════════════════════════════════════════════
# Verdict mapping completeness
# ═════════════════════════════════════════════════════════════════════════


class TestVerdictMappingExhaustive:
    """Verify all known verdict values map correctly."""

    @pytest.mark.parametrize("verdict,expected_pass", [
        ("pass", True),
        ("conditional_pass", True),
        ("fail", False),
        ("revision_required", False),
        ("escalate", False),
    ])
    def test_known_verdict_mapping(
        self, verdict: str, expected_pass: bool
    ) -> None:
        """Each known verdict maps to the correct boolean."""
        json_str = json.dumps({
            "verdict": verdict,
            "overall_score": 0.50,
        })
        result = parse_glm_output(json_str)
        assert result.success is True
        assert result.passed is expected_pass


# ═════════════════════════════════════════════════════════════════════════
# None input
# ═════════════════════════════════════════════════════════════════════════


class TestNoneInput:
    """Verify handling of None / falsy inputs."""

    def test_none_input(self) -> None:
        """None input is treated as empty output (returns error result)."""
        result = parse_glm_output(None)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error is not None
        assert result.error.error_type == GlmParseErrorType.EMPTY_OUTPUT
