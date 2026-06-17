"""Comprehensive tests for threshold gate evaluation — Sub-AC 6.3.2.

Test coverage:
- Basic within/exceeded decisions at known scores and thresholds
- Boundary-inclusive behaviour (score == threshold → within)
- Score marginally above/below threshold
- Known scores at boundary values (0.0, 0.30, 1.0)
- Out-of-range scores (negative, > 1.0) → clamped
- Out-of-range thresholds → clamped
- Zero threshold with zero score
- Ceiling threshold with ceiling score
- GateResult dataclass properties (is_boundary, to_dict)
- margin calculation correctness
- was_clamped detection for all clamping scenarios
- Invertibility: exceeded == not within
- gate_from_divergence_report convenience wrapper
- Type validation (non-numeric inputs, bool)
- Large float values and precision edge cases
"""

from __future__ import annotations

import math

import pytest

from src.threshold_gate import (
    DEFAULT_THRESHOLD,
    MAX_VALID_SCORE,
    MIN_VALID_SCORE,
    GateResult,
    evaluate_threshold,
    gate_from_divergence_report,
)


# ═════════════════════════════════════════════════════════════════════════
# Tests: basic within/exceeded decisions
# ═════════════════════════════════════════════════════════════════════════


class TestBasicDecisions:
    """Basic within/exceeded decisions at known scores and thresholds."""

    def test_score_below_threshold_is_within(self) -> None:
        """Score below threshold → within."""
        result = evaluate_threshold(0.25, 0.30)
        assert result.exceeded is False
        assert result.within is True

    def test_score_above_threshold_is_exceeded(self) -> None:
        """Score above threshold → exceeded."""
        result = evaluate_threshold(0.35, 0.30)
        assert result.exceeded is True
        assert result.within is False

    def test_score_equals_threshold_is_within_inclusive(self) -> None:
        """Score == threshold → within (boundary-inclusive)."""
        result = evaluate_threshold(0.30, 0.30)
        assert result.exceeded is False
        assert result.within is True
        assert result.is_boundary is True

    def test_default_threshold_is_0_30(self) -> None:
        """Default threshold when not specified is 0.30."""
        result = evaluate_threshold(0.25)
        assert result.effective_threshold == 0.30

    def test_score_far_below_threshold(self) -> None:
        """Score far below threshold → within."""
        result = evaluate_threshold(0.0, 0.50)
        assert result.within is True
        assert result.exceeded is False

    def test_score_far_above_threshold(self) -> None:
        """Score far above threshold → exceeded."""
        result = evaluate_threshold(0.95, 0.10)
        assert result.exceeded is True
        assert result.within is False


# ═════════════════════════════════════════════════════════════════════════
# Tests: boundary behaviour (the Sub-AC 6.3.2 contract)
# ═════════════════════════════════════════════════════════════════════════


class TestBoundaryBehaviour:
    """Boundary-inclusive and edge-case threshold behaviour."""

    # ── score == threshold (boundary inclusive) ──────────────────────

    def test_boundary_at_0_0(self) -> None:
        """score=0.0, threshold=0.0 → within."""
        result = evaluate_threshold(0.0, 0.0)
        assert result.within is True
        assert result.exceeded is False
        assert result.is_boundary is True
        assert result.margin == 0.0

    def test_boundary_at_0_30(self) -> None:
        """score=0.30, threshold=0.30 → within."""
        result = evaluate_threshold(0.30, 0.30)
        assert result.within is True
        assert result.exceeded is False
        assert result.is_boundary is True

    def test_boundary_at_1_0(self) -> None:
        """score=1.0, threshold=1.0 → within."""
        result = evaluate_threshold(1.0, 1.0)
        assert result.within is True
        assert result.exceeded is False
        assert result.is_boundary is True

    def test_boundary_at_custom(self) -> None:
        """score=0.85, threshold=0.85 → within."""
        result = evaluate_threshold(0.85, 0.85)
        assert result.within is True
        assert result.is_boundary is True

    # ── just above / just below ──────────────────────────────────────

    def test_just_below_threshold(self) -> None:
        """Score 0.299999, threshold 0.30 → within."""
        result = evaluate_threshold(0.299999, 0.30)
        assert result.within is True

    def test_just_above_threshold(self) -> None:
        """Score 0.300001, threshold 0.30 → exceeded."""
        result = evaluate_threshold(0.300001, 0.30)
        assert result.exceeded is True

    def test_epsilon_above_zero_threshold(self) -> None:
        """Score epsilon, threshold 0.0 → exceeded."""
        result = evaluate_threshold(1e-10, 0.0)
        assert result.exceeded is True

    def test_epsilon_below_one_threshold(self) -> None:
        """Score 0.999999999, threshold 1.0 → within."""
        result = evaluate_threshold(0.999999999, 1.0)
        assert result.within is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: known scores at key thresholds
