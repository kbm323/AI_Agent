"""Tests for the round-1 packet set assembler.

Sub-AC 5a-4: Round-1 packet set assembly — collect N independently
generated per-persona packets into a complete round-1 opinion set,
verify count matches active personas and all packets pass structure
validation.

Coverage:
    - Happy path: all personas present, all valid → assembled=True
    - Count mismatch: fewer results than expected personas
    - Missing persona: expected persona with no result at all
    - Extra persona: result for a persona not in the expected list
    - Generation failure: result with success=False
    - Duplicate persona_id entries
    - Structural validation failure (field-level errors)
    - Structural validation: None opinion_packet with success=True
    - Strict mode vs non-strict mode
    - Empty inputs (results=[], expected_role_ids=())
    - Wrong types for parameters
    - Multiple personas: 3, 5, 7 active roles
    - All failure case: no successful generations
    - Mixed: some succeed, some fail, some missing
    - Error detail inspection: PacketAssemblyError fields
    - RoundPacketSetResult property accessors
    - persona_ids_assembled and persona_ids_missing accuracy
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.opinion_packet_validator import (
    OpinionPacketValidationReport,
    validate_opinion_packet,
)
from src.persona_opinion_generator import (
    OpinionGenerationResult,
    PersonaDefinition,
)
from src.round_packet_assembler import (
    PacketAssemblyError,
    RoundPacketSetResult,
    _detect_duplicates,
    _group_results_by_persona,
    _revalidate_packets,
    _verify_count,
    assemble_round_one_packets,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper factories
# ═════════════════════════════════════════════════════════════════════════


def _make_success_result(
    role_id: str,
    *,
    confidence: float = 0.85,
    agenda_item_ref: str = "test-agenda",
    opinion_content: str = "Test opinion content.",
    timestamp: str = "2026-06-10T14:30:00Z",
) -> OpinionGenerationResult:
    """Create a successful OpinionGenerationResult with a valid packet."""
    packet: dict[str, object] = {
        "persona_id": role_id,
        "agenda_item_ref": agenda_item_ref,
        "opinion_content": opinion_content,
        "confidence": confidence,
        "timestamp": timestamp,
    }
    report = validate_opinion_packet(packet)
    assert report.passed, f"Test helper produced invalid packet: {report.errors}"
    return OpinionGenerationResult(
        success=True,
        opinion_packet=packet,
        validation_report=report,
        role_id=role_id,
        model_name="qwen-max",
        duration_seconds=1.5,
    )


def _make_failure_result(
    role_id: str,
    *,
    error_message: str = "Generation failed.",
) -> OpinionGenerationResult:
    """Create a failed OpinionGenerationResult."""
    return OpinionGenerationResult(
        success=False,
        role_id=role_id,
        model_name="qwen-max",
        duration_seconds=0.5,
        error_message=error_message,
    )


def _make_invalid_packet_result(
    role_id: str,
    *,
    packet_overrides: dict[str, Any] | None = None,
) -> OpinionGenerationResult:
    """Create a result with success=True but an invalid packet.

    The packet will fail ``validate_opinion_packet()``.
    """
    base_packet: dict[str, Any] = {
        "persona_id": role_id,
        "agenda_item_ref": "test-agenda",
        "opinion_content": "Some opinion.",
        "confidence": 0.8,
        "timestamp": "2026-06-10T14:30:00Z",
    }
    if packet_overrides:
        base_packet.update(packet_overrides)

    report = validate_opinion_packet(base_packet)
    # This result has success=True deliberately — the assembler
    # should catch the invalid packet in re-validation gate.
    return OpinionGenerationResult(
        success=True,
        opinion_packet=base_packet,
        validation_report=report,
        role_id=role_id,
        model_name="qwen-max",
        duration_seconds=1.0,
    )


def _make_none_packet_result(role_id: str) -> OpinionGenerationResult:
    """Create a result with success=True but opinion_packet=None."""
    return OpinionGenerationResult(
        success=True,
        opinion_packet=None,
        role_id=role_id,
        model_name="qwen-max",
        duration_seconds=0.3,
    )


# ═════════════════════════════════════════════════════════════════════════
# 1. Happy path — complete valid set
# ═════════════════════════════════════════════════════════════════════════


class TestHappyPath:
    """Verify the assembler produces assembled=True for complete valid sets."""

    def test_three_personas_all_valid(self) -> None:
        """All 3 expected personas present with valid packets."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
            _make_success_result("marketing-lead"),
        ]
        expected = ("art-director", "tech-director", "marketing-lead")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is True
        assert assembled.actual_count == 3
        assert assembled.expected_count == 3
        assert assembled.failed_count == 0
        assert assembled.error_count == 0
        # Sorted alphabetically: art-director, marketing-lead, tech-director
        assert assembled.persona_ids_assembled == (
            "art-director",
            "marketing-lead",
            "tech-director",
        )
        assert assembled.persona_ids_missing == ()
        assert assembled.all_personas_present is True
        assert len(assembled.opinion_packets) == 3

    def test_five_personas_all_valid(self) -> None:
        """All 5 expected personas present with valid packets."""
        role_ids = (
            "content-director",
            "art-director",
            "tech-director",
            "marketing-lead",
            "scriptwriter",
        )
        results = [_make_success_result(rid) for rid in role_ids]

        assembled = assemble_round_one_packets(results, role_ids)

        assert assembled.assembled is True
        assert assembled.actual_count == 5
        assert assembled.expected_count == 5
        assert assembled.failed_count == 0
        assert assembled.error_count == 0

    def test_seven_personas_all_valid(self) -> None:
        """Max 7 agents per meeting (per Seed constraint)."""
        role_ids = (
            "content-director",
            "art-director",
            "tech-director",
            "marketing-lead",
            "scriptwriter",
            "character-designer",
            "backend-engineer",
        )
        results = [_make_success_result(rid) for rid in role_ids]

        assembled = assemble_round_one_packets(results, role_ids)

        assert assembled.assembled is True
        assert assembled.actual_count == 7

    def test_single_persona(self) -> None:
        """Single-persona meeting (e.g. solo review)."""
        role_ids = ("art-director",)
        results = [_make_success_result("art-director")]

        assembled = assemble_round_one_packets(results, role_ids)

        assert assembled.assembled is True
        assert assembled.actual_count == 1
        assert len(assembled.opinion_packets) == 1
        assert assembled.opinion_packets[0]["persona_id"] == "art-director"

    def test_opinion_packets_in_expected_order(self) -> None:
        """Assembled packets should be sorted by role_id (deterministic)."""
        role_ids = ("tech-director", "art-director", "marketing-lead")
        results = [
            _make_success_result("tech-director"),
            _make_success_result("art-director"),
            _make_success_result("marketing-lead"),
        ]

        assembled = assemble_round_one_packets(results, role_ids)

        # Sorted by role_id: art-director, marketing-lead, tech-director
        assert assembled.persona_ids_assembled == (
            "art-director",
            "marketing-lead",
            "tech-director",
        )
        packet_ids = [p["persona_id"] for p in assembled.opinion_packets]
        assert packet_ids == ["art-director", "marketing-lead", "tech-director"]

    def test_packet_content_preserved(self) -> None:
        """Opinion packet content must be passed through unchanged."""
        results = [
            _make_success_result(
                "art-director",
                confidence=0.92,
                opinion_content="We should use neon-noir palette.",
                timestamp="2026-06-10T14:30:00Z",
            ),
        ]
        assembled = assemble_round_one_packets(
            results, ("art-director",)
        )

        pkt = assembled.opinion_packets[0]
        assert pkt["persona_id"] == "art-director"
        assert pkt["confidence"] == 0.92
        assert pkt["opinion_content"] == "We should use neon-noir palette."
        assert pkt["timestamp"] == "2026-06-10T14:30:00Z"


