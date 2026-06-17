"""Comprehensive tests for the Qwen response field-type validator.

Sub-AC 2b-2: Qwen response field-type validator — accepts parsed dict,
validates all seven fields against expected schema (agenda_type: enum
restricted set, tags/risk_tags/roles: string arrays,
validator_required/codex_required: booleans, plus seventh contextual
field), aggregates type-mismatch and missing-field errors into
structured report.

Tested with mock dict payloads covering:
- all-valid payload (baseline)
- each field individually invalid
- missing required fields
- null / non-dict input
- contextual field edge cases (confidence out of range, wrong type)
- error aggregation / multiple simultaneous errors
- ValidationReport properties and immutability
"""

from __future__ import annotations

import pytest

from src.qwen_field_validator import (
    ALL_VALIDATED_FIELDS,
    FieldValidationError,
    ValidationReport,
    validate_qwen_response,
)


# ═══════════════════════════════════════════════════════════════════════
# Helper: build a valid payload with optional overrides
# ═══════════════════════════════════════════════════════════════════════

def _valid_payload(**overrides: object) -> dict[str, object]:
    """Return a fully valid Qwen classification dict."""
    defaults: dict[str, object] = {
        "agenda_type": "creative_production",
        "tags": ["character-design", "visual-concept", "sns-strategy"],
        "risk_tags": ["brand"],
        "required_roles": ["coordinator", "art-director", "marketing-lead"],
        "optional_roles": ["concept-artist", "sns-strategist"],
        "validator_required": True,
        "codex_required": False,
    }
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid payload — baseline
# ═══════════════════════════════════════════════════════════════════════

class TestValidPayload:
    """Verify that a fully correct payload passes validation."""

    def test_all_fields_valid_passes(self) -> None:
        report = validate_qwen_response(_valid_payload())
        assert report.passed
        assert report.error_count == 0
        assert len(report.errors) == 0

    def test_all_agenda_types_accepted(self) -> None:
        """Every valid agenda_type should pass enum validation."""
        valid_types = [
            "creative_production",
            "technical_development",
            "marketing_strategy",
            "risk_assessment",
            "general_planning",
            "project_review",
        ]
        for at in valid_types:
            report = validate_qwen_response(_valid_payload(agenda_type=at))
            assert report.passed, f"agenda_type '{at}' should be valid"

    def test_empty_risk_tags_accepted(self) -> None:
        """Empty risk_tags is valid — low-risk topics may have none."""
        report = validate_qwen_response(
            _valid_payload(risk_tags=[])
        )
        assert report.passed

    def test_empty_optional_roles_accepted(self) -> None:
        """Empty optional_roles is valid — not every meeting needs extras."""
        report = validate_qwen_response(
            _valid_payload(optional_roles=[])
        )
        assert report.passed

    def test_empty_tags_accepted(self) -> None:
        """Empty tags is tolerated — Qwen may legitimately return []."""
        report = validate_qwen_response(_valid_payload(tags=[]))
        assert report.passed

    def test_both_booleans_false_accepted(self) -> None:
        report = validate_qwen_response(
            _valid_payload(validator_required=False, codex_required=False)
        )
        assert report.passed

    def test_with_contextual_fields(self) -> None:
        """Confidence and reasoning are validated when present but not required."""
        payload = _valid_payload()
        payload["confidence"] = 0.92
        payload["reasoning"] = "Multi-domain topic spans art and marketing."
        report = validate_qwen_response(payload)
        assert report.passed

    def test_without_contextual_fields(self) -> None:
        """Missing confidence/reasoning should not cause failure."""
        report = validate_qwen_response(_valid_payload())
        assert report.passed

    def test_total_fields_checked_matches_declared(self) -> None:
        report = validate_qwen_response(_valid_payload())
        assert report.total_fields_checked == len(ALL_VALIDATED_FIELDS)


# ═══════════════════════════════════════════════════════════════════════
# 2. agenda_type validation
# ═══════════════════════════════════════════════════════════════════════