# ═════════════════════════════════════════════════════════════════════════


class TestKnownScoresAtKeyThresholds:
    """Test with known scores and threshold boundaries as required by AC."""

    # Threshold = 0.0 (strictest — any positive score exceeds)
    def test_threshold_zero_score_zero(self) -> None:
        result = evaluate_threshold(0.0, 0.0)
        assert result.within is True
        assert result.margin == 0.0

    def test_threshold_zero_score_positive(self) -> None:
        result = evaluate_threshold(0.01, 0.0)
        assert result.exceeded is True
        assert result.margin > 0.0

    def test_threshold_zero_score_one(self) -> None:
        result = evaluate_threshold(1.0, 0.0)
        assert result.exceeded is True
        assert result.margin == 1.0

    # Threshold = 0.30 (default)
    def test_default_threshold_various_scores(self) -> None:
        # Known scores mapped to expected decisions
        cases = [
            (0.00, False, True),   # within
            (0.15, False, True),   # within
            (0.29, False, True),   # within
            (0.30, False, True),   # within (boundary)
            (0.31, True, False),   # exceeded
            (0.50, True, False),   # exceeded
            (0.75, True, False),   # exceeded
            (1.00, True, False),   # exceeded
        ]
        for score, expected_exceeded, expected_within in cases:
            result = evaluate_threshold(score, 0.30)
            assert result.exceeded == expected_exceeded, (
                f"score={score}: expected exceeded={expected_exceeded}, "
                f"got {result.exceeded}"
            )
            assert result.within == expected_within, (
                f"score={score}: expected within={expected_within}, "
                f"got {result.within}"
            )

    # Threshold = 0.85 (convergence threshold)
    def test_convergence_threshold_various_scores(self) -> None:
        cases = [
            (0.84, False, True),
            (0.85, False, True),   # boundary
            (0.86, True, False),
        ]
        for score, expected_exceeded, expected_within in cases:
            result = evaluate_threshold(score, 0.85)
            assert result.exceeded == expected_exceeded, (
                f"score={score} vs 0.85"
            )
            assert result.within == expected_within

    # Threshold = 1.0 (most permissive — only score 1.0 is within)
    def test_threshold_one_score_one(self) -> None:
        result = evaluate_threshold(1.0, 1.0)
        assert result.within is True

    def test_threshold_one_score_below(self) -> None:
        result = evaluate_threshold(0.99, 1.0)
        assert result.within is True
        assert result.exceeded is False

    def test_threshold_one_score_below_small(self) -> None:
        result = evaluate_threshold(0.0, 1.0)
        assert result.within is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: out-of-range scores (clamping)
# ═════════════════════════════════════════════════════════════════════════