# ═════════════════════════════════════════════════════════════════════════
# 2. Count mismatch
# ═════════════════════════════════════════════════════════════════════════


class TestCountMismatch:
    """Verify the assembler detects and reports count mismatches."""

    def test_fewer_results_than_expected(self) -> None:
        """Only 1 result for 3 expected roles."""
        results = [_make_success_result("art-director")]
        expected = ("art-director", "tech-director", "marketing-lead")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert assembled.actual_count == 1
        assert assembled.expected_count == 3
        assert assembled.persona_ids_missing == (
            "marketing-lead",
            "tech-director",
        )
        assert any(
            e.error_category == "count_mismatch"
            for e in assembled.errors
        )
        assert any(
            e.error_category == "missing_persona"
            for e in assembled.errors
        )

    def test_more_results_than_expected(self) -> None:
        """4 results for 2 expected roles."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
            _make_success_result("marketing-lead"),
            _make_success_result("scriptwriter"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert assembled.actual_count == 2  # only expected ones assembled
        assert assembled.expected_count == 2

    def test_zero_expected_personas_raises(self) -> None:
        """Empty expected_role_ids must raise ValueError."""
        with pytest.raises(ValueError, match="expected_role_ids must be"):
            assemble_round_one_packets(
                [_make_success_result("test")],
                (),
            )

    def test_empty_results_raises(self) -> None:
        """Empty results list must raise ValueError."""
        with pytest.raises(ValueError, match="results must be"):
            assemble_round_one_packets(
                [],
                ("art-director",),
            )


# ═════════════════════════════════════════════════════════════════════════
# 3. Missing / extra / failed personas
# ═════════════════════════════════════════════════════════════════════════


class TestPersonaDiscrepancies:
    """Verify individual persona-level discrepancy detection."""

    def test_missing_persona_no_result(self) -> None:
        """An expected persona has no result at all."""
        results = [
            _make_success_result("art-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert "tech-director" in assembled.persona_ids_missing
        assert any(
            e.error_category == "missing_persona"
            and e.persona_id == "tech-director"
            for e in assembled.errors
        )

    def test_failed_generation(self) -> None:
        """A persona has a result but it failed."""
        results = [
            _make_success_result("art-director"),
            _make_failure_result("tech-director", error_message="timeout"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert assembled.failed_count == 1
        assert "tech-director" in assembled.persona_ids_missing
        assert any(
            e.error_category == "generation_failure"
            and e.persona_id == "tech-director"
            for e in assembled.errors
        )

    def test_extra_persona_not_expected(self) -> None:
        """A result exists for a persona not in expected_role_ids."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("unexpected-role"),
        ]
        expected = ("art-director",)

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert any(
            e.error_category == "extra_persona"
            and e.persona_id == "unexpected-role"
            for e in assembled.errors
        )
        # The unexpected role's packet should NOT be in the assembled set
        assert "unexpected-role" not in assembled.persona_ids_assembled

    def test_all_failed(self) -> None:
        """No successful generations at all."""
        results = [
            _make_failure_result("art-director"),
            _make_failure_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert assembled.actual_count == 0
        assert assembled.failed_count == 2
        assert assembled.opinion_packets == ()
        assert assembled.persona_ids_assembled == ()

    def test_mixed_success_failure_missing(self) -> None:
        """Mix of success, failure, and completely missing personas."""
        results = [
            _make_success_result("art-director"),
            _make_failure_result("tech-director"),
            # scriptwriter: no result at all
        ]
        expected = ("art-director", "tech-director", "scriptwriter")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert assembled.actual_count == 1
        assert assembled.failed_count == 1
        missing = set(assembled.persona_ids_missing)
        assert missing == {"tech-director", "scriptwriter"}


# ═════════════════════════════════════════════════════════════════════════
# 4. Duplicate detection
# ═════════════════════════════════════════════════════════════════════════


class TestDuplicateDetection:
    """Verify duplicate persona_id entries are detected."""

    def test_duplicate_success_results(self) -> None:
        """Two successful results for the same persona."""
        results = [
            _make_success_result("art-director", confidence=0.9),
            _make_success_result("art-director", confidence=0.7),
        ]
        expected = ("art-director",)

        assembled = assemble_round_one_packets(results, expected)

        # Duplicate error should be reported, but first result is kept
        assert any(
            e.error_category == "duplicate_persona"
            and e.persona_id == "art-director"
            for e in assembled.errors
        )
        # Should still assemble (first result is used)
        assert assembled.assembled is True
        assert assembled.actual_count == 1
        assert assembled.opinion_packets[0]["confidence"] == 0.9

    def test_duplicate_first_fails_second_succeeds(self) -> None:
        """First result fails, second succeeds — should use the success."""
        results = [
            _make_failure_result("art-director"),
            _make_success_result("art-director", confidence=0.85),
        ]
        expected = ("art-director",)

        assembled = assemble_round_one_packets(results, expected)

        assert any(
            e.error_category == "duplicate_persona"
            for e in assembled.errors
        )
        assert assembled.assembled is True
        assert assembled.actual_count == 1

    def test_triple_duplicate(self) -> None:
        """Three results for the same persona."""
        results = [
            _make_success_result("art-director", confidence=0.9),
            _make_success_result("art-director", confidence=0.7),
            _make_success_result("art-director", confidence=0.5),
        ]
        expected = ("art-director",)

        assembled = assemble_round_one_packets(results, expected)

        dup_errors = [
            e for e in assembled.errors
            if e.error_category == "duplicate_persona"
        ]
        assert len(dup_errors) == 2  # 2nd and 3rd entries are duplicates
        assert assembled.assembled is True
        assert assembled.opinion_packets[0]["confidence"] == 0.9

    def test_no_duplicates_across_different_personas(self) -> None:
        """Different personas — no duplicates expected."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        dup_errors = [
            e for e in assembled.errors
            if e.error_category == "duplicate_persona"
        ]
        assert len(dup_errors) == 0
        assert assembled.assembled is True


# ═════════════════════════════════════════════════════════════════════════
# 5. Structure re-validation
# ═════════════════════════════════════════════════════════════════════════


class TestStructureRevalidation:
    """Verify the second-pass structure re-validation gate."""

    def test_valid_packets_pass_revalidation(self) -> None:
        """All valid packets should pass re-validation."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is True
        assert not any(
            e.error_category == "structural_validation"
            for e in assembled.errors
        )

    def test_invalid_packet_missing_confidence(self) -> None:
        """A packet missing required 'confidence' field should fail."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"confidence": None},  # type: ignore[dict-item]
        )
        results = [
            invalid,
            _make_success_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert "art-director" in assembled.persona_ids_missing
        assert any(
            e.error_category == "structural_validation"
            and e.persona_id == "art-director"
            for e in assembled.errors
        )
        # tech-director should still be assembled
        assert "tech-director" in assembled.persona_ids_assembled

    def test_invalid_packet_wrong_confidence_type(self) -> None:
        """Confidence field is a string instead of float."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"confidence": "high"},  # type: ignore[dict-item]
        )
        results = [invalid]
        expected = ("art-director",)

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        assert any(
            e.error_category == "structural_validation"
            for e in assembled.errors
        )

    def test_invalid_packet_confidence_out_of_range(self) -> None:
        """Confidence > 1.0 or < 0.0."""
        for bad_conf in (1.5, -0.1, 999.0):
            invalid = _make_invalid_packet_result(
                "art-director",
                packet_overrides={"confidence": bad_conf},
            )
            assembled = assemble_round_one_packets(
                [invalid], ("art-director",)
            )
            assert assembled.assembled is False, (
                f"Confidence {bad_conf} should be rejected"
            )

    def test_invalid_packet_bad_timestamp(self) -> None:
        """Non-ISO-8601 timestamp should fail."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"timestamp": "not-a-timestamp"},
        )
        assembled = assemble_round_one_packets(
            [invalid], ("art-director",)
        )
        assert assembled.assembled is False
        assert any(
            e.error_category == "structural_validation"
            for e in assembled.errors
        )

    def test_invalid_packet_empty_opinion_content(self) -> None:
        """Empty opinion_content should fail."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"opinion_content": ""},
        )
        assembled = assemble_round_one_packets(
            [invalid], ("art-director",)
        )
        assert assembled.assembled is False

    def test_invalid_packet_invalid_persona_id_format(self) -> None:
        """persona_id not in kebab-case should fail."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"persona_id": "NotKebabCase!!"},
        )
        assembled = assemble_round_one_packets(
            [invalid], ("art-director",)
        )
        assert assembled.assembled is False

    def test_none_opinion_packet(self) -> None:
        """Result with success=True but opinion_packet=None."""
        result = _make_none_packet_result("art-director")
        assembled = assemble_round_one_packets(
            [result], ("art-director",)
        )
        assert assembled.assembled is False
        assert any(
            e.error_category == "structural_validation"
            and e.persona_id == "art-director"
            for e in assembled.errors
        )

    def test_structural_error_detail_includes_report(self) -> None:
        """The PacketAssemblyError.detail should carry the validation report."""
        invalid = _make_invalid_packet_result(
            "art-director",
            packet_overrides={"confidence": "bad"},
        )
        assembled = assemble_round_one_packets(
            [invalid], ("art-director",)
        )
        struct_errs = [
            e for e in assembled.errors
            if e.error_category == "structural_validation"
        ]
        assert len(struct_errs) == 1
        assert isinstance(
            struct_errs[0].detail, OpinionPacketValidationReport
        )
        assert not struct_errs[0].detail.passed


# ═════════════════════════════════════════════════════════════════════════
# 6. Strict mode
# ═════════════════════════════════════════════════════════════════════════


class TestStrictMode:
    """Verify strict=True behavior for structural validation failures."""

    def test_strict_mode_rejects_structurally_invalid_packet(self) -> None:
        """In strict mode, even one invalid packet blocks assembly."""
        results = [
            _make_invalid_packet_result(
                "art-director",
                packet_overrides={"confidence": "bad"},
            ),
            _make_success_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(
            results, expected, strict=True,
        )

        assert assembled.assembled is False
        assert any(
            e.error_category == "structural_validation"
            for e in assembled.errors
        )

    def test_strict_mode_all_valid_still_passes(self) -> None:
        """Strict mode with all valid packets should still pass."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
        ]
        expected = ("art-director", "tech-director")

        assembled = assemble_round_one_packets(
            results, expected, strict=True,
        )

        assert assembled.assembled is True

    def test_non_strict_mode_invalid_only_excludes_bad_packets(self) -> None:
        """Non-strict: invalid packet is excluded but valid ones assembled."""
        results = [
            _make_invalid_packet_result(
                "art-director",
                packet_overrides={"confidence": "bad"},
            ),
            _make_success_result("tech-director"),
            _make_success_result("marketing-lead"),
        ]
        expected = ("art-director", "tech-director", "marketing-lead")

        assembled = assemble_round_one_packets(
            results, expected, strict=False,
        )

        assert assembled.assembled is False  # count mismatch due to bad art-director
        assert "art-director" in assembled.persona_ids_missing
        assert assembled.actual_count == 2
        # tech-director and marketing-lead should still be assembled
        assert set(assembled.persona_ids_assembled) == {
            "tech-director", "marketing-lead"
        }


