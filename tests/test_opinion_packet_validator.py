"""Comprehensive tests for the opinion packet validator.

Sub-AC 5a-1: Opinion packet schema validation — defines required fields
(persona_id, agenda_item_ref, opinion_content, confidence, timestamp)
and verifies packet structure completeness via a validator function
with valid/invalid packet test cases.

Tested with mock dict payloads covering:
- all-valid packet (baseline)
- each required field individually missing
- each field individually invalid (wrong type, empty, out of range)
- null/non-dict input
- persona_id kebab-case validation
- timestamp ISO-8601 format validation
- confidence float range and type checks
- error aggregation / multiple simultaneous errors
- OpinionPacketValidationReport properties and immutability
"""

from __future__ import annotations

import pytest

from src.opinion_packet_validator import (
    OPINION_PACKET_FIELDS,
    OpinionFieldValidationError,
    OpinionPacketValidationReport,
    validate_opinion_packet,
)


# ═══════════════════════════════════════════════════════════════════════
# Helper: build a valid opinion packet with optional overrides
# ═══════════════════════════════════════════════════════════════════════

def _valid_packet(**overrides: object) -> dict[str, object]:
    """Return a fully valid opinion packet dict."""
    defaults: dict[str, object] = {
        "persona_id": "art-director",
        "agenda_item_ref": "character-visual-concept",
        "opinion_content": (
            "We should adopt a neon-noir visual palette with "
            "high-contrast silhouettes for the protagonist line."
        ),
        "confidence": 0.88,
        "timestamp": "2026-06-10T14:30:00Z",
    }
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid packet — baseline
# ═══════════════════════════════════════════════════════════════════════


class TestValidPacket:
    """Verify that a fully correct opinion packet passes validation."""

    def test_all_fields_valid_passes(self) -> None:
        report = validate_opinion_packet(_valid_packet())
        assert report.passed
        assert report.error_count == 0
        assert len(report.errors) == 0

    def test_total_fields_checked_matches_declared(self) -> None:
        report = validate_opinion_packet(_valid_packet())
        assert report.total_fields_checked == len(OPINION_PACKET_FIELDS)

    def test_valid_persona_ids(self) -> None:
        """All common kebab-case persona IDs should pass."""
        valid_ids = [
            "art-director",
            "marketing-lead",
            "technical-director",
            "coordinator",
            "sns-strategist",
            "concept-artist",
            "sound-designer",
            "project-manager",
            "risk-analyst",
            "data-engineer",
        ]
        for pid in valid_ids:
            report = validate_opinion_packet(
                _valid_packet(persona_id=pid)
            )
            assert report.passed, f"persona_id '{pid}' should be valid"

    def test_integer_confidence_accepted(self) -> None:
        """Integer 0 and 1 should be accepted and cast to float."""
        for val in (0, 1):
            report = validate_opinion_packet(
                _valid_packet(confidence=val)
            )
            assert report.passed, f"confidence={val} should pass"

    def test_various_timestamp_formats_accepted(self) -> None:
        """Multiple valid ISO-8601 variants should pass."""
        timestamps = [
            "2026-06-10T14:30:00Z",
            "2026-06-10T14:30:00+09:00",
            "2026-06-10T14:30:00-05:00",
            "2026-06-10T14:30:00.123456Z",
            "2026-06-10T14:30:00.999+00:00",
            "2026-01-01T00:00:00Z",
            "2026-12-31T23:59:59Z",
            "2026-06-10 14:30:00",  # space separator tolerated
        ]
        for ts in timestamps:
            report = validate_opinion_packet(
                _valid_packet(timestamp=ts)
            )
            assert report.passed, f"timestamp '{ts}' should be valid"

    def test_extended_opinion_content_accepted(self) -> None:
        """Opinion content can be long (up to token budget limits)."""
        long_opinion = "X" * 5000
        report = validate_opinion_packet(
            _valid_packet(opinion_content=long_opinion)
        )
        assert report.passed

    def test_single_char_opinion_content_accepted(self) -> None:
        """Single non-whitespace character is valid content."""
        report = validate_opinion_packet(
            _valid_packet(opinion_content="A")
        )
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 2. persona_id validation
# ═══════════════════════════════════════════════════════════════════════


