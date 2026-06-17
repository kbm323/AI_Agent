"""Tests for the structured rebuttal/revision exchange module.

Sub-AC 5b-2: Structured Rebuttal/Revision Exchange — given detected
conflict pairs and original opinion packets, execute a rebuttal round
where conflicting personas exchange structured counterarguments and
optionally revise positions; testable by asserting rebuttal packets
are generated and revisions are reflected.

Coverage:
- Happy path: two conflicting personas exchange rebuttals
- Rebuttal packets generated for both sides of every conflict pair
- Counterargument points are structured (1-5 points)
- Acknowledges validity flag is set correctly
- Revision records are created when stance changes
- Revised positions are attached to rebuttal packets
- Conflict resolution tracking (resolved vs unresolved count)
- Multiple conflict pairs produce correct number of rebuttals
- No revisions when stances remain unchanged
- exchange_round_complete flag
- RebuttalExchangeResult properties (total_rebuttals, total_revisions, etc.)
- RebuttalPacket properties (has_revision, stance_changed)
- Direct opposition rebuttal structure
- Incompatible recommendation rebuttal structure
- Priority divergence rebuttal structure
- Methodological difference rebuttal structure
- Injectable rebuttal generator for deterministic testing
- Thread-safe injectable overrides
- ValueError / TypeError input validation
- Korean-language rebuttal content
- Mixed Korean/English rebuttal content
- Empty opinion_packets raises ValueError
- Wrong type for conflict_result raises TypeError
- RevisionRecord dataclass field validation
- RebuttalExchangeResult dataclass field validation
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
    _default_generate_rebuttal,
    _build_counterargument_points,
    _should_acknowledge_validity,
    _should_revise,
    _compute_revised_stance,
    _check_pair_resolved,
    _are_directly_opposing,
    execute_rebuttal_exchange,
    inject_rebuttal_generator,
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


# ═════════════════════════════════════════════════════════════════════════
# 1. Default rebuttal generator unit tests
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultRebuttalGenerator:
    """Test the default rebuttal generator logic."""

    def test_generates_rebuttal_summary(self) -> None:
        """Default generator produces a non-empty summary."""
        conflict = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
        )
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="art-director",
                target_persona="tech-director",
                rebutter_opinion="네온 팔레트를 채택해야 합니다.",
                target_opinion="네온 팔레트에 반대합니다.",
                rebutter_confidence=0.9,
                target_confidence=0.85,
            )
        )
        assert summary
        assert "art-director" in summary
        assert "tech-director" in summary

    def test_generates_counterargument_points(self) -> None:
        """Default generator produces 1-5 counterargument points."""
        conflict = _make_conflict_pair(
            conflict_type="direct_opposition",
            stance_a="support",
            stance_b="oppose",
        )
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="art-director",
                target_persona="tech-director",
                rebutter_opinion="네온 팔레트를 채택해야 합니다.",
                target_opinion="네온 팔레트에 반대합니다.",
                rebutter_confidence=0.9,
                target_confidence=0.85,
            )
        )
        assert 1 <= len(points) <= 5
        for point in points:
            assert isinstance(point, str)
            assert len(point) > 0

    def test_acknowledges_validity_for_high_confidence_opponent(self) -> None:
        """High-confidence opponent earns validity acknowledgement."""
        conflict = _make_conflict_pair(
            conflict_type="direct_opposition",
            stance_a="oppose",
            stance_b="support",
            confidence_a=0.75,
            confidence_b=0.95,
        )
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="tech-director",
                target_persona="art-director",
                rebutter_opinion="네온 팔레트에 반대합니다.",
                target_opinion="네온 팔레트를 채택해야 합니다.",
                rebutter_confidence=0.75,
                target_confidence=0.95,
            )
        )
        # Target confidence >= 0.85 should trigger acknowledgement
        # for direct_opposition when rebutter is oppose
        assert ack is True

    def test_no_acknowledgement_for_low_confidence_opponent(self) -> None:
        """Low-confidence opponent may not earn acknowledgement when rebutter is oppose."""
        conflict = _make_conflict_pair(
            conflict_type="direct_opposition",
            stance_a="oppose",
            stance_b="support",
            persona_a="art-director",
            persona_b="tech-director",
            confidence_a=0.9,
            confidence_b=0.5,
        )
        # art-director (stance=oppose) rebuts tech-director (stance=support, conf=0.5)
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="art-director",  # stance=oppose
                target_persona="tech-director",     # stance=support, conf=0.5
                rebutter_opinion="네온 팔레트에 반대합니다.",
                target_opinion="네온 팔레트를 채택해야 합니다.",
                rebutter_confidence=0.9,
                target_confidence=0.5,  # target has low confidence
            )
        )
        # For oppose stance + direct_opposition, threshold is >= 0.85
        # target_confidence=0.5 < 0.85 → no acknowledgement
        assert ack is False

    def test_returns_confidence_after(self) -> None:
        """Confidence after rebuttal is returned."""
        conflict = _make_conflict_pair()
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="art-director",
                target_persona="tech-director",
                rebutter_opinion="Some opinion.",
                target_opinion="Other opinion.",
                rebutter_confidence=0.9,
                target_confidence=0.85,
            )
        )
        assert 0.0 <= conf <= 1.0

    def test_revision_for_low_severity_conflict(self) -> None:
        """Low-severity conflict where target has higher confidence triggers revision."""
        conflict = _make_conflict_pair(
            conflict_type="priority_divergence",
            stance_a="conditional_support",
            stance_b="support",
            severity=0.4,
            confidence_a=0.6,
            confidence_b=0.9,
        )
        summary, points, ack, rev_stance, rev_summary, conf = (
            _default_generate_rebuttal(
                conflict=conflict,
                rebutting_persona="art-director",
                target_persona="tech-director",
                rebutter_opinion="조건부로 지지합니다.",
                target_opinion="찬성합니다.",
                rebutter_confidence=0.6,
                target_confidence=0.9,
            )
        )
        # For priority_divergence with severity <= 0.4, revision should occur
        assert rev_stance == "support"  # moved from conditional_support to support


# ═════════════════════════════════════════════════════════════════════════
# 2. Counterargument point building tests
# ═════════════════════════════════════════════════════════════════════════


class TestCounterargumentPoints:
    """Test the counterargument point builder."""

    def test_direct_opposition_points(self) -> None:
        """Direct opposition produces refutation-style points."""
        points = _build_counterargument_points(
            conflict_type="direct_opposition",
            rebutter_stance="oppose",
            target_stance="support",
            rebutter_position="Oppose neon palette.",
            target_position="Support neon palette.",
            rebutter_opinion="네온 팔레트에 반대합니다.",
        )
        assert len(points) >= 2
        assert any("disagreement" in p.lower() for p in points)

    def test_incompatible_recommendation_points(self) -> None:
        """Incompatible recommendation produces divergence points."""
        points = _build_counterargument_points(
            conflict_type="incompatible_recommendation",
            rebutter_stance="support",
            target_stance="support",
            rebutter_position="Adopt approach.",
            target_position="Reject approach.",
            rebutter_opinion="이 방안을 채택해야 합니다.",
        )
        assert len(points) >= 2
        assert any("divergence" in p.lower() or "conflict" in p.lower() for p in points)

    def test_priority_divergence_points(self) -> None:
        """Priority divergence produces timing/urgency points."""
        points = _build_counterargument_points(
            conflict_type="priority_divergence",
            rebutter_stance="support",
            target_stance="conditional_support",
            rebutter_position="Adopt immediately.",
            target_position="Adopt conditionally.",
            rebutter_opinion="즉시 채택해야 합니다.",
        )
        assert len(points) >= 2

    def test_methodological_difference_points(self) -> None:
        """Methodological difference produces approach-comparison points."""
        points = _build_counterargument_points(
            conflict_type="methodological_difference",
            rebutter_stance="alternative_proposal",
            target_stance="support",
            rebutter_position="Alternative approach.",
            target_position="Original approach.",
            rebutter_opinion="대안을 제시합니다.",
        )
        assert len(points) >= 2

    def test_factual_disagreement_points(self) -> None:
        """Factual disagreement produces evidence-based points."""
        points = _build_counterargument_points(
            conflict_type="factual_disagreement",
            rebutter_stance="oppose",
            target_stance="support",
            rebutter_position="Facts contradict.",
            target_position="Facts support.",
            rebutter_opinion="데이터가 다릅니다.",
        )
        assert len(points) >= 2

    def test_max_five_points(self) -> None:
        """Never more than 5 points returned."""
        points = _build_counterargument_points(
            conflict_type="direct_opposition",
            rebutter_stance="oppose",
            target_stance="support",
            rebutter_position="X",
            target_position="Y",
            rebutter_opinion="해야 합니다. 추천합니다. 필요합니다. " * 10,
        )
        assert len(points) <= 5


# ═════════════════════════════════════════════════════════════════════════
# 3. Validity acknowledgement tests
# ═════════════════════════════════════════════════════════════════════════


class TestAcknowledgeValidity:
    """Test the validity acknowledgement logic."""

    def test_high_confidence_target_acknowledged(self) -> None:
        """Target with high confidence in direct opposition acknowledged."""
        assert _should_acknowledge_validity(
            conflict_type="direct_opposition",
            rebutter_stance="oppose",
            target_confidence=0.9,
        ) is True

    def test_low_confidence_target_not_acknowledged(self) -> None:
        """Target with low confidence not acknowledged in oppose stance."""
        assert _should_acknowledge_validity(
            conflict_type="direct_opposition",
            rebutter_stance="oppose",
            target_confidence=0.5,
        ) is False

    def test_non_opposition_always_acknowledges_medium_confidence(self) -> None:
        """Non-direct-opposition conflicts acknowledge at >= 0.5."""
        assert _should_acknowledge_validity(
            conflict_type="priority_divergence",
            rebutter_stance="support",
            target_confidence=0.55,
        ) is True


# ═════════════════════════════════════════════════════════════════════════
# 4. Revision decision tests
# ═════════════════════════════════════════════════════════════════════════


class TestShouldRevise:
    """Test the revision decision logic."""

    def test_revise_when_low_severity_target_higher_confidence(self) -> None:
        """Low severity + higher target confidence → revise."""
        assert _should_revise(
            severity=0.4,
            rebutter_stance="conditional_support",
            target_confidence=0.9,
            rebutter_confidence=0.6,
            acknowledges=True,
        ) is True

    def test_revise_when_acknowledges_and_neutral(self) -> None:
        """Acknowledges validity + neutral stance → revise."""
        assert _should_revise(
            severity=0.6,
            rebutter_stance="neutral",
            target_confidence=0.8,
            rebutter_confidence=0.5,
            acknowledges=True,
        ) is True

    def test_no_revise_high_severity(self) -> None:
        """High severity direct opposition → don't revise easily."""
        # severity > 0.5 and not ack-related condition → no revise
        assert _should_revise(
            severity=0.9,
            rebutter_stance="oppose",
            target_confidence=0.9,
            rebutter_confidence=0.85,
            acknowledges=False,
        ) is False

    def test_no_revise_when_no_acknowledgement(self) -> None:
        """No acknowledgement + not low severity → no revise."""
        assert _should_revise(
            severity=0.7,
            rebutter_stance="oppose",
            target_confidence=0.7,
            rebutter_confidence=0.8,
            acknowledges=False,
        ) is False

    def test_very_low_severity_always_revises(self) -> None:
        """Severity <= 0.4 always triggers revision."""
        assert _should_revise(
            severity=0.3,
            rebutter_stance="oppose",
            target_confidence=0.5,
            rebutter_confidence=0.5,
            acknowledges=False,
        ) is True


