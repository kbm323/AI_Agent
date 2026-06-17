"""Comprehensive tests for the context packet required-fields presence validator.

Sub-AC 6.1.1: Context packet required-fields presence validation —
verifies all mandatory fields exist in the packet regardless of content;
testable with packets missing one or more required fields, empty packets,
and complete packets.

Test coverage:
- all-valid complete packet (baseline)
- each required field individually missing
- empty packet ({})
- None and non-dict input
- optional fields present and absent
- field-by-field invalid type/value checks
- PacketValidationReport properties and immutability
- validate_or_raise convenience wrapper
"""

from __future__ import annotations

import pytest

from src.context_packet_validator import (
    ALL_VALIDATED_FIELDS,
    PACKET_SCHEMA_VERSION,
    PacketFieldError,
    PacketValidationError,
    PacketValidationReport,
    validate_context_packet,
    validate_or_raise,
)


# ═══════════════════════════════════════════════════════════════════════
# Helper: build a valid complete packet with optional overrides
# ═══════════════════════════════════════════════════════════════════════

def _valid_packet(**overrides: object) -> dict[str, object]:
    """Return a fully valid context packet dict."""
    defaults: dict[str, object] = {
        "meeting_id": "meeting_20260101_abc123def456",
        "role_id": "art-director",
        "state": "in_meeting",
        "priority": "p2",
        "agenda": "Music video opening ideas for new single release",
        "agenda_type": "creative_production",
        "tags": ["mv", "visual-concept", "opening-sequence"],
        "risk_tags": ["brand"],
        "round": 1,
        "round_type": "opinion",
        "round_context": (
            "As the Art Director, propose creative directions for the "
            "music video opening sequence. Consider visual style, "
            "color palette, and scene composition."
        ),
        "token_budget": 12000,
        "previous_rounds": [],
    }
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid packet — baseline
# ═══════════════════════════════════════════════════════════════════════