class TestPersonaIdValidation:
    """Verify persona_id kebab-case and type checks."""

    def test_none_persona_id_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(persona_id=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "persona_id"
        )
        assert err.error_type == "missing"

    def test_empty_persona_id_rejected(self) -> None:
        for val in ("", "   ", "\t\n"):
            report = validate_opinion_packet(
                _valid_packet(persona_id=val)
            )
            assert not report.passed, f"persona_id={repr(val)} should fail"
            err = next(
                e for e in report.errors if e.field_name == "persona_id"
            )
            assert err.error_type == "empty_string"

    def test_non_string_persona_id_rejected(self) -> None:
        for val in (42, 3.14, True, [], {}):
            report = validate_opinion_packet(
                _valid_packet(persona_id=val)  # type: ignore[arg-type]
            )
            assert not report.passed, (
                f"persona_id={type(val).__name__} should fail"
            )
            err = next(
                e for e in report.errors if e.field_name == "persona_id"
            )
            assert err.error_type == "wrong_type"

    def test_invalid_kebab_case_rejected(self) -> None:
        """Non-kebab-case identifiers should be rejected."""
        invalid_ids = [
            "Art-Director",          # uppercase
            "art_director",          # underscore
            "-art-director",         # leading hyphen
            "art-director-",         # trailing hyphen
            "art--director",         # double hyphen
            "art director",          # space
            "123-start",             # starts with digit (not lower-alpha)
            "art@director",          # special char
        ]
        for pid in invalid_ids:
            report = validate_opinion_packet(
                _valid_packet(persona_id=pid)
            )
            assert not report.passed, (
                f"persona_id '{pid}' should be invalid kebab-case"
            )
            err = next(
                e for e in report.errors if e.field_name == "persona_id"
            )
            # Either invalid_value (wrong format) or empty_string
            assert err.error_type in ("invalid_value", "empty_string")


# ═══════════════════════════════════════════════════════════════════════
# 3. agenda_item_ref validation
# ═══════════════════════════════════════════════════════════════════════


class TestAgendaItemRefValidation:
    """Verify agenda_item_ref non-empty string checks."""

    def test_none_agenda_item_ref_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(agenda_item_ref=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.field_name == "agenda_item_ref"
        )
        assert err.error_type == "missing"

    def test_empty_agenda_item_ref_rejected(self) -> None:
        for val in ("", "   ", "\t"):
            report = validate_opinion_packet(
                _valid_packet(agenda_item_ref=val)
            )
            assert not report.passed
            err = next(
                e for e in report.errors
                if e.field_name == "agenda_item_ref"
            )
            assert err.error_type == "empty_string"

    def test_non_string_agenda_item_ref_rejected(self) -> None:
        for val in (42, 3.14, True, [], {}):
            report = validate_opinion_packet(
                _valid_packet(agenda_item_ref=val)  # type: ignore[arg-type]
            )
            assert not report.passed
            err = next(
                e for e in report.errors
                if e.field_name == "agenda_item_ref"
            )
            assert err.error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 4. opinion_content validation
# ═══════════════════════════════════════════════════════════════════════


class TestOpinionContentValidation:
    """Verify opinion_content non-empty string checks."""

    def test_none_opinion_content_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(opinion_content=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.field_name == "opinion_content"
        )
        assert err.error_type == "missing"

    def test_empty_opinion_content_rejected(self) -> None:
        for val in ("", "   ", "\n\n"):
            report = validate_opinion_packet(
                _valid_packet(opinion_content=val)
            )
            assert not report.passed
            err = next(
                e for e in report.errors
                if e.field_name == "opinion_content"
            )
            assert err.error_type == "empty_string"

    def test_non_string_opinion_content_rejected(self) -> None:
        for val in (42, 3.14, True, [], {"key": "val"}):
            report = validate_opinion_packet(
                _valid_packet(opinion_content=val)  # type: ignore[arg-type]
            )
            assert not report.passed
            err = next(
                e for e in report.errors
                if e.field_name == "opinion_content"
            )
            assert err.error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 5. Confidence validation
# ═══════════════════════════════════════════════════════════════════════


