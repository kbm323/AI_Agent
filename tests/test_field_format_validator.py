"""Comprehensive tests for the field format and type validator.

Sub-AC 6.1.2: Field format and type validation — verifies each field
conforms to its expected type, schema, and format constraints; testable
with malformed types, invalid enums, and boundary-value format inputs.

Test coverage:
- meeting_id format validation (valid, malformed, missing, wrong type)
- non_empty_string validation (empty, whitespace, None, wrong type)
- enum_string validation (valid values, invalid values, case-insensitive,
  whitespace-trimmed, None, wrong type)
- kebab_id_string validation (valid, malformed, uppercase, underscores, etc.)
- string_array validation (valid, empty, non-string items, empty-string
  items, allow_empty=False)
- boolean strict validation (True/False only, rejects ints, strings, None)
- positive_int validation (valid, zero, negative, float, boolean)
- non_negative_int validation (valid, negative, boundary zero)
- int_in_range validation (boundary values, out-of-range, boolean)
- float_0_1 validation (boundary values, out-of-range, boolean rejected)
- iso8601_string validation (valid formats, invalid formats, edge cases,
  allow_empty variant)
- list validation (valid, None, wrong type)
- list_of_dicts validation (all dicts, mixed, non-dict items)
- dict_or_none validation
- string_or_none validation
- string_or_empty validation
- path_string validation
- version_string validation (valid, malformed)
- validate_field single-field API
- validate_dict_fields API
- validate_manifest_fields integration
- validate_context_packet_format integration
- None and non-dict input
- Error aggregation (multiple simultaneous errors)
- FormatValidationReport properties (errors_by_field, error_count)
"""

from __future__ import annotations

import pytest

from src.field_format_validator import (
    ALL_MANIFEST_FIELDS,
    CONTEXT_PACKET_FORMAT_FIELDS,
    MANIFEST_CORE_FIELDS,
    VALID_AGENDA_TYPES,
    VALID_PRIORITIES,
    VALID_ROUND_TYPES,
    VALID_STATES,
    VALID_VERDICTS,
    FormatValidationError,
    FormatValidationReport,
    validate_context_packet_format,
    validate_dict_fields,
    validate_field,
    validate_manifest_fields,
)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _valid_manifest(
    **overrides: object,
) -> dict[str, object]:
    """Return a fully valid meeting manifest dict."""
    defaults: dict[str, object] = {
        "meeting_id": "meeting_20260610_5a36918413b1",
        "state": "created",
        "priority": "p2",
        "agenda": "Music video opening ideas",
        "agenda_type": "creative_production",
        "tags": ["mv", "visual-concept", "opening-sequence"],
        "risk_tags": ["brand"],
        "required_roles": ["coordinator", "art-director"],
        "optional_roles": ["concept-artist"],
        "round_count": 0,
        "validation_score": 0.0,
        "validation_verdict": "",
        "validator_required": True,
        "codex_required": False,
        "consensus": "",
        "user_id": "u1",
        "channel_id": "c1",
        "thread_id": "",
        "guild_id": "",
        "error_log": [],
        "manifest_path": "/home/kbm/F:ai-projects/AI_Agent/meetings/m1/manifest.json",
        "created_at": "2026-06-10T14:21:52Z",
        "updated_at": "2026-06-10T14:21:52Z",
        "schema_version": "meeting-manifest.v1",
        "max_rounds": 3,
        "max_agents_per_meeting": 7,
        "token_limit_worker": 12000,
        "token_limit_validator": 20000,
        "token_limit_codex": 30000,
        "primary_validator_model": "glm-5.1",
        "conditional_validator_model": "gpt-5.5",
        "completed_step": None,
        "meetings_root": "meetings",
        "max_concurrent_meetings": 2,
        "max_concurrent_opencode_calls": 4,
    }
    defaults.update(overrides)
    return defaults


def _valid_context_packet(
    **overrides: object,
) -> dict[str, object]:
    """Return a fully valid context packet dict."""
    defaults: dict[str, object] = {
        "meeting_id": "meeting_20260610_5a36918413b1",
        "role_id": "art-director",
        "state": "in_meeting",
        "priority": "p2",
        "agenda": "Music video opening ideas",
        "agenda_type": "creative_production",
        "tags": ["mv", "visual-concept"],
        "risk_tags": ["brand"],
        "round": 1,
        "round_type": "opinion",
        "round_context": "Propose creative directions for the MV opening.",
        "token_budget": 12000,
        "previous_rounds": [],
    }
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid payload — baseline
# ═══════════════════════════════════════════════════════════════════════

class TestValidPayload:
    """Verify that fully correct payloads pass format validation."""

    def test_valid_manifest_passes(self) -> None:
        report = validate_manifest_fields(_valid_manifest())
        assert report.passed
        assert report.error_count == 0
        assert report.schema_version == "field-format-validation.v1"

    def test_valid_context_packet_passes(self) -> None:
        report = validate_context_packet_format(_valid_context_packet())
        assert report.passed
        assert report.error_count == 0

    def test_all_fields_checked_count(self) -> None:
        report = validate_manifest_fields(_valid_manifest())
        assert report.total_fields_checked == len(ALL_MANIFEST_FIELDS)

    def test_context_packet_fields_checked_count(self) -> None:
        report = validate_context_packet_format(_valid_context_packet())
        assert report.total_fields_checked == len(
            CONTEXT_PACKET_FORMAT_FIELDS
        )