# ═════════════════════════════════════════════════════════════════════════
# 7. Type validation
# ═════════════════════════════════════════════════════════════════════════


class TestTypeValidation:
    """Verify parameter type checks raise appropriate errors."""

    def test_results_not_a_list_raises(self) -> None:
        """results must be a list."""
        with pytest.raises(TypeError, match="results must be a list"):
            assemble_round_one_packets(
                "not-a-list",  # type: ignore[arg-type]
                ("test",),
            )

    def test_expected_role_ids_not_a_tuple_raises(self) -> None:
        """expected_role_ids must be a tuple."""
        with pytest.raises(TypeError, match="expected_role_ids must be a tuple"):
            assemble_round_one_packets(
                [_make_success_result("test")],
                ["test"],  # type: ignore[arg-type]
            )


# ═════════════════════════════════════════════════════════════════════════
# 8. Result property tests
# ═════════════════════════════════════════════════════════════════════════


class TestRoundPacketSetResultProperties:
    """Verify RoundPacketSetResult properties and convenience accessors."""

    def test_assembled_true_implies_zero_errors(self) -> None:
        """When assembled=True, error_count must be 0."""
        results = [_make_success_result("art-director")]
        assembled = assemble_round_one_packets(results, ("art-director",))
        assert assembled.assembled is True
        assert assembled.error_count == 0
        assert assembled.errors == ()

    def test_assembled_false_implies_nonzero_errors(self) -> None:
        """When assembled=False, errors should be present."""
        results = [_make_failure_result("art-director")]
        assembled = assemble_round_one_packets(results, ("art-director",))
        assert assembled.assembled is False
        assert assembled.error_count > 0

    def test_all_personas_present_when_complete(self) -> None:
        """all_personas_present is True when all expected are assembled."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("tech-director"),
        ]
        assembled = assemble_round_one_packets(
            results, ("art-director", "tech-director")
        )
        assert assembled.all_personas_present is True

    def test_all_personas_present_false_when_missing(self) -> None:
        """all_personas_present is False when any persona is missing."""
        results = [_make_success_result("art-director")]
        assembled = assemble_round_one_packets(
            results, ("art-director", "tech-director")
        )
        assert assembled.all_personas_present is False

    def test_opinion_packets_is_tuple(self) -> None:
        """opinion_packets must be an immutable tuple."""
        results = [_make_success_result("art-director")]
        assembled = assemble_round_one_packets(results, ("art-director",))
        assert isinstance(assembled.opinion_packets, tuple)

    def test_result_is_immutable(self) -> None:
        """RoundPacketSetResult must be frozen (immutable)."""
        results = [_make_success_result("art-director")]
        assembled = assemble_round_one_packets(results, ("art-director",))
        with pytest.raises(Exception):
            assembled.assembled = False  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# 9. PacketAssemblyError properties
# ═════════════════════════════════════════════════════════════════════════


class TestPacketAssemblyError:
    """Verify PacketAssemblyError dataclass correctness."""

    def test_construction(self) -> None:
        """Basic construction with all fields."""
        err = PacketAssemblyError(
            error_category="count_mismatch",
            message="Expected 3 but got 2.",
            persona_id="test-role",
            detail={"extra": "data"},
        )
        assert err.error_category == "count_mismatch"
        assert err.message == "Expected 3 but got 2."
        assert err.persona_id == "test-role"
        assert err.detail == {"extra": "data"}

    def test_defaults(self) -> None:
        """persona_id defaults to '' and detail defaults to None."""
        err = PacketAssemblyError(
            error_category="missing_persona",
            message="Missing role.",
        )
        assert err.persona_id == ""
        assert err.detail is None

    def test_immutable(self) -> None:
        """PacketAssemblyError must be frozen."""
        err = PacketAssemblyError(
            error_category="test",
            message="test",
        )
        with pytest.raises(Exception):
            err.message = "changed"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# 10. Internal helper tests
# ═════════════════════════════════════════════════════════════════════════


class TestGroupResultsByPersona:
    """Verify _group_results_by_persona behavior."""

    def test_basic_grouping(self) -> None:
        """Unique role_ids group correctly."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-b"),
        ]
        grouped = _group_results_by_persona(results)
        assert len(grouped) == 2
        assert grouped["role-a"].role_id == "role-a"
        assert grouped["role-b"].role_id == "role-b"

    def test_duplicate_prefer_first_success(self) -> None:
        """First success is kept over second."""
        results = [
            _make_success_result("role-a", confidence=0.99),
            _make_success_result("role-a", confidence=0.50),
        ]
        grouped = _group_results_by_persona(results)
        assert grouped["role-a"].opinion_packet["confidence"] == 0.99  # type: ignore[index]

    def test_duplicate_prefer_success_over_failure(self) -> None:
        """If first fails, second success replaces it."""
        results = [
            _make_failure_result("role-a"),
            _make_success_result("role-a", confidence=0.80),
        ]
        grouped = _group_results_by_persona(results)
        assert grouped["role-a"].success is True
        assert grouped["role-a"].opinion_packet["confidence"] == 0.80  # type: ignore[index]

    def test_duplicate_both_fail(self) -> None:
        """If both fail, first failure is kept."""
        results = [
            _make_failure_result("role-a", error_message="first error"),
            _make_failure_result("role-a", error_message="second error"),
        ]
        grouped = _group_results_by_persona(results)
        assert grouped["role-a"].success is False
        assert grouped["role-a"].error_message == "first error"