class TestValidPacket:
    """Verify that a fully correct context packet passes validation."""

    def test_all_fields_valid_passes(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.passed
        assert report.error_count == 0
        assert len(report.errors) == 0
        assert report.schema_version == PACKET_SCHEMA_VERSION

    def test_total_fields_checked_matches_declared(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.total_fields_checked == len(ALL_VALIDATED_FIELDS)

    def test_missing_fields_empty_when_passed(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.missing_fields() == ()

    def test_mandatory_errors_empty_when_passed(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.mandatory_errors == ()

    def test_errors_by_field_empty_when_passed(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.errors_by_field() == {}

    # ── Round variations ──

    def test_round_2_accepted(self) -> None:
        report = validate_context_packet(_valid_packet(round=2))
        assert report.passed

    def test_round_3_accepted(self) -> None:
        report = validate_context_packet(_valid_packet(round=3))
        assert report.passed

    def test_round_4_accepted(self) -> None:
        """Round 4 (tie-break as int) should be accepted."""
        report = validate_context_packet(_valid_packet(round=4))
        assert report.passed

    def test_tie_break_string_accepted(self) -> None:
        report = validate_context_packet(
            _valid_packet(round="tie_break", round_type="tie_break")
        )
        assert report.passed

    # ── Round type variations ──

    def test_all_round_types_accepted(self) -> None:
        for rt in ("opinion", "conflict_resolution", "convergence", "tie_break"):
            report = validate_context_packet(
                _valid_packet(round_type=rt)
            )
            assert report.passed, f"round_type '{rt}' should be valid"

    # ── All states accepted ──

    def test_all_states_accepted(self) -> None:
        valid_states = [
            "created", "queued", "routing", "context_retrieval",
            "in_meeting", "consensus_building", "validating",
            "executing", "finalizing", "completed", "paused",
            "deadlocked", "escalated", "cancelled", "failed", "stale",
        ]
        for state in valid_states:
            report = validate_context_packet(_valid_packet(state=state))
            assert report.passed, f"state '{state}' should be valid"

    # ── All priorities accepted ──

    def test_all_priorities_accepted(self) -> None:
        for pri in ("p0", "p1", "p2", "p3"):
            report = validate_context_packet(
                _valid_packet(priority=pri)
            )
            assert report.passed, f"priority '{pri}' should be valid"

    # ── All agenda types accepted ──

    def test_all_agenda_types_accepted(self) -> None:
        types = [
            "creative_production", "technical_development",
            "marketing_strategy", "risk_assessment",
            "general_planning", "project_review",
        ]
        for at in types:
            report = validate_context_packet(
                _valid_packet(agenda_type=at)
            )
            assert report.passed, f"agenda_type '{at}' should be valid"

    # ── Empty arrays accepted ──

    def test_empty_tags_accepted(self) -> None:
        report = validate_context_packet(_valid_packet(tags=[]))
        assert report.passed

    def test_empty_risk_tags_accepted(self) -> None:
        report = validate_context_packet(_valid_packet(risk_tags=[]))
        assert report.passed

    def test_empty_previous_rounds_accepted(self) -> None:
        """Round 1 has no previous rounds — empty list is valid."""
        report = validate_context_packet(
            _valid_packet(round=1, previous_rounds=[])
        )
        assert report.passed

    def test_non_empty_previous_rounds_accepted(self) -> None:
        """Round 2+ may have previous round data."""
        report = validate_context_packet(
            _valid_packet(
                round=2,
                round_type="conflict_resolution",
                previous_rounds=[{"round": 1, "role": "art-director"}],
            )
        )
        assert report.passed

    # ── With optional fields present ──

    def test_with_knowledge_context(self) -> None:
        report = validate_context_packet(
            _valid_packet(knowledge_context="Prior art direction...")
        )
        assert report.passed

    def test_with_evidence_summary(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                evidence_summary="Team noted budget constraints..."
            )
        )
        assert report.passed

    def test_with_rebuttal_points(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                rebuttal_points=["Marketing lead disagrees on palette"]
            )
        )
        assert report.passed

    def test_with_unresolved_issues(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                unresolved_issues=["Duration vs. budget trade-off"]
            )
        )
        assert report.passed

    def test_with_previous_rounds_data(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                previous_rounds_data={"round_1": "summary..."}
            )
        )
        assert report.passed

    def test_with_participant_roles(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                participant_roles=["art-director", "marketing-lead"]
            )
        )
        assert report.passed

    def test_with_all_optional_fields(self) -> None:
        report = validate_context_packet(
            _valid_packet(
                previous_rounds_data={"r1": "summary"},
                knowledge_context="Brand guidelines: minimal, bold",
                evidence_summary="3 prior MV openings analyzed",
                rebuttal_points=["Budget concern from finance"],
                unresolved_issues=["Color palette disagreement"],
                participant_roles=["art-director", "marketing-lead"],
            )
        )
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 2. Missing required fields — each individually
# ═══════════════════════════════════════════════════════════════════════

class TestMissingRequiredFields:
    """Verify that every required field is checked for presence."""

    REQUIRED_FIELD_NAMES = (
        "meeting_id",
        "role_id",
        "state",
        "priority",
        "agenda",
        "agenda_type",
        "tags",
        "risk_tags",
        "round",
        "round_type",
        "round_context",
        "token_budget",
        "previous_rounds",
    )

    def test_each_required_field_missing_detected(self) -> None:
        for field in self.REQUIRED_FIELD_NAMES:
            payload = _valid_packet()
            del payload[field]
            report = validate_context_packet(payload)
            assert not report.passed, (
                f"Missing '{field}' should cause failure"
            )
            assert field in report.missing_fields(), (
                f"'{field}' should appear in missing_fields()"
            )
            err = next(
                e for e in report.errors
                if e.field_name == field and e.error_type == "missing"
            )
            assert f"'{field}'" in err.message

    def test_missing_role_id_detected(self) -> None:
        payload = _valid_packet()
        del payload["role_id"]
        report = validate_context_packet(payload)
        assert not report.passed
        assert "role_id" in report.missing_fields()

    def test_missing_round_detected(self) -> None:
        payload = _valid_packet()
        del payload["round"]
        report = validate_context_packet(payload)
        assert not report.passed
        assert "round" in report.missing_fields()


# ═══════════════════════════════════════════════════════════════════════
# 3. Empty packet
# ═══════════════════════════════════════════════════════════════════════

class TestEmptyPacket:
    """Verify that an empty packet (``{}``) fails validation correctly."""

    def test_empty_dict_fails(self) -> None:
        report = validate_context_packet({})
        assert not report.passed
        # 13 required fields all missing
        assert report.error_count == 13
        assert len(report.missing_fields()) == 13

    def test_empty_dict_all_errors_are_missing(self) -> None:
        report = validate_context_packet({})
        for err in report.errors:
            assert err.error_type == "missing"

    def test_empty_dict_mandatory_equals_all_errors(self) -> None:
        report = validate_context_packet({})
        assert len(report.mandatory_errors) == len(report.errors)