# ═════════════════════════════════════════════════════════════════════════
# 5. Revised stance computation tests
# ═════════════════════════════════════════════════════════════════════════


class TestRevisedStance:
    """Test the revised stance computation."""

    def test_oppose_moves_to_conditional(self) -> None:
        """Oppose → conditional_support (moderate shift)."""
        assert _compute_revised_stance("oppose", "support") == "conditional_support"

    def test_support_accepts_alternative(self) -> None:
        """Support → conditional_support when target offers alternative."""
        assert (
            _compute_revised_stance("support", "alternative_proposal")
            == "conditional_support"
        )

    def test_conditional_aligns_to_support(self) -> None:
        """Conditional_support → support when target is support."""
        assert _compute_revised_stance("conditional_support", "support") == "support"

    def test_neutral_moves_to_conditional(self) -> None:
        """Neutral → conditional_support when target supports."""
        assert _compute_revised_stance("neutral", "support") == "conditional_support"

    def test_neutral_moves_to_target_direction(self) -> None:
        """Neutral moves toward the target stance."""
        assert _compute_revised_stance("neutral", "oppose") == "oppose"

    def test_alternative_converges_to_conditional(self) -> None:
        """Alternative_proposal → conditional_support when target supports."""
        assert (
            _compute_revised_stance("alternative_proposal", "support")
            == "conditional_support"
        )

    def test_same_stance_returns_original(self) -> None:
        """Same stance returns unchanged."""
        assert _compute_revised_stance("support", "support") == "support"