class TestOutOfRangeScores:
    """Scores outside [0.0, 1.0] are clamped."""

    def test_negative_score_clamped_to_zero(self) -> None:
        result = evaluate_threshold(-0.5, 0.30)
        assert result.score == -0.5          # original preserved
        assert result.effective_score == 0.0  # clamped
        assert result.was_clamped is True
        assert result.within is True         # 0.0 <= 0.30

    def test_score_above_one_clamped_to_one(self) -> None:
        result = evaluate_threshold(1.5, 0.30)
        assert result.score == 1.5
        assert result.effective_score == 1.0
        assert result.was_clamped is True
        assert result.exceeded is True       # 1.0 > 0.30

    def test_very_negative_score(self) -> None:
        result = evaluate_threshold(-999.0, 0.30)
        assert result.effective_score == 0.0
        assert result.was_clamped is True

    def test_very_large_score(self) -> None:
        result = evaluate_threshold(1e6, 0.30)
        assert result.effective_score == 1.0
        assert result.was_clamped is True

    def test_negative_score_at_threshold_zero(self) -> None:
        """Clamped to 0.0, threshold is 0.0 → within (boundary)."""
        result = evaluate_threshold(-0.1, 0.0)
        assert result.effective_score == 0.0
        assert result.within is True
        assert result.is_boundary is True

    def test_above_one_score_at_threshold_one(self) -> None:
        """Clamped to 1.0, threshold is 1.0 → within (boundary)."""
        result = evaluate_threshold(2.0, 1.0)
        assert result.effective_score == 1.0
        assert result.within is True
        assert result.is_boundary is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: out-of-range thresholds (clamping)
# ═════════════════════════════════════════════════════════════════════════


class TestOutOfRangeThresholds:
    """Thresholds outside [0.0, 1.0] are clamped."""

    def test_negative_threshold_clamped_to_zero(self) -> None:
        result = evaluate_threshold(0.0, -0.50)
        assert result.threshold == -0.50
        assert result.effective_threshold == 0.0
        assert result.was_clamped is True

    def test_threshold_above_one_clamped_to_one(self) -> None:
        result = evaluate_threshold(0.5, 2.0)
        assert result.threshold == 2.0
        assert result.effective_threshold == 1.0
        assert result.was_clamped is True

    def test_both_out_of_range(self) -> None:
        """Both score and threshold out of range → both clamped."""
        result = evaluate_threshold(-0.5, 2.0)
        assert result.effective_score == 0.0
        assert result.effective_threshold == 1.0
        assert result.was_clamped is True
        # 0.0 <= 1.0 → within
        assert result.within is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: margin calculation
# ═════════════════════════════════════════════════════════════════════════


class TestMarginCalculation:
    """Verify margin correctness."""

    def test_margin_positive_when_exceeded(self) -> None:
        result = evaluate_threshold(0.50, 0.30)
        assert result.margin == 0.20

    def test_margin_negative_when_within(self) -> None:
        result = evaluate_threshold(0.10, 0.30)
        assert result.margin == -0.20

    def test_margin_zero_at_boundary(self) -> None:
        result = evaluate_threshold(0.30, 0.30)
        assert result.margin == 0.0

    def test_margin_precision(self) -> None:
        """Margin should be rounded to 10 decimal places."""
        result = evaluate_threshold(0.333333333333, 0.30)
        assert abs(result.margin - 0.0333333333) < 1e-9