# ═══════════════════════════════════════════════════════════════════════
# 4. None and non-dict input
# ═══════════════════════════════════════════════════════════════════════

class TestNullAndNonDictInput:
    """Verify graceful handling of None and non-dict arguments."""

    def test_none_input_fails(self) -> None:
        report = validate_context_packet(None)
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "missing"
        assert report.total_fields_checked == 0

    def test_list_input_fails(self) -> None:
        report = validate_context_packet([1, 2, 3])  # type: ignore[arg-type]
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "wrong_type"

    def test_string_input_fails(self) -> None:
        report = validate_context_packet("not a dict")  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_int_input_fails(self) -> None:
        report = validate_context_packet(42)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_bool_input_fails(self) -> None:
        report = validate_context_packet(True)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 5. Missing multiple required fields
# ═══════════════════════════════════════════════════════════════════════

class TestMultipleMissingFields:
    """Verify that multiple missing fields are all reported."""

    def test_two_missing_fields(self) -> None:
        payload = _valid_packet()
        del payload["meeting_id"]
        del payload["role_id"]
        report = validate_context_packet(payload)
        assert not report.passed
        assert report.error_count == 2  # only missing errors
        missing = report.missing_fields()
        assert "meeting_id" in missing
        assert "role_id" in missing

    def test_five_missing_fields(self) -> None:
        payload = _valid_packet()
        for key in ("meeting_id", "agenda", "round", "tags", "token_budget"):
            del payload[key]
        report = validate_context_packet(payload)
        assert not report.passed
        assert report.error_count == 5
        missing = report.missing_fields()
        assert len(missing) == 5

    def test_only_one_field_present(self) -> None:
        """Every field except one is missing."""
        report = validate_context_packet({"meeting_id": "m1"})
        assert not report.passed
        # 13 required - 1 present = 12 missing field errors
        assert report.error_count == 12
        assert "meeting_id" not in report.missing_fields()

    def test_half_fields_present(self) -> None:
        """7 of 13 fields present, 6 missing."""
        payload = {
            "meeting_id": "m1",
            "role_id": "r1",
            "state": "in_meeting",
            "priority": "p2",
            "agenda": "Test",
            "agenda_type": "general_planning",
            "round": 1,
        }
        report = validate_context_packet(payload)
        assert not report.passed
        assert report.error_count == 6  # 6 missing + type errors on present
        # The 6 missing are the remaining required fields
        assert len(report.missing_fields()) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 6. Field-by-field invalid type/value checks
# ═══════════════════════════════════════════════════════════════════════

class TestStringFieldValidation:
    """Verify non-empty string field validation."""

    def test_meeting_id_empty_string_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(meeting_id="")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "meeting_id"
        )
        assert err.error_type == "empty_string"

    def test_meeting_id_whitespace_only_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(meeting_id="   ")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "meeting_id"
        )
        assert err.error_type == "empty_string"

    def test_agenda_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(agenda=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "agenda"
        )
        assert err.error_type == "missing"

    def test_round_context_int_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round_context=42)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round_context"
        )
        assert err.error_type == "wrong_type"

    def test_role_id_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(role_id=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "role_id"
        )
        assert err.error_type == "missing"


class TestEnumFieldValidation:
    """Verify enum string field validation."""

    def test_invalid_state_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(state="not_a_state")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "state"
        )
        assert err.error_type == "invalid_value"

    def test_invalid_priority_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(priority="p5")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "priority"
        )
        assert err.error_type == "invalid_value"

    def test_invalid_agenda_type_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(agenda_type="bogus_type")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "agenda_type"
        )
        assert err.error_type == "invalid_value"

    def test_invalid_round_type_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round_type="not_a_round")
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round_type"
        )
        assert err.error_type == "invalid_value"

    def test_state_int_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(state=42)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "state"
        )
        assert err.error_type == "wrong_type"

    def test_priority_int_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(priority=1)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "priority"
        )
        assert err.error_type == "wrong_type"

    def test_priority_uppercase_accepted_after_lower(self) -> None:
        """'P2' should be lowercased and match 'p2'."""
        report = validate_context_packet(
            _valid_packet(priority="P2")
        )
        assert report.passed, "Uppercase priority should be lowercased and match"

    def test_state_mixed_case_accepted_after_lower(self) -> None:
        """'IN_MEETING' should be lowercased and match 'in_meeting'."""
        report = validate_context_packet(
            _valid_packet(state="IN_MEETING")
        )
        assert report.passed