# ═════════════════════════════════════════════════════════════════════════
# 6. Pair resolution tests
# ═════════════════════════════════════════════════════════════════════════


class TestPairResolved:
    """Test the conflict pair resolution check."""

    def test_compatible_stances_resolved(self) -> None:
        """Both moved to compatible stances → resolved."""
        conflict = _make_conflict_pair(
            stance_a="support", stance_b="oppose",
        )
        assert _check_pair_resolved("conditional_support", "conditional_support", conflict) is True
        assert _check_pair_resolved("support", "support", conflict) is True

    def test_directly_opposing_still_unresolved(self) -> None:
        """Still opposing → unresolved."""
        conflict = _make_conflict_pair(
            stance_a="support", stance_b="oppose",
        )
        assert _check_pair_resolved("support", "oppose", conflict) is False

    def test_opposing_stances(self) -> None:
        """Direct opposition check."""
        assert _are_directly_opposing("support", "oppose") is True
        assert _are_directly_opposing("oppose", "support") is True
        assert _are_directly_opposing("support", "support") is False
        assert _are_directly_opposing("neutral", "neutral") is False
        assert _are_directly_opposing("conditional_support", "support") is False


# ═════════════════════════════════════════════════════════════════════════
# 7. Integration: execute_rebuttal_exchange with conflict results
# ═════════════════════════════════════════════════════════════════════════