class TestAgendaTypeValidation:
    """Verify agenda_type enum and type checks."""

    def test_invalid_enum_value_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(agenda_type="bogus_meeting_type")
        )
        assert not report.passed
        assert report.error_count >= 1
        err = next(e for e in report.errors if e.field_name == "agenda_type")
        assert err.error_type == "invalid_value"
        assert "bogus_meeting_type" in err.actual

    def test_non_string_agenda_type_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(agenda_type=42)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(e for e in report.errors if e.field_name == "agenda_type")
        assert err.error_type == "wrong_type"

    def test_none_agenda_type_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(agenda_type=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(e for e in report.errors if e.field_name == "agenda_type")
        assert err.error_type == "wrong_type"

    def test_boolean_agenda_type_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(agenda_type=True)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(e for e in report.errors if e.field_name == "agenda_type")
        assert err.error_type == "wrong_type"

    def test_whitespace_trimmed_before_enum_check(self) -> None:
        """Trailing whitespace should be stripped so 'general_planning '
        matches."""
        report = validate_qwen_response(
            _valid_payload(agenda_type="general_planning ")
        )
        assert report.passed, (
            "Whitespace around agenda_type should be stripped "
            "before enum matching"
        )

    def test_case_sensitive_match(self) -> None:
        """'Creative_Production' (wrong case) should be rejected."""
        report = validate_qwen_response(
            _valid_payload(agenda_type="Creative_Production")
        )
        assert not report.passed


# ═══════════════════════════════════════════════════════════════════════
# 3. String array validation (tags, risk_tags, required_roles, optional_roles)
# ═══════════════════════════════════════════════════════════════════════

class TestStringArrayValidation:
    """Verify string-array type checks across all four array fields."""

    ARRAY_FIELDS = ("tags", "risk_tags", "required_roles", "optional_roles")

    def test_none_rejected_for_each_field(self) -> None:
        for field in self.ARRAY_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: None})
            )
            assert not report.passed, f"{field}=None should fail"
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "missing", (
                f"{field}=None should produce 'missing' error, "
                f"got '{err.error_type}'"
            )

    def test_string_instead_of_list_rejected(self) -> None:
        for field in self.ARRAY_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: "not_a_list"})
            )
            assert not report.passed, (
                f"{field}='str' should fail"
            )
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "wrong_type"

    def test_dict_instead_of_list_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(tags={"key": "value"})  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(e for e in report.errors if e.field_name == "tags")
        assert err.error_type == "wrong_type"

    def test_list_with_int_items_rejected(self) -> None:
        for field in self.ARRAY_FIELDS:
            payload = _valid_payload(**{field: [1, 2, 3]})
            report = validate_qwen_response(payload)
            assert not report.passed, (
                f"{field}=[1,2,3] should fail"
            )
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "non_string_item"

    def test_list_with_mixed_types_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(tags=["valid", 42, None, True])
        )
        assert not report.passed
        err = next(e for e in report.errors if e.field_name == "tags")
        assert err.error_type == "non_string_item"
        assert "3" in err.message  # 3 non-string items

    def test_list_with_float_items_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(required_roles=[3.14, 2.71])
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "required_roles"
        )
        assert err.error_type == "non_string_item"

    def test_empty_list_accepted_for_all_array_fields(self) -> None:
        for field in ("risk_tags", "optional_roles"):
            report = validate_qwen_response(
                _valid_payload(**{field: []})
            )
            assert report.passed, f"Empty {field} should be accepted"

    def test_int_instead_of_list_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(tags=42)  # type: ignore[arg-type]
        )
        assert not report.passed

    def test_boolean_instead_of_list_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(tags=True)  # type: ignore[arg-type]
        )
        assert not report.passed

    def test_none_items_in_list_flagged(self) -> None:
        """None inside a list should be caught as non-string."""
        report = validate_qwen_response(
            _valid_payload(required_roles=["coordinator", None])
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "required_roles"
        )
        assert err.error_type == "non_string_item"

    def test_large_non_string_list_reports_truncated_indices(self) -> None:
        """When >5 non-string items, indices should be truncated in message."""
        payload = _valid_payload(tags=[1, 2, 3, 4, 5, 6, 7])
        report = validate_qwen_response(payload)
        err = next(e for e in report.errors if e.field_name == "tags")
        assert "…" in err.message, (
            "Message should truncate long index lists"
        )


# ═══════════════════════════════════════════════════════════════════════
# 4. Boolean validation (validator_required, codex_required)
# ═══════════════════════════════════════════════════════════════════════