# ═══════════════════════════════════════════════════════════════════════
# 2. meeting_id format validation
# ═══════════════════════════════════════════════════════════════════════

class TestMeetingIdFormat:
    """Verify meeting_id regex pattern validation."""

    VALID_IDS = [
        "meeting_20260610_5a36918413b1",
        "meeting_20260101_000000000000",
        "meeting_20991231_ffffffffffff",
        "meeting_20260610_abcdef012345",
    ]

    INVALID_IDS = [
        ("meeting_20260610_5a36918413b", "too few hex chars (11)"),
        ("meeting_20260610_5a36918413b1X", "uppercase hex char"),
        ("meeting_20260610_5a3691841", "too short overall"),
        ("meeting_202606105a36918413b1", "missing underscore before hex"),
        ("meeting_202606-10_5a36918413b1", "hyphen in date part"),
        ("Meeting_20260610_5a36918413b1", "uppercase M"),
        ("meeting_20260610_GGGGGGGGGGGG", "non-hex chars"),
    ]

    def test_valid_meeting_ids_accepted(self) -> None:
        for mid in self.VALID_IDS:
            report = validate_manifest_fields(
                _valid_manifest(meeting_id=mid)
            )
            assert report.passed, f"meeting_id '{mid}' should be valid"

    def test_invalid_meeting_ids_rejected(self) -> None:
        for mid, _desc in self.INVALID_IDS:
            report = validate_manifest_fields(
                _valid_manifest(meeting_id=mid)
            )
            assert not report.passed, (
                f"meeting_id '{mid}' ({_desc}) should be rejected"
            )

    def test_meeting_id_none_rejected(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(meeting_id=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "meeting_id"
        )
        assert err.error_type == "missing"

    def test_meeting_id_int_rejected(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(meeting_id=42)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "meeting_id"
        )
        assert err.error_type == "wrong_type"

    def test_meeting_id_whitespace_only_rejected(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(meeting_id="   ")
        )
        assert not report.passed


# ═══════════════════════════════════════════════════════════════════════
# 3. non_empty_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestNonEmptyString:
    """Verify non-empty string validation in isolation."""

    def test_valid_string_accepted(self) -> None:
        errors = validate_field("agenda", "Test agenda", "non_empty_string")
        assert len(errors) == 0

    def test_empty_string_rejected(self) -> None:
        errors = validate_field("agenda", "", "non_empty_string")
        assert len(errors) == 1
        assert errors[0].error_type == "empty_string"

    def test_whitespace_only_rejected(self) -> None:
        errors = validate_field("agenda", "   \t\n", "non_empty_string")
        assert len(errors) == 1
        assert errors[0].error_type == "empty_string"

    def test_none_rejected(self) -> None:
        errors = validate_field("agenda", None, "non_empty_string")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_int_rejected(self) -> None:
        errors = validate_field("agenda", 42, "non_empty_string")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_bool_rejected(self) -> None:
        errors = validate_field("agenda", True, "non_empty_string")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 4. enum_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestEnumString:
    """Verify enum string validation with various edge cases."""

    def test_valid_enum_accepted(self) -> None:
        for val in VALID_STATES:
            errors = validate_field(
                "state", val, "enum_string", enum=VALID_STATES
            )
            assert len(errors) == 0, f"state '{val}' should be valid"

    def test_invalid_enum_rejected(self) -> None:
        errors = validate_field(
            "state", "bogus_state", "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 1
        assert errors[0].error_type == "invalid_value"
        assert "bogus_state" in errors[0].actual

    def test_case_insensitive_accepted(self) -> None:
        """Case-insensitive matching should accept uppercase variants."""
        for val in ("P0", "P1", "P2", "P3"):
            errors = validate_field(
                "priority", val, "enum_string", enum=VALID_PRIORITIES
            )
            assert len(errors) == 0, (
                f"priority '{val}' should be accepted (case-insensitive)"
            )

    def test_mixed_case_accepted(self) -> None:
        errors = validate_field(
            "state", "In_Meeting", "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 0

    def test_whitespace_trimmed(self) -> None:
        errors = validate_field(
            "state", "  created  ", "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 0, "Whitespace-trimmed enum should match"

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "state", None, "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_int_rejected(self) -> None:
        errors = validate_field(
            "state", 42, "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_bool_rejected(self) -> None:
        errors = validate_field(
            "state", False, "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 1

    def test_all_valid_priorities_accepted(self) -> None:
        for pri in sorted(VALID_PRIORITIES):
            errors = validate_field(
                "priority", pri, "enum_string", enum=VALID_PRIORITIES
            )
            assert len(errors) == 0

    def test_p5_rejected(self) -> None:
        errors = validate_field(
            "priority", "p5", "enum_string", enum=VALID_PRIORITIES
        )
        assert len(errors) == 1
        assert errors[0].error_type == "invalid_value"

    def test_all_verdicts_accepted(self) -> None:
        for verdict in sorted(VALID_VERDICTS):
            errors = validate_field(
                "verdict", verdict, "enum_string", enum=VALID_VERDICTS
            )
            assert len(errors) == 0

    def test_empty_verdict_rejected(self) -> None:
        """Empty string is not in the verdict enum."""
        errors = validate_field(
            "verdict", "", "enum_string", enum=VALID_VERDICTS
        )
        assert len(errors) == 1

    def test_all_round_types_accepted(self) -> None:
        for rt in sorted(VALID_ROUND_TYPES):
            errors = validate_field(
                "round_type", rt, "enum_string", enum=VALID_ROUND_TYPES
            )
            assert len(errors) == 0

    def test_all_agenda_types_accepted(self) -> None:
        for at in sorted(VALID_AGENDA_TYPES):
            errors = validate_field(
                "agenda_type", at, "enum_string",
                enum=VALID_AGENDA_TYPES,
            )
            assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════
# 5. kebab_id_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestKebabIdString:
    """Verify kebab-case identifier validation."""

    VALID_IDS = [
        "art-director",
        "marketing-lead",
        "coordinator",
        "sns-strategist",
        "concept-artist",
        "openclaw-owner",
        "hermes-reviewer",
        "team-lead-2",
        "x",
        "a-b-c-d",
        "project-reviewer-1",
    ]

    INVALID_IDS = [
        ("Art-Director", "uppercase letter"),
        ("ART-director", "uppercase letters"),
        ("art_director", "underscore"),
        ("art director", "space"),
        ("-art-director", "leading hyphen"),
        ("art-director-", "trailing hyphen"),
        ("art--director", "double hyphen"),
        ("123-start", "leading digit"),
        ("", "empty string"),
        ("   ", "whitespace only"),
    ]

    def test_valid_ids_accepted(self) -> None:
        for rid in self.VALID_IDS:
            errors = validate_field(
                "role_id", rid, "kebab_id_string"
            )
            assert len(errors) == 0, f"'{rid}' should be valid kebab-case"

    def test_invalid_ids_rejected(self) -> None:
        for rid, desc in self.INVALID_IDS:
            errors = validate_field(
                "role_id", rid, "kebab_id_string"
            )
            assert len(errors) >= 1, (
                f"'{rid}' ({desc}) should be rejected"
            )

    def test_none_rejected(self) -> None:
        errors = validate_field("role_id", None, "kebab_id_string")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_int_rejected(self) -> None:
        errors = validate_field("role_id", 42, "kebab_id_string")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 6. string_array validation
# ═══════════════════════════════════════════════════════════════════════

class TestStringArray:
    """Verify string array validation with various edge cases."""

    def test_valid_array_accepted(self) -> None:
        errors = validate_field("tags", ["mv", "visual"], "string_array")
        assert len(errors) == 0

    def test_empty_array_accepted(self) -> None:
        errors = validate_field("risk_tags", [], "string_array")
        assert len(errors) == 0

    def test_none_rejected(self) -> None:
        errors = validate_field("tags", None, "string_array")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_string_instead_of_list_rejected(self) -> None:
        errors = validate_field("tags", "not_a_list", "string_array")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_dict_instead_of_list_rejected(self) -> None:
        errors = validate_field("tags", {"k": "v"}, "string_array")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_non_string_items_flagged(self) -> None:
        errors = validate_field("tags", [1, 2, 3], "string_array")
        assert len(errors) == 1
        assert errors[0].error_type == "non_string_item"

    def test_mixed_items_flagged(self) -> None:
        errors = validate_field(
            "tags", ["valid", 42, None, True], "string_array"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "non_string_item"
        assert "3" in errors[0].message  # 3 non-string items

    def test_empty_string_items_flagged(self) -> None:
        errors = validate_field(
            "tags", ["valid", "", "  ", "also_valid"], "string_array"
        )
        assert len(errors) >= 1
        empty_err = next(
            e for e in errors if e.error_type == "empty_string"
        )
        assert "2" in empty_err.message  # 2 empty-string items

    def test_non_empty_array_required(self) -> None:
        """string_array_non_empty rejects empty lists."""
        errors = validate_field(
            "required_roles", [], "string_array_non_empty"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "empty_array"

    def test_non_empty_array_accepts_populated(self) -> None:
        errors = validate_field(
            "required_roles", ["coordinator"], "string_array_non_empty"
        )
        assert len(errors) == 0

    def test_large_non_string_list_truncated_indices(self) -> None:
        errors = validate_field(
            "tags", [1, 2, 3, 4, 5, 6, 7], "string_array"
        )
        assert len(errors) >= 1
        assert "…" in errors[0].message, (
            "Message should truncate long index lists"
        )

    def test_int_instead_of_list_rejected(self) -> None:
        errors = validate_field("tags", 42, "string_array")
        assert len(errors) == 1

    def test_boolean_instead_of_list_rejected(self) -> None:
        errors = validate_field("tags", True, "string_array")
        assert len(errors) == 1

    def test_float_item_flagged(self) -> None:
        errors = validate_field(
            "required_roles", [3.14, 2.71], "string_array"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "non_string_item"

    def test_none_item_in_list_flagged(self) -> None:
        errors = validate_field(
            "required_roles", ["coordinator", None], "string_array"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "non_string_item"


# ═══════════════════════════════════════════════════════════════════════
# 7. boolean validation (strict)
# ═══════════════════════════════════════════════════════════════════════

class TestBoolean:
    """Verify strict boolean validation (True/False only)."""

    def test_true_accepted(self) -> None:
        errors = validate_field(
            "validator_required", True, "boolean"
        )
        assert len(errors) == 0

    def test_false_accepted(self) -> None:
        errors = validate_field(
            "codex_required", False, "boolean"
        )
        assert len(errors) == 0

    def test_int_1_rejected(self) -> None:
        errors = validate_field(
            "validator_required", 1, "boolean"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_int_0_rejected(self) -> None:
        errors = validate_field(
            "validator_required", 0, "boolean"
        )
        assert len(errors) == 1

    def test_string_true_rejected(self) -> None:
        for val in ("true", "false", "True", "False", "yes", "no"):
            errors = validate_field(
                "validator_required", val, "boolean"
            )
            assert len(errors) == 1, f"'{val}' should be rejected"

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "validator_required", None, "boolean"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_float_rejected(self) -> None:
        errors = validate_field(
            "validator_required", 1.0, "boolean"
        )
        assert len(errors) == 1

    def test_list_rejected(self) -> None:
        errors = validate_field(
            "validator_required", [True], "boolean"
        )
        assert len(errors) == 1

    def test_none_accepted_when_allow_none(self) -> None:
        errors = validate_field(
            "completed_step_bool", None, "boolean_or_none"
        )
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════
# 8. positive_int validation
# ═══════════════════════════════════════════════════════════════════════

class TestPositiveInt:
    """Verify positive integer validation and boundary values."""

    def test_valid_values_accepted(self) -> None:
        for val in (1, 2, 10, 100, 12000, 20000, 30000, 999999):
            errors = validate_field(
                "token_budget", val, "positive_int"
            )
            assert len(errors) == 0, f"value {val} should be valid"

    def test_zero_rejected(self) -> None:
        errors = validate_field("token_budget", 0, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_negative_rejected(self) -> None:
        errors = validate_field("token_budget", -1, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_negative_large_rejected(self) -> None:
        errors = validate_field("token_budget", -99999, "positive_int")
        assert len(errors) == 1

    def test_float_rejected(self) -> None:
        errors = validate_field("token_budget", 1.5, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_float_zero_rejected(self) -> None:
        errors = validate_field("token_budget", 0.0, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_bool_rejected(self) -> None:
        errors = validate_field("token_budget", True, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_none_rejected(self) -> None:
        errors = validate_field("token_budget", None, "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_string_rejected(self) -> None:
        errors = validate_field("token_budget", "12000", "positive_int")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_int_like_float_accepted(self) -> None:
        """An int-as-float like 1.0 should be accepted as positive int."""
        errors = validate_field("token_budget", 1.0, "positive_int")
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════
# 9. non_negative_int validation
# ═══════════════════════════════════════════════════════════════════════

class TestNonNegativeInt:
    """Verify non-negative integer validation."""

    def test_zero_accepted(self) -> None:
        errors = validate_field("round_count", 0, "non_negative_int")
        assert len(errors) == 0

    def test_positive_accepted(self) -> None:
        errors = validate_field("round_count", 1, "non_negative_int")
        assert len(errors) == 0

    def test_negative_rejected(self) -> None:
        errors = validate_field("round_count", -1, "non_negative_int")
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_bool_rejected(self) -> None:
        errors = validate_field("round_count", False, "non_negative_int")
        assert len(errors) == 1

    def test_none_rejected(self) -> None:
        errors = validate_field("round_count", None, "non_negative_int")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"


# ═══════════════════════════════════════════════════════════════════════
# 10. int_in_range validation
# ═══════════════════════════════════════════════════════════════════════

class TestIntInRange:
    """Verify bounded integer range validation."""

    def test_boundary_values_accepted(self) -> None:
        for val in (0, 1, 2, 3, 4):
            errors = validate_field(
                "round_count", val, "int_in_range", min=0, max=4
            )
            assert len(errors) == 0, f"value {val} should be in [0, 4]"

    def test_below_range_rejected(self) -> None:
        errors = validate_field(
            "round_count", -1, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_above_range_rejected(self) -> None:
        errors = validate_field(
            "round_count", 5, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_float_rejected(self) -> None:
        errors = validate_field(
            "round_count", 2.5, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_bool_rejected(self) -> None:
        errors = validate_field(
            "round_count", True, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "round_count", None, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_far_out_of_range_rejected(self) -> None:
        errors = validate_field(
            "round_count", 999, "int_in_range", min=0, max=4
        )
        assert len(errors) == 1

    def test_int_like_float_accepted(self) -> None:
        """3.0 is an int-like float — should be accepted."""
        errors = validate_field(
            "round_count", 3.0, "int_in_range", min=0, max=4
        )
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════
# 11. float_0_1 validation
# ═══════════════════════════════════════════════════════════════════════

class TestFloat01:
    """Verify float in [0.0, 1.0] validation."""

    def test_boundary_values_accepted(self) -> None:
        for val in (0.0, 0.5, 0.92, 1.0):
            errors = validate_field(
                "validation_score", val, "float_0_1"
            )
            assert len(errors) == 0, f"value {val} should be valid"

    def test_int_values_accepted(self) -> None:
        for val in (0, 1):
            errors = validate_field(
                "validation_score", val, "float_0_1"
            )
            assert len(errors) == 0, f"int {val} should be accepted"

    def test_below_zero_rejected(self) -> None:
        errors = validate_field(
            "validation_score", -0.1, "float_0_1"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_above_one_rejected(self) -> None:
        errors = validate_field(
            "validation_score", 1.5, "float_0_1"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "out_of_range"

    def test_large_out_of_range_rejected(self) -> None:
        errors = validate_field(
            "validation_score", 999.0, "float_0_1"
        )
        assert len(errors) == 1

    def test_boolean_rejected(self) -> None:
        """JSON booleans are not valid for float fields."""
        for val in (True, False):
            errors = validate_field(
                "validation_score", val, "float_0_1"
            )
            assert len(errors) == 1, f"bool {val} should be rejected"
            assert errors[0].error_type == "wrong_type"

    def test_string_rejected(self) -> None:
        errors = validate_field(
            "validation_score", "high", "float_0_1"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "validation_score", None, "float_0_1"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_none_accepted_when_allow_none(self) -> None:
        errors = validate_field(
            "confidence", None, "float_0_1_or_none"
        )
        assert len(errors) == 0

    def test_threshold_values_accepted(self) -> None:
        """0.85 is the pass threshold — should validate fine."""
        errors = validate_field(
            "validation_score", 0.85, "float_0_1"
        )
        assert len(errors) == 0

    def test_epsilon_accepted(self) -> None:
        """Very small positive float should be accepted."""
        errors = validate_field(
            "validation_score", 1e-10, "float_0_1"
        )
        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════
# 12. iso8601_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestIso8601String:
    """Verify ISO-8601 timestamp validation."""

    VALID_TIMESTAMPS = [
        "2026-06-10T14:21:52Z",
        "2026-06-10T14:21:52+09:00",
        "2026-06-10T14:21:52-05:00",
        "2026-06-10T14:21:52.092608Z",
        "2026-06-10T14:21:52.123456+00:00",
        "2026-01-01T00:00:00Z",
        "2026-12-31T23:59:59Z",
        "2026-06-10 14:21:52",  # space separator accepted
    ]

    INVALID_TIMESTAMPS = [
        ("2026-06-10", "date only, no time"),
        ("14:21:52", "time only, no date"),
        ("2026/06/10 14:21:52", "wrong separator"),
        ("not-a-timestamp", "nonsense"),
        ("20260610T142152Z", "no separators"),
        ("2026-06-10T14:30:00Zextra", "trailing garbage"),
    ]

    def test_valid_timestamps_accepted(self) -> None:
        for ts in self.VALID_TIMESTAMPS:
            errors = validate_field(
                "created_at", ts, "iso8601_string"
            )
            assert len(errors) == 0, f"timestamp '{ts}' should be valid"

    def test_invalid_timestamps_rejected(self) -> None:
        for ts, _desc in self.INVALID_TIMESTAMPS:
            errors = validate_field(
                "created_at", ts, "iso8601_string"
            )
            assert len(errors) >= 1, (
                f"timestamp '{ts}' ({_desc}) should be rejected"
            )

    def test_none_rejected(self) -> None:
        errors = validate_field("created_at", None, "iso8601_string")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_int_rejected(self) -> None:
        errors = validate_field("created_at", 20260610, "iso8601_string")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_empty_string_rejected(self) -> None:
        errors = validate_field("created_at", "", "iso8601_string")
        assert len(errors) == 1
        assert errors[0].error_type == "empty_string"

    def test_empty_string_accepted_when_allow_empty(self) -> None:
        errors = validate_field(
            "thread_id", "", "iso8601_or_empty"
        )
        assert len(errors) == 0

    def test_invalid_still_rejected_when_allow_empty(self) -> None:
        errors = validate_field(
            "thread_id", "not-valid", "iso8601_or_empty"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 13. list validation
# ═══════════════════════════════════════════════════════════════════════

class TestList:
    """Verify generic list validation."""

    def test_non_empty_list_accepted(self) -> None:
        errors = validate_field(
            "previous_rounds", [1, 2, 3], "list"
        )
        assert len(errors) == 0

    def test_empty_list_accepted(self) -> None:
        errors = validate_field("previous_rounds", [], "list")
        assert len(errors) == 0

    def test_none_rejected(self) -> None:
        errors = validate_field("previous_rounds", None, "list")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_dict_rejected(self) -> None:
        errors = validate_field(
            "previous_rounds", {"k": "v"}, "list"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_string_rejected(self) -> None:
        errors = validate_field(
            "previous_rounds", "not a list", "list"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 14. list_of_dicts validation
# ═══════════════════════════════════════════════════════════════════════

class TestListOfDicts:
    """Verify list-of-dicts validation (for error_log etc.)."""

    def test_all_dicts_accepted(self) -> None:
        errors = validate_field(
            "error_log",
            [{"timestamp": "t1", "message": "m1"}],
            "list_of_dicts",
        )
        assert len(errors) == 0

    def test_empty_list_accepted(self) -> None:
        errors = validate_field("error_log", [], "list_of_dicts")
        assert len(errors) == 0

    def test_none_rejected(self) -> None:
        errors = validate_field("error_log", None, "list_of_dicts")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_string_rejected(self) -> None:
        errors = validate_field(
            "error_log", "not a list", "list_of_dicts"
        )
        assert len(errors) == 1

    def test_non_dict_items_flagged(self) -> None:
        errors = validate_field(
            "error_log", ["string_item", 42, {"valid": "dict"}],
            "list_of_dicts",
        )
        assert len(errors) == 1
        assert errors[0].error_type == "invalid_entry"
        assert "2" in errors[0].message  # 2 non-dict items

    def test_all_non_dict_items_flagged(self) -> None:
        errors = validate_field(
            "error_log", [1, 2, 3], "list_of_dicts"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 15. dict_or_none validation
# ═══════════════════════════════════════════════════════════════════════

class TestDictOrNone:
    """Verify dict-or-None validation."""

    def test_dict_accepted(self) -> None:
        errors = validate_field(
            "previous_rounds_data", {"r1": "summary"}, "dict_or_none"
        )
        assert len(errors) == 0

    def test_none_accepted(self) -> None:
        errors = validate_field(
            "previous_rounds_data", None, "dict_or_none"
        )
        assert len(errors) == 0

    def test_string_rejected(self) -> None:
        errors = validate_field(
            "previous_rounds_data", "not a dict", "dict_or_none"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_list_rejected(self) -> None:
        errors = validate_field(
            "previous_rounds_data", [1, 2], "dict_or_none"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 16. string_or_none validation
# ═══════════════════════════════════════════════════════════════════════

class TestStringOrNone:
    """Verify string-or-None validation."""

    def test_string_accepted(self) -> None:
        errors = validate_field(
            "completed_step", "some_step", "string_or_none"
        )
        assert len(errors) == 0

    def test_none_accepted(self) -> None:
        errors = validate_field(
            "completed_step", None, "string_or_none"
        )
        assert len(errors) == 0

    def test_int_rejected(self) -> None:
        errors = validate_field(
            "completed_step", 42, "string_or_none"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"

    def test_list_rejected(self) -> None:
        errors = validate_field(
            "completed_step", ["a"], "string_or_none"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 17. string_or_empty validation
# ═══════════════════════════════════════════════════════════════════════

class TestStringOrEmpty:
    """Verify string-or-empty validation (empty string is OK)."""

    def test_non_empty_accepted(self) -> None:
        errors = validate_field(
            "consensus", "We agree.", "string_or_empty"
        )
        assert len(errors) == 0

    def test_empty_accepted(self) -> None:
        errors = validate_field("consensus", "", "string_or_empty")
        assert len(errors) == 0

    def test_none_rejected(self) -> None:
        errors = validate_field("consensus", None, "string_or_empty")
        assert len(errors) == 1
        assert errors[0].error_type == "missing"

    def test_int_rejected(self) -> None:
        errors = validate_field("consensus", 42, "string_or_empty")
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 18. path_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestPathString:
    """Verify path string validation."""

    def test_valid_path_accepted(self) -> None:
        errors = validate_field(
            "manifest_path",
            "/home/kbm/project/meetings/m1/manifest.json",
            "path_string",
        )
        assert len(errors) == 0

    def test_relative_path_accepted(self) -> None:
        errors = validate_field(
            "manifest_path", "meetings/m1/manifest.json", "path_string"
        )
        assert len(errors) == 0

    def test_windows_path_accepted(self) -> None:
        errors = validate_field(
            "manifest_path",
            r"C:\Users\kbm\meetings\m1\manifest.json",
            "path_string",
        )
        assert len(errors) == 0

    def test_empty_rejected(self) -> None:
        errors = validate_field(
            "manifest_path", "", "path_string"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "empty_string"

    def test_whitespace_rejected(self) -> None:
        errors = validate_field(
            "manifest_path", "   ", "path_string"
        )
        assert len(errors) == 1

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "manifest_path", None, "path_string"
        )
        assert len(errors) == 1

    def test_int_rejected(self) -> None:
        errors = validate_field(
            "manifest_path", 42, "path_string"
        )
        assert len(errors) == 1
        assert errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 19. version_string validation
# ═══════════════════════════════════════════════════════════════════════

class TestVersionString:
    """Verify version string format validation (name.vN)."""

    VALID_VERSIONS = [
        "meeting-manifest.v1",
        "field-format-validation.v1",
        "context-packet-validation.v1",
        "opinion-packet.v1",
        "a.v0",
        "a.v999",
    ]

    INVALID_VERSIONS = [
        ("v1", "missing name"),
        ("meeting-manifest.v", "missing version number"),
        ("meeting-manifest.vX", "letter instead of number"),
        ("meeting-manifest.v1.0", "extra dot"),
        ("Meeting-Manifest.v1", "uppercase"),
        ("", "empty string"),
    ]

    def test_valid_versions_accepted(self) -> None:
        for ver in self.VALID_VERSIONS:
            errors = validate_field(
                "schema_version", ver, "version_string"
            )
            assert len(errors) == 0, f"version '{ver}' should be valid"

    def test_invalid_versions_rejected(self) -> None:
        for ver, desc in self.INVALID_VERSIONS:
            errors = validate_field(
                "schema_version", ver, "version_string"
            )
            assert len(errors) >= 1, (
                f"version '{ver}' ({desc}) should be rejected"
            )

    def test_none_rejected(self) -> None:
        errors = validate_field(
            "schema_version", None, "version_string"
        )
        assert len(errors) == 1

    def test_int_rejected(self) -> None:
        errors = validate_field(
            "schema_version", 1, "version_string"
        )
        assert len(errors) == 1


# ═══════════════════════════════════════════════════════════════════════
# 20. None and non-dict input
# ═══════════════════════════════════════════════════════════════════════

class TestNullAndNonDictInput:
    """Verify graceful handling of None and non-dict arguments."""

    def test_none_manifest_fails(self) -> None:
        report = validate_manifest_fields(None)  # type: ignore[arg-type]
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "missing"
        assert report.total_fields_checked == 0

    def test_list_manifest_fails(self) -> None:
        report = validate_manifest_fields([1, 2, 3])  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "wrong_type"

    def test_string_manifest_fails(self) -> None:
        report = validate_manifest_fields("not a dict")  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_int_manifest_fails(self) -> None:
        report = validate_manifest_fields(42)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_bool_manifest_fails(self) -> None:
        report = validate_manifest_fields(True)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_none_context_packet_fails(self) -> None:
        report = validate_context_packet_format(None)  # type: ignore[arg-type]
        assert not report.passed
        assert report.error_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 21. Multiple simultaneous errors
# ═══════════════════════════════════════════════════════════════════════

class TestMultipleErrors:
    """Verify that multiple field errors are aggregated."""

    def test_multiple_invalid_fields(self) -> None:
        payload = _valid_manifest(
            meeting_id="bad_id",
            state="bogus",
            priority="p99",
            agenda_type="nope",
            round_count=-5,
            validation_score=2.5,
            validator_required=1,
            codex_required="yes",
        )
        report = validate_manifest_fields(payload)
        assert not report.passed
        # Should have at least 8 errors (one per invalid field)
        assert report.error_count >= 8

    def test_all_errors_preserved(self) -> None:
        """Empty dict should produce errors for all required fields."""
        report = validate_manifest_fields({})
        assert not report.passed
        # Count required fields across all categories
        required_count = sum(
            1 for f in ALL_MANIFEST_FIELDS if f.get("required", True)
        )
        assert report.error_count == required_count

    def test_errors_by_field_groups_correctly(self) -> None:
        payload = _valid_manifest(state="bogus", priority="p99")
        report = validate_manifest_fields(payload)
        grouped = report.errors_by_field()
        assert "state" in grouped
        assert "priority" in grouped
        assert len(grouped["state"]) == 1
        assert len(grouped["priority"]) == 1


# ═══════════════════════════════════════════════════════════════════════
# 22. FormatValidationReport properties
# ═══════════════════════════════════════════════════════════════════════

class TestReportProperties:
    """Verify FormatValidationReport dataclass behaviour."""

    def test_passed_report_zero_errors(self) -> None:
        report = validate_manifest_fields(_valid_manifest())
        assert report.passed
        assert report.error_count == 0
        assert report.errors_by_field() == {}

    def test_failed_report_has_errors(self) -> None:
        report = validate_manifest_fields({})
        assert not report.passed
        assert report.error_count > 0

    def test_schema_version(self) -> None:
        report = validate_manifest_fields(_valid_manifest())
        assert report.schema_version == "field-format-validation.v1"

    def test_total_fields_checked(self) -> None:
        report = validate_manifest_fields(_valid_manifest())
        assert report.total_fields_checked == len(ALL_MANIFEST_FIELDS)


# ═══════════════════════════════════════════════════════════════════════
# 23. validate_dict_fields API
# ═══════════════════════════════════════════════════════════════════════

class TestValidateDictFields:
    """Verify the validate_dict_fields generic API."""

    def test_custom_field_set(self) -> None:
        fields = (
            {"name": "priority", "kind": "enum_string",
             "enum": VALID_PRIORITIES, "required": True},
            {"name": "round_count", "kind": "int_in_range",
             "min": 0, "max": 4, "required": True},
            {"name": "tags", "kind": "string_array", "required": True},
        )
        data = {"priority": "p2", "round_count": 1, "tags": ["mv"]}
        report = validate_dict_fields(data, fields)
        assert report.passed

    def test_custom_field_set_fails(self) -> None:
        fields = (
            {"name": "priority", "kind": "enum_string",
             "enum": VALID_PRIORITIES, "required": True},
        )
        data = {"priority": "p5"}
        report = validate_dict_fields(data, fields)
        assert not report.passed

    def test_optional_field_absent_ok(self) -> None:
        fields = (
            {"name": "req", "kind": "non_empty_string", "required": True},
            {"name": "opt", "kind": "string_or_none", "required": False},
        )
        data = {"req": "hello"}
        report = validate_dict_fields(data, fields)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 24. validate_field single-field API
# ═══════════════════════════════════════════════════════════════════════

class TestValidateField:
    """Verify the validate_field single-field convenience API."""

    def test_valid_field_returns_empty(self) -> None:
        errors = validate_field("priority", "p2", "enum_string",
                                enum=VALID_PRIORITIES)
        assert errors == []

    def test_invalid_field_returns_errors(self) -> None:
        errors = validate_field("priority", "p5", "enum_string",
                                enum=VALID_PRIORITIES)
        assert len(errors) == 1

    def test_unknown_kind_returns_empty(self) -> None:
        """Unknown validator kinds should be silently skipped."""
        errors = validate_field("some_field", "value", "unknown_kind")
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════
# 25. Boundary-value format inputs (per AC requirement)
# ═══════════════════════════════════════════════════════════════════════

class TestBoundaryValueInputs:
    """Verify boundary-value format inputs are handled correctly."""

    def test_token_budget_boundaries(self) -> None:
        """Token budgets at limit boundaries."""
        # 0 rejected
        errors = validate_field("token_budget", 0, "positive_int")
        assert len(errors) == 1

        # 1 accepted (minimum positive)
        errors = validate_field("token_budget", 1, "positive_int")
        assert len(errors) == 0

        # Very large accepted
        errors = validate_field("token_budget", 999999, "positive_int")
        assert len(errors) == 0

        # Negative rejected
        errors = validate_field("token_budget", -1, "positive_int")
        assert len(errors) == 1

    def test_round_count_boundaries(self) -> None:
        """Round count at boundaries [0, 4]."""
        for val in (0, 4):
            errors = validate_field(
                "round_count", val, "int_in_range", min=0, max=4
            )
            assert len(errors) == 0

        for val in (-1, 5):
            errors = validate_field(
                "round_count", val, "int_in_range", min=0, max=4
            )
            assert len(errors) == 1

    def test_validation_score_boundaries(self) -> None:
        """Validation score at [0.0, 1.0] boundaries."""
        # Exactly at boundaries
        for val in (0.0, 1.0, 0, 1):
            errors = validate_field(
                "validation_score", val, "float_0_1"
            )
            assert len(errors) == 0

        # Just outside boundaries
        for val in (-0.0001, 1.0001):
            errors = validate_field(
                "validation_score", val, "float_0_1"
            )
            assert len(errors) == 1

    def test_meeting_id_boundary_lengths(self) -> None:
        """Meeting ID at boundary lengths."""
        # Minimum valid length: meeting_20260610_ + 12 hex chars = 30
        valid = "meeting_20260610_000000000000"  # 30 chars
        errors = validate_field("meeting_id", valid, "meeting_id")
        assert len(errors) == 0

        # Too short
        too_short = "meeting_20260610_00000000000"  # 29 chars (11 hex)
        errors = validate_field("meeting_id", too_short, "meeting_id")
        assert len(errors) == 1

    def test_error_log_boundary(self) -> None:
        """Error log with boundary cases."""
        # Empty list OK
        errors = validate_field("error_log", [], "list_of_dicts")
        assert len(errors) == 0

        # Single valid entry OK
        errors = validate_field(
            "error_log",
            [{"timestamp": "t", "message": "m"}],
            "list_of_dicts",
        )
        assert len(errors) == 0

        # Mixed entries (first invalid)
        errors = validate_field(
            "error_log", ["not a dict", {"ok": "dict"}], "list_of_dicts"
        )
        assert len(errors) == 1

    def test_iso8601_edge_cases(self) -> None:
        """ISO-8601 boundary/edge timestamps."""
        # Midnight
        errors = validate_field("ts", "2026-01-01T00:00:00Z", "iso8601_string")
        assert len(errors) == 0

        # With microseconds
        errors = validate_field(
            "ts", "2026-06-10T23:59:59.999999Z", "iso8601_string"
        )
        assert len(errors) == 0

        # With positive timezone
        errors = validate_field(
            "ts", "2026-06-10T14:30:00+09:00", "iso8601_string"
        )
        assert len(errors) == 0

        # With negative timezone
        errors = validate_field(
            "ts", "2026-06-10T14:30:00-05:00", "iso8601_string"
        )
        assert len(errors) == 0

        # Leaking characters
        errors = validate_field(
            "ts", "2026-06-10T14:30:00Zextra", "iso8601_string"
        )
        assert len(errors) == 1

    def test_enum_boundary_empty(self) -> None:
        """Empty string for enum fields."""
        errors = validate_field(
            "state", "", "enum_string", enum=VALID_STATES
        )
        assert len(errors) == 1  # Empty not in any enum


# ═══════════════════════════════════════════════════════════════════════
# 26. Manifest integration — valid variants
# ═══════════════════════════════════════════════════════════════════════

class TestManifestIntegration:
    """Verify manifest fields handle real-world variations."""

    def test_manifest_with_all_states(self) -> None:
        for state in sorted(VALID_STATES):
            report = validate_manifest_fields(
                _valid_manifest(state=state)
            )
            assert report.passed, f"state '{state}' should be valid"

    def test_manifest_with_all_priorities(self) -> None:
        for pri in sorted(VALID_PRIORITIES):
            report = validate_manifest_fields(
                _valid_manifest(priority=pri)
            )
            assert report.passed

    def test_manifest_with_all_agenda_types(self) -> None:
        for at in sorted(VALID_AGENDA_TYPES):
            report = validate_manifest_fields(
                _valid_manifest(agenda_type=at)
            )
            assert report.passed

    def test_manifest_with_all_verdicts(self) -> None:
        for verdict in sorted(VALID_VERDICTS):
            report = validate_manifest_fields(
                _valid_manifest(validation_verdict=verdict)
            )
            assert report.passed

    def test_manifest_with_error_log_entries(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(
                error_log=[
                    {
                        "timestamp": "2026-06-10T14:30:00Z",
                        "error_type": "pre_condition_violation",
                        "message": "Round limit exhausted",
                        "severity": "warning",
                        "recovery": "Address the guard condition",
                    }
                ]
            )
        )
        assert report.passed

    def test_manifest_with_thread_id(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(thread_id="thread_abc123")
        )
        assert report.passed

    def test_manifest_round_count_progression(self) -> None:
        """Round counts 0 through 4 should all be valid."""
        for rc in (0, 1, 2, 3, 4):
            report = validate_manifest_fields(
                _valid_manifest(round_count=rc)
            )
            assert report.passed, f"round_count={rc} should be valid"

    def test_manifest_round_count_5_rejected(self) -> None:
        report = validate_manifest_fields(
            _valid_manifest(round_count=5)
        )
        assert not report.passed

    def test_manifest_validation_score_progression(self) -> None:
        """Common validation scores should pass."""
        for score in (0.0, 0.5, 0.85, 1.0):
            report = validate_manifest_fields(
                _valid_manifest(validation_score=score)
            )
            assert report.passed, f"validation_score={score} should pass"

    def test_context_packet_all_round_types(self) -> None:
        for rt in sorted(VALID_ROUND_TYPES):
            report = validate_context_packet_format(
                _valid_context_packet(round_type=rt)
            )
            assert report.passed, f"round_type '{rt}' should be valid"

    def test_context_packet_round_variations(self) -> None:
        for rnd in (1, 2, 3, 4):
            report = validate_context_packet_format(
                _valid_context_packet(round=rnd)
            )
            assert report.passed, f"round={rnd} should be valid"

    def test_context_packet_all_states(self) -> None:
        for state in sorted(VALID_STATES):
            report = validate_context_packet_format(
                _valid_context_packet(state=state)
            )
            assert report.passed, f"state '{state}' should be valid"