# ═════════════════════════════════════════════════════════════════════════
# Tests: GateResult dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestGateResultDataclass:
    """GateResult dataclass properties."""

    def test_exceeded_and_within_are_complements(self) -> None:
        """exceeded == not within for all cases."""
        test_cases = [
            (0.0, 0.0),
            (0.0, 0.30),
            (0.30, 0.30),
            (0.50, 0.30),
            (1.0, 0.30),
            (0.85, 0.85),
            (-0.5, 0.30),
            (1.5, 0.30),
            (0.25, -0.1),
            (0.25, 1.5),
        ]
        for score, threshold in test_cases:
            result = evaluate_threshold(score, threshold)
            assert result.exceeded == (not result.within), (
                f"score={score}, threshold={threshold}: "
                f"exceeded={result.exceeded}, within={result.within}"
            )

    def test_is_boundary_true_only_at_equality(self) -> None:
        """is_boundary True only when effective_score == effective_threshold."""
        assert evaluate_threshold(0.30, 0.30).is_boundary is True
        assert evaluate_threshold(0.0, 0.0).is_boundary is True
        assert evaluate_threshold(1.0, 1.0).is_boundary is True
        assert evaluate_threshold(0.299999, 0.30).is_boundary is False
        assert evaluate_threshold(0.300001, 0.30).is_boundary is False

    def test_is_boundary_with_clamping(self) -> None:
        """is_boundary respects clamped values, not originals."""
        # score=-0.1 clamped to 0.0, threshold=0.0 → boundary
        result = evaluate_threshold(-0.1, 0.0)
        assert result.effective_score == 0.0
        assert result.effective_threshold == 0.0
        assert result.is_boundary is True

    def test_to_dict_structure(self) -> None:
        result = evaluate_threshold(0.25, 0.30)
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["score"] == 0.25
        assert d["effective_score"] == 0.25
        assert d["threshold"] == 0.30
        assert d["effective_threshold"] == 0.30
        assert d["exceeded"] is False
        assert d["within"] is True
        assert d["margin"] == -0.05
        assert d["was_clamped"] is False

    def test_to_dict_with_clamping(self) -> None:
        result = evaluate_threshold(-0.5, 2.0)
        d = result.to_dict()
        assert d["score"] == -0.5
        assert d["effective_score"] == 0.0
        assert d["threshold"] == 2.0
        assert d["effective_threshold"] == 1.0
        assert d["was_clamped"] is True

    def test_frozen(self) -> None:
        """GateResult is immutable."""
        result = evaluate_threshold(0.50, 0.30)
        with pytest.raises(Exception):
            result.exceeded = False  # type: ignore[misc]

    def test_creating_directly(self) -> None:
        """GateResult can be constructed directly."""
        gr = GateResult(
            score=0.35,
            effective_score=0.35,
            threshold=0.30,
            effective_threshold=0.30,
            exceeded=True,
            within=False,
            margin=0.05,
            was_clamped=False,
        )
        assert gr.exceeded is True
        assert gr.within is False
        assert gr.margin == 0.05
        assert gr.was_clamped is False


# ═════════════════════════════════════════════════════════════════════════
# Tests: type validation
# ═════════════════════════════════════════════════════════════════════════


class TestTypeValidation:
    """Input type validation."""

    def test_none_score_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="score must be a number"):
            evaluate_threshold(None, 0.30)  # type: ignore[arg-type]

    def test_string_score_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="score must be a number"):
            evaluate_threshold("0.5", 0.30)  # type: ignore[arg-type]

    def test_list_score_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="score must be a number"):
            evaluate_threshold([0.5], 0.30)  # type: ignore[arg-type]

    def test_bool_score_raises_type_error(self) -> None:
        """bool is a subclass of int but should be rejected."""
        with pytest.raises(TypeError, match="not bool"):
            evaluate_threshold(True, 0.30)  # type: ignore[arg-type]

    def test_bool_threshold_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="not bool"):
            evaluate_threshold(0.50, False)  # type: ignore[arg-type]

    def test_none_threshold_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="threshold must be a number"):
            evaluate_threshold(0.50, None)  # type: ignore[arg-type]

    def test_string_threshold_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="threshold must be a number"):
            evaluate_threshold(0.50, "0.3")  # type: ignore[arg-type]

    def test_valid_int_inputs(self) -> None:
        """int inputs should be accepted (0, 1)."""
        result = evaluate_threshold(0, 1)
        assert result.within is True

    def test_valid_float_inputs(self) -> None:
        """Standard float inputs should work."""
        result = evaluate_threshold(0.5, 0.3)
        assert result.exceeded is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: gate_from_divergence_report convenience wrapper