class TestExecuteRebuttalExchange:
    """Integration tests for the public execute_rebuttal_exchange API."""

    def test_happy_path_two_personas_one_conflict(self) -> None:
        """Two personas with direct opposition → 2 rebuttal packets."""
        # Use fixed extractor to guarantee conflict detection
        def fixed_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="visual-direction",
                    label="비주얼 디렉션",
                    key_terms=("비주얼",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet(
                "art-director",
                "1. 네온 팔레트를 채택해야 합니다. 찬성합니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "1. 네온 팔레트에 반대합니다. 파스텔이 더 안전합니다.",
                0.85,
            ),
        ]
        from src.conflict_detector import inject_topic_extractor

        conflicts = detect_conflicts(packets, _inject_extractor=fixed_extractor)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        assert exchange.exchange_round_complete is True
        assert exchange.total_rebuttals >= 2
        assert isinstance(exchange.rebuttal_packets, tuple)

    def test_every_conflict_produces_two_rebuttals(self) -> None:
        """Each conflict pair produces exactly 2 rebuttals (A→B and B→A)."""
        # Use injected extractor for deterministic topics
        def fixed_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="visual-direction",
                    label="비주얼 디렉션",
                    key_terms=("비주얼",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.9),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.85),
            _make_packet("marketing-lead", "네온 팔레트에 반대합니다.", 0.7),
        ]

        conflicts = detect_conflicts(packets, _inject_extractor=fixed_extractor)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        # N conflict pairs → 2*N rebuttals
        assert exchange.total_rebuttals == len(conflicts.conflict_pairs) * 2

    def test_rebuttal_packet_structure(self) -> None:
        """Rebuttal packets have all required fields."""
        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.9),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.85),
        ]
        conflicts = detect_conflicts(packets)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        for rb in exchange.rebuttal_packets:
            assert rb.rebuttal_id.startswith("rebuttal-")
            assert isinstance(rb.conflict_pair_index, int)
            assert rb.rebutting_persona
            assert rb.target_persona
            assert rb.counterargument_summary
            assert len(rb.counterargument_points) >= 1
            assert isinstance(rb.acknowledges_validity, bool)
            assert 0.0 <= rb.confidence_after <= 1.0
            assert rb.stance_after

    def test_rebuttal_packets_cover_both_directions(self) -> None:
        """Both A→B and B→A rebuttals are generated for each conflict."""
        def fixed_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="visual-direction",
                    label="비주얼 디렉션",
                    key_terms=("비주얼",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.9),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.85),
        ]
        conflicts = detect_conflicts(packets, _inject_extractor=fixed_extractor)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        rebutting_personas = {rb.rebutting_persona for rb in exchange.rebuttal_packets}
        target_personas = {rb.target_persona for rb in exchange.rebuttal_packets}

        # Both personas should appear as rebutters and targets
        assert "art-director" in rebutting_personas
        assert "tech-director" in rebutting_personas
        assert "art-director" in target_personas
        assert "tech-director" in target_personas

    def test_has_revision_property(self) -> None:
        """RebuttalPacket.has_revision reflects presence of revised_position."""
        rb_without = RebuttalPacket(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            topic_id="test-topic",
            rebutting_persona="art-director",
            target_persona="tech-director",
            counterargument_summary="Summary.",
            counterargument_points=("Point 1",),
            acknowledges_validity=False,
        )
        assert rb_without.has_revision is False

        rb_with = RebuttalPacket(
            rebuttal_id="rb-002",
            conflict_pair_index=0,
            topic_id="test-topic",
            rebutting_persona="tech-director",
            target_persona="art-director",
            counterargument_summary="Summary.",
            counterargument_points=("Point 1",),
            acknowledges_validity=True,
            revised_position=TopicPosition(
                persona_id="tech-director",
                topic_id="test-topic",
                stance="conditional_support",
                summary="Revised.",
                supporting_points=(),
                confidence=0.8,
                recommendation_direction="maintain",
            ),
        )
        assert rb_with.has_revision is True

    def test_rebuttal_exchange_result_properties(self) -> None:
        """RebuttalExchangeResult properties are correct."""
        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.9),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.85),
        ]
        conflicts = detect_conflicts(packets)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        assert isinstance(exchange.total_rebuttals, int)
        assert exchange.total_rebuttals == len(exchange.rebuttal_packets)
        assert isinstance(exchange.total_revisions, int)
        assert exchange.total_revisions == len(exchange.revisions)
        assert isinstance(exchange.has_revisions, bool)
        assert isinstance(exchange.all_resolved, bool)
        assert isinstance(exchange.conflict_pairs_resolved, int)
        assert isinstance(exchange.conflict_pairs_unresolved, int)
        assert (
            exchange.conflict_pairs_resolved + exchange.conflict_pairs_unresolved
            == len(conflicts.conflict_pairs)
        )

    def test_revision_record_structure(self) -> None:
        """RevisionRecord has all required fields."""
        revision = RevisionRecord(
            persona_id="art-director",
            topic_id="test-topic",
            original_stance="oppose",
            original_summary="Oppose neon.",
            revised_stance="conditional_support",
            revised_summary="Conditional support.",
            revision_rationale="Rebuttal prompted re-evaluation.",
            confidence_before=0.9,
            confidence_after=0.75,
        )
        assert revision.persona_id == "art-director"
        assert revision.original_stance == "oppose"
        assert revision.revised_stance == "conditional_support"
        assert 0.0 <= revision.confidence_before <= 1.0
        assert 0.0 <= revision.confidence_after <= 1.0