class TestConfidenceValidation:
    """Verify confidence float range and type checks.

    Unlike the Qwen field validator where confidence is optional,
    opinion packets REQUIRE a confidence score.
    """

    def test_valid_confidence_values_accepted(self) -> None:
        for val in (0.0, 0.5, 0.88, 1.0, 0, 1):
            report = validate_opinion_packet(
                _valid_packet(confidence=val)
            )
            assert report.passed, f"confidence={val} should pass"

    def test_none_confidence_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "missing"

    def test_confidence_below_zero_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=-0.01)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"

    def test_confidence_above_one_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=1.01)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"

    def test_confidence_boolean_rejected(self) -> None:
        """JSON booleans are not valid for confidence field."""
        for val in (True, False):
            report = validate_opinion_packet(
                _valid_packet(confidence=val)
            )
            assert not report.passed
            err = next(
                e for e in report.errors if e.field_name == "confidence"
            )
            assert err.error_type == "wrong_type"

    def test_confidence_string_rejected(self) -> None:
        for val in ("0.5", "high", "medium"):
            report = validate_opinion_packet(
                _valid_packet(confidence=val)
            )
            assert not report.passed
            err = next(
                e for e in report.errors if e.field_name == "confidence"
            )
            assert err.error_type == "wrong_type"

    def test_confidence_array_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=[0.5])  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "wrong_type"

    def test_confidence_very_large_number_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=999.0)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"

    def test_confidence_very_small_negative_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(confidence=-0.0001)
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "confidence"
        )
        assert err.error_type == "invalid_value"


# ═══════════════════════════════════════════════════════════════════════
# 6. Timestamp validation
# ═══════════════════════════════════════════════════════════════════════


class TestTimestampValidation:
    """Verify timestamp ISO-8601 format checks."""

    def test_none_timestamp_rejected(self) -> None:
        report = validate_opinion_packet(
            _valid_packet(timestamp=None)  # type: ignore[arg-type]
        )
        assert not report.passed
        err = next(
            e for e in report.errors if e.field_name == "timestamp"
        )
        assert err.error_type == "missing"

    def test_empty_timestamp_rejected(self) -> None:
        for val in ("", "   "):
            report = validate_opinion_packet(
                _valid_packet(timestamp=val)
            )
            assert not report.passed
            err = next(
                e for e in report.errors if e.field_name == "timestamp"
            )
            assert err.error_type == "empty_string"

    def test_non_string_timestamp_rejected(self) -> None:
        for val in (42, 3.14, True, [], {}):
            report = validate_opinion_packet(
                _valid_packet(timestamp=val)  # type: ignore[arg-type]
            )
            assert not report.passed
            err = next(
                e for e in report.errors if e.field_name == "timestamp"
            )
            assert err.error_type == "wrong_type"

    def test_invalid_timestamp_formats_rejected(self) -> None:
        """Various malformed timestamp strings should fail."""
        invalid_ts = [
            "2026-06-10",                     # date only
            "14:30:00",                       # time only
            "2026/06/10 14:30:00",            # wrong separator
            "2026-06-10T14:30",               # missing seconds
            "20260610T143000Z",               # no separators
            "not-a-timestamp",                # garbage
            "2026-06-10T14:30:00ZZ",          # double Z
            "2026-06-10T14:30:00+09:00extra", # trailing garbage
        ]
        for ts in invalid_ts:
            report = validate_opinion_packet(
                _valid_packet(timestamp=ts)
            )
            assert not report.passed, f"timestamp '{ts}' should fail"
            err = next(
                e for e in report.errors if e.field_name == "timestamp"
            )
            assert err.error_type == "bad_timestamp", (
                f"Expected 'bad_timestamp' for '{ts}', "
                f"got '{err.error_type}'"
            )


# ═══════════════════════════════════════════════════════════════════════
# 7. Missing required fields
# ═══════════════════════════════════════════════════════════════════════


class TestMissingFields:
    """Verify that every required field is checked for presence."""

    REQUIRED_FIELD_NAMES = (
        "persona_id",
        "agenda_item_ref",
        "opinion_content",
        "confidence",
        "timestamp",
    )

    def test_each_required_field_missing_detected(self) -> None:
        for field in self.REQUIRED_FIELD_NAMES:
            payload = _valid_packet()
            del payload[field]
            report = validate_opinion_packet(payload)
            assert not report.passed, (
                f"Missing '{field}' should cause failure"
            )
            err = next(
                e for e in report.errors if e.field_name == field
            )
            assert err.error_type == "missing"

    def test_all_fields_missing(self) -> None:
        """Empty dict should produce one error per required field."""
        report = validate_opinion_packet({})
        assert not report.passed
        assert report.error_count == len(self.REQUIRED_FIELD_NAMES)
        for field in self.REQUIRED_FIELD_NAMES:
            assert any(
                e.field_name == field and e.error_type == "missing"
                for e in report.errors
            ), f"Missing-field error for '{field}' not found"


