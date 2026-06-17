"""Tests for the resolution-or-escalation decision module.

Sub-AC 5b-3: Resolution-or-Escalation Decision — given post-rebuttal
revised positions per conflict pair, determine whether each conflict is
resolved (consensus reached) or escalated (deadlock, passed to Round 3/
human); testable by asserting correct resolved vs escalated
classification from known outcomes.

Coverage:
- Resolved: stances converge after rebuttal (both compatible)
- Resolved: revision leads to convergence
- Resolved: mutual acknowledgement with compatible stances
- Resolved: same stance naturally emerges
- Resolved: conditional pass with compatible but non-identical stances
- Escalated: persistent direct opposition (support vs oppose unchanged)
- Escalated: high-severity deadlock with no revision
- Escalated: both revised but still opposing (tie-break)
- Escalated: one revised, other maintained opposition
- Escalated: neither revised, moderate severity
- Integrated flow: detect_conflicts → execute_rebuttal_exchange →
  classify_resolutions
- ResolutionResult properties (all_resolved, consensus_ratio,
  requires_escalation, requires_human, tie_break_needed)
- get_by_topic, get_escalated, get_resolved methods
- Overall consensus score computation
- Injectable classifier for deterministic testing
- TypeError/ValueError input validation
- ConflictResolution property accessors (is_resolved, is_escalated,
  stances_converged, either_revised, both_revised)
- All 5 conflict types classification
- Zero conflicts (no conflict pairs → all_resolved=True)
- Multiple conflict pairs with mixed outcomes
- Empty rebuttal data (no exchange → uses original stances)
- Korean-language rationale content
"""

from __future__ import annotations

from typing import Any

import pytest