# ═════════════════════════════════════════════════════════════════════════
# 8. Input validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Test input validation for execute_rebuttal_exchange."""

    def test_empty_opinion_packets_raises(self) -> None:
        """Empty opinion_packets raises ValueError."""
        conflict_result = _make_conflict_result()
        with pytest.raises(ValueError, match="non-empty"):
            execute_rebuttal_exchange(conflict_result, [])

    def test_wrong_conflict_result_type_raises(self) -> None:
        """Non-ConflictDetectionResult raises TypeError."""
        with pytest.raises(TypeError):
            execute_rebuttal_exchange("not a result", [{"persona_id": "x"}])  # type: ignore[arg-type]

    def test_non_dict_in_packets_skipped_gracefully(self) -> None:
        """Non-dict entries in opinion_packets are skipped (don't crash)."""
        conflict_pair = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict_pair,),
            personas=("art-director", "tech-director"),
        )
        packets: list[Any] = [
            _make_packet("art-director", "네온 채택.", 0.9),
            "not a dict",
            _make_packet("tech-director", "네온 반대.", 0.85),
        ]
        # Should not raise; skips non-dict entries
        exchange = execute_rebuttal_exchange(conflict_result, packets)
        assert exchange.exchange_round_complete is True

    def test_no_conflicts_empty_result(self) -> None:
        """Conflict result with no conflicts → zero rebuttals."""
        conflict_result = _make_conflict_result(
            conflict_pairs=(),
            personas=("art-director", "tech-director"),
        )
        packets = [
            _make_packet("art-director", "네온 채택.", 0.9),
            _make_packet("tech-director", "네온 반대.", 0.85),
        ]
        exchange = execute_rebuttal_exchange(conflict_result, packets)
        assert exchange.total_rebuttals == 0
        assert exchange.total_revisions == 0


