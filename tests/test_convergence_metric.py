"""Tests for the convergence metric computation module.

Sub-AC 5c-1: Convergence metric computation — given a set of positions
from the current round and the previous round, compute a numerical
convergence score (e.g. position delta norm, agreement ratio) and
determine whether it meets the configured threshold, testable with
synthetic position sets producing known convergence scores.

Coverage:
- Complete convergence (all positions stable, all personas agree)
- Complete divergence (all positions opposing, high delta)
- Near-convergence (just below threshold)
- Insufficient data (empty input, single-persona)
- Deadlock detection (all opposing, no movement)
- Partial convergence (some topics converged, others not)
- Single topic with varying agreement ratios
- Multi-topic multi-persona scenarios
- Threshold boundary testing (exactly at threshold)
- Position delta computation accuracy
- Vector encoding correctness
- Stance compatibility checks
- Recommendation compatibility checks
- ConvergenceConfig validation
- RoundPosition validation
- Injection mechanism for delta/agreement/score computers
- convergence_from_conflict_resolutions helper
- Korean-language topic IDs and persona IDs
- Edge cases: zero confidence, maximum confidence, missing previous round
- Mixed stance/recommendation combinations
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import pytest

from src.convergence_metric import (
    DEFAULT_CONVERGENCE_THRESHOLD,
    ConvergenceConfig,
    ConvergenceResult,
    PositionDelta,
    RoundPosition,
    TopicAgreement,
    _are_positions_compatible,
    _are_recommendations_compatible,
    _are_stances_compatible,
    _compute_agreement_ratio,
    _compute_composite_score,
    _compute_delta_norm,
    _compute_position_deltas,
    _compute_topic_agreements,
    _encode_position_vector,
    _vector_euclidean_distance,
    compute_convergence,
    convergence_from_conflict_resolutions,
    inject_agreement_computer,
    inject_delta_computer,
    inject_score_computer,
    reset_injectables,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper factories
# ═════════════════════════════════════════════════════════════════════════


def _make_position(
    persona_id: str = "art-director",
    topic_id: str = "budget-allocation",
    stance: str = "support",
    recommendation: str = "adopt",
    confidence: float = 0.85,
    round_number: int = 1,
) -> RoundPosition:
    """Create a ``RoundPosition`` with minimal boilerplate."""
    return RoundPosition(
        persona_id=persona_id,
        topic_id=topic_id,
        stance=stance,
        recommendation=recommendation,
        confidence=confidence,
        round_number=round_number,
    )


# ═════════════════════════════════════════════════════════════════════════
# RoundPosition validation
# ═════════════════════════════════════════════════════════════════════════


class TestRoundPosition:
    """Validation and construction of RoundPosition."""

    def test_valid_position(self) -> None:
        pos = _make_position()
        assert pos.persona_id == "art-director"
        assert pos.topic_id == "budget-allocation"
        assert pos.stance == "support"
        assert pos.recommendation == "adopt"
        assert pos.confidence == 0.85
        assert pos.round_number == 1

    def test_confidence_above_1_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _make_position(confidence=1.5)

    def test_confidence_below_0_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            _make_position(confidence=-0.1)

    def test_confidence_exactly_1_ok(self) -> None:
        pos = _make_position(confidence=1.0)
        assert pos.confidence == 1.0

    def test_confidence_exactly_0_ok(self) -> None:
        pos = _make_position(confidence=0.0)
        assert pos.confidence == 0.0

    def test_round_number_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="round_number"):
            _make_position(round_number=0)

    def test_round_number_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="round_number"):
            _make_position(round_number=-1)

    def test_round_number_4_tie_break_ok(self) -> None:
        pos = _make_position(round_number=4)
        assert pos.round_number == 4

    def test_immutability(self) -> None:
        pos = _make_position()
        with pytest.raises(Exception):
            pos.confidence = 0.5  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _make_position()
        b = _make_position()
        assert a == b
        assert hash(a) == hash(b)

    def test_inequality(self) -> None:
        a = _make_position(stance="support")
        b = _make_position(stance="oppose")
        assert a != b

    def test_korean_persona_id(self) -> None:
        pos = _make_position(persona_id="아트-디렉터")
        assert pos.persona_id == "아트-디렉터"

    def test_korean_topic_id(self) -> None:
        pos = _make_position(topic_id="예산-할당")
        assert pos.topic_id == "예산-할당"


# ═════════════════════════════════════════════════════════════════════════
# ConvergenceConfig validation
# ═════════════════════════════════════════════════════════════════════════


class TestConvergenceConfig:
    """Validation of ConvergenceConfig."""

    def test_default_config(self) -> None:
        cfg = ConvergenceConfig()
        assert cfg.threshold == 0.85
        assert cfg.delta_weight == 0.4
        assert cfg.agreement_weight == 0.6
        assert cfg.min_agreement_ratio == 0.5

    def test_custom_config(self) -> None:
        cfg = ConvergenceConfig(threshold=0.9, delta_weight=0.3, agreement_weight=0.7)
        assert cfg.threshold == 0.9
        assert cfg.delta_weight == 0.3
        assert cfg.agreement_weight == 0.7

    def test_weights_must_sum_to_1(self) -> None:
        with pytest.raises(ValueError, match="weights must sum"):
            ConvergenceConfig(delta_weight=0.5, agreement_weight=0.6)

    def test_threshold_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="threshold"):
            ConvergenceConfig(threshold=1.5)

    def test_weights_exact_sum(self) -> None:
        cfg = ConvergenceConfig(delta_weight=0.25, agreement_weight=0.75)
        assert cfg.delta_weight + cfg.agreement_weight == 1.0

    def test_min_agreement_ratio_default(self) -> None:
        cfg = ConvergenceConfig()
        assert cfg.min_agreement_ratio == 0.5


# ═════════════════════════════════════════════════════════════════════════
# Vector encoding
# ═════════════════════════════════════════════════════════════════════════


class TestVectorEncoding:
    """Position vector encoding correctness."""

    def test_support_encodes_positive(self) -> None:
        vec = _encode_position_vector("support", "adopt", 0.9)
        assert vec[0] == 1.0
        assert vec[1] == 1.0
        assert vec[2] == 0.9

    def test_oppose_encodes_negative(self) -> None:
        vec = _encode_position_vector("oppose", "reject", 0.9)
        assert vec[0] == -1.0
        assert vec[1] == -1.0
        assert vec[2] == 0.9

    def test_neutral_encodes_zero(self) -> None:
        vec = _encode_position_vector("neutral", "maintain", 0.5)
        assert vec[0] == 0.0
        assert vec[1] == 0.0
        assert vec[2] == 0.5

    def test_conditional_support_encodes_mid(self) -> None:
        vec = _encode_position_vector("conditional_support", "explore", 0.7)
        assert vec[0] == 0.5
        assert vec[1] == 0.25
        assert vec[2] == 0.7

    def test_alternative_proposal_encodes_slight_negative(self) -> None:
        vec = _encode_position_vector("alternative_proposal", "defer", 0.6)
        assert vec[0] == -0.25
        assert vec[1] == -0.25
        assert vec[2] == 0.6

    def test_unknown_stance_defaults_neutral(self) -> None:
        vec = _encode_position_vector("unknown_stance", "adopt", 0.5)
        assert vec[0] == 0.0

    def test_unknown_recommendation_defaults_neutral(self) -> None:
        vec = _encode_position_vector("support", "unknown_rec", 0.5)
        assert vec[1] == 0.0

    def test_increase_encodes_positive(self) -> None:
        vec = _encode_position_vector("support", "increase", 0.8)
        assert vec[1] == 0.75

    def test_decrease_encodes_negative(self) -> None:
        vec = _encode_position_vector("support", "decrease", 0.8)
        assert vec[1] == -0.75

    def test_defer_encodes_mild_negative(self) -> None:
        vec = _encode_position_vector("neutral", "defer", 0.5)
        assert vec[1] == -0.25

    def test_maintain_encodes_zero(self) -> None:
        vec = _encode_position_vector("neutral", "maintain", 0.5)
        assert vec[1] == 0.0


# ═════════════════════════════════════════════════════════════════════════
# Euclidean distance
# ═════════════════════════════════════════════════════════════════════════


class TestEuclideanDistance:
    """Vector Euclidean distance computation."""

    def test_identical_vectors_distance_zero(self) -> None:
        v = (1.0, 1.0, 0.9)
        d = _vector_euclidean_distance(v, v)
        assert d == 0.0

    def test_maximally_opposite_vectors(self) -> None:
        v1 = (1.0, 1.0, 1.0)  # support, adopt, max confidence
        v2 = (-1.0, -1.0, 0.0)  # oppose, reject, zero confidence
        d = _vector_euclidean_distance(v1, v2)
        # sqrt((2)^2 + (2)^2 + (1)^2) / 3 = sqrt(9)/3 = 3/3 = 1.0
        assert math.isclose(d, 1.0, rel_tol=1e-9)

    def test_halfway_distance(self) -> None:
        v1 = (1.0, 1.0, 1.0)
        v2 = (0.0, 0.0, 0.5)
        d = _vector_euclidean_distance(v1, v2)
        # sqrt(1+1+0.25)/3 = sqrt(2.25)/3 = 1.5/3 = 0.5
        assert math.isclose(d, 0.5, rel_tol=1e-9)

    def test_distance_in_range(self) -> None:
        """All distances should be in [0, 1]."""
        import random

        rng = random.Random(42)
        for _ in range(100):
            v1 = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0, 1))
            v2 = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0, 1))
            d = _vector_euclidean_distance(v1, v2)
            assert 0.0 <= d <= 1.0, f"d={d} for v1={v1}, v2={v2}"


# ═════════════════════════════════════════════════════════════════════════
# Stance / recommendation compatibility
# ═════════════════════════════════════════════════════════════════════════


class TestStanceCompatibility:
    """Stance compatibility logic."""

    def test_support_oppose_incompatible(self) -> None:
        assert not _are_stances_compatible("support", "oppose")
        assert not _are_stances_compatible("oppose", "support")

    def test_support_conditional_support_compatible(self) -> None:
        assert _are_stances_compatible("support", "conditional_support")
        assert _are_stances_compatible("conditional_support", "support")

    def test_neutral_compatible_with_all(self) -> None:
        for stance in ("support", "oppose", "conditional_support", "alternative_proposal"):
            assert _are_stances_compatible("neutral", stance)
            assert _are_stances_compatible(stance, "neutral")

    def test_same_stance_compatible(self) -> None:
        assert _are_stances_compatible("support", "support")
        assert _are_stances_compatible("oppose", "oppose")
        assert _are_stances_compatible("conditional_support", "conditional_support")

    def test_alternative_proposal_compatible_with_most(self) -> None:
        assert _are_stances_compatible("alternative_proposal", "support")
        assert _are_stances_compatible("support", "alternative_proposal")
        assert _are_stances_compatible("alternative_proposal", "neutral")
        assert _are_stances_compatible("alternative_proposal", "conditional_support")


class TestRecommendationCompatibility:
    """Recommendation direction compatibility logic."""

    def test_adopt_reject_incompatible(self) -> None:
        assert not _are_recommendations_compatible("adopt", "reject")
        assert not _are_recommendations_compatible("reject", "adopt")

    def test_increase_decrease_incompatible(self) -> None:
        assert not _are_recommendations_compatible("increase", "decrease")
        assert not _are_recommendations_compatible("decrease", "increase")

    def test_adopt_explore_compatible(self) -> None:
        assert _are_recommendations_compatible("adopt", "explore")
        assert _are_recommendations_compatible("explore", "adopt")

    def test_same_recommendation_compatible(self) -> None:
        assert _are_recommendations_compatible("adopt", "adopt")
        assert _are_recommendations_compatible("maintain", "maintain")
        assert _are_recommendations_compatible("defer", "defer")


class TestPositionCompatibility:
    """Full position compatibility."""

    def test_compatible_positions(self) -> None:
        a = _make_position(stance="support", recommendation="adopt")
        b = _make_position(stance="conditional_support", recommendation="adopt")
        assert _are_positions_compatible(a, b)

    def test_incompatible_stance_makes_incompatible(self) -> None:
        a = _make_position(stance="support", recommendation="adopt")
        b = _make_position(stance="oppose", recommendation="adopt")
        assert not _are_positions_compatible(a, b)

    def test_incompatible_recommendation_makes_incompatible(self) -> None:
        a = _make_position(stance="support", recommendation="adopt")
        b = _make_position(stance="support", recommendation="reject")
        assert not _are_positions_compatible(a, b)

    def test_both_incompatible(self) -> None:
        a = _make_position(stance="support", recommendation="adopt")
        b = _make_position(stance="oppose", recommendation="reject")
        assert not _are_positions_compatible(a, b)


# ═════════════════════════════════════════════════════════════════════════
# Position delta computation
# ═════════════════════════════════════════════════════════════════════════


class TestPositionDeltaComputation:
    """Position delta between rounds."""

    def test_no_change_produces_zero_delta(self) -> None:
        pos1 = _make_position(round_number=1)
        pos2 = _make_position(round_number=2)
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 1
        assert deltas[0].vector_delta == 0.0
        assert deltas[0].normalized_delta == 0.0
        assert not deltas[0].stance_changed
        assert not deltas[0].recommendation_changed
        assert deltas[0].confidence_delta == 0.0

    def test_stance_change_produces_positive_delta(self) -> None:
        pos1 = _make_position(stance="support", round_number=1)
        pos2 = _make_position(stance="oppose", round_number=2)
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 1
        assert deltas[0].vector_delta > 0.0
        assert deltas[0].stance_changed
        assert not deltas[0].recommendation_changed
        assert deltas[0].confidence_delta == 0.0

    def test_full_revision_produces_max_delta(self) -> None:
        pos1 = _make_position(
            stance="support", recommendation="adopt", confidence=1.0, round_number=1
        )
        pos2 = _make_position(
            stance="oppose", recommendation="reject", confidence=0.0, round_number=2
        )
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 1
        assert math.isclose(deltas[0].vector_delta, 1.0, rel_tol=1e-9)
        assert deltas[0].stance_changed
        assert deltas[0].recommendation_changed
        assert deltas[0].confidence_delta == -1.0

    def test_confidence_only_change(self) -> None:
        pos1 = _make_position(confidence=0.5, round_number=1)
        pos2 = _make_position(confidence=0.9, round_number=2)
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 1
        assert deltas[0].confidence_delta == 0.4
        assert deltas[0].vector_delta > 0.0
        assert not deltas[0].stance_changed

    def test_mismatched_topics_skipped(self) -> None:
        pos1 = _make_position(topic_id="budget", round_number=1)
        pos2 = _make_position(topic_id="timeline", round_number=2)
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 0

    def test_mismatched_personas_skipped(self) -> None:
        pos1 = _make_position(persona_id="art-director", round_number=1)
        pos2 = _make_position(persona_id="tech-director", round_number=2)
        deltas = _compute_position_deltas([pos2], [pos1])
        assert len(deltas) == 0

    def test_multiple_positions_mixed(self) -> None:
        prev = [
            _make_position("art-director", "budget", "support", "adopt", 0.9, 1),
            _make_position("tech-director", "budget", "oppose", "reject", 0.8, 1),
            _make_position("art-director", "timeline", "neutral", "maintain", 0.5, 1),
        ]
        curr = [
            _make_position("art-director", "budget", "support", "adopt", 0.9, 2),  # no change
            _make_position("tech-director", "budget", "conditional_support", "explore", 0.7, 2),  # big change
            _make_position("art-director", "timeline", "neutral", "maintain", 0.5, 2),  # no change
        ]
        deltas = _compute_position_deltas(curr, prev)
        assert len(deltas) == 3
        # art-director-budget: no change
        assert deltas[0].vector_delta == 0.0
        # tech-director-budget: big change
        assert deltas[1].vector_delta > 0.3
        assert deltas[1].stance_changed
        assert deltas[1].recommendation_changed
        # art-director-timeline: no change
        assert deltas[2].vector_delta == 0.0

    def test_korean_topics_delta(self) -> None:
        prev = [_make_position("아트-디렉터", "예산", "support", "adopt", 0.9, 1)]
        curr = [_make_position("아트-디렉터", "예산", "oppose", "reject", 0.5, 2)]
        deltas = _compute_position_deltas(curr, prev)
        assert len(deltas) == 1
        assert deltas[0].stance_changed


class TestDeltaNorm:
    """Delta norm (RMS of deltas)."""

    def test_all_no_change_delta_norm_1(self) -> None:
        """When all deltas are zero, RMS is 0, inverted = 1.0 (perfect stability)."""
        deltas = [
            PositionDelta("p1", "t1", False, False, 0.0, 0.0, 0.0),
            PositionDelta("p2", "t1", False, False, 0.0, 0.0, 0.0),
        ]
        norm = _compute_delta_norm(deltas)
        assert norm == 1.0

    def test_all_max_delta_delta_norm_0(self) -> None:
        """When all deltas are max, RMS is 1.0, inverted = 0.0 (maximum divergence)."""
        deltas = [
            PositionDelta("p1", "t1", True, True, -1.0, 1.0, 1.0),
            PositionDelta("p2", "t1", True, True, -1.0, 1.0, 1.0),
        ]
        norm = _compute_delta_norm(deltas)
        assert norm == 0.0

    def test_mixed_deltas_intermediate_norm(self) -> None:
        deltas = [
            PositionDelta("p1", "t1", False, False, 0.0, 0.0, 0.0),  # 0
            PositionDelta("p2", "t1", True, True, -1.0, 1.0, 1.0),  # 1.0
        ]
        norm = _compute_delta_norm(deltas)
        # RMS = sqrt((0^2 + 1^2)/2) = sqrt(0.5) ≈ 0.7071
        # norm = 1 - 0.7071 ≈ 0.2929
        expected = 1.0 - math.sqrt(0.5)
        assert math.isclose(norm, expected, rel_tol=1e-9)

    def test_empty_deltas_returns_1_no_prior(self) -> None:
        """No deltas (no matched positions) = cannot assess = max divergence signal."""
        norm = _compute_delta_norm([])
        assert norm == 1.0


# ═════════════════════════════════════════════════════════════════════════
# Topic agreement computation
# ═════════════════════════════════════════════════════════════════════════


class TestTopicAgreement:
    """Per-topic agreement ratio computation."""

    def test_all_agree_ratio_1(self) -> None:
        positions = [
            _make_position("art-director", "budget", "support", "adopt"),
            _make_position("tech-director", "budget", "support", "adopt"),
            _make_position("marketing-lead", "budget", "conditional_support", "adopt"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert len(agreements) == 1
        assert agreements[0].agreement_ratio == 1.0
        assert agreements[0].opposing_pairs == ()
        assert agreements[0].total_pairs == 3  # C(3,2) = 3

    def test_all_oppose_ratio_0(self) -> None:
        positions = [
            _make_position("art-director", "budget", "support", "adopt"),
            _make_position("tech-director", "budget", "oppose", "reject"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert len(agreements) == 1
        assert agreements[0].agreement_ratio == 0.0
        assert len(agreements[0].opposing_pairs) == 1

    def test_single_persona_trivially_agreed(self) -> None:
        positions = [_make_position("art-director", "budget", "support", "adopt")]
        agreements = _compute_topic_agreements(positions)
        assert agreements[0].agreement_ratio == 1.0
        assert agreements[0].total_pairs == 0

    def test_mixed_agreement_partial_ratio(self) -> None:
        positions = [
            _make_position("art-director", "budget", "support", "adopt"),
            _make_position("tech-director", "budget", "oppose", "reject"),
            _make_position("marketing-lead", "budget", "conditional_support", "adopt"),
        ]
        agreements = _compute_topic_agreements(positions)
        # Pairs:
        #   art-tech: support/adopt vs oppose/reject → stance opposing → incompatible
        #   art-marketing: support/adopt vs conditional_support/adopt → compatible
        #   tech-marketing: oppose/reject vs conditional_support/adopt → recs opposing → incompatible
        # Ratio: 1/3 ≈ 0.333
        assert math.isclose(agreements[0].agreement_ratio, 1 / 3, rel_tol=1e-9)
        assert len(agreements[0].opposing_pairs) == 2

    def test_four_personas_two_opposing(self) -> None:
        positions = [
            _make_position("a", "t1", "support", "adopt"),
            _make_position("b", "t1", "oppose", "reject"),
            _make_position("c", "t1", "support", "adopt"),
            _make_position("d", "t1", "oppose", "reject"),
        ]
        agreements = _compute_topic_agreements(positions)
        # Total pairs = 6
        # Compatible: a-c (both support), b-d (both oppose)
        # Incompatible: a-b, a-d, b-c, c-d (cross-support/oppose)
        # Ratio = 2/6 = 0.333
        assert math.isclose(agreements[0].agreement_ratio, 1 / 3, rel_tol=1e-9)
        assert len(agreements[0].opposing_pairs) == 4

    def test_all_neutral_all_compatible(self) -> None:
        positions = [
            _make_position("a", "t1", "neutral", "maintain"),
            _make_position("b", "t1", "neutral", "maintain"),
            _make_position("c", "t1", "neutral", "defer"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert agreements[0].agreement_ratio == 1.0

    def test_two_topics_independent_agreement(self) -> None:
        positions = [
            # Topic budget: all agree
            _make_position("a", "budget", "support", "adopt"),
            _make_position("b", "budget", "support", "adopt"),
            # Topic timeline: disagree
            _make_position("a", "timeline", "support", "adopt"),
            _make_position("b", "timeline", "oppose", "reject"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert len(agreements) == 2
        budget = [ta for ta in agreements if ta.topic_id == "budget"][0]
        timeline = [ta for ta in agreements if ta.topic_id == "timeline"][0]
        assert budget.agreement_ratio == 1.0
        assert timeline.agreement_ratio == 0.0

    def test_increase_maintain_compatible(self) -> None:
        positions = [
            _make_position("a", "t1", "support", "increase"),
            _make_position("b", "t1", "support", "maintain"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert agreements[0].agreement_ratio == 1.0

    def test_increase_decrease_incompatible(self) -> None:
        positions = [
            _make_position("a", "t1", "support", "increase"),
            _make_position("b", "t1", "support", "decrease"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert agreements[0].agreement_ratio == 0.0

    def test_korean_topics_agreement(self) -> None:
        positions = [
            _make_position("아트-디렉터", "예산", "support", "adopt"),
            _make_position("기술-디렉터", "예산", "support", "adopt"),
        ]
        agreements = _compute_topic_agreements(positions)
        assert agreements[0].agreement_ratio == 1.0


class TestAgreementRatio:
    """Overall agreement ratio across topics."""

    def test_single_topic_mean(self) -> None:
        ta = TopicAgreement("t1", 2, 1, 1, 1.0, ())
        ratio = _compute_agreement_ratio((ta,))
        assert ratio == 1.0

    def test_two_topics_mean(self) -> None:
        ta1 = TopicAgreement("t1", 2, 1, 1, 1.0, ())
        ta2 = TopicAgreement("t2", 2, 0, 1, 0.0, (("a", "b"),))
        ratio = _compute_agreement_ratio((ta1, ta2))
        assert ratio == 0.5

    def test_empty_topics_returns_0(self) -> None:
        ratio = _compute_agreement_ratio(())
        assert ratio == 0.0

    def test_three_topics_varied(self) -> None:
        ta1 = TopicAgreement("t1", 2, 1, 1, 1.0, ())
        ta2 = TopicAgreement("t2", 3, 2, 3, 2 / 3, (("a", "b"),))
        ta3 = TopicAgreement("t3", 2, 0, 1, 0.0, (("a", "b"),))
        ratio = _compute_agreement_ratio((ta1, ta2, ta3))
        expected = (1.0 + 2 / 3 + 0.0) / 3
        assert math.isclose(ratio, expected, rel_tol=1e-9)


# ═════════════════════════════════════════════════════════════════════════
# Composite score
# ═════════════════════════════════════════════════════════════════════════


class TestCompositeScore:
    """Composite convergence score computation."""

    def test_perfect_convergence_full_score(self) -> None:
        cfg = ConvergenceConfig()
        score = _compute_composite_score(1.0, 1.0, cfg)
        assert score == 1.0

    def test_complete_divergence_zero_score(self) -> None:
        cfg = ConvergenceConfig()
        score = _compute_composite_score(0.0, 0.0, cfg)
        assert score == 0.0

    def test_default_weights(self) -> None:
        cfg = ConvergenceConfig()  # 0.4 delta, 0.6 agreement
        score = _compute_composite_score(0.5, 0.8, cfg)
        expected = 0.4 * 0.5 + 0.6 * 0.8  # 0.20 + 0.48 = 0.68
        assert math.isclose(score, expected, rel_tol=1e-9)

    def test_custom_weights(self) -> None:
        cfg = ConvergenceConfig(delta_weight=0.3, agreement_weight=0.7)
        score = _compute_composite_score(0.5, 0.8, cfg)
        expected = 0.3 * 0.5 + 0.7 * 0.8  # 0.15 + 0.56 = 0.71
        assert math.isclose(score, expected, rel_tol=1e-9)

    def test_equal_weights(self) -> None:
        cfg = ConvergenceConfig(delta_weight=0.5, agreement_weight=0.5)
        score = _compute_composite_score(0.6, 0.9, cfg)
        expected = 0.5 * 0.6 + 0.5 * 0.9  # 0.3 + 0.45 = 0.75
        assert math.isclose(score, expected, rel_tol=1e-9)


# ═════════════════════════════════════════════════════════════════════════
# Main API: compute_convergence
# ═════════════════════════════════════════════════════════════════════════


class TestComputeConvergence:
    """Main ``compute_convergence`` function."""

    # ── Happy path: complete convergence ────────────────────────────

    def test_complete_convergence_two_rounds(self) -> None:
        """All personas agree and positions are stable between rounds."""
        prev = [
            _make_position("art-director", "budget", "support", "adopt", 0.9, 1),
            _make_position("tech-director", "budget", "support", "adopt", 0.85, 1),
        ]
        curr = [
            _make_position("art-director", "budget", "support", "adopt", 0.9, 2),
            _make_position("tech-director", "budget", "support", "adopt", 0.85, 2),
        ]
        result = compute_convergence(curr, prev)
        assert result.has_converged
        assert result.composite_score >= 0.85
        assert result.convergence_status == "converged"
        assert result.matched_position_count == 2
        assert result.agreement_ratio == 1.0
        assert result.delta_norm == 1.0  # no changes = perfect stability

    def test_convergence_with_stable_positions_high_agreement(self) -> None:
        """3 personas, 2 agree, 1 conditional_support (compatible)."""
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.85, 1),
            _make_position("c", "t1", "conditional_support", "adopt", 0.8, 1),
        ]
        curr = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "support", "adopt", 0.85, 2),
            _make_position("c", "t1", "conditional_support", "adopt", 0.8, 2),
        ]
        result = compute_convergence(curr, prev)
        assert result.has_converged
        assert result.composite_score >= 0.85
        assert result.convergence_status == "converged"

    # ── Complete divergence ─────────────────────────────────────────

    def test_complete_divergence(self) -> None:
        """All personas oppose each other and positions are unstable."""
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.85, 1),
        ]
        curr = [
            _make_position("a", "t1", "oppose", "reject", 0.5, 2),
            _make_position("b", "t1", "oppose", "reject", 0.5, 2),
        ]
        result = compute_convergence(curr, prev)
        assert not result.has_converged
        assert result.composite_score < 0.85
        assert result.agreement_ratio == 1.0  # they agree with each other... in opposition
        # but delta_norm is low because positions changed drastically
        assert result.delta_norm < 0.5

    def test_divergence_both_disagreeing_and_changing(self) -> None:
        """Personas disagree with each other, and positions shift a lot."""
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "neutral", "maintain", 0.5, 1),
        ]
        curr = [
            _make_position("a", "t1", "oppose", "reject", 0.3, 2),
            _make_position("b", "t1", "oppose", "reject", 0.4, 2),
        ]
        result = compute_convergence(curr, prev)
        assert not result.has_converged
        assert result.composite_score < 0.7
        assert result.convergence_status in ("diverging", "near_convergence")

    # ── Near convergence ────────────────────────────────────────────

    def test_near_convergence_just_below_threshold(self) -> None:
        """Score is close to threshold but below."""
        # Craft positions such that composite is just below 0.85
        # agreement_ratio 0.67 (2/3 compatible among 3 personas)
        # delta_norm ~ 0.7 (some movement)
        # composite = 0.4*0.7 + 0.6*0.667 = 0.28 + 0.4 = 0.68 → below threshold
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "oppose", "reject", 0.8, 1),
            _make_position("c", "t1", "conditional_support", "adopt", 0.7, 1),
        ]
        curr = [
            _make_position("a", "t1", "support", "adopt", 0.85, 2),
            _make_position("b", "t1", "conditional_support", "explore", 0.7, 2),  # moved
            _make_position("c", "t1", "conditional_support", "adopt", 0.75, 2),
        ]
        result = compute_convergence(curr, prev)
        assert result.composite_score < 0.85
        assert result.convergence_status in ("diverging", "near_convergence")
        assert not result.has_converged

    # ── Deadlock detection ──────────────────────────────────────────

    def test_deadlock_all_opposing_no_movement(self) -> None:
        """Everyone is opposed, no one moves — deadlock."""
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "oppose", "reject", 0.9, 1),
        ]
        curr = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "oppose", "reject", 0.9, 2),
        ]
        result = compute_convergence(curr, prev)
        assert not result.has_converged
        assert result.agreement_ratio == 0.0
        assert result.convergence_status == "deadlocked"

    def test_deadlock_multiple_topics(self) -> None:
        """Two topics, both deadlocked."""
        prev = [
            _make_position("a", "budget", "support", "adopt", 0.9, 1),
            _make_position("b", "budget", "oppose", "reject", 0.9, 1),
            _make_position("a", "timeline", "support", "adopt", 0.9, 1),
            _make_position("b", "timeline", "oppose", "reject", 0.9, 1),
        ]
        curr = [
            _make_position("a", "budget", "support", "adopt", 0.9, 2),
            _make_position("b", "budget", "oppose", "reject", 0.9, 2),
            _make_position("a", "timeline", "support", "adopt", 0.9, 2),
            _make_position("b", "timeline", "oppose", "reject", 0.9, 2),
        ]
        result = compute_convergence(curr, prev)
        assert not result.has_converged
        assert result.agreement_ratio == 0.0
        assert result.convergence_status == "deadlocked"

    # ── Threshold boundary testing ──────────────────────────────────

    def test_exactly_at_threshold_converges(self) -> None:
        """Composite score exactly at threshold means convergence."""
        # Build positions where composite = 0.85 exactly
        # Need agreement=1.0 and delta_norm such that 0.4*delta + 0.6*1.0 = 0.85
        # => 0.4*delta = 0.25 => delta = 0.625
        # delta_norm=0.625 means RMS=0.375 → all deltas ~ 0.375
        # Use one persona, one topic, delta=0.375
        prev = [_make_position("a", "t1", "support", "adopt", 0.9, 1)]
        # To get delta=0.375: change confidence enough
        # vector_delta raw = 0.375 * 3 = 1.125
        # confidence change of 1.125 (from 0.9 to -0.225... not possible)
        # Let's instead use a combination: leave stance same, change rec slightly
        # Actually, let's just build positions that produce the desired score
        # agreement = 1.0 (all compatible), delta_norm = 0.625
        # To get delta_norm=0.625 we need RMS of normalized_deltas = 0.375
        # This is hard to hit exactly with real encoding. Let's use a
        # custom threshold instead to test the boundary.
        prev_pos = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.9, 1),
        ]
        curr_pos = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "support", "adopt", 0.9, 2),
        ]
        # With no changes, delta_norm=1.0, agreement=1.0, composite=1.0
        result = compute_convergence(curr_pos, prev_pos, threshold=1.0)
        assert result.has_converged
        assert result.composite_score >= 1.0
        assert result.convergence_status == "converged"

    def test_just_above_threshold_converges(self) -> None:
        prev_pos = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.9, 1),
        ]
        curr_pos = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "support", "adopt", 0.9, 2),
        ]
        # Score is 1.0, threshold 0.85 — converges
        result = compute_convergence(curr_pos, prev_pos)
        assert result.has_converged
        assert result.composite_score >= 0.85

    # ── Single position / insufficient data ─────────────────────────

    def test_single_position_no_prior(self) -> None:
        pos = [_make_position()]
        result = compute_convergence(pos, None)
        # Single persona trivially agrees (agreement_ratio=1.0, delta_norm=1.0)
        # composite = 0.4*1.0 + 0.6*1.0 = 1.0 >= 0.85 → converges
        assert result.has_converged
        assert result.delta_norm == 1.0  # no prior round
        assert result.matched_position_count == 0

    def test_single_persona_single_topic(self) -> None:
        prev = [_make_position(round_number=1)]
        curr = [_make_position(round_number=2)]
        result = compute_convergence(curr, prev)
        assert result.has_converged  # Single persona trivially agrees
        assert result.agreement_ratio == 1.0
        assert result.matched_position_count == 1

    # ── Empty input validation ──────────────────────────────────────

    def test_empty_current_positions_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            compute_convergence([], [])

    def test_wrong_type_raises(self) -> None:
        with pytest.raises(TypeError, match="RoundPosition"):
            compute_convergence([{"persona_id": "test"}], [])  # type: ignore[list-item]

    def test_wrong_type_in_previous_raises(self) -> None:
        curr = [_make_position()]
        with pytest.raises(TypeError, match="RoundPosition"):
            compute_convergence(curr, [{"persona_id": "test"}])  # type: ignore[list-item]

    # ── Custom threshold override ───────────────────────────────────

    def test_custom_threshold_override(self) -> None:
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "oppose", "reject", 0.9, 1),
        ]
        curr = [
            _make_position("a", "t1", "conditional_support", "explore", 0.7, 2),
            _make_position("b", "t1", "conditional_support", "explore", 0.7, 2),
        ]
        # In curr: both conditional_support/explore → agreement=1.0, some delta
        # Natural composite ~ 0.77-0.82, below 0.85
        result_strict = compute_convergence(curr, prev, threshold=0.85)
        assert not result_strict.has_converged
        # But with a lower threshold (and matching min_agreement_ratio)...
        cfg = ConvergenceConfig(threshold=0.7, min_agreement_ratio=0.5)
        result_lenient = compute_convergence(curr, prev, config=cfg)
        assert result_lenient.has_converged

    # ── Custom config ───────────────────────────────────────────────

    def test_custom_config(self) -> None:
        """Custom config with different weights."""
        cfg = ConvergenceConfig(
            threshold=0.7, delta_weight=0.2, agreement_weight=0.8
        )
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.9, 1),
        ]
        curr = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "support", "adopt", 0.9, 2),
        ]
        result = compute_convergence(curr, prev, config=cfg)
        assert result.has_converged
        assert result.config == cfg

    # ── Multi-topic with partial convergence ────────────────────────

    def test_partial_convergence_multi_topic(self) -> None:
        prev = [
            # Topic budget - agreed, stable
            _make_position("a", "budget", "support", "adopt", 0.9, 1),
            _make_position("b", "budget", "support", "adopt", 0.85, 1),
            # Topic timeline - disagree, stable
            _make_position("a", "timeline", "support", "adopt", 0.9, 1),
            _make_position("b", "timeline", "oppose", "reject", 0.85, 1),
        ]
        curr = [
            _make_position("a", "budget", "support", "adopt", 0.9, 2),
            _make_position("b", "budget", "support", "adopt", 0.85, 2),
            _make_position("a", "timeline", "support", "adopt", 0.9, 2),
            _make_position("b", "timeline", "oppose", "reject", 0.85, 2),
        ]
        result = compute_convergence(curr, prev)
        # Budget: agreement=1.0, Timeline: agreement=0.0 → overall=0.5
        # delta_norm=1.0 (no changes) → composite = 0.4*1.0 + 0.6*0.5 = 0.4 + 0.3 = 0.7
        assert result.agreement_ratio == 0.5
        assert result.delta_norm == 1.0
        assert math.isclose(result.composite_score, 0.7, rel_tol=1e-9)
        assert not result.has_converged  # 0.7 < 0.85
        assert result.matched_position_count == 4

    # ── Convergence with movement toward agreement ──────────────────

    def test_movement_toward_agreement(self) -> None:
        """Persona B moves from oppose/reject toward conditional_support/explore.

        Good-faith movement shows convergence progress, even if not yet above
        the default 0.85 threshold (composite ≈ 0.815 with the large shift).
        """
        prev = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "oppose", "reject", 0.9, 1),
        ]
        curr = [
            _make_position("a", "t1", "support", "adopt", 0.9, 2),
            _make_position("b", "t1", "conditional_support", "explore", 0.7, 2),
        ]
        result = compute_convergence(curr, prev)
        # Agreement in curr: support/adopt vs conditional_support/explore → compatible
        # agreement_ratio = 1.0
        assert result.agreement_ratio == 1.0
        # delta_norm: B changed significantly from oppose/reject to conditional_support/explore
        assert result.delta_norm < 1.0
        # composite ≈ 0.815, just below default 0.85 threshold
        # convergence_status should reflect near-convergence or diverging
        assert result.composite_score > 0.75
        assert result.convergence_status in ("near_convergence", "diverging")
        # Not fully converged yet, but shows good progress
        assert not result.has_converged

    # ── Korean position sets ────────────────────────────────────────

    def test_korean_positions_convergence(self) -> None:
        prev = [
            _make_position("아트-디렉터", "예산-할당", "support", "adopt", 0.9, 1),
            _make_position("기술-디렉터", "예산-할당", "support", "adopt", 0.85, 1),
        ]
        curr = [
            _make_position("아트-디렉터", "예산-할당", "support", "adopt", 0.9, 2),
            _make_position("기술-디렉터", "예산-할당", "support", "adopt", 0.85, 2),
        ]
        result = compute_convergence(curr, prev)
        assert result.has_converged
        assert result.composite_score >= 0.85

    def test_korean_deadlock(self) -> None:
        prev = [
            _make_position("아트-디렉터", "예산", "support", "adopt", 0.9, 1),
            _make_position("기술-디렉터", "예산", "oppose", "reject", 0.9, 1),
        ]
        curr = [
            _make_position("아트-디렉터", "예산", "support", "adopt", 0.9, 2),
            _make_position("기술-디렉터", "예산", "oppose", "reject", 0.9, 2),
        ]
        result = compute_convergence(curr, prev)
        assert result.convergence_status == "deadlocked"

    # ── Result structural integrity ─────────────────────────────────

    def test_result_has_all_fields(self) -> None:
        result = compute_convergence([_make_position()], [_make_position()])
        assert isinstance(result.position_deltas, tuple)
        assert isinstance(result.delta_norm, float)
        assert isinstance(result.topic_agreements, tuple)
        assert isinstance(result.agreement_ratio, float)
        assert isinstance(result.composite_score, float)
        assert isinstance(result.threshold, float)
        assert isinstance(result.has_converged, bool)
        assert isinstance(result.convergence_status, str)
        assert result.convergence_status in (
            "converged", "near_convergence", "diverging",
            "deadlocked", "insufficient_data",
        )
        assert isinstance(result.matched_position_count, int)
        assert isinstance(result.total_current_positions, int)
        assert isinstance(result.config, ConvergenceConfig)
        assert isinstance(result.detail, str)
        assert len(result.detail) > 0

    def test_result_immutability(self) -> None:
        result = compute_convergence([_make_position()], [_make_position()])
        with pytest.raises(Exception):
            result.composite_score = 0.5  # type: ignore[misc]

    # ── Large synthetic position sets ───────────────────────────────

    def test_large_position_set_7_personas_3_topics(self) -> None:
        """Simulate a full meeting with 7 personas across 3 topics."""
        personas = [
            "ceo", "cto", "cfo", "art-director", "tech-director",
            "marketing-lead", "operations-lead",
        ]
        topics = ["budget-allocation", "timeline-planning", "risk-assessment"]
        stances = ["support", "conditional_support", "neutral",
                   "alternative_proposal"]

        import random
        rng = random.Random(42)

        def random_stance() -> str:
            return rng.choice(stances)

        def random_rec() -> str:
            return rng.choice(["adopt", "explore", "maintain", "defer"])

        prev: list[RoundPosition] = []
        curr: list[RoundPosition] = []
        for p in personas:
            for t in topics:
                stance = random_stance()
                rec = random_rec()
                conf = rng.uniform(0.6, 1.0)
                prev.append(RoundPosition(p, t, stance, rec, conf, 1))
                # In current round, sometimes shift position (simulate convergence)
                new_stance = stance
                new_rec = rec
                new_conf = conf
                if rng.random() < 0.3:  # 30% chance of movement
                    new_stance = rng.choice(stances)
                    new_rec = rng.choice(["adopt", "explore", "maintain", "defer"])
                    new_conf = rng.uniform(0.6, 1.0)
                curr.append(RoundPosition(p, t, new_stance, new_rec, new_conf, 2))

        result = compute_convergence(curr, prev)
        # Should have all 21 positions matched
        assert result.matched_position_count == 21
        assert result.total_current_positions == 21
        assert len(result.topic_agreements) == 3
        assert 0.0 <= result.composite_score <= 1.0


# ═════════════════════════════════════════════════════════════════════════
# Injection mechanism
# ═════════════════════════════════════════════════════════════════════════


class TestInjection:
    """Injection of custom delta/agreement/score computers."""

    def teardown_method(self) -> None:
        reset_injectables()

    def test_inject_delta_computer(self) -> None:
        def custom_deltas(
            curr: Sequence[RoundPosition], prev: Sequence[RoundPosition]
        ) -> tuple[PositionDelta, ...]:
            return (
                PositionDelta("test", "t1", False, False, 0.0, 0.5, 0.5),
            )

        inject_delta_computer(custom_deltas)
        prev = [_make_position(round_number=1)]
        curr = [_make_position(round_number=2)]
        result = compute_convergence(curr, prev)
        assert result.position_deltas[0].vector_delta == 0.5
        assert result.delta_norm == 0.5  # 1 - 0.5

    def test_inject_agreement_computer(self) -> None:
        def custom_agreements(
            curr: Sequence[RoundPosition],
        ) -> tuple[TopicAgreement, ...]:
            return (
                TopicAgreement("t1", 2, 1, 1, 0.5, (("a", "b"),)),
            )

        inject_agreement_computer(custom_agreements)
        result = compute_convergence([_make_position()], [_make_position()])
        assert result.agreement_ratio == 0.5

    def test_inject_score_computer(self) -> None:
        def custom_score(
            delta_norm: float, agreement_ratio: float, config: ConvergenceConfig
        ) -> float:
            return 0.42

        inject_score_computer(custom_score)
        result = compute_convergence([_make_position()], [_make_position()])
        assert result.composite_score == 0.42

    def test_reset_restores_defaults(self) -> None:
        # Inject then reset
        inject_score_computer(lambda d, a, c: 0.99)
        result1 = compute_convergence([_make_position()], [_make_position()])
        assert result1.composite_score == 0.99

        reset_injectables()
        result2 = compute_convergence([_make_position()], [_make_position()])
        assert result2.composite_score != 0.99
        assert result2.composite_score == 1.0  # perfect convergence


# ═════════════════════════════════════════════════════════════════════════
# convergence_from_conflict_resolutions helper
# ═════════════════════════════════════════════════════════════════════════


class TestConvergenceFromResolutions:
    """convergence_from_conflict_resolutions convenience function."""

    def test_all_resolved_high_confidence(self) -> None:
        result = convergence_from_conflict_resolutions(
            resolved_count=10, total_conflicts=10, avg_resolution_confidence=1.0
        )
        assert result.has_converged
        assert result.agreement_ratio == 1.0
        assert result.composite_score == 1.0
        assert result.convergence_status == "converged"

    def test_all_resolved_low_confidence(self) -> None:
        result = convergence_from_conflict_resolutions(
            resolved_count=10, total_conflicts=10, avg_resolution_confidence=0.5
        )
        # composite = 0.7*1.0 + 0.3*0.5 = 0.70 + 0.15 = 0.85
        assert math.isclose(result.composite_score, 0.85, rel_tol=1e-9)
        assert result.has_converged  # exactly at threshold

    def test_partial_resolution(self) -> None:
        result = convergence_from_conflict_resolutions(
            resolved_count=5, total_conflicts=10, avg_resolution_confidence=0.8
        )
        # composite = 0.7*0.5 + 0.3*0.8 = 0.35 + 0.24 = 0.59
        assert math.isclose(result.composite_score, 0.59, rel_tol=1e-9)
        assert not result.has_converged

    def test_zero_conflicts_raises(self) -> None:
        with pytest.raises(ValueError, match="total_conflicts must be positive"):
            convergence_from_conflict_resolutions(0, 0)

    def test_custom_threshold(self) -> None:
        result = convergence_from_conflict_resolutions(
            resolved_count=3, total_conflicts=10,
            avg_resolution_confidence=0.6, threshold=0.3,
        )
        assert result.threshold == 0.3
        assert result.has_converged  # composite ≈ 0.39 > 0.3

    def test_result_structure(self) -> None:
        result = convergence_from_conflict_resolutions(5, 5, 0.9)
        assert result.position_deltas == ()
        assert result.delta_norm == 1.0
        assert result.topic_agreements == ()
        assert result.matched_position_count == 0
        assert result.total_current_positions == 5
        assert "Derived from conflict resolutions" in result.detail


# ═════════════════════════════════════════════════════════════════════════
# Integration-like scenarios
# ═════════════════════════════════════════════════════════════════════════


class TestIntegrationScenarios:
    """Multi-round, multi-persona scenarios mimicking real meetings."""

    def test_three_round_progression(self) -> None:
        """Simulate a typical 3-round meeting with improving convergence."""
        personas = ["ceo", "cto", "cfo", "art-director"]
        topic = "product-launch-strategy"

        # Round 1: diverse opinions, some opposition
        round1 = [
            _make_position("ceo", topic, "support", "adopt", 0.95, 1),
            _make_position("cto", topic, "conditional_support", "explore", 0.7, 1),
            _make_position("cfo", topic, "oppose", "defer", 0.85, 1),
            _make_position("art-director", topic, "support", "adopt", 0.8, 1),
        ]

        # Round 2: CFO moved to conditional_support, CTO still exploring
        round2 = [
            _make_position("ceo", topic, "support", "adopt", 0.9, 2),
            _make_position("cto", topic, "support", "adopt", 0.75, 2),  # moved!
            _make_position("cfo", topic, "conditional_support", "explore", 0.7, 2),  # moved!
            _make_position("art-director", topic, "support", "adopt", 0.85, 2),
        ]

        # Round 3: almost everyone on board
        round3 = [
            _make_position("ceo", topic, "support", "adopt", 0.9, 3),
            _make_position("cto", topic, "support", "adopt", 0.85, 3),
            _make_position("cfo", topic, "conditional_support", "adopt", 0.75, 3),  # moved!
            _make_position("art-director", topic, "support", "adopt", 0.9, 3),
        ]

        # R1→R2: significant movement, still some disagreement
        r1r2 = compute_convergence(round2, round1)
        assert r1r2.agreement_ratio == 1.0  # all compatible now!
        assert r1r2.delta_norm < 1.0  # there was movement
        # Agreement is 1.0, delta is high due to movement
        # composite = 0.6*1.0 + 0.4*delta_norm
        # With significant movement, should be >= 0.85 if delta_norm >= 0.625
        assert r1r2.composite_score > 0.8
        assert r1r2.has_converged

        # R2→R3: minor movement, full agreement
        r2r3 = compute_convergence(round3, round2)
        assert r2r3.agreement_ratio == 1.0
        assert r2r3.delta_norm > r1r2.delta_norm  # more stable than r1→r2
        assert r2r3.has_converged
        assert r2r3.composite_score >= r1r2.composite_score  # better convergence

    def test_tie_break_round_after_deadlock(self) -> None:
        """Simulate deadlock after 3 rounds requiring tie-break."""
        topic = "critical-hiring-decision"
        # CEO vs CTO deadlocked for 3 rounds
        round1 = [
            _make_position("ceo", topic, "support", "adopt", 0.95, 1),
            _make_position("cto", topic, "oppose", "reject", 0.95, 1),
        ]
        round2 = [
            _make_position("ceo", topic, "support", "adopt", 0.95, 2),
            _make_position("cto", topic, "oppose", "reject", 0.95, 2),
        ]
        round3 = [
            _make_position("ceo", topic, "support", "adopt", 0.95, 3),
            _make_position("cto", topic, "oppose", "reject", 0.95, 3),
        ]

        r1r2 = compute_convergence(round2, round1)
        assert r1r2.convergence_status == "deadlocked"

        r2r3 = compute_convergence(round3, round2)
        assert r2r3.convergence_status == "deadlocked"

        # All three pairwise comparisons show deadlock
        assert not r1r2.has_converged
        assert not r2r3.has_converged

    def test_no_previous_round_first_round_convergence(self) -> None:
        """First round has no previous round to compare against."""
        round1 = [
            _make_position("a", "t1", "support", "adopt", 0.9, 1),
            _make_position("b", "t1", "support", "adopt", 0.85, 1),
        ]
        result = compute_convergence(round1, None)
        assert result.delta_norm == 1.0  # no prior, default
        assert result.matched_position_count == 0
        assert result.agreement_ratio == 1.0  # current agreement still computed
        # composite = 0.4*1.0 + 0.6*1.0 = 1.0 → converges
        assert result.has_converged