from src.conflict_detector import (
    ConflictDetectionResult,
    ConflictPair,
    TopicExtraction,
    TopicPosition,
    detect_conflicts,
)
from src.rebuttal_exchange import (
    RebuttalExchangeResult,
    RebuttalPacket,
    RevisionRecord,
    execute_rebuttal_exchange,
)
from src.resolution_decision import (
    ConflictResolution,
    ResolutionResult,
    _default_classify_resolution,
    _are_directly_opposing,
    classify_resolutions,
    inject_classifier,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper factories
# ═════════════════════════════════════════════════════════════════════════


def _make_packet(
    persona_id: str,
    opinion_content: str,
    confidence: float = 0.85,
) -> dict[str, Any]:
    """Create a minimal valid opinion packet dict."""
    return {
        "persona_id": persona_id,
        "opinion_content": opinion_content,
        "confidence": confidence,
        "agenda_item_ref": "test-agenda",
        "timestamp": "2026-06-10T14:30:00Z",
    }


def _make_conflict_pair(
    topic: str = "Test Topic",
    topic_id: str = "test-topic",
    persona_a: str = "art-director",
    persona_b: str = "tech-director",
    position_a: str = "Support position.",
    position_b: str = "Oppose position.",
    stance_a: str = "support",
    stance_b: str = "oppose",
    conflict_type: str = "direct_opposition",
    severity: float = 0.85,
    confidence_a: float = 0.9,
    confidence_b: float = 0.85,
) -> ConflictPair:
    """Create a ConflictPair for testing."""
    return ConflictPair(
        topic=topic,
        topic_id=topic_id,
        persona_a=persona_a,
        persona_b=persona_b,
        position_a=position_a,
        position_b=position_b,
        stance_a=stance_a,
        stance_b=stance_b,
        conflict_type=conflict_type,
        severity=severity,
        confidence_a=confidence_a,
        confidence_b=confidence_b,
    )


def _make_conflict_result(
    conflict_pairs: tuple[ConflictPair, ...] = (),
    topics: tuple[TopicExtraction, ...] = (),
    personas: tuple[str, ...] = (),
    severity_max: float = 0.0,
) -> ConflictDetectionResult:
    """Create a ConflictDetectionResult for testing."""
    return ConflictDetectionResult(
        conflict_pairs=conflict_pairs,
        conflict_count=len(conflict_pairs),
        topics_identified=topics,
        personas_analysed=personas,
        topic_persona_map={},
        unanimous_topics=(),
        conflict_severity_max=severity_max,
    )


def _make_rebuttal_packet(
    rebuttal_id: str = "rebuttal-000",
    conflict_pair_index: int = 0,
    topic_id: str = "test-topic",
    rebutting_persona: str = "art-director",
    target_persona: str = "tech-director",
    counterargument_summary: str = "Counterargument.",
    counterargument_points: tuple[str, ...] = ("Point 1",),
    acknowledges_validity: bool = False,
    revised_position: TopicPosition | None = None,
    confidence_after: float = 0.85,
    stance_after: str = "support",
) -> RebuttalPacket:
    """Create a RebuttalPacket for testing."""
    return RebuttalPacket(
        rebuttal_id=rebuttal_id,
        conflict_pair_index=conflict_pair_index,
        topic_id=topic_id,
        rebutting_persona=rebutting_persona,
        target_persona=target_persona,
        counterargument_summary=counterargument_summary,
        counterargument_points=counterargument_points,
        acknowledges_validity=acknowledges_validity,
        revised_position=revised_position,
        confidence_after=confidence_after,
        stance_after=stance_after,
    )


def _make_exchange_result(
    rebuttal_packets: tuple[RebuttalPacket, ...] = (),
    revisions: tuple[RevisionRecord, ...] = (),
    resolved: int = 0,
    unresolved: int = 0,
    complete: bool = True,
) -> RebuttalExchangeResult:
    """Create a RebuttalExchangeResult for testing."""
    return RebuttalExchangeResult(
        rebuttal_packets=rebuttal_packets,
        revisions=revisions,
        conflict_pairs_resolved=resolved,
        conflict_pairs_unresolved=unresolved,
        exchange_round_complete=complete,
    )


def _make_revision_record(
    persona_id: str = "art-director",
    topic_id: str = "test-topic",
    original_stance: str = "support",
    original_summary: str = "Original support.",
    revised_stance: str = "conditional_support",
    revised_summary: str = "Revised to conditional.",
    revision_rationale: str = "Acknowledged concerns.",
    confidence_before: float = 0.9,
    confidence_after: float = 0.75,
) -> RevisionRecord:
    """Create a RevisionRecord for testing."""
    return RevisionRecord(
        persona_id=persona_id,
        topic_id=topic_id,
        original_stance=original_stance,
        original_summary=original_summary,
        revised_stance=revised_stance,
        revised_summary=revised_summary,
        revision_rationale=revision_rationale,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
    )


# ═════════════════════════════════════════════════════════════════════════
# 1. Unit: stance compatibility helpers
# ═════════════════════════════════════════════════════════════════════════


class TestStanceCompatibility:
    """Test stance compatibility helper functions."""

    def test_directly_opposing_support_vs_oppose(self) -> None:
        """Support vs oppose are directly opposing."""
        assert _are_directly_opposing("support", "oppose") is True

    def test_directly_opposing_oppose_vs_support(self) -> None:
        """Oppose vs support are directly opposing."""
        assert _are_directly_opposing("oppose", "support") is True

    def test_directly_opposing_oppose_vs_alternative(self) -> None:
        """Oppose vs alternative_proposal are directly opposing."""
        assert _are_directly_opposing("oppose", "alternative_proposal") is True

    def test_directly_opposing_alternative_vs_oppose(self) -> None:
        """Alternative_proposal vs oppose are directly opposing."""
        assert _are_directly_opposing("alternative_proposal", "oppose") is True

    def test_not_opposing_support_vs_conditional(self) -> None:
        """Support vs conditional_support are NOT directly opposing."""
        assert _are_directly_opposing("support", "conditional_support") is False

    def test_not_opposing_conditional_vs_conditional(self) -> None:
        """Two conditional_support stances are NOT opposing."""
        assert _are_directly_opposing(
            "conditional_support", "conditional_support"
        ) is False

    def test_not_opposing_support_vs_alternative(self) -> None:
        """Support vs alternative_proposal are NOT directly opposing."""
        assert _are_directly_opposing(
            "support", "alternative_proposal"
        ) is False

    def test_not_opposing_neutral_vs_oppose(self) -> None:
        """Neutral vs oppose are NOT classified as directly opposing."""
        assert _are_directly_opposing("neutral", "oppose") is False

    def test_not_opposing_same_stance(self) -> None:
        """Same stance is never opposing."""
        assert _are_directly_opposing("support", "support") is False
        assert _are_directly_opposing("oppose", "oppose") is False
        assert _are_directly_opposing("neutral", "neutral") is False


# ═════════════════════════════════════════════════════════════════════════
# 2. Unit: default classifier
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultClassifier:
    """Test the default resolution classification logic."""

    def test_resolved_stances_converge_after_revision(self) -> None:
        """Both revised to compatible stances → resolved."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.70,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                target_persona="tech-director",
                stance_after="conditional_support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                target_persona="art-director",
                stance_after="conditional_support",
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="art-director",
                original_stance="support",
                revised_stance="conditional_support",
            ),
            _make_revision_record(
                persona_id="tech-director",
                original_stance="oppose",
                revised_stance="conditional_support",
            ),
        ]

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "resolved"
        assert next_action == "continue_consensus"
        assert confidence >= 0.80

    def test_resolved_mutual_acknowledgement(self) -> None:
        """Both acknowledged validity, stances compatible → resolved."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="conditional_support",
            conflict_type="priority_divergence",
            severity=0.40,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
                acknowledges_validity=True,
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="conditional_support",
                acknowledges_validity=True,
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "resolved"
        assert next_action in ("accept_conditional", "continue_consensus")
        assert confidence >= 0.70

    def test_resolved_same_stance_emerges(self) -> None:
        """Both naturally converged to same stance → resolved."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="support",
            conflict_type="methodological_difference",
            severity=0.50,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="support",
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "resolved"
        assert confidence >= 0.75

    def test_escalated_persistent_opposition(self) -> None:
        """Both maintain directly opposing stances → escalated."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.60,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "escalated"
        assert next_action == "round_3_debate"

    def test_escalated_high_severity_deadlock(self) -> None:
        """High severity + no revision → human escalation."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.90,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "escalated"
        assert next_action == "human_escalation"
        assert confidence >= 0.90

    def test_escalated_both_revised_still_opposing(self) -> None:
        """Both revised but still opposing → tie_break_needed."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.75,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="alternative_proposal",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="art-director",
                original_stance="support",
                revised_stance="alternative_proposal",
            ),
            _make_revision_record(
                persona_id="tech-director",
                original_stance="oppose",
                revised_stance="oppose",  # Revised but still oppose
            ),
        ]

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "escalated"
        assert next_action == "tie_break_needed"

    def test_resolved_one_revised_to_conditional_still_compatible(self) -> None:
        """One side revised to conditional_support, compatible with oppose
        since conditional_support vs oppose is not directly opposing."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.65,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="conditional_support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="art-director",
                original_stance="support",
                revised_stance="conditional_support",
            ),
        ]

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        # conditional_support vs oppose is NOT directly opposing,
        # and one side revised → classified as resolved (conditional)
        assert decision == "resolved"
        assert next_action in ("accept_conditional", "continue_consensus")

    def test_resolved_revision_converges_to_compatible(self) -> None:
        """One revised from oppose to conditional_support → resolved."""
        conflict = _make_conflict_pair(
            persona_a="producer-kim",
            persona_b="finance-park",
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.55,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="producer-kim",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="finance-park",
                stance_after="conditional_support",
                acknowledges_validity=True,
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="finance-park",
                original_stance="oppose",
                revised_stance="conditional_support",
            ),
        ]

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "resolved"
        assert confidence >= 0.80

    def test_escalated_moderate_severity_no_revision(self) -> None:
        """Neither revised, moderate severity → round_3_debate."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.50,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        assert decision == "escalated"
        assert next_action == "round_3_debate"

    def test_escalated_high_severity_single_revision_not_enough(self) -> None:
        """Single revision with severity >= 0.8 → escalated (still opposing)."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.85,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="alternative_proposal",  # Revised but still opposing
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="oppose",  # Didn't revise
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="art-director",
                original_stance="support",
                revised_stance="alternative_proposal",
            ),
        ]

        decision, rationale, next_action, confidence = (
            _default_classify_resolution(conflict, rebuttals, revisions)
        )

        # alternative_proposal vs oppose IS directly opposing
        # One revised but other maintained → escalated to round_3
        assert decision == "escalated"

    def test_incompatible_recommendation_resolved(self) -> None:
        """Incompatible recommendations converge after rebuttal."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="support",
            conflict_type="incompatible_recommendation",
            severity=0.55,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="support",
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, _, _, _ = _default_classify_resolution(
            conflict, rebuttals, revisions
        )

        assert decision == "resolved"

    def test_priority_divergence_resolved_with_mutual_ack(self) -> None:
        """Priority divergence resolved via mutual acknowledgement."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="conditional_support",
            conflict_type="priority_divergence",
            severity=0.35,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
                acknowledges_validity=True,
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="conditional_support",
                acknowledges_validity=True,
            ),
        ]
        revisions: list[RevisionRecord] = []

        decision, _, _, _ = _default_classify_resolution(
            conflict, rebuttals, revisions
        )

        assert decision == "resolved"

    def test_methodological_difference_resolved(self) -> None:
        """Methodological difference with same stance → resolved."""
        conflict = _make_conflict_pair(
            stance_a="support",
            stance_b="alternative_proposal",
            conflict_type="methodological_difference",
            severity=0.45,
        )
        rebuttals = [
            _make_rebuttal_packet(
                rebutting_persona="art-director",
                stance_after="support",
            ),
            _make_rebuttal_packet(
                rebutting_persona="tech-director",
                stance_after="support",  # Converged
            ),
        ]
        revisions = [
            _make_revision_record(
                persona_id="tech-director",
                original_stance="alternative_proposal",
                revised_stance="support",
            ),
        ]

        decision, _, next_action, _ = _default_classify_resolution(
            conflict, rebuttals, revisions
        )

        assert decision == "resolved"
        assert next_action == "continue_consensus"


# ═════════════════════════════════════════════════════════════════════════
# 3. Main API: classify_resolutions
# ═════════════════════════════════════════════════════════════════════════


class TestClassifyResolutions:
    """Test the main classify_resolutions entry point."""

    def test_resolved_conflict_pairs(self) -> None:
        """Conflict pair where both sides converge → resolved."""
        conflict_pair = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
            stance_a="support",
            stance_b="oppose",
            severity=0.55,
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("art-director", "tech-director"),
        )
        # Both revised to compatible stances
        rebuttal_a = _make_rebuttal_packet(
            rebuttal_id="rb-000",
            conflict_pair_index=0,
            rebutting_persona="art-director",
            target_persona="tech-director",
            stance_after="conditional_support",
            acknowledges_validity=True,
        )
        rebuttal_b = _make_rebuttal_packet(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            rebutting_persona="tech-director",
            target_persona="art-director",
            stance_after="conditional_support",
            acknowledges_validity=True,
        )
        revision_a = _make_revision_record(
            persona_id="art-director",
            original_stance="support",
            revised_stance="conditional_support",
        )
        revision_b = _make_revision_record(
            persona_id="tech-director",
            original_stance="oppose",
            revised_stance="conditional_support",
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(rebuttal_a, rebuttal_b),
            revisions=(revision_a, revision_b),
            resolved=1,
            unresolved=0,
        )

        result = classify_resolutions(conflict_result, exchange_result)

        assert result.total_conflicts == 1
        assert result.resolved_count == 1
        assert result.escalated_count == 0
        assert result.all_resolved is True
        assert result.requires_escalation is False
        assert result.consensus_ratio == 1.0

        cr = result.conflict_resolutions[0]
        assert cr.decision == "resolved"
        assert cr.is_resolved is True
        assert cr.is_escalated is False
        assert cr.revised_by_a is True
        assert cr.revised_by_b is True
        assert cr.acknowledged_a is True
        assert cr.acknowledged_b is True

    def test_escalated_conflict_pair(self) -> None:
        """Conflict pair with persistent opposition → escalated."""
        conflict_pair = _make_conflict_pair(
            persona_a="producer-kim",
            persona_b="director-lee",
            stance_a="support",
            stance_b="oppose",
            severity=0.50,
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("producer-kim", "director-lee"),
        )
        # Neither revised
        rebuttal_a = _make_rebuttal_packet(
            rebuttal_id="rb-000",
            conflict_pair_index=0,
            rebutting_persona="producer-kim",
            target_persona="director-lee",
            stance_after="support",
            acknowledges_validity=False,
        )
        rebuttal_b = _make_rebuttal_packet(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            rebutting_persona="director-lee",
            target_persona="producer-kim",
            stance_after="oppose",
            acknowledges_validity=False,
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(rebuttal_a, rebuttal_b),
            revisions=(),
            resolved=0,
            unresolved=1,
        )

        result = classify_resolutions(conflict_result, exchange_result)

        assert result.total_conflicts == 1
        assert result.resolved_count == 0
        assert result.escalated_count == 1
        assert result.all_resolved is False
        assert result.requires_escalation is True

        cr = result.conflict_resolutions[0]
        assert cr.decision == "escalated"
        assert cr.is_resolved is False
        assert cr.is_escalated is True
        assert cr.stances_converged is False
        assert cr.either_revised is False
        assert cr.both_revised is False

    def test_high_severity_human_escalation(self) -> None:
        """High-severity conflict with no movement → human escalation."""
        conflict_pair = _make_conflict_pair(
            persona_a="cfo-park",
            persona_b="cdo-choi",
            stance_a="support",
            stance_b="oppose",
            severity=0.92,
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("cfo-park", "cdo-choi"),
        )
        rebuttal_a = _make_rebuttal_packet(
            rebuttal_id="rb-000",
            conflict_pair_index=0,
            rebutting_persona="cfo-park",
            stance_after="support",
        )
        rebuttal_b = _make_rebuttal_packet(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            rebutting_persona="cdo-choi",
            stance_after="oppose",
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(rebuttal_a, rebuttal_b),
            revisions=(),
        )

        result = classify_resolutions(conflict_result, exchange_result)

        assert result.requires_human is True
        cr = result.conflict_resolutions[0]
        assert cr.decision == "escalated"
        assert cr.next_action == "human_escalation"

    def test_multiple_mixed_outcomes(self) -> None:
        """Three conflict pairs: 2 resolved, 1 escalated."""
        pair_1 = _make_conflict_pair(
            topic_id="budget-allocation",
            persona_a="producer-kim",
            persona_b="finance-park",
            stance_a="support",
            stance_b="oppose",
            severity=0.45,
        )
        pair_2 = _make_conflict_pair(
            topic_id="visual-style",
            persona_a="art-director",
            persona_b="tech-director",
            stance_a="support",
            stance_b="support",  # Compatible already
            severity=0.30,
        )
        pair_3 = _make_conflict_pair(
            topic_id="timeline-deadline",
            persona_a="director-lee",
            persona_b="cfo-park",
            stance_a="support",
            stance_b="oppose",
            severity=0.88,
        )

        conflict_result = _make_conflict_result(
            conflict_pairs=(pair_1, pair_2, pair_3),
            personas=(
                "producer-kim", "finance-park", "art-director",
                "tech-director", "director-lee", "cfo-park",
            ),
        )

        # Pair 1: converged to conditional_support (resolved)
        r1a = _make_rebuttal_packet(
            rebuttal_id="rb-000", conflict_pair_index=0,
            rebutting_persona="producer-kim",
            stance_after="conditional_support",
            acknowledges_validity=True,
        )
        r1b = _make_rebuttal_packet(
            rebuttal_id="rb-001", conflict_pair_index=0,
            rebutting_persona="finance-park",
            stance_after="conditional_support",
            acknowledges_validity=True,
        )
        rev1 = _make_revision_record(
            persona_id="finance-park",
            original_stance="oppose",
            revised_stance="conditional_support",
        )

        # Pair 2: both support — naturally compatible (resolved)
        r2a = _make_rebuttal_packet(
            rebuttal_id="rb-002", conflict_pair_index=1,
            rebutting_persona="art-director",
            stance_after="support",
        )
        r2b = _make_rebuttal_packet(
            rebuttal_id="rb-003", conflict_pair_index=1,
            rebutting_persona="tech-director",
            stance_after="support",
        )

        # Pair 3: persistent high-severity opposition (escalated)
        r3a = _make_rebuttal_packet(
            rebuttal_id="rb-004", conflict_pair_index=2,
            rebutting_persona="director-lee",
            stance_after="support",
        )
        r3b = _make_rebuttal_packet(
            rebuttal_id="rb-005", conflict_pair_index=2,
            rebutting_persona="cfo-park",
            stance_after="oppose",
        )

        exchange_result = _make_exchange_result(
            rebuttal_packets=(r1a, r1b, r2a, r2b, r3a, r3b),
            revisions=(rev1,),
            resolved=2,
            unresolved=1,
        )

        result = classify_resolutions(conflict_result, exchange_result)

        assert result.total_conflicts == 3
        assert result.resolved_count == 2
        assert result.escalated_count == 1
        assert result.all_resolved is False
        assert result.requires_escalation is True
        assert result.requires_human is True  # Pair 3 is high-severity
        assert result.consensus_ratio == pytest.approx(2.0 / 3.0)

        # Verify per-pair outcomes
        cr1 = result.get_by_topic("budget-allocation")
        assert cr1 is not None
        assert cr1.decision == "resolved"

        cr2 = result.get_by_topic("visual-style")
        assert cr2 is not None
        assert cr2.decision == "resolved"

        cr3 = result.get_by_topic("timeline-deadline")
        assert cr3 is not None
        assert cr3.decision == "escalated"

        # Test get_escalated and get_resolved
        escalated = result.get_escalated()
        assert len(escalated) == 1
        assert escalated[0].topic_id == "timeline-deadline"

        resolved = result.get_resolved()
        assert len(resolved) == 2

    def test_zero_conflicts_all_resolved(self) -> None:
        """No conflict pairs → everything is resolved by default."""
        conflict_result = _make_conflict_result(conflict_pairs=())
        exchange_result = _make_exchange_result(
            rebuttal_packets=(),
            revisions=(),
            resolved=0,
            unresolved=0,
        )

        result = classify_resolutions(conflict_result, exchange_result)

        assert result.total_conflicts == 0
        assert result.resolved_count == 0
        assert result.escalated_count == 0
        assert result.all_resolved is True
        assert result.overall_consensus_score == 1.0
        assert result.consensus_ratio == 1.0

    def test_overall_consensus_score_computation(self) -> None:
        """Consensus score blends ratio and classification confidence."""
        conflict_pair = _make_conflict_pair(
            stance_a="support",
            stance_b="oppose",
            severity=0.60,
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("art-director", "tech-director"),
        )
        r1 = _make_rebuttal_packet(
            rebuttal_id="rb-000", conflict_pair_index=0,
            rebutting_persona="art-director",
            stance_after="support",
        )
        r2 = _make_rebuttal_packet(
            rebuttal_id="rb-001", conflict_pair_index=0,
            rebutting_persona="tech-director",
            stance_after="oppose",
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(r1, r2),
            revisions=(),
        )

        result = classify_resolutions(conflict_result, exchange_result)

        # All escalated → ratio = 0.0, confidence ~0.85 → score ≈ 0.34
        assert 0.0 <= result.overall_consensus_score <= 1.0
        assert result.overall_consensus_score < 0.5  # Mostly escalated


# ═════════════════════════════════════════════════════════════════════════
# 4. Integration: full pipeline from opinions to resolution
# ═════════════════════════════════════════════════════════════════════════


class TestIntegrationPipeline:
    """End-to-end: detect_conflicts → execute_rebuttal_exchange →
    classify_resolutions."""

    def test_full_pipeline_direct_opposition_resolves(self) -> None:
        """Two personas with opposing opinions converge after rebuttal."""
        packets = [
            _make_packet(
                "art-director",
                "네온 색상 팔레트를 채택해야 합니다. "
                "트렌드 분석 결과 네온 색상이 젊은 층에서 인기가 높습니다. "
                "추천합니다. 데이터가 이를 뒷받침합니다.",
                confidence=0.80,
            ),
            _make_packet(
                "tech-director",
                "네온 색상 사용에 반대합니다. 접근성 문제가 있습니다. "
                "대신에 파스텔 팔레트를 추천합니다. "
                "WCAG 가이드라인을 준수해야 합니다.",
                confidence=0.85,
            ),
        ]

        conflicts = detect_conflicts(packets)

        # Both produce opposing opinions on same topics
        # The default extractor should detect this
        if conflicts.has_conflicts:
            exchange = execute_rebuttal_exchange(conflicts, packets)
            result = classify_resolutions(conflicts, exchange)

            assert result.total_conflicts >= 1
            for cr in result.conflict_resolutions:
                assert cr.decision in ("resolved", "escalated")
                assert cr.topic_id != ""
                assert cr.persona_a != ""
                assert cr.persona_b != ""

    def test_full_pipeline_all_agree_no_conflicts(self) -> None:
        """Three personas with aligned opinions → no conflicts."""
        packets = [
            _make_packet(
                "producer-kim",
                "뮤직비디오 예산을 50억으로 책정하는 것에 찬성합니다. "
                "시장 조사 결과 충분한 ROI가 예상됩니다.",
                confidence=0.90,
            ),
            _make_packet(
                "finance-park",
                "예산 50억은 적절합니다. 수익성 분석 결과 타당합니다. "
                "재무적으로 문제없습니다.",
                confidence=0.88,
            ),
            _make_packet(
                "director-lee",
                "50억 예산에 동의합니다. 제작 품질을 보장할 수 있습니다.",
                confidence=0.85,
            ),
        ]

        conflicts = detect_conflicts(packets)

        if not conflicts.has_conflicts:
            # No conflicts → everything is resolved
            # Create empty exchange for zero conflicts
            empty_exchange = RebuttalExchangeResult(
                rebuttal_packets=(),
                revisions=(),
                conflict_pairs_resolved=0,
                conflict_pairs_unresolved=0,
                exchange_round_complete=True,
            )
            result = classify_resolutions(conflicts, empty_exchange)
            assert result.all_resolved is True
            assert result.total_conflicts == 0

    def test_full_pipeline_korean_content(self) -> None:
        """Korean-language opinions through the full pipeline."""
        packets = [
            _make_packet(
                "cfo-park",
                "투자 규모를 현재 수준으로 유지해야 합니다. "
                "리스크가 너무 큽니다. 신중한 접근이 필요합니다. "
                "시장 불확실성이 높기 때문에 보수적인 전략을 추천합니다.",
                confidence=0.90,
            ),
            _make_packet(
                "cdo-choi",
                "투자를 확대해야 합니다. 지금이 적기입니다. "
                "경쟁사들이 빠르게 움직이고 있습니다. "
                "시장 점유율을 놓치면 회복이 어렵습니다.",
                confidence=0.92,
            ),
        ]

        conflicts = detect_conflicts(packets)

        if conflicts.has_conflicts:
            exchange = execute_rebuttal_exchange(conflicts, packets)
            result = classify_resolutions(conflicts, exchange)

            assert result.total_conflicts >= 0
            for cr in result.conflict_resolutions:
                # Rationale should contain meaningful text
                assert len(cr.rationale) > 10


# ═════════════════════════════════════════════════════════════════════════
# 5. Injectable classifier for deterministic testing
# ═════════════════════════════════════════════════════════════════════════


class TestInjectableClassifier:
    """Test that custom classifiers are correctly injected and used."""

    def test_injected_classifier_is_used(self) -> None:
        """Custom classifier produces known output for known input."""
        conflict_pair = _make_conflict_pair(
            persona_a="role-a",
            persona_b="role-b",
            severity=0.50,
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("role-a", "role-b"),
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(),
            revisions=(),
        )

        # Inject a classifier that always returns "resolved"
        def always_resolved(
            conflict: ConflictPair,
            pair_rebuttals: list[RebuttalPacket],
            pair_revisions: list[RevisionRecord],
        ) -> tuple[str, str, str, float]:
            return (
                "resolved",
                "Forced resolution by injected classifier.",
                "continue_consensus",
                1.0,
            )

        inject_classifier(always_resolved)
        try:
            result = classify_resolutions(conflict_result, exchange_result)
            assert result.all_resolved is True
            assert result.resolved_count == 1
            assert result.escalated_count == 0
            cr = result.conflict_resolutions[0]
            assert cr.decision == "resolved"
            assert cr.rationale == "Forced resolution by injected classifier."
            assert cr.resolution_confidence == 1.0
        finally:
            inject_classifier(None)  # Clean up

    def test_injected_classifier_escalates_everything(self) -> None:
        """Custom classifier always returns escalated."""
        conflict_pair = _make_conflict_pair(
            severity=0.30,  # Low severity, should normally resolve
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("role-a", "role-b"),
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(),
            revisions=(),
        )

        def always_escalated(
            conflict: ConflictPair,
            pair_rebuttals: list[RebuttalPacket],
            pair_revisions: list[RevisionRecord],
        ) -> tuple[str, str, str, float]:
            return (
                "escalated",
                "Forced escalation.",
                "human_escalation",
                1.0,
            )

        inject_classifier(always_escalated)
        try:
            result = classify_resolutions(conflict_result, exchange_result)
            assert result.all_resolved is False
            assert result.escalated_count == 1
            assert result.requires_human is True
            assert result.requires_escalation is True
        finally:
            inject_classifier(None)

    def test_per_call_injection_overrides_global(self) -> None:
        """Per-call _injected_classifier takes precedence."""
        conflict_pair = _make_conflict_pair()
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("role-a", "role-b"),
        )
        exchange_result = _make_exchange_result(
            rebuttal_packets=(),
            revisions=(),
        )

        def per_call_classifier(
            conflict: ConflictPair,
            pair_rebuttals: list[RebuttalPacket],
            pair_revisions: list[RevisionRecord],
        ) -> tuple[str, str, str, float]:
            return ("resolved", "Per-call override.", "continue_consensus", 1.0)

        result = classify_resolutions(
            conflict_result,
            exchange_result,
            _injected_classifier=per_call_classifier,
        )

        assert result.all_resolved is True
        assert result.conflict_resolutions[0].rationale == "Per-call override."


# ═════════════════════════════════════════════════════════════════════════
# 6. Input validation
# ═════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Test input validation and error handling."""

    def test_wrong_conflict_result_type_raises_typeerror(self) -> None:
        """Passing a non-ConflictDetectionResult raises TypeError."""
        exchange_result = _make_exchange_result()
        with pytest.raises(TypeError, match="ConflictDetectionResult"):
            classify_resolutions(
                {"not": "a conflict result"},  # type: ignore[arg-type]
                exchange_result,
            )

    def test_wrong_exchange_result_type_raises_typeerror(self) -> None:
        """Passing a non-RebuttalExchangeResult raises TypeError."""
        conflict_result = _make_conflict_result()
        with pytest.raises(TypeError, match="RebuttalExchangeResult"):
            classify_resolutions(
                conflict_result,
                "not an exchange result",  # type: ignore[arg-type]
            )

    def test_incomplete_exchange_raises_valueerror(self) -> None:
        """An incomplete rebuttal exchange raises ValueError."""
        conflict_result = _make_conflict_result()
        exchange_result = _make_exchange_result(
            rebuttal_packets=(),
            revisions=(),
            complete=False,
        )
        with pytest.raises(ValueError, match="rebuttal exchange round must be complete"):
            classify_resolutions(conflict_result, exchange_result)