class TestBooleanValidation:
    """Verify strict boolean checks for validator_required and codex_required."""

    BOOLEAN_FIELDS = ("validator_required", "codex_required")

    def test_true_and_false_accepted(self) -> None:
        for field in self.BOOLEAN_FIELDS:
            for val in (True, False):
                report = validate_qwen_response(
                    _valid_payload(**{field: val})
                )
                assert report.passed, (
                    f"{field}={val} should pass"
                )

    def test_integer_1_rejected(self) -> None:
        """1 is truthy but NOT a bool — must be strict."""
        for field in self.BOOLEAN_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: 1})
            )
            assert not report.passed, (
                f"{field}=1 should fail (strict bool required)"
            )
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "wrong_type"

    def test_integer_0_rejected(self) -> None:
        for field in self.BOOLEAN_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: 0})
            )
            assert not report.passed

    def test_string_bool_rejected(self) -> None:
        for field in self.BOOLEAN_FIELDS:
            for val in ("true", "false", "True", "False", "yes", "no"):
                report = validate_qwen_response(
                    _valid_payload(**{field: val})
                )
                assert not report.passed, (
                    f"{field}='{val}' should fail"
                )

    def test_none_rejected(self) -> None:
        for field in self.BOOLEAN_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: None})
            )
            assert not report.passed
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "missing"

    def test_float_rejected(self) -> None:
        for field in self.BOOLEAN_FIELDS:
            report = validate_qwen_response(
                _valid_payload(**{field: 1.0})
            )
            assert not report.passed

    def test_list_rejected(self) -> None:
        report = validate_qwen_response(
            _valid_payload(validator_required=[True])  # type: ignore[arg-type]
        )
        assert not report.passed


# ═══════════════════════════════════════════════════════════════════════
# 5. Missing required fields
# ═══════════════════════════════════════════════════════════════════════

class TestMissingFields:
    """Verify that every required field is checked for presence."""

    REQUIRED_FIELD_NAMES = (
        "agenda_type",
        "tags",
        "risk_tags",
        "required_roles",
        "optional_roles",
        "validator_required",
        "codex_required",
    )

    def test_each_required_field_missing_detected(self) -> None:
        for field in self.REQUIRED_FIELD_NAMES:
            payload = _valid_payload()
            del payload[field]
            report = validate_qwen_response(payload)
            assert not report.passed, (
                f"Missing '{field}' should cause failure"
            )
            err = next(e for e in report.errors if e.field_name == field)
            assert err.error_type == "missing"

    def test_all_fields_missing(self) -> None:
        """Empty dict should produce one error per required field."""
        report = validate_qwen_response({})
        assert not report.passed
        assert report.error_count == len(self.REQUIRED_FIELD_NAMES)
        for field in self.REQUIRED_FIELD_NAMES:
            assert any(
                e.field_name == field and e.error_type == "missing"
                for e in report.errors
            ), f"Missing-field error for '{field}' not found"

    def test_contextual_fields_missing_is_ok(self) -> None:
        """Confidence and reasoning absence should NOT be an error."""
        payload = _valid_payload()
        # verify payload doesn't have them
        assert "confidence" not in payload
        assert "reasoning" not in payload
        report = validate_qwen_response(payload)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 6. Null / non-dict input
# ═══════════════════════════════════════════════════════════════════════