# ═════════════════════════════════════════════════════════════════════════
# 9. Injectable rebuttal generator tests
# ═════════════════════════════════════════════════════════════════════════


class TestInjectableGenerator:
    """Test with an injected rebuttal generator for deterministic results."""

    def test_inject_custom_generator(self) -> None:
        """Custom generator is used instead of the default."""
        def custom_generator(
            conflict, rebutting, target,
            r_opinion, t_opinion, r_conf, t_conf,
        ) -> tuple[str, tuple[str, ...], bool, str, str, float]:
            return (
                f"Custom rebuttal: {rebutting} → {target}",
                ("Custom point 1", "Custom point 2"),
                True,
                "support",
                "Custom revised position.",
                0.88,
            )

        inject_rebuttal_generator(custom_generator)
        try:
            conflict = _make_conflict_pair(
                persona_a="art-director",
                persona_b="tech-director",
            )
            conflict_result = _make_conflict_result(
                conflict_pairs=(conflict,),
                personas=("art-director", "tech-director"),
            )
            packets = [
                _make_packet("art-director", "Opinion A.", 0.9),
                _make_packet("tech-director", "Opinion B.", 0.85),
            ]
            exchange = execute_rebuttal_exchange(conflict_result, packets)

            assert exchange.total_rebuttals == 2
            for rb in exchange.rebuttal_packets:
                assert "Custom rebuttal" in rb.counterargument_summary
                assert "Custom point 1" in rb.counterargument_points
        finally:
            inject_rebuttal_generator(None)

    def test_inject_per_call_generator(self) -> None:
        """Per-call _injected_generator overrides the default."""
        def per_call_generator(
            conflict, rebutting, target,
            r_opinion, t_opinion, r_conf, t_conf,
        ) -> tuple[str, tuple[str, ...], bool, str, str, float]:
            return (
                f"Per-call: {rebutting}",
                ("Per-call point",),
                False,
                "oppose",
                "Per-call revised.",
                0.5,
            )

        conflict = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict,),
            personas=("art-director", "tech-director"),
        )
        packets = [
            _make_packet("art-director", "Opinion A.", 0.9),
            _make_packet("tech-director", "Opinion B.", 0.85),
        ]
        exchange = execute_rebuttal_exchange(
            conflict_result,
            packets,
            _injected_generator=per_call_generator,
        )

        assert exchange.total_rebuttals == 2
        for rb in exchange.rebuttal_packets:
            assert "Per-call" in rb.counterargument_summary

    def test_custom_generator_produces_revisions(self) -> None:
        """Custom generator that always changes stance produces revisions."""
        def always_revise_generator(
            conflict, rebutting, target,
            r_opinion, t_opinion, r_conf, t_conf,
        ) -> tuple[str, tuple[str, ...], bool, str, str, float]:
            # Always return a different stance from what the conflict says
            if rebutting == conflict.persona_a:
                return ("Summary", ("Point",), True, "conditional_support", "Revised A.", 0.8)
            else:
                return ("Summary", ("Point",), True, "support", "Revised B.", 0.85)

        conflict = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
            stance_a="oppose",  # will change from oppose → conditional_support
            stance_b="oppose",  # will change from oppose → support
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict,),
            personas=("art-director", "tech-director"),
        )
        packets = [
            _make_packet("art-director", "Opinion A.", 0.9),
            _make_packet("tech-director", "Opinion B.", 0.85),
        ]
        exchange = execute_rebuttal_exchange(
            conflict_result,
            packets,
            _injected_generator=always_revise_generator,
        )

        # Both personas changed stance → 2 revisions
        assert exchange.total_revisions == 2
        assert exchange.has_revisions is True