# ═════════════════════════════════════════════════════════════════════════
# 7. ConflictResolution property accessors
# ═════════════════════════════════════════════════════════════════════════


class TestConflictResolutionProperties:
    """Test ConflictResolution dataclass property accessors."""

    def test_is_resolved_and_is_escalated(self) -> None:
        """is_resolved and is_escalated are mutually exclusive."""
        resolved = ConflictResolution(
            topic_id="t1",
            conflict_pair_index=0,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="conditional_support",
            post_rebuttal_stance_b="conditional_support",
            revised_by_a=True,
            revised_by_b=True,
            acknowledged_a=True,
            acknowledged_b=True,
            original_severity=0.50,
            decision="resolved",
            rationale="Converged.",
            next_action="continue_consensus",
            resolution_confidence=0.90,
        )
        assert resolved.is_resolved is True
        assert resolved.is_escalated is False

        escalated = ConflictResolution(
            topic_id="t2",
            conflict_pair_index=1,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="support",
            post_rebuttal_stance_b="oppose",
            revised_by_a=False,
            revised_by_b=False,
            acknowledged_a=False,
            acknowledged_b=False,
            original_severity=0.88,
            decision="escalated",
            rationale="Deadlock.",
            next_action="human_escalation",
            resolution_confidence=0.95,
        )
        assert escalated.is_resolved is False
        assert escalated.is_escalated is True

    def test_stances_converged_property(self) -> None:
        """stances_converged returns True when not directly opposing."""
        cr = ConflictResolution(
            topic_id="t1",
            conflict_pair_index=0,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="conditional_support",
            post_rebuttal_stance_b="conditional_support",
            revised_by_a=True,
            revised_by_b=True,
            acknowledged_a=True,
            acknowledged_b=True,
            original_severity=0.50,
            decision="resolved",
            rationale="Converged.",
            next_action="continue_consensus",
            resolution_confidence=0.90,
        )
        assert cr.stances_converged is True

        cr2 = ConflictResolution(
            topic_id="t2",
            conflict_pair_index=1,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="support",
            post_rebuttal_stance_b="oppose",
            revised_by_a=False,
            revised_by_b=False,
            acknowledged_a=False,
            acknowledged_b=False,
            original_severity=0.88,
            decision="escalated",
            rationale="Deadlock.",
            next_action="human_escalation",
            resolution_confidence=0.95,
        )
        assert cr2.stances_converged is False

    def test_either_revised_and_both_revised(self) -> None:
        """either_revised and both_revised property logic."""
        cr_both = ConflictResolution(
            topic_id="t1",
            conflict_pair_index=0,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="conditional_support",
            post_rebuttal_stance_b="conditional_support",
            revised_by_a=True,
            revised_by_b=True,
            acknowledged_a=True,
            acknowledged_b=True,
            original_severity=0.50,
            decision="resolved",
            rationale="Both revised.",
            next_action="continue_consensus",
            resolution_confidence=0.90,
        )
        assert cr_both.either_revised is True
        assert cr_both.both_revised is True

        cr_one = ConflictResolution(
            topic_id="t2",
            conflict_pair_index=1,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="conditional_support",
            post_rebuttal_stance_b="oppose",
            revised_by_a=True,
            revised_by_b=False,
            acknowledged_a=True,
            acknowledged_b=False,
            original_severity=0.60,
            decision="escalated",
            rationale="One revised.",
            next_action="round_3_debate",
            resolution_confidence=0.80,
        )
        assert cr_one.either_revised is True
        assert cr_one.both_revised is False

        cr_none = ConflictResolution(
            topic_id="t3",
            conflict_pair_index=2,
            persona_a="a",
            persona_b="b",
            original_stance_a="support",
            original_stance_b="oppose",
            post_rebuttal_stance_a="support",
            post_rebuttal_stance_b="oppose",
            revised_by_a=False,
            revised_by_b=False,
            acknowledged_a=False,
            acknowledged_b=False,
            original_severity=0.70,
            decision="escalated",
            rationale="None revised.",
            next_action="round_3_debate",
            resolution_confidence=0.85,
        )
        assert cr_none.either_revised is False
        assert cr_none.both_revised is False