# ═══════════════════════════════════════════════════════════════════════
# 8. Null / non-dict input
# ═══════════════════════════════════════════════════════════════════════


class TestNullAndNonDictInput:
    """Verify graceful handling of None and non-dict arguments."""

    def test_none_input_fails(self) -> None:
        report = validate_opinion_packet(None)
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "missing"
        assert report.total_fields_checked == 0

    def test_list_input_fails(self) -> None:
        report = validate_opinion_packet([1, 2, 3])  # type: ignore[arg-type]
        assert not report.passed
        assert report.error_count == 1
        assert report.errors[0].field_name == "<root>"
        assert report.errors[0].error_type == "wrong_type"

    def test_string_input_fails(self) -> None:
        report = validate_opinion_packet("not a dict")  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_int_input_fails(self) -> None:
        report = validate_opinion_packet(42)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"

    def test_float_input_fails(self) -> None:
        report = validate_opinion_packet(3.14)  # type: ignore[arg-type]
        assert not report.passed

    def test_bool_input_fails(self) -> None:
        report = validate_opinion_packet(True)  # type: ignore[arg-type]
        assert not report.passed
        assert report.errors[0].error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 9. Error aggregation — multiple simultaneous errors
# ═══════════════════════════════════════════════════════════════════════


class TestErrorAggregation:
    """Verify that multiple errors are all collected (no early-exit)."""

    def test_all_fields_invalid_returns_all_errors(self) -> None:
        """Every field invalid — should return 5 distinct errors."""
        report = validate_opinion_packet({
            "persona_id": 123,
            "agenda_item_ref": "",
            "opinion_content": None,
            "confidence": 1.5,
            "timestamp": "yesterday",
        })
        assert not report.passed
        # persona_id: wrong_type, agenda_item_ref: empty_string,
        # opinion_content: missing (None), confidence: invalid_value,
        # timestamp: bad_timestamp — 5 errors total
        assert report.error_count == 5

        error_fields = {e.field_name for e in report.errors}
        assert error_fields == {
            "persona_id",
            "agenda_item_ref",
            "opinion_content",
            "confidence",
            "timestamp",
        }

    def test_mixed_valid_and_invalid_collects_only_invalid(self) -> None:
        """Valid fields should not produce errors alongside invalid ones."""
        report = validate_opinion_packet({
            "persona_id": "valid-director",
            "agenda_item_ref": "valid-ref",
            "opinion_content": "valid content",
            "confidence": 2.0,   # invalid
            "timestamp": "nope",  # invalid
        })
        assert not report.passed
        assert report.error_count == 2
        error_fields = {e.field_name for e in report.errors}
        assert error_fields == {"confidence", "timestamp"}

    def test_three_fields_broken_fourth_and_fifth_valid(self) -> None:
        report = validate_opinion_packet({
            "persona_id": "",
            "agenda_item_ref": None,
            "opinion_content": "valid",
            "confidence": True,
            "timestamp": "2026-06-10T14:30:00Z",
        })
        assert not report.passed
        assert report.error_count == 3
        # persona_id: empty, agenda_item_ref: missing,
        # confidence: wrong_type — 3 errors.
        # opinion_content and timestamp are valid.
        error_types = {e.error_type for e in report.errors}
        assert "empty_string" in error_types
        assert "missing" in error_types
        assert "wrong_type" in error_types

    def test_errors_by_field_groups_correctly(self) -> None:
        report = validate_opinion_packet({})
        grouped = report.errors_by_field()
        assert len(grouped) == 5
        for field in grouped:
            assert len(grouped[field]) == 1
            assert grouped[field][0].error_type == "missing"


# ═══════════════════════════════════════════════════════════════════════
# 10. Report properties and immutability
# ═══════════════════════════════════════════════════════════════════════