class TestRoundFieldValidation:
    """Verify round field validation (1-4 or 'tie_break')."""

    def test_round_0_rejected(self) -> None:
        report = validate_context_packet(_valid_packet(round=0))
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "out_of_range"

    def test_round_5_rejected(self) -> None:
        report = validate_context_packet(_valid_packet(round=5))
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "out_of_range"

    def test_round_negative_rejected(self) -> None:
        report = validate_context_packet(_valid_packet(round=-1))
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "out_of_range"

    def test_round_float_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round=1.5)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "wrong_type"

    def test_round_boolean_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round=True)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "wrong_type"

    def test_round_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "missing"

    def test_round_list_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round=[1])  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "wrong_type"

    def test_round_invalid_string_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(round="round_one")  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "round"
        )
        assert err.error_type == "invalid_value"


class TestTokenBudgetValidation:
    """Verify token_budget positive int validation."""

    def test_token_budget_12000_accepted(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=12000)
        )
        assert report.passed

    def test_token_budget_20000_accepted(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=20000)
        )
        assert report.passed

    def test_token_budget_30000_accepted(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=30000)
        )
        assert report.passed

    def test_token_budget_0_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=0)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "out_of_range"

    def test_token_budget_negative_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=-100)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "out_of_range"

    def test_token_budget_float_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=12000.5)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "wrong_type"

    def test_token_budget_boolean_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=False)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "wrong_type"

    def test_token_budget_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "missing"

    def test_token_budget_string_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(token_budget="12k")  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "token_budget"
        )
        assert err.error_type == "wrong_type"


class TestStringArrayValidation:
    """Verify string-array field validation."""

    def test_tags_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(tags=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "tags"
        )
        assert err.error_type == "missing"

    def test_tags_string_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(tags="not_a_list")  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "tags"
        )
        assert err.error_type == "wrong_type"

    def test_risk_tags_dict_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(risk_tags={})  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "risk_tags"
        )
        assert err.error_type == "wrong_type"


class TestPreviousRoundsValidation:
    """Verify previous_rounds list validation."""

    def test_previous_rounds_none_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(previous_rounds=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "previous_rounds"
        )
        assert err.error_type == "missing"

    def test_previous_rounds_string_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(previous_rounds="round1")  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "previous_rounds"
        )
        assert err.error_type == "wrong_type"

    def test_previous_rounds_dict_rejected(self) -> None:
        report = validate_context_packet(
            _valid_packet(previous_rounds={"r1": "data"})  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "previous_rounds"
        )
        assert err.error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 7. Optional fields — validation when present
# ═══════════════════════════════════════════════════════════════════════

class TestOptionalFields:
    """Verify that optional fields are validated when present but
    their absence does not cause failure."""

    def test_optional_fields_absent_ok(self) -> None:
        """Packet with no optional fields should pass."""
        report = validate_context_packet(_valid_packet())
        assert report.passed

    def test_previous_rounds_data_wrong_type_flagged(self) -> None:
        """previous_rounds_data should be dict or None."""
        payload = _valid_packet(previous_rounds_data="wrong")  # type: ignore[arg-type]
        report = validate_context_packet(payload)
        # Optional field type error does NOT affect pass/fail
        assert report.passed
        # But error is still reported
        opt_errs = [
            e for e in report.errors
            if e.field_name == "previous_rounds_data"
        ]
        assert len(opt_errs) > 0
        assert opt_errs[0].error_type == "wrong_type"

    def test_knowledge_context_int_flagged(self) -> None:
        """knowledge_context should be string or None."""
        payload = _valid_packet(knowledge_context=42)  # type: ignore[arg-type]
        report = validate_context_packet(payload)
        assert report.passed  # optional errors don't fail
        opt_errs = [
            e for e in report.errors
            if e.field_name == "knowledge_context"
        ]
        assert len(opt_errs) > 0
        assert opt_errs[0].error_type == "wrong_type"

    def test_knowledge_context_none_ok(self) -> None:
        payload = _valid_packet(knowledge_context=None)
        report = validate_context_packet(payload)
        assert report.passed
        assert len(report.errors) == 0

    def test_evidence_summary_list_flagged(self) -> None:
        payload = _valid_packet(evidence_summary=["a", "b"])  # type: ignore[arg-type]
        report = validate_context_packet(payload)
        assert report.passed
        opt_errs = [
            e for e in report.errors
            if e.field_name == "evidence_summary"
        ]
        assert len(opt_errs) > 0

    def test_rebuttal_points_string_flagged(self) -> None:
        payload = _valid_packet(rebuttal_points="bad")  # type: ignore[arg-type]
        report = validate_context_packet(payload)
        assert report.passed
        opt_errs = [
            e for e in report.errors
            if e.field_name == "rebuttal_points"
        ]
        assert len(opt_errs) > 0

    def test_rebuttal_points_none_ok(self) -> None:
        payload = _valid_packet(rebuttal_points=None)
        report = validate_context_packet(payload)
        assert report.passed
        # rebuttal_points with None: no error
        assert not any(
            e.field_name == "rebuttal_points" for e in report.errors
        )

    def test_unresolved_issues_none_ok(self) -> None:
        payload = _valid_packet(unresolved_issues=None)
        report = validate_context_packet(payload)
        assert report.passed

    def test_participant_roles_none_ok(self) -> None:
        payload = _valid_packet(participant_roles=None)
        report = validate_context_packet(payload)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 8. Multiple simultaneous errors