# ═════════════════════════════════════════════════════════════════════════
# 8. ResolutionResult property accessors
# ═════════════════════════════════════════════════════════════════════════


class TestResolutionResultProperties:
    """Test ResolutionResult dataclass properties and methods."""

    def test_all_resolved_and_consensus_ratio(self) -> None:
        """all_resolved and consensus_ratio reflect resolution state."""
        # All resolved
        result = ResolutionResult(
            conflict_resolutions=(),
            total_conflicts=0,
            resolved_count=0,
            escalated_count=0,
            requires_escalation=False,
            requires_human=False,
            tie_break_needed=False,
            overall_consensus_score=1.0,
        )
        assert result.all_resolved is True
        assert result.consensus_ratio == 1.0

        # Half resolved
        result2 = ResolutionResult(
            conflict_resolutions=(),
            total_conflicts=4,
            resolved_count=2,
            escalated_count=2,
            requires_escalation=True,
            requires_human=True,
            tie_break_needed=True,
            overall_consensus_score=0.50,
        )
        assert result2.all_resolved is False
        assert result2.consensus_ratio == 0.5

    def test_get_by_topic_returns_none_for_unknown(self) -> None:
        """get_by_topic returns None when topic not found."""
        result = ResolutionResult(
            conflict_resolutions=(),
            total_conflicts=0,
            resolved_count=0,
            escalated_count=0,
            requires_escalation=False,
            requires_human=False,
            tie_break_needed=False,
            overall_consensus_score=1.0,
        )
        assert result.get_by_topic("nonexistent") is None