class TestReportProperties:
    """Verify OpinionPacketValidationReport behaviour."""

    def test_passed_report_has_empty_errors(self) -> None:
        report = validate_opinion_packet(_valid_packet())
        assert report.passed
        assert report.error_count == 0
        assert report.errors == ()
        assert report.total_fields_checked == len(OPINION_PACKET_FIELDS)

    def test_failed_report_has_positive_error_count(self) -> None:
        report = validate_opinion_packet({})
        assert not report.passed
        assert report.error_count > 0
        assert len(report.errors) == report.error_count

    def test_report_is_immutable(self) -> None:
        """Report dataclass is frozen — mutation should raise."""
        report = validate_opinion_packet(_valid_packet())
        with pytest.raises(Exception):
            report.passed = False  # type: ignore[misc]

    def test_error_dataclass_is_immutable(self) -> None:
        """Error dataclass is frozen — mutation should raise."""
        report = validate_opinion_packet({})
        err = report.errors[0]
        with pytest.raises(Exception):
            err.field_name = "hacked"  # type: ignore[misc]

    def test_errors_by_field_on_passed_report(self) -> None:
        report = validate_opinion_packet(_valid_packet())
        assert report.errors_by_field() == {}

    def test_error_message_contains_field_name(self) -> None:
        report = validate_opinion_packet({})
        for err in report.errors:
            assert err.field_name in err.message or err.field_name == "<root>", (
                f"Error message for '{err.field_name}' should reference "
                f"the field: {err.message}"
            )


# ═══════════════════════════════════════════════════════════════════════
# 11. Integration-style: real meeting scenario packets
# ═══════════════════════════════════════════════════════════════════════


class TestRealMeetingScenarios:
    """Verify validation with realistic meeting-round opinion packets."""

    def test_seven_role_packets_all_valid(self) -> None:
        """Simulate a full round of 7 team-leader opinions."""
        packets = [
            {
                "persona_id": "coordinator",
                "agenda_item_ref": "quarterly-talent-review",
                "opinion_content": "All teams should submit talent metrics.",
                "confidence": 0.95,
                "timestamp": "2026-06-10T15:00:00Z",
            },
            {
                "persona_id": "art-director",
                "agenda_item_ref": "character-design-pipeline",
                "opinion_content": "We need 3 more concept artists for Q3.",
                "confidence": 0.82,
                "timestamp": "2026-06-10T15:01:00Z",
            },
            {
                "persona_id": "technical-director",
                "agenda_item_ref": "engine-upgrade",
                "opinion_content": "UE 5.6 migration feasible by September.",
                "confidence": 0.78,
                "timestamp": "2026-06-10T15:02:00Z",
            },
            {
                "persona_id": "marketing-lead",
                "agenda_item_ref": "sns-campaign-launch",
                "opinion_content": "TikTok teaser campaign ready for Friday.",
                "confidence": 0.91,
                "timestamp": "2026-06-10T15:03:00Z",
            },
            {
                "persona_id": "sound-designer",
                "agenda_item_ref": "bgm-production",
                "opinion_content": "3 tracks complete, 2 need revision.",
                "confidence": 0.74,
                "timestamp": "2026-06-10T15:04:00Z",
            },
            {
                "persona_id": "project-manager",
                "agenda_item_ref": "sprint-retrospective",
                "opinion_content": "Velocity improved 12% this sprint.",
                "confidence": 0.88,
                "timestamp": "2026-06-10T15:05:00Z",
            },
            {
                "persona_id": "risk-analyst",
                "agenda_item_ref": "q3-risk-register",
                "opinion_content": "Brand dilution risk elevated to P1.",
                "confidence": 0.67,
                "timestamp": "2026-06-10T15:06:00Z",
            },
        ]
        for i, packet in enumerate(packets):
            report = validate_opinion_packet(packet)
            assert report.passed, (
                f"Packet {i} ({packet.get('persona_id')}) should pass: "
                f"{report.errors}"
            )

    def test_malformed_packet_in_batch_detected(self) -> None:
        """One bad packet in a batch should be caught."""
        packets = [
            _valid_packet(persona_id="coordinator"),
            _valid_packet(persona_id="art-director"),
            {
                "persona_id": "",           # invalid
                "agenda_item_ref": "test",
                "opinion_content": "test",
                "confidence": 0.5,
                "timestamp": "not-a-date",  # invalid
            },
        ]
        results = [validate_opinion_packet(p) for p in packets]
        assert results[0].passed
        assert results[1].passed
        assert not results[2].passed
        assert results[2].error_count == 2  # empty persona_id + bad timestamp