# ═════════════════════════════════════════════════════════════════════════
# 10. Korean-language support
# ═════════════════════════════════════════════════════════════════════════


class TestKoreanSupport:
    """Test rebuttal exchange with Korean-language opinions."""

    def test_korean_opinions_produce_rebuttals(self) -> None:
        """Korean opinion text produces valid rebuttals."""
        packets = [
            _make_packet(
                "art-director",
                (
                    "1. 캐릭터 디자인은 기존 방향을 유지해야 합니다.\n"
                    "2. 배경은 판타지 스타일로 변경을 추천합니다."
                ),
                0.9,
            ),
            _make_packet(
                "tech-director",
                (
                    "1. 캐릭터 디자인 변경은 위험합니다. 기술적 제약이 있습니다.\n"
                    "2. 배경 변경에 찬성합니다."
                ),
                0.85,
            ),
        ]
        conflicts = detect_conflicts(packets)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        assert exchange.total_rebuttals >= 2
        for rb in exchange.rebuttal_packets:
            assert rb.rebutting_persona
            assert rb.counterargument_summary

    def test_mixed_korean_english_rebuttals(self) -> None:
        """Mixed Korean/English opinions handled correctly."""
        def fixed_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="visual-direction",
                    label="Visual Direction",
                    key_terms=("visual", "direction"),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet(
                "art-director",
                "1. We should adopt the neon palette. 채택해야 합니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "1. I oppose the neon palette. 반대합니다. Use pastel instead.",
                0.85,
            ),
        ]
        conflicts = detect_conflicts(packets, _inject_extractor=fixed_extractor)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        assert exchange.exchange_round_complete is True
        assert exchange.total_rebuttals >= 2


# ═════════════════════════════════════════════════════════════════════════
# 11. Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case tests for the rebuttal exchange."""

    def test_different_persona_order_in_packets(self) -> None:
        """Packet order doesn't affect rebuttal generation."""
        conflict = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict,),
            personas=("art-director", "tech-director"),
        )

        # Packets in reverse order
        packets_reverse = [
            _make_packet("tech-director", "네온 반대.", 0.85),
            _make_packet("art-director", "네온 채택.", 0.9),
        ]
        exchange = execute_rebuttal_exchange(conflict_result, packets_reverse)

        assert exchange.total_rebuttals == 2
        # Both personas should appear
        personas = {rb.rebutting_persona for rb in exchange.rebuttal_packets}
        assert "art-director" in personas
        assert "tech-director" in personas

    def test_high_confidence_both_sides(self) -> None:
        """Both personas with high confidence produce strong rebuttals."""
        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.95),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.95),
        ]
        conflicts = detect_conflicts(packets)
        exchange = execute_rebuttal_exchange(conflicts, packets)

        for rb in exchange.rebuttal_packets:
            # High confidence both sides → more points, validity acknowledged
            assert len(rb.counterargument_points) >= 1

    def test_missing_opinion_content(self) -> None:
        """Persona with empty opinion content doesn't crash."""
        conflict = _make_conflict_pair(
            persona_a="art-director",
            persona_b="tech-director",
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(conflict,),
            personas=("art-director", "tech-director"),
        )
        packets = [
            {"persona_id": "art-director", "confidence": 0.9},  # no opinion_content
            _make_packet("tech-director", "네온 반대.", 0.85),
        ]
        # Should not raise
        exchange = execute_rebuttal_exchange(conflict_result, packets)
        assert exchange.exchange_round_complete is True

    def test_multiple_conflict_pairs_with_mixed_severity(self) -> None:
        """Multiple conflict pairs with varying severity produce correct results."""
        cp1 = _make_conflict_pair(
            topic_id="topic-1",
            persona_a="a",
            persona_b="b",
            severity=0.9,
            conflict_type="direct_opposition",
            stance_a="support",
            stance_b="oppose",
        )
        cp2 = _make_conflict_pair(
            topic_id="topic-2",
            persona_a="a",
            persona_b="c",
            severity=0.4,
            conflict_type="priority_divergence",
            stance_a="support",
            stance_b="conditional_support",
        )
        conflict_result = _make_conflict_result(
            conflict_pairs=(cp1, cp2),
            personas=("a", "b", "c"),
        )
        packets = [
            _make_packet("a", "네온 채택해야 합니다.", 0.9),
            _make_packet("b", "네온 반대합니다.", 0.85),
            _make_packet("c", "조건부로 찬성합니다.", 0.7),
        ]
        exchange = execute_rebuttal_exchange(conflict_result, packets)

        assert exchange.total_rebuttals == 4  # 2 conflicts × 2 rebuttals
        # Low-severity pair may be resolved after revision
        assert exchange.conflict_pairs_resolved + exchange.conflict_pairs_unresolved == 2


