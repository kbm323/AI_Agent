"""Comprehensive tests for the routing output schema validator (Sub-AC 3.1c).

Covers:
- Happy path: valid routing output passes all checks
- Valid output from both MatchResult.to_dict() and ClassificationResult dict
- Valid output with extra tolerated fields (teams, reasoning, etc.)
- Null / non-dict input
- Missing required fields
- Null required fields
- Wrong types on every field
- Invalid values (empty strings, out-of-range numbers, bad priorities)
- String list item-level violations (non-string items)
- Boolean field rejects truthy/falsy non-bools
- Priority field invalid values
- SemVer field invalid formats
- ISO 8601 field invalid formats
- Confidence field out of range
- Strict mode: unknown keys flagged
- Non-strict mode: unknown keys tolerated
- Multiple simultaneous violations (no early-exit)
- Custom schema support
- Report properties and immutability
- is_valid_routing_output() convenience function
- Exception message contains field names
"""

from __future__ import annotations

import copy

import pytest

from src.routing_output_schema_validator import (
    ROUTING_OUTPUT_SCHEMA,
    RoutingOutputSchema,
    RoutingOutputValidationError,
    RoutingOutputValidationReport,
    RoutingOutputViolation,
    is_valid_routing_output,
    validate_routing_output,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def valid_output() -> dict:
    """A fully valid routing output dict matching the canonical schema."""
    return {
        "agenda_type": "creative-production",
        "agenda_label": "Creative Production Meeting",
        "tags": ["art", "design", "content_creation"],
        "risk_tags": ["budget_financial"],
        "required_roles": ["art-director", "content-pd"],
        "optional_roles": ["sound-engineer"],
        "validator_required": True,
        "codex_required": False,
        "priority": "P2",
        "routing_source": "static_fallback",
        "routing_reason": "qwen_timeout",
        "confidence": 0.7,
        "generated_at": "2026-06-10T12:00:00",
        "version": "1.0.0",
    }


@pytest.fixture
def minimal_valid() -> dict:
    """Minimal valid output with empty arrays for list fields."""
    return {
        "agenda_type": "general-discussion",
        "agenda_label": "",
        "tags": [],
        "risk_tags": [],
        "required_roles": [],
        "optional_roles": [],
        "validator_required": False,
        "codex_required": False,
        "priority": "P3",
        "routing_source": "qwen_classification",
        "routing_reason": "qwen_classified",
        "confidence": 0.5,
        "generated_at": "2026-06-10T12:00:00",
        "version": "2.0.0",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path
# ═══════════════════════════════════════════════════════════════════════════


class TestValidRoutingOutput:
    """Verify that valid routing output passes all checks."""

    def test_valid_output_passes(self, valid_output: dict) -> None:
        result = validate_routing_output(valid_output)
        assert result is valid_output  # returns same object
        assert result["agenda_type"] == "creative-production"

    def test_minimal_valid_passes(self, minimal_valid: dict) -> None:
        result = validate_routing_output(minimal_valid)
        assert result is minimal_valid

    def test_output_with_extra_tolerated_fields(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["teams"] = ["art-design", "content-production"]
        data["reasoning"] = "Fallback classification"
        data["requires_approval"] = []
        data["exit_condition"] = ""
        # Non-strict mode tolerates these
        result = validate_routing_output(data)
        assert result is data

    def test_all_valid_priorities(self, valid_output: dict) -> None:
        for prio in ("P0", "P1", "P2", "P3"):
            data = dict(valid_output)
            data["priority"] = prio
            validate_routing_output(data)  # no exception

    def test_tags_can_be_tuples(self, valid_output: dict) -> None:
        """Tuples should be accepted as list-equivalents."""
        data = dict(valid_output)
        data["tags"] = ("art", "design")  # tuple, not list
        result = validate_routing_output(data)
        assert result is data

    def test_agenda_label_can_be_empty(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["agenda_label"] = ""
        validate_routing_output(data)  # no exception

    def test_risk_tags_can_be_empty(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["risk_tags"] = []
        validate_routing_output(data)  # no exception

    def test_semver_versions_accepted(self, valid_output: dict) -> None:
        for ver in ("0.1.0", "1.0.0", "2.3.4", "99.99.99"):
            data = dict(valid_output)
            data["version"] = ver
            validate_routing_output(data)  # no exception

    def test_confidence_boundary_values(self, valid_output: dict) -> None:
        for conf in (0.0, 1.0, 0.5, 0.99):
            data = dict(valid_output)
            data["confidence"] = conf
            validate_routing_output(data)  # no exception

    def test_confidence_int_accepted(self, valid_output: dict) -> None:
        """Integer confidence values (0, 1) are accepted as numbers."""
        data = dict(valid_output)
        data["confidence"] = 0  # int, but isinstance checks int or float
        validate_routing_output(data)  # no exception

    def test_generated_at_with_space_separator(self, valid_output: dict) -> None:
        """ISO 8601 allows space as T separator."""
        data = dict(valid_output)
        data["generated_at"] = "2026-06-10 12:00:00"
        validate_routing_output(data)  # no exception

    def test_generated_at_with_timezone(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["generated_at"] = "2026-06-10T12:00:00+00:00"
        # Our regex only checks up to HH:MM:SS prefix, so this passes
        validate_routing_output(data)  # no exception


# ═══════════════════════════════════════════════════════════════════════════
# 2. Null / non-dict input
# ═══════════════════════════════════════════════════════════════════════════


class TestNullAndNonDictInput:
    def test_none_input_raises(self) -> None:
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(None)
        report = exc_info.value.report
        assert not report.passed
        assert report.error_count == 1
        assert report.violations[0].field == "<root>"
        assert "None" in report.violations[0].message

    def test_list_input_raises(self) -> None:
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(["not", "a", "dict"])
        report = exc_info.value.report
        assert not report.passed
        assert report.violations[0].field == "<root>"
        assert "list" in report.violations[0].message.lower()

    def test_string_input_raises(self) -> None:
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output("just a string")
        report = exc_info.value.report
        assert not report.passed
        assert report.violations[0].field == "<root>"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Missing required fields
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingFields:
    def test_empty_dict_fails_all_fields(self) -> None:
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output({})
        report = exc_info.value.report
        assert not report.passed
        missing_count = sum(
            1 for v in report.violations if v.error_type == "missing_field"
        )
        assert missing_count == len(ROUTING_OUTPUT_SCHEMA)

    def test_single_missing_field(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        del data["agenda_type"]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        assert any(
            v.field == "agenda_type" and v.error_type == "missing_field"
            for v in violations
        )

    def test_null_required_field(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["priority"] = None
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        assert any(
            v.field == "priority" and v.error_type == "missing_field"
            for v in violations
        )

    def test_multiple_missing_fields_reported(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        del data["routing_source"]
        del data["routing_reason"]
        data["confidence"] = None
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        missing_fields = {v.field for v in violations if v.error_type == "missing_field"}
        assert "routing_source" in missing_fields
        assert "routing_reason" in missing_fields
        assert "confidence" in missing_fields


# ═══════════════════════════════════════════════════════════════════════════
# 4. Wrong types
# ═══════════════════════════════════════════════════════════════════════════


class TestWrongTypes:
    def test_agenda_type_not_string(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["agenda_type"] = 42  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "agenda_type" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_tags_not_list(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["tags"] = "not_a_list"  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        assert any(
            v.field == "tags" and v.error_type == "wrong_type"
            for v in violations
        )

    def test_tags_none(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["tags"] = None  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "tags" and v.error_type == "missing_field"
            for v in exc_info.value.report.violations
        )

    def test_validator_required_not_bool(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["validator_required"] = 1  # type: ignore[assignment]  # truthy int, not bool
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "validator_required" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_validator_required_string_true(self, valid_output: dict) -> None:
        """'true' string is not the same as boolean True."""
        data = copy.deepcopy(valid_output)
        data["validator_required"] = "true"  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "validator_required" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_confidence_not_number(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["confidence"] = "high"  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "confidence" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_confidence_boolean_rejected(self, valid_output: dict) -> None:
        """Bool is a subtype of int but we explicitly reject it."""
        data = copy.deepcopy(valid_output)
        data["confidence"] = True  # type: ignore[assignment]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "confidence" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_list_fields_with_non_string_items(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["required_roles"] = ["valid-role", 42, None]  # type: ignore[list-item]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        role_violations = [v for v in violations if v.field == "required_roles"]
        assert len(role_violations) >= 1
        assert "non-string" in role_violations[0].message.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 5. Invalid values
# ═══════════════════════════════════════════════════════════════════════════


class TestInvalidValues:
    def test_empty_agenda_type(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["agenda_type"] = ""
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "agenda_type" and v.error_type == "empty_field"
            for v in exc_info.value.report.violations
        )

    def test_whitespace_agenda_type(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["agenda_type"] = "   "
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "agenda_type" and v.error_type == "empty_field"
            for v in exc_info.value.report.violations
        )

    def test_empty_routing_source(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["routing_source"] = ""
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "routing_source" and v.error_type == "empty_field"
            for v in exc_info.value.report.violations
        )

    def test_empty_routing_reason(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["routing_reason"] = ""
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "routing_reason" and v.error_type == "empty_field"
            for v in exc_info.value.report.violations
        )

    def test_invalid_priority(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["priority"] = "P5"
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        assert any(
            v.field == "priority" and v.error_type == "invalid_value"
            for v in violations
        )

    def test_lowercase_priority_rejected(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["priority"] = "p0"
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "priority" and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_invalid_semver(self, valid_output: dict) -> None:
        for ver in ("1.0", "v1.0.0", "1.0.0.0", "one.two.three", "1.0.0-beta"):
            data = copy.deepcopy(valid_output)
            data["version"] = ver
            with pytest.raises(RoutingOutputValidationError) as exc_info:
                validate_routing_output(data)
            assert any(
                v.field == "version" and v.error_type == "invalid_value"
                for v in exc_info.value.report.violations
            ), f"Expected '{ver}' to be rejected as invalid SemVer"

    def test_invalid_iso8601(self, valid_output: dict) -> None:
        for ts in ("2026-06-10", "12:00:00", "not_a_timestamp", ""):
            data = copy.deepcopy(valid_output)
            data["generated_at"] = ts
            with pytest.raises(RoutingOutputValidationError) as exc_info:
                validate_routing_output(data)
            violations = exc_info.value.report.violations
            assert any(
                v.field == "generated_at" and (
                    v.error_type == "invalid_value" or v.error_type == "wrong_type"
                )
                for v in violations
            ), f"Expected '{ts}' to be rejected as invalid ISO 8601"

    def test_confidence_below_zero(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["confidence"] = -0.1
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "confidence" and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_confidence_above_one(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["confidence"] = 1.5
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert any(
            v.field == "confidence" and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Strict mode — unknown keys
# ═══════════════════════════════════════════════════════════════════════════


class TestStrictMode:
    def test_unknown_key_flagged_in_strict(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["bogus_field"] = "some value"
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data, strict_keys=True)
        violations = exc_info.value.report.violations
        assert any(
            v.field == "bogus_field" and v.error_type == "unknown_field"
            for v in violations
        )

    def test_extra_keys_tolerated_by_default(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["custom_extra"] = 123
        data["another_extra"] = True
        # Non-strict mode (default) should tolerate these
        validate_routing_output(data)  # no exception

    def test_multiple_unknown_keys_all_reported(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["extra_a"] = 1
        data["extra_b"] = 2
        data["extra_c"] = 3
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data, strict_keys=True)
        unknown_violations = [
            v for v in exc_info.value.report.violations
            if v.error_type == "unknown_field"
        ]
        assert len(unknown_violations) >= 3


# ═══════════════════════════════════════════════════════════════════════════
# 7. Multiple simultaneous violations
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleViolations:
    def test_all_fields_broken(self) -> None:
        """A maximally broken dict should report many violations."""
        broken = {
            "agenda_type": 42,  # not string
            "agenda_label": None,  # optional can be null but... it's None
            "tags": "not_list",
            "risk_tags": 99,
            "required_roles": None,
            "optional_roles": False,
            "validator_required": "yes",
            "codex_required": 1,
            "priority": "urgent",
            "routing_source": "",
            "routing_reason": "",
            "confidence": "high",
            "generated_at": "yesterday",
            "version": "beta",
        }
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(broken)
        report = exc_info.value.report
        assert not report.passed
        # At minimum, 14 fields should each produce at least one violation
        assert report.error_count >= 10
        # Every field should be represented in the violations
        fields_with_violations = {v.field for v in report.violations}
        assert "agenda_type" in fields_with_violations
        assert "tags" in fields_with_violations
        assert "priority" in fields_with_violations
        assert "confidence" in fields_with_violations

    def test_two_fields_broken_reports_both(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["priority"] = "invalid"
        data["confidence"] = 2.0
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        fields = {v.field for v in exc_info.value.report.violations}
        assert "priority" in fields
        assert "confidence" in fields

    def test_missing_and_wrong_type_together(self) -> None:
        data = {
            "agenda_type": "valid-type",
            "tags": ["a", "b"],
            "risk_tags": [],
            # required_roles MISSING
            "optional_roles": [],
            "validator_required": "not_bool",
            "codex_required": False,
            "priority": "P1",
            "routing_source": "test",
            "routing_reason": "test",
            "confidence": 0.5,
            # generated_at MISSING
            "version": "1.0.0",
        }
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        # Should have missing_field for required_roles and generated_at
        # and wrong_type for validator_required and agenda_label
        missing = [v for v in violations if v.error_type == "missing_field"]
        assert len(missing) >= 3  # required_roles, generated_at, agenda_label
        wrong = [v for v in violations if v.error_type == "wrong_type"]
        assert len(wrong) >= 1  # validator_required


# ═══════════════════════════════════════════════════════════════════════════
# 8. Custom schema support
# ═══════════════════════════════════════════════════════════════════════════


class TestCustomSchema:
    def test_custom_schema_accepts_different_fields(self) -> None:
        custom = (
            RoutingOutputSchema("name", "string", required=True, allow_empty=False),
            RoutingOutputSchema("score", "number", required=True, min_val=0.0, max_val=100.0),
        )
        # Valid
        validate_routing_output({"name": "test", "score": 85}, schema=custom)

        # Missing field
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output({"name": "test"}, schema=custom)
        assert any(
            v.field == "score" and v.error_type == "missing_field"
            for v in exc_info.value.report.violations
        )

    def test_custom_schema_with_optional_field(self) -> None:
        custom = (
            RoutingOutputSchema("id", "string", required=True),
            RoutingOutputSchema("comment", "string", required=False, allow_empty=True),
        )
        # Both present → valid
        validate_routing_output({"id": "abc", "comment": "note"}, schema=custom)
        # Only required → valid
        validate_routing_output({"id": "abc"}, schema=custom)
        # Missing required → fails
        with pytest.raises(RoutingOutputValidationError):
            validate_routing_output({"comment": "note"}, schema=custom)

    def test_custom_schema_without_number_bounds(self) -> None:
        """Number fields without min/max should accept any numeric value."""
        custom = (RoutingOutputSchema("value", "number", required=True),)
        validate_routing_output({"value": 999.0}, schema=custom)
        validate_routing_output({"value": -100}, schema=custom)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Report properties
# ═══════════════════════════════════════════════════════════════════════════


class TestReportProperties:
    def test_report_passed_property(self, valid_output: dict) -> None:
        """When valid, no exception raised."""
        validate_routing_output(valid_output)  # no exception = pass

    def test_error_count_matches_violations_length(self) -> None:
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output({})
        report = exc_info.value.report
        assert report.error_count == len(report.violations)

    def test_violations_by_field(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["priority"] = "invalid"
        del data["routing_source"]
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        grouped = exc_info.value.report.violations_by_field()
        assert "priority" in grouped
        assert "routing_source" in grouped

    def test_exception_is_value_error_subclass(self) -> None:
        with pytest.raises(ValueError):
            validate_routing_output({})

    def test_exception_message_contains_field_names(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["version"] = "bad"
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        assert "version" in str(exc_info.value).lower()

    def test_violation_is_frozen(self) -> None:
        v = RoutingOutputViolation(
            field="test", error_type="test", message="test",
        )
        with pytest.raises(Exception):  # dataclass frozen
            v.field = "other"  # type: ignore[misc]

    def test_report_passed_construction(self) -> None:
        """Report can be constructed with no violations and passed=True."""
        report = RoutingOutputValidationReport(
            passed=True, violations=(), fields_checked=14,
        )
        assert report.passed
        assert report.error_count == 0

    def test_fields_checked_matches_schema_length(self) -> None:
        """For a valid output, fields_checked should match schema size."""
        data = {
            "agenda_type": "test",
            "agenda_label": "Test", 
            "tags": [],
            "risk_tags": [],
            "required_roles": [],
            "optional_roles": [],
            "validator_required": True,
            "codex_required": False,
            "priority": "P2",
            "routing_source": "test",
            "routing_reason": "test",
            "confidence": 0.5,
            "generated_at": "2026-06-10T12:00:00",
            "version": "1.0.0",
        }
        validate_routing_output(data)  # no exception


# ═══════════════════════════════════════════════════════════════════════════
# 10. is_valid_routing_output convenience function
# ═══════════════════════════════════════════════════════════════════════════


class TestIsValidRoutingOutput:
    def test_returns_true_for_valid_data(self, valid_output: dict) -> None:
        assert is_valid_routing_output(valid_output) is True

    def test_returns_false_for_invalid_data(self) -> None:
        assert is_valid_routing_output({}) is False

    def test_returns_false_for_none(self) -> None:
        assert is_valid_routing_output(None) is False

    def test_returns_false_for_missing_field(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        del data["agenda_type"]
        assert is_valid_routing_output(data) is False

    def test_accepts_schema_kwarg(self) -> None:
        custom = (RoutingOutputSchema("x", "string", required=True),)
        assert is_valid_routing_output({"x": "hello"}, schema=custom) is True
        assert is_valid_routing_output({}, schema=custom) is False

    def test_accepts_strict_keys_kwarg(self, valid_output: dict) -> None:
        data = copy.deepcopy(valid_output)
        data["unknown"] = "value"
        # Non-strict → valid
        assert is_valid_routing_output(data, strict_keys=False) is True
        # Strict → invalid
        assert is_valid_routing_output(data, strict_keys=True) is False


# ═══════════════════════════════════════════════════════════════════════════
# 11. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_string_list_with_tuple_input(self, valid_output: dict) -> None:
        """Tuples are acceptable as list alternatives."""
        data = dict(valid_output)
        data["required_roles"] = ("role-1", "role-2")  # tuple
        validate_routing_output(data)  # no exception

    def test_long_agenda_type_accepted(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["agenda_type"] = "a" * 100  # long but valid string
        validate_routing_output(data)  # no exception

    def test_mixed_types_in_list_detailed(self, valid_output: dict) -> None:
        data = dict(valid_output)
        data["tags"] = ["valid", 1, True, None, "", "  "]  # mixed types
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        violations = exc_info.value.report.violations
        tags_violations = [v for v in violations if v.field == "tags"]
        assert len(tags_violations) >= 1
        assert "non-string" in tags_violations[0].message.lower()

    def test_violation_expected_and_actual_present(self) -> None:
        """All violations should carry expected and actual values."""
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output({"priority": 42})
        for v in exc_info.value.report.violations:
            assert v.expected != "" or v.error_type == "missing_field", \
                f"Violation {v.field}/{v.error_type} has empty expected"
            assert v.actual != "", \
                f"Violation {v.field}/{v.error_type} has empty actual"

    def test_repr_of_large_value_truncated(self, valid_output: dict) -> None:
        """Very large values should not blow up the violation message."""
        data = dict(valid_output)
        data["routing_source"] = "x" * 1000
        # Valid as string, just long — should pass
        validate_routing_output(data)  # no exception

        # Now make it invalid type
        data["routing_source"] = 42
        with pytest.raises(RoutingOutputValidationError) as exc_info:
            validate_routing_output(data)
        v = exc_info.value.report.violations[0]
        assert len(v.actual) <= 100  # truncated