class TestNullAndNonDictInput:
    """Verify graceful handling of None and non-dict arguments."""

    def test_none_input_fails(self) -> None:
        report = validate_qwen_response(None)
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "missing"
        assert report.total_fields_checked == 0

    def test_list_input_fails(self) -> None:
        report = validate_qwen_response([1, 2, 3])  # type: ignore[arg-type]
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "wrong_type"

    def test_string_input_fails(self) -> None:
        report = validate_qwen_response("not a dict")  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_int_input_fails(self) -> None:
        report = validate_qwen_response(42)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 7. Contextual field validation (confidence, reasoning)
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceValidation:
    """Verify confidence float range and type checks."""

    def test_valid_confidence_accepted(self) -> None:
        for val in (0.0, 0.5, 0.92, 1.0, 0, 1):
            payload = _valid_payload()
            payload["confidence"] = val
            report = validate_qwen_response(payload)
            assert report.passed, f"confidence={val} should pass"

    def test_confidence_below_zero_rejected(self) -> None:
        payload = _valid_payload()
        payload["confidence"] = -0.1
        report = validate_qwen_response(payload)
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"

    def test_confidence_above_one_rejected(self) -> None:
        payload = _valid_payload()
        payload["confidence"] = 1.5
        report = validate_qwen_response(payload)
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"

    def test_confidence_boolean_rejected(self) -> None:
        """JSON booleans (true/false) are not valid for float field."""
        payload = _valid_payload()
        payload["confidence"] = True
        report = validate_qwen_response(payload)
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "wrong_type"

    def test_confidence_string_rejected(self) -> None:
        payload = _valid_payload()
        payload["confidence"] = "high"
        report = validate_qwen_response(payload)
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "wrong_type"

    def test_confidence_none_accepted(self) -> None:
        """None confidence is fine — Coordinator supplies default."""
        payload = _valid_payload()
        payload["confidence"] = None
        report = validate_qwen_response(payload)
        assert report.passed

    def test_confidence_as_float_str_rejected(self) -> None:
        """'0.92' is a string, not a float."""
        payload = _valid_payload()
        payload["confidence"] = "0.92"
        report = validate_qwen_response(payload)
        assert not report.passed


class TestReasoningValidation:
    """Verify reasoning string type check."""

    def test_valid_reasoning_accepted(self) -> None:
        payload = _valid_payload()
        payload["reasoning"] = "Multi-domain topic."
        report = validate_qwen_response(payload)
        assert report.passed

    def test_reasoning_none_accepted(self) -> None:
        payload = _valid_payload()
        payload["reasoning"] = None
        report = validate_qwen_response(payload)
        assert report.passed

    def test_reasoning_non_string_rejected(self) -> None:
        payload = _valid_payload()
        payload["reasoning"] = 42
        report = validate_qwen_response(payload)
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "reasoning"
        )
        assert err.error_type == "wrong_type"

    def test_reasoning_boolean_rejected(self) -> None:
        payload = _valid_payload()
        payload["reasoning"] = True
        report = validate_qwen_response(payload)
        assert not report.passed

    def test_reasoning_list_rejected(self) -> None:
        payload = _valid_payload()
        payload["reasoning"] = ["reason", "one", "two"]
        report = validate_qwen_response(payload)
        assert not report.passed


# ═══════════════════════════════════════════════════════════════════════
# 8. Error aggregation and multi-error scenarios
# ═══════════════════════════════════════════════════════════════════════

class TestErrorAggregation:
    """Verify that ALL errors are collected — no early-exit on first."""

    def test_multiple_errors_all_reported(self) -> None:
        """Six fields wrong → six errors reported (not just the first)."""
        report = validate_qwen_response({
            "agenda_type": 123,
            "tags": "not_list",
            "risk_tags": None,
            "required_roles": [1, 2],
            "optional_roles": None,
            "validator_required": "yes",
            "codex_required": 0,
        })
        assert not report.passed
        # 7 fields, 6 invalid, 1 missing (codex_required had 0 which is wrong_type)
        # wait: all 7 are present but most have wrong types
        # agenda_type: wrong_type (int)
        # tags: wrong_type (str)
        # risk_tags: missing (None)
        # required_roles: non_string_item
        # optional_roles: missing (None)
        # validator_required: wrong_type (str)
        # codex_required: wrong_type (int)
        assert report.error_count >= 6, (
            f"Expected at least 6 errors, got {report.error_count}"
        )

    def test_errors_by_field_groups_correctly(self) -> None:
        """errors_by_field() should group by field name."""
        payload = _valid_payload()
        payload["tags"] = "not_a_list"
        payload["agenda_type"] = "bogus"
        payload["confidence"] = 2.0
        report = validate_qwen_response(payload)

        grouped = report.errors_by_field()
        assert "tags" in grouped
        assert "agenda_type" in grouped
        assert "confidence" in grouped
        assert len(grouped["tags"]) == 1

    def test_passed_when_no_errors(self) -> None:
        report = validate_qwen_response(_valid_payload())
        assert report.passed
        assert report.error_count == 0
        assert len(report.errors) == 0
        assert report.errors_by_field() == {}