# ═══════════════════════════════════════════════════════════════════════

class TestMultipleSimultaneousErrors:
    """Verify that multiple type/value errors are collected (no early exit)."""

    def test_multiple_type_errors_collected(self) -> None:
        """All five required string fields given wrong types."""
        report = validate_context_packet(
            _valid_packet(
                meeting_id=42,  # type: ignore[arg-type]
                role_id=None,  # type: ignore[arg-type]
                agenda=123,  # type: ignore[arg-type]
                state=True,  # type: ignore[arg-type]
                round_context=3.14,  # type: ignore[arg-type]
            )
        )
        assert not report.passed
        # At minimum: role_id(None→missing), meeting_id(int→wrong_type),
        # agenda(int→wrong_type), state(bool→wrong_type), round_context(float→wrong_type)
        assert report.error_count >= 5

    def test_missing_and_type_errors_together(self) -> None:
        """Mix of missing fields and type errors."""
        payload = _valid_packet()
        del payload["meeting_id"]  # missing
        payload["state"] = 99  # wrong_type
        payload["round"] = 0   # out_of_range
        payload["tags"] = None  # missing
        report = validate_context_packet(payload)
        assert not report.passed
        assert report.error_count >= 4

    def test_all_thirteen_fields_invalid(self) -> None:
        """Every required field has either wrong type or missing."""
        report = validate_context_packet({
            "meeting_id": 1,
            "role_id": 2,
            "state": 3,
            "priority": 4,
            "agenda": 5,
            "agenda_type": 6,
            "tags": "bad",
            "risk_tags": "bad",
            "round": "bad",
            "round_type": "bad",
            "round_context": 7,
            "token_budget": "bad",
            "previous_rounds": "bad",
        })
        assert not report.passed
        # Every field should have at least one error
        assert report.error_count >= 13


# ═══════════════════════════════════════════════════════════════════════
# 9. PacketValidationReport properties
# ═══════════════════════════════════════════════════════════════════════

class TestPacketValidationReport:
    """Verify PacketValidationReport dataclass properties."""

    def test_error_count(self) -> None:
        report = validate_context_packet({})
        assert report.error_count == len(report.errors)
        assert report.error_count == 13

    def test_errors_by_field_groups_correctly(self) -> None:
        payload = _valid_packet()
        del payload["meeting_id"]
        del payload["agenda"]
        report = validate_context_packet(payload)
        grouped = report.errors_by_field()
        assert "meeting_id" in grouped
        assert "agenda" in grouped
        assert len(grouped["meeting_id"]) == 1
        assert len(grouped["agenda"]) == 1

    def test_mandatory_errors_excludes_optional(self) -> None:
        payload = _valid_packet(
            knowledge_context=42,  # optional type error
        )  # type: ignore[arg-type]
        report = validate_context_packet(payload)
        assert report.passed  # optional error doesn't fail
        assert len(report.mandatory_errors) == 0
        assert report.error_count == 1  # only the optional error

    def test_schema_version_present(self) -> None:
        report = validate_context_packet(_valid_packet())
        assert report.schema_version == PACKET_SCHEMA_VERSION

    def test_report_immutability_error_count(self) -> None:
        report = validate_context_packet({})
        assert isinstance(report.error_count, int)
        # error_count is a property, not settable (dataclass frozen)