# ═════════════════════════════════════════════════════════════════════════


class TestGateFromDivergenceReport:
    """gate_from_divergence_report wrapper behaves identically."""

    def test_wrapper_matches_evaluate_threshold(self) -> None:
        """Wrapper produces same result as direct evaluate_threshold."""
        divergence_score = 0.35
        threshold = 0.30

        direct = evaluate_threshold(divergence_score, threshold)
        wrapped = gate_from_divergence_report(divergence_score, threshold)

        assert direct.exceeded == wrapped.exceeded
        assert direct.within == wrapped.within
        assert direct.margin == wrapped.margin

    def test_wrapper_defaults_to_default_threshold(self) -> None:
        result = gate_from_divergence_report(0.25)
        assert result.effective_threshold == DEFAULT_THRESHOLD

    def test_wrapper_handles_clamping(self) -> None:
        result = gate_from_divergence_report(-0.1, 0.30)
        assert result.was_clamped is True
        assert result.within is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: Sub-AC 6.3.2 contract verification
# ═════════════════════════════════════════════════════════════════════════


class TestSubAC632Contract:
    """Verify the module fulfills the Sub-AC 6.3.2 contract."""

    def test_accepts_divergence_score_and_threshold(self) -> None:
        """Accepts a divergence score and a configured threshold value."""
        result = evaluate_threshold(score=0.42, threshold=0.35)
        assert isinstance(result, GateResult)
        assert result.exceeded is True

    def test_returns_binary_exceed_within_decision(self) -> None:
        """Returns a binary exceed/within decision."""
        result = evaluate_threshold(0.20, 0.30)
        assert isinstance(result.exceeded, bool)
        assert isinstance(result.within, bool)
        assert result.exceeded != result.within  # always opposite

    def test_testable_with_known_scores_and_thresholds(self) -> None:
        """Testable with known scores and threshold boundaries."""
        # Score 0.0 at threshold 0.0 → within
        r1 = evaluate_threshold(0.0, 0.0)
        assert r1.within is True

        # Score 1.0 at threshold 0.30 → exceeded
        r2 = evaluate_threshold(1.0, 0.30)
        assert r2.exceeded is True

        # Score 0.30 at threshold 0.30 → within (boundary)
        r3 = evaluate_threshold(0.30, 0.30)
        assert r3.within is True
        assert r3.is_boundary is True

        # Score 1.0 at threshold 1.0 → within (ceiling boundary)
        r4 = evaluate_threshold(1.0, 1.0)
        assert r4.within is True

    def test_edge_case_zero_threshold(self) -> None:
        """Edge case: threshold = 0.0."""
        # Score 0.0 → within
        assert evaluate_threshold(0.0, 0.0).within is True
        # Any positive score → exceeded
        assert evaluate_threshold(1e-6, 0.0).exceeded is True

    def test_edge_case_ceiling_threshold(self) -> None:
        """Edge case: threshold = 1.0."""
        assert evaluate_threshold(0.0, 1.0).within is True
        assert evaluate_threshold(0.5, 1.0).within is True
        assert evaluate_threshold(1.0, 1.0).within is True

    def test_edge_case_negative_score(self) -> None:
        """Edge case: negative score → clamped to 0.0."""
        result = evaluate_threshold(-0.3, 0.30)
        assert result.was_clamped is True
        assert result.effective_score == 0.0
        assert result.within is True

    def test_edge_case_score_above_one(self) -> None:
        """Edge case: score > 1.0 → clamped to 1.0."""
        result = evaluate_threshold(2.0, 0.30)
        assert result.was_clamped is True
        assert result.effective_score == 1.0
        assert result.exceeded is True

    def test_constants_exported(self) -> None:
        """Required constants are exported and have correct values."""
        assert DEFAULT_THRESHOLD == 0.30
        assert MIN_VALID_SCORE == 0.0
        assert MAX_VALID_SCORE == 1.0