# ═══════════════════════════════════════════════════════════════════════
# 9. ValidationReport immutability
# ═══════════════════════════════════════════════════════════════════════

class TestValidationReportImmutability:
    """Verify report and error dataclasses are frozen."""

    def test_report_is_immutable(self) -> None:
        report = validate_qwen_response(_valid_payload())
        with pytest.raises(Exception):
            report.passed = False  # type: ignore[misc]

    def test_error_is_immutable(self) -> None:
        report = validate_qwen_response(_valid_payload(agenda_type="bogus"))
        err = report.errors[0]
        with pytest.raises(Exception):
            err.field_name = "changed"  # type: ignore[misc]

    def test_errors_tuple_is_immutable(self) -> None:
        report = validate_qwen_response(_valid_payload())
        with pytest.raises(Exception):
            report.errors.append(  # type: ignore[union-attr]
                FieldValidationError(
                    field_name="x",
                    error_type="test",
                    message="",
                    expected="",
                    actual="",
                )
            )


# ═══════════════════════════════════════════════════════════════════════
# 10. Edge cases and boundary conditions
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Miscellaneous edge cases and boundary conditions."""

    def test_single_item_role_list_accepted(self) -> None:
        report = validate_qwen_response(
            _valid_payload(required_roles=["coordinator"])
        )
        assert report.passed

    def test_many_tags_accepted(self) -> None:
        """Large string arrays should not cause issues."""
        tags = [f"tag-{i}" for i in range(100)]
        report = validate_qwen_response(_valid_payload(tags=tags))
        assert report.passed

    def test_extra_unknown_fields_ignored(self) -> None:
        """Validator should not fail on unexpected extra fields."""
        payload = _valid_payload()
        payload["extra_field"] = "should be ignored"
        payload["another"] = 42
        report = validate_qwen_response(payload)
        assert report.passed, (
            "Extra unknown fields should be silently ignored"
        )

    def test_confidence_boundary_values(self) -> None:
        """0.0 and 1.0 are valid inclusive bounds."""
        for val in (0.0, 1.0):
            payload = _valid_payload()
            payload["confidence"] = val
            report = validate_qwen_response(payload)
            assert report.passed, f"confidence={val} should pass"

    def test_agenda_type_with_internal_spaces_not_stripped(self) -> None:
        """'creative production' (space instead of underscore) should fail."""
        report = validate_qwen_response(
            _valid_payload(agenda_type="creative production")
        )
        assert not report.passed

    def test_empty_string_in_tags_not_causing_failure(self) -> None:
        """Empty strings inside arrays are logged but do not cause
        the overall validation to fail (downstream normalisation strips)."""
        report = validate_qwen_response(
            _valid_payload(tags=["valid", "", "  "])
        )
        # Currently, empty strings are detected but don't add errors
        # This test verifies the current behaviour
        assert report.passed

    def test_very_long_tag_string(self) -> None:
        """Very long tag strings should be fine."""
        long_tag = "a" * 500
        report = validate_qwen_response(
            _valid_payload(tags=[long_tag])
        )
        assert report.passed

    def test_unicode_tags(self) -> None:
        """Unicode (Korean, Japanese, emoji) in tags should pass."""
        report = validate_qwen_response(
            _valid_payload(tags=["캐릭터-디자인", "ビジュアル", "🎨"])
        )
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 11. FieldValidationError representation
# ═══════════════════════════════════════════════════════════════════════

class TestFieldValidationErrorRepresentation:
    """Verify error objects carry all required metadata."""

    def test_error_has_all_fields(self) -> None:
        err = FieldValidationError(
            field_name="test_field",
            error_type="wrong_type",
            message="Something went wrong.",
            expected="list[str]",
            actual="str",
        )
        assert err.field_name == "test_field"
        assert err.error_type == "wrong_type"
        assert err.message == "Something went wrong."
        assert err.expected == "list[str]"
        assert err.actual == "str"

    def test_error_repr_is_readable(self) -> None:
        err = FieldValidationError(
            field_name="tags",
            error_type="wrong_type",
            message="tags must be a list, got str",
            expected="list[str]",
            actual="str",
        )
        rep = repr(err)
        assert "tags" in rep
        assert "wrong_type" in rep
        assert "str" in rep