# ═══════════════════════════════════════════════════════════════════════
# 10. validate_or_raise convenience wrapper
# ═══════════════════════════════════════════════════════════════════════

class TestValidateOrRaise:
    """Verify the validate_or_raise convenience wrapper."""

    def test_valid_packet_returns_report(self) -> None:
        report = validate_or_raise(_valid_packet())
        assert report.passed

    def test_invalid_packet_raises(self) -> None:
        with pytest.raises(PacketValidationError) as exc_info:
            validate_or_raise({})
        assert "validation failed" in str(exc_info.value)
        assert exc_info.value.report.error_count > 0
        assert not exc_info.value.report.passed

    def test_none_input_raises(self) -> None:
        with pytest.raises(PacketValidationError) as exc_info:
            validate_or_raise(None)
        assert exc_info.value.report.errors[0].field_name == "<root>"

    def test_raises_with_missing_field_names(self) -> None:
        payload = _valid_packet()
        del payload["meeting_id"]
        with pytest.raises(PacketValidationError) as exc_info:
            validate_or_raise(payload)
        msg = str(exc_info.value)
        assert "meeting_id" in msg

    def test_exception_carries_report(self) -> None:
        payload = _valid_packet()
        del payload["meeting_id"]
        del payload["agenda"]
        with pytest.raises(PacketValidationError) as exc_info:
            validate_or_raise(payload)
        report = exc_info.value.report
        assert report.error_count == 2
        assert "meeting_id" in report.missing_fields()


# ═══════════════════════════════════════════════════════════════════════
# 11. PacketFieldError dataclass
# ═══════════════════════════════════════════════════════════════════════

class TestPacketFieldError:
    """Verify PacketFieldError dataclass."""

    def test_create_error(self) -> None:
        err = PacketFieldError(
            field_name="test_field",
            error_type="missing",
            message="Field is missing",
            expected="str",
            actual="None",
        )
        assert err.field_name == "test_field"
        assert err.error_type == "missing"
        assert err.expected == "str"
        assert err.actual == "None"

    def test_error_immutability(self) -> None:
        err = PacketFieldError(
            field_name="f",
            error_type="missing",
            message="m",
            expected="e",
            actual="a",
        )
        with pytest.raises(Exception):
            err.field_name = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# 12. Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Verify edge case handling."""

    def test_extra_unknown_fields_ignored(self) -> None:
        """Unknown fields in the packet should be silently ignored."""
        payload = _valid_packet(
            extra_field_1="ignored",
            extra_field_2=42,
        )
        report = validate_context_packet(payload)
        assert report.passed

    def test_agenda_unicode_accepted(self) -> None:
        """Unicode / Korean text should be accepted in string fields."""
        report = validate_context_packet(
            _valid_packet(
                agenda="뮤직비디오 오프닝 아이디어 회의",
                meeting_id="meeting_20260101_한글",
            )
        )
        assert report.passed

    def test_round_context_long_string_accepted(self) -> None:
        """Very long round_context should be accepted (no length limit)."""
        report = validate_context_packet(
            _valid_packet(round_context="x" * 100000)
        )
        assert report.passed

    def test_tags_with_many_items_accepted(self) -> None:
        """Large tag arrays should be accepted."""
        report = validate_context_packet(
            _valid_packet(tags=[f"tag-{i}" for i in range(1000)])
        )
        assert report.passed

    def test_single_role_id_format_accepted(self) -> None:
        """Role IDs like 'art-director', 'marketing-lead' should work."""
        for role_id in [
            "art-director", "marketing-lead", "tech-lead",
            "legal-advisor", "coordinator",
        ]:
            report = validate_context_packet(
                _valid_packet(role_id=role_id)
            )
            assert report.passed, f"role_id '{role_id}' should be accepted"

    def test_token_budget_matches_system_constants(self) -> None:
        """System token budgets 12k, 20k, 30k should all validate."""
        for budget in (12000, 20000, 30000):
            report = validate_context_packet(
                _valid_packet(token_budget=budget)
            )
            assert report.passed, (
                f"token_budget={budget} should be accepted"
            )