# ═════════════════════════════════════════════════════════════════════════
# 12. RebuttalPacket and RevisionRecord dataclass validation
# ═════════════════════════════════════════════════════════════════════════


class TestDataclassValidation:
    """Test dataclass field types and defaults."""

    def test_rebuttal_packet_defaults(self) -> None:
        """RebuttalPacket default values are correct."""
        rb = RebuttalPacket(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            topic_id="test",
            rebutting_persona="persona-a",
            target_persona="persona-b",
            counterargument_summary="Summary.",
            counterargument_points=("P1", "P2"),
            acknowledges_validity=False,
        )
        assert rb.revised_position is None
        assert rb.confidence_after == 0.0
        assert rb.stance_after == "neutral"
        assert rb.has_revision is False

    def test_rebuttal_packet_with_revision(self) -> None:
        """RebuttalPacket with revised_position has correct properties."""
        rev_pos = TopicPosition(
            persona_id="persona-a",
            topic_id="test",
            stance="conditional_support",
            summary="Revised.",
            supporting_points=(),
            confidence=0.75,
            recommendation_direction="maintain",
        )
        rb = RebuttalPacket(
            rebuttal_id="rb-001",
            conflict_pair_index=0,
            topic_id="test",
            rebutting_persona="persona-a",
            target_persona="persona-b",
            counterargument_summary="Summary.",
            counterargument_points=("P1",),
            acknowledges_validity=True,
            revised_position=rev_pos,
            confidence_after=0.75,
            stance_after="conditional_support",
        )
        assert rb.has_revision is True

    def test_revision_record_all_fields(self) -> None:
        """RevisionRecord stores all fields correctly."""
        rr = RevisionRecord(
            persona_id="art-director",
            topic_id="visual-direction",
            original_stance="oppose",
            original_summary="원래 반대 입장.",
            revised_stance="conditional_support",
            revised_summary="수정된 조건부 지지.",
            revision_rationale="상대방 의견 설득력 있음.",
            confidence_before=0.9,
            confidence_after=0.72,
        )
        assert rr.persona_id == "art-director"
        assert rr.topic_id == "visual-direction"
        assert rr.original_stance != rr.revised_stance
        assert rr.confidence_before > rr.confidence_after

    def test_rebuttal_exchange_result_defaults(self) -> None:
        """RebuttalExchangeResult with no conflicts is valid."""
        result = RebuttalExchangeResult(
            rebuttal_packets=(),
            revisions=(),
            conflict_pairs_resolved=0,
            conflict_pairs_unresolved=0,
            exchange_round_complete=True,
        )
        assert result.total_rebuttals == 0
        assert result.total_revisions == 0
        assert result.has_revisions is False
        assert result.all_resolved is True