class TestDetectDuplicates:
    """Verify _detect_duplicates behavior."""

    def test_no_duplicates(self) -> None:
        """No duplicates — no errors."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-b"),
        ]
        grouped = _group_results_by_persona(results)
        errors = _detect_duplicates(results, grouped)
        assert len(errors) == 0

    def test_one_duplicate(self) -> None:
        """One duplicate detected."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-a"),
        ]
        grouped = _group_results_by_persona(results)
        errors = _detect_duplicates(results, grouped)
        assert len(errors) == 1
        assert errors[0].error_category == "duplicate_persona"
        assert errors[0].persona_id == "role-a"

    def test_multiple_duplicates(self) -> None:
        """Multiple duplicates across different personas."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-a"),
            _make_success_result("role-b"),
            _make_success_result("role-b"),
            _make_success_result("role-b"),
        ]
        grouped = _group_results_by_persona(results)
        errors = _detect_duplicates(results, grouped)
        # role-a: 1 duplicate, role-b: 2 duplicates = 3 total
        assert len(errors) == 3


class TestVerifyCount:
    """Verify _verify_count behavior."""

    def test_all_present(self) -> None:
        """All expected personas present and successful."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-b"),
        ]
        expected = ("role-a", "role-b")
        grouped = _group_results_by_persona(results)
        errors = _verify_count(results, expected, grouped)
        assert len(errors) == 0

    def test_missing_persona(self) -> None:
        """One expected persona completely missing."""
        results = [_make_success_result("role-a")]
        expected = ("role-a", "role-b")
        grouped = _group_results_by_persona(results)
        errors = _verify_count(results, expected, grouped)
        assert any(
            e.error_category == "missing_persona"
            and e.persona_id == "role-b"
            for e in errors
        )
        assert any(
            e.error_category == "count_mismatch"
            for e in errors
        )

    def test_extra_persona(self) -> None:
        """Extra persona not in expected list."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-c"),
        ]
        expected = ("role-a",)
        grouped = _group_results_by_persona(results)
        errors = _verify_count(results, expected, grouped)
        assert any(
            e.error_category == "extra_persona"
            and e.persona_id == "role-c"
            for e in errors
        )


class TestRevalidatePackets:
    """Verify _revalidate_packets behavior."""

    def test_all_valid(self) -> None:
        """All packets valid — no errors."""
        results = [
            _make_success_result("role-a"),
            _make_success_result("role-b"),
        ]
        grouped = _group_results_by_persona(results)
        errors = _revalidate_packets(grouped)
        assert len(errors) == 0

    def test_one_invalid(self) -> None:
        """One invalid packet detected in re-validation."""
        results = [
            _make_invalid_packet_result(
                "role-a",
                packet_overrides={"confidence": "bad"},
            ),
            _make_success_result("role-b"),
        ]
        grouped = _group_results_by_persona(results)
        errors = _revalidate_packets(grouped)
        assert len(errors) == 1
        assert errors[0].error_category == "structural_validation"
        assert errors[0].persona_id == "role-a"

    def test_none_packet(self) -> None:
        """opinion_packet is None but success=True."""
        results = [_make_none_packet_result("role-a")]
        grouped = _group_results_by_persona(results)
        errors = _revalidate_packets(grouped)
        assert len(errors) == 1
        assert errors[0].error_category == "structural_validation"


# ═════════════════════════════════════════════════════════════════════════
# 11. Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case scenarios for the assembler."""

    def test_large_confidence_values_accepted(self) -> None:
        """Confidence of exactly 0.0 and 1.0 should be valid."""
        for conf in (0.0, 1.0):
            results = [_make_success_result("role-a", confidence=conf)]
            assembled = assemble_round_one_packets(
                results, ("role-a",)
            )
            assert assembled.assembled is True, (
                f"Confidence {conf} should be accepted"
            )

    def test_unicode_opinion_content(self) -> None:
        """Korean and emoji content in opinion should be preserved."""
        korean_content = "캐릭터 디자인은 네온 느와르 스타일로 진행합시다 🎨✨"
        results = [
            _make_success_result("art-director", opinion_content=korean_content),
        ]
        assembled = assemble_round_one_packets(
            results, ("art-director",)
        )
        assert assembled.assembled is True
        assert assembled.opinion_packets[0]["opinion_content"] == korean_content

    def test_timestamp_with_timezone_offset(self) -> None:
        """ISO-8601 timestamps with +09:00 offset should be valid."""
        results = [
            _make_success_result(
                "role-a",
                timestamp="2026-06-10T14:30:00+09:00",
            ),
        ]
        assembled = assemble_round_one_packets(results, ("role-a",))
        assert assembled.assembled is True

    def test_timestamp_with_milliseconds(self) -> None:
        """ISO-8601 timestamps with milliseconds should be valid."""
        results = [
            _make_success_result(
                "role-a",
                timestamp="2026-06-10T14:30:00.123Z",
            ),
        ]
        assembled = assemble_round_one_packets(results, ("role-a",))
        assert assembled.assembled is True

    def test_many_errors_collected(self) -> None:
        """Assembler should collect all errors, not early-exit."""
        results = [
            _make_success_result("art-director"),
            _make_success_result("art-director"),  # duplicate
            _make_failure_result("tech-director"),  # failed
            _make_invalid_packet_result(
                "marketing-lead",
                packet_overrides={"confidence": "bad"},  # invalid
            ),
            # "scriptwriter": completely missing
        ]
        expected = (
            "art-director",
            "tech-director",
            "marketing-lead",
            "scriptwriter",
        )

        assembled = assemble_round_one_packets(results, expected)

        assert assembled.assembled is False
        # Should have: duplicate (1), failed (1), missing (1),
        # structural (1), count_mismatch (1) = at least 5 errors
        assert assembled.error_count >= 4
        categories = {e.error_category for e in assembled.errors}
        assert "duplicate_persona" in categories
        assert "generation_failure" in categories
        assert "missing_persona" in categories
        assert "structural_validation" in categories
        assert "count_mismatch" in categories
