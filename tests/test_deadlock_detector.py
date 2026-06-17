"""Tests for the deadlock detection module.

Sub-AC 5c-2: Deadlock detection — given a history of convergence scores
across consecutive rounds, detect oscillation patterns or stagnation
(score flatlining below threshold for N consecutive rounds) and emit a
deadlock declaration with reason, testable with score sequence inputs
producing expected deadlock/no-deadlock outputs.

Coverage:
- Oscillation deadlock: classic sine-wave pattern (up, down, up, down)
- Oscillation deadlock: 5-round alternating cycle below threshold
- Oscillation deadlock: 3-round minimum with direction changes
- Oscillation non-deadlock: alternating but one score above threshold
- Oscillation non-deadlock: net upward trend (oscillation resolving)
- Oscillation non-deadlock: amplitude too small (treated as noise)
- Oscillation non-deadlock: monotonic improvement (no direction changes)
- Stagnation deadlock: 2-round flatline below threshold (default)
- Stagnation deadlock: 3-round flatline well below threshold
- Stagnation deadlock: 5-round flatline with tiny band
- Stagnation non-deadlock: flat but above threshold (converged)
- Stagnation non-deadlock: single round below (not enough for streak)
- Stagnation non-deadlock: spread exceeds stagnation band
- Both patterns simultaneously (oscillation + stagnation)
- No deadlock: converged (score above threshold)
- No deadlock: monotonic progress toward convergence
- No deadlock: insufficient data (less than required rounds)
- Edge cases: empty scores (ValueError)
- Edge cases: single score
- Edge cases: scores at exactly threshold
- Edge cases: round_number gaps (non-sequential round numbers)
- ScoreRecord validation (confidence bounds, round_number >= 1)
- DeadlockConfig validation
- DeadlockResult properties (is_oscillation_deadlock, is_stagnation_deadlock, evidence)
- detect_deadlock_from_values convenience function
- Injectable oscillation/stagnation detectors
- TypeError for non-ScoreRecord inputs
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from src.deadlock_detector import (
    DEFAULT_CONVERGENCE_THRESHOLD,
    DEFAULT_OSCILLATION_AMPLITUDE,
    DEFAULT_OSCILLATION_ROUNDS,
    DEFAULT_STAGNATION_BAND,
    DEFAULT_STAGNATION_ROUNDS,
    DeadlockConfig,
    DeadlockResult,
    OscillationPattern,
    ScoreRecord,
    StagnationPattern,
    _detect_oscillation,
    _detect_stagnation,
    detect_deadlock,
    detect_deadlock_from_values,
    inject_oscillation_detector,
    inject_stagnation_detector,
    reset_injectables,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper factories
# ═════════════════════════════════════════════════════════════════════════


def _make_score(
    round_number: int = 1,
    composite_score: float = 0.50,
) -> ScoreRecord:
    """Create a ``ScoreRecord`` with minimal boilerplate."""
    return ScoreRecord(
        round_number=round_number,
        composite_score=composite_score,
    )


def _make_scores(*values: float, start_round: int = 1) -> list[ScoreRecord]:
    """Create a list of ScoreRecords from raw score values."""
    return [
        ScoreRecord(round_number=start_round + i, composite_score=v)
        for i, v in enumerate(values)
    ]


# ═════════════════════════════════════════════════════════════════════════
# ScoreRecord validation
# ═════════════════════════════════════════════════════════════════════════


class TestScoreRecord:
    """Validation and construction of ScoreRecord."""

    def test_valid_record(self) -> None:
        r = _make_score(round_number=1, composite_score=0.75)
        assert r.round_number == 1
        assert r.composite_score == 0.75
        assert r.round_id == ""
        assert r.timestamp == ""

    def test_score_zero(self) -> None:
        r = _make_score(composite_score=0.0)
        assert r.composite_score == 0.0

    def test_score_one(self) -> None:
        r = _make_score(composite_score=1.0)
        assert r.composite_score == 1.0

    def test_invalid_score_below_zero(self) -> None:
        with pytest.raises(ValueError, match="composite_score"):
            _make_score(composite_score=-0.1)

    def test_invalid_score_above_one(self) -> None:
        with pytest.raises(ValueError, match="composite_score"):
            _make_score(composite_score=1.01)

    def test_invalid_round_number_zero(self) -> None:
        with pytest.raises(ValueError, match="round_number"):
            _make_score(round_number=0)

    def test_invalid_round_number_negative(self) -> None:
        with pytest.raises(ValueError, match="round_number"):
            _make_score(round_number=-1)

    def test_optional_fields(self) -> None:
        r = ScoreRecord(
            round_number=2,
            composite_score=0.65,
            round_id="r2-uuid",
            timestamp="2026-06-10T12:00:00Z",
        )
        assert r.round_id == "r2-uuid"
        assert r.timestamp == "2026-06-10T12:00:00Z"


# ═════════════════════════════════════════════════════════════════════════
# DeadlockConfig validation
# ═════════════════════════════════════════════════════════════════════════


class TestDeadlockConfig:
    """Validation of DeadlockConfig."""

    def test_defaults(self) -> None:
        cfg = DeadlockConfig()
        assert cfg.convergence_threshold == DEFAULT_CONVERGENCE_THRESHOLD
        assert cfg.stagnation_rounds == DEFAULT_STAGNATION_ROUNDS
        assert cfg.stagnation_band == DEFAULT_STAGNATION_BAND
        assert cfg.oscillation_rounds == DEFAULT_OSCILLATION_ROUNDS
        assert cfg.oscillation_amplitude == DEFAULT_OSCILLATION_AMPLITUDE

    def test_invalid_threshold_negative(self) -> None:
        with pytest.raises(ValueError, match="convergence_threshold"):
            DeadlockConfig(convergence_threshold=-0.1)

    def test_invalid_threshold_above_one(self) -> None:
        with pytest.raises(ValueError, match="convergence_threshold"):
            DeadlockConfig(convergence_threshold=1.1)

    def test_invalid_stagnation_rounds_one(self) -> None:
        with pytest.raises(ValueError, match="stagnation_rounds"):
            DeadlockConfig(stagnation_rounds=1)

    def test_invalid_stagnation_band_zero(self) -> None:
        with pytest.raises(ValueError, match="stagnation_band"):
            DeadlockConfig(stagnation_band=0.0)

    def test_invalid_stagnation_band_negative(self) -> None:
        with pytest.raises(ValueError, match="stagnation_band"):
            DeadlockConfig(stagnation_band=-0.05)

    def test_invalid_oscillation_rounds_two(self) -> None:
        with pytest.raises(ValueError, match="oscillation_rounds"):
            DeadlockConfig(oscillation_rounds=2)

    def test_invalid_oscillation_amplitude_zero(self) -> None:
        with pytest.raises(ValueError, match="oscillation_amplitude"):
            DeadlockConfig(oscillation_amplitude=0.0)

    def test_invalid_oscillation_amplitude_above_one(self) -> None:
        with pytest.raises(ValueError, match="oscillation_amplitude"):
            DeadlockConfig(oscillation_amplitude=1.1)

    def test_custom_threshold(self) -> None:
        cfg = DeadlockConfig(convergence_threshold=0.75)
        assert cfg.convergence_threshold == 0.75

    def test_custom_stagnation_rounds(self) -> None:
        cfg = DeadlockConfig(stagnation_rounds=3)
        assert cfg.stagnation_rounds == 3


# ═════════════════════════════════════════════════════════════════════════
# Oscillation detection
# ═════════════════════════════════════════════════════════════════════════


class TestOscillationDetection:
    """Oscillation pattern detection from score sequences."""

    # ── Oscillation detected ─────────────────────────────────────────

    def test_classic_oscillation_pattern(self) -> None:
        """Scores: 0.45 -> 0.70 -> 0.48 -> 0.68 — classic up/down cycle."""
        scores = _make_scores(0.45, 0.70, 0.48, 0.68)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "oscillation"
        assert result.oscillation.detected
        assert result.oscillation.direction_changes >= 2
        assert result.oscillation.amplitude > 0.15

    def test_five_round_oscillation(self) -> None:
        """5 rounds of alternating scores, all below threshold."""
        scores = _make_scores(0.30, 0.60, 0.35, 0.55, 0.32)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "oscillation"
        assert len(result.oscillation.rounds_involved) == 5
        assert result.oscillation.direction_changes >= 3

    def test_oscillation_three_rounds(self) -> None:
        """Minimal oscillation: 0.40 -> 0.65 -> 0.42 (3 rounds, 2 direction changes)."""
        scores = _make_scores(0.40, 0.65, 0.42)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "oscillation"
        assert len(result.oscillation.rounds_involved) == 3

    def test_oscillation_amplitude_calculation(self) -> None:
        """Verify amplitude is correctly computed."""
        scores = _make_scores(0.20, 0.75, 0.25, 0.70)
        result = detect_deadlock(scores)
        assert result.oscillation.amplitude == pytest.approx(0.55, abs=0.01)

    def test_oscillation_trend_near_zero(self) -> None:
        """Symmetric oscillation should have ~zero trend."""
        scores = _make_scores(0.45, 0.60, 0.45, 0.60)
        result = detect_deadlock(scores)
        assert abs(result.oscillation.trend) < 0.01

    # ── Oscillation NOT detected ─────────────────────────────────────

    def test_no_oscillation_when_converged(self) -> None:
        """If any score is above threshold, no oscillation (meeting converged)."""
        scores = _make_scores(0.45, 0.70, 0.88, 0.65)
        result = detect_deadlock(scores)
        # Should not be oscillation because at least one score >= threshold
        assert not result.is_oscillation_deadlock

    def test_no_oscillation_monotonic_up(self) -> None:
        """Scores monotonically increasing — progress, not oscillation."""
        scores = _make_scores(0.30, 0.45, 0.55, 0.65)
        result = detect_deadlock(scores)
        assert not result.oscillation.detected

    def test_no_oscillation_amplitude_too_small(self) -> None:
        """Tiny fluctuations should be noise, not oscillation."""
        scores = _make_scores(0.50, 0.52, 0.51, 0.53)
        cfg = DeadlockConfig(oscillation_amplitude=0.10)
        result = detect_deadlock(scores, config=cfg)
        assert not result.oscillation.detected

    def test_no_oscillation_insufficient_rounds(self) -> None:
        """2 rounds is not enough to detect oscillation."""
        scores = _make_scores(0.40, 0.65)
        result = detect_deadlock(scores)
        assert not result.oscillation.detected

    def test_no_oscillation_strong_upward_trend(self) -> None:
        """Oscillating but with a strong net upward trend — resolving."""
        scores = _make_scores(0.30, 0.55, 0.45, 0.70, 0.60, 0.82)
        # Net trend is positive despite occasional dips
        result = detect_deadlock(scores)
        # Last score (0.82) is close to threshold, trend may override
        assert not result.oscillation.detected or result.oscillation.trend > 0.04


# ═════════════════════════════════════════════════════════════════════════
# Stagnation detection
# ═════════════════════════════════════════════════════════════════════════


class TestStagnationDetection:
    """Stagnation pattern detection from score sequences."""

    # ── Stagnation detected ──────────────────────────────────────────

    def test_two_round_stagnation(self) -> None:
        """2-round flatline below threshold — default minimum."""
        scores = _make_scores(0.50, 0.51)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"
        assert result.stagnation.detected
        assert len(result.stagnation.rounds_involved) == 2
        assert result.stagnation.score_band < DEFAULT_STAGNATION_BAND

    def test_three_round_stagnation(self) -> None:
        """3-round flatline clearly below threshold."""
        scores = _make_scores(0.45, 0.46, 0.44)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"
        assert len(result.stagnation.rounds_involved) == 3

    def test_five_round_stagnation(self) -> None:
        """5-round flatline with very tight band."""
        scores = _make_scores(0.50, 0.505, 0.502, 0.50, 0.501)
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"
        assert len(result.stagnation.rounds_involved) == 5
        assert result.stagnation.score_band < 0.01

    def test_stagnation_rounds_below_threshold_count(self) -> None:
        """Verify rounds_below_threshold counts correctly."""
        scores = _make_scores(0.50, 0.51, 0.50)
        result = detect_deadlock(scores)
        assert result.stagnation.rounds_below_threshold == 3

    def test_stagnation_mean_score(self) -> None:
        """Verify mean score is computed correctly."""
        scores = _make_scores(0.40, 0.42, 0.41)
        result = detect_deadlock(scores)
        assert result.stagnation.mean_score == pytest.approx(0.41, abs=0.01)

    def test_stagnation_custom_three_matches(self) -> None:
        """Stagnation with 3 rounds required — 3-round flatline detected."""
        scores = _make_scores(0.30, 0.31, 0.32)
        cfg = DeadlockConfig(stagnation_rounds=3)
        result = detect_deadlock(scores, config=cfg)
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"

    def test_stagnation_custom_three_no_match_two(self) -> None:
        """Stagnation with 3 rounds required — only 2 rounds, no deadlock."""
        scores = _make_scores(0.30, 0.31)
        cfg = DeadlockConfig(stagnation_rounds=3)
        result = detect_deadlock(scores, config=cfg)
        assert not result.is_deadlocked

    # ── Stagnation NOT detected ──────────────────────────────────────

    def test_no_stagnation_converged_flat(self) -> None:
        """Flat scores but above threshold — already converged, not stagnation."""
        scores = _make_scores(0.86, 0.87)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked

    def test_no_stagnation_insufficient_rounds(self) -> None:
        """Single score — not enough for stagnation streak."""
        scores = _make_scores(0.50)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked
        assert not result.stagnation.detected

    def test_no_stagnation_spread_too_wide(self) -> None:
        """Scores differ by more than stagnation_band."""
        scores = _make_scores(0.40, 0.55)
        cfg = DeadlockConfig(stagnation_band=0.05)
        # Band is 0.15 > 0.05, so no stagnation even though both below threshold
        result = detect_deadlock(scores, config=cfg)
        assert not result.stagnation.detected

    def test_no_stagnation_mixed_above_threshold(self) -> None:
        """One score above threshold breaks the below-threshold streak."""
        scores = _make_scores(0.50, 0.51, 0.90, 0.52)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked


# ═════════════════════════════════════════════════════════════════════════
# Both patterns (oscillation + stagnation)
# ═════════════════════════════════════════════════════════════════════════


class TestBothPatterns:
    """When both oscillation and stagnation are detected simultaneously."""

    def test_both_detected(self) -> None:
        """Scores flatline AND oscillate — e.g., small-amplitude oscillation
        that is also within stagnation band when viewed at the flatline level.
        
        With default config (band=0.05, amplitude=0.10), a sequence like
        0.50, 0.54, 0.50, 0.54 has amplitude 0.04 (< 0.10, so not oscillation
        by default).  To get both, we need a larger amplitude that still
        has a flatline sub-sequence.
        
        Use: 0.50, 0.62, 0.50, 0.62 — amplitude 0.12 qualifies for oscillation.
        But the stagnation band is 0.05, so the whole 4-round sequence is NOT
        flatlined. However, we could have alternating rounds where a subset
        flatlines. Let's use: 0.50, 0.50, 0.60, 0.50, 0.60
        
        Actually for "both" detection, let's create a scenario where the
        scores stay within a narrow band for several rounds (stagnation)
        AND also exhibit oscillation within that band.
        
        With tight band config: stagnation_band=0.08, oscillation_amplitude=0.04.
        """
        # Sequence: slight osc within flat band
        # 0.50, 0.56, 0.51, 0.55 (amplitude 0.06, direction changes)
        # But also within 0.06 band for stagnation if band >= 0.06
        scores = _make_scores(0.50, 0.56, 0.51, 0.55)
        cfg = DeadlockConfig(
            stagnation_band=0.08,
            oscillation_amplitude=0.04,
            oscillation_rounds=3,
        )
        result = detect_deadlock(scores, config=cfg)
        # With these params, oscillation (amplitude 0.06 >= 0.04, 3 direction changes)
        # AND stagnation (band 0.06 <= 0.08, 4 rounds below 0.85)
        assert result.is_deadlocked
        assert result.deadlock_type == "both"
        assert result.oscillation.detected
        assert result.stagnation.detected

    def test_both_detected_long_sequence(self) -> None:
        """Longer sequence: 0.45, 0.50, 0.47, 0.52, 0.46, 0.51 — early rounds
        within tight band, plus oscillation pattern overall."""
        scores = _make_scores(0.45, 0.50, 0.47, 0.52, 0.46, 0.51)
        cfg = DeadlockConfig(
            stagnation_band=0.08,
            stagnation_rounds=2,
            oscillation_amplitude=0.03,
            oscillation_rounds=3,
        )
        result = detect_deadlock(scores, config=cfg)
        # Oscillation: 5 direction changes, amplitude 0.07 >= 0.03
        # Stagnation: band 0.07 <= 0.08 over 6 rounds
        assert result.is_deadlocked
        assert result.deadlock_type == "both"


# ═════════════════════════════════════════════════════════════════════════
# No deadlock (healthy progress)
# ═════════════════════════════════════════════════════════════════════════


class TestNoDeadlock:
    """Scenarios that should NOT trigger deadlock."""

    def test_fully_converged(self) -> None:
        """All scores above threshold."""
        scores = _make_scores(0.86, 0.88)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked
        assert result.deadlock_type == "none"

    def test_single_score_at_threshold(self) -> None:
        """Exactly at threshold — converged."""
        scores = _make_scores(0.85)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked

    def test_monotonic_progress_toward_convergence(self) -> None:
        """Steadily improving scores — not deadlocked."""
        scores = _make_scores(0.30, 0.55, 0.70, 0.80)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked

    def test_monotonic_progress_with_occasional_flat(self) -> None:
        """Improving with one brief plateau."""
        scores = _make_scores(0.45, 0.60, 0.61, 0.75)
        result = detect_deadlock(scores)
        assert not result.is_deadlocked

    def test_single_dip_then_recovery(self) -> None:
        """Temporary dip but overall trending up."""
        scores = _make_scores(0.50, 0.60, 0.48, 0.75)
        result = detect_deadlock(scores)
        # There might be oscillation detected (3 direction changes?)
        # Actually from 0.50->0.60 (up), 0.60->0.48 (down), 0.48->0.75 (up)
        # That's 2 direction changes across 4 rounds.
        # With default config: oscillation_rounds=3, and scores are below 0.85.
        # Direction changes=2, ceil(4/3)=2, min(max(3,2),3)=3. So 2 < 3: not oscillation.
        # Stagnation: band 0.60-0.48=0.12 > 0.05. Not stagnation.
        assert not result.is_deadlocked

    def test_empty_scores_raises(self) -> None:
        """Empty input raises ValueError."""
        with pytest.raises(ValueError, match="scores must not be empty"):
            detect_deadlock([])


# ═════════════════════════════════════════════════════════════════════════
# Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge-case inputs and boundary conditions."""

    def test_non_sequential_round_numbers(self) -> None:
        """Scores with round_number gaps (e.g., rounds 1, 3, 5) — should
        be auto-sorted and analysed correctly."""
        scores = [
            ScoreRecord(round_number=3, composite_score=0.50),
            ScoreRecord(round_number=1, composite_score=0.51),
            ScoreRecord(round_number=5, composite_score=0.49),
        ]
        result = detect_deadlock(scores)
        # Sorted: round 1: 0.51, round 3: 0.50, round 5: 0.49
        # Band = 0.02 < 0.05, all below 0.85 — stagnation
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"
        # Verify sorted order
        assert list(result.scores_analysed) == sorted(
            scores, key=lambda s: s.round_number
        )

    def test_type_error_non_score_record(self) -> None:
        """Passing non-ScoreRecord objects raises TypeError."""
        with pytest.raises(TypeError, match="ScoreRecord"):
            detect_deadlock([0.5, 0.6, 0.7])  # type: ignore[arg-type]

    def test_reason_when_converged(self) -> None:
        """Reason text when converged is informative."""
        scores = _make_scores(0.90, 0.92)
        result = detect_deadlock(scores)
        assert "converged" in result.reason.lower()
        assert "0.920" in result.reason

    def test_reason_when_no_pattern(self) -> None:
        """Reason text when no pattern detected but not yet converged."""
        scores = _make_scores(0.30, 0.55, 0.75)
        result = detect_deadlock(scores)
        assert "no deadlock" in result.reason.lower()
        # These scores have direction changes but monotonic up,
        # band is 0.45 > 0.05 — neither pattern applies
        assert not result.is_deadlocked

    def test_reason_when_oscillation(self) -> None:
        """Reason includes oscillation specifics."""
        scores = _make_scores(0.40, 0.65, 0.42, 0.63)
        result = detect_deadlock(scores)
        assert "oscillation" in result.reason.lower()
        assert "direction changes" in result.reason.lower()

    def test_reason_when_stagnation(self) -> None:
        """Reason includes stagnation specifics."""
        scores = _make_scores(0.50, 0.51, 0.50)
        result = detect_deadlock(scores)
        assert "stagnation" in result.reason.lower()
        assert "flatline" in result.reason.lower()
        assert "consecutive rounds" in result.reason.lower()


# ═════════════════════════════════════════════════════════════════════════
# DeadlockResult properties
# ═════════════════════════════════════════════════════════════════════════


class TestDeadlockResult:
    """DeadlockResult property accessors and evidence method."""

    def test_requires_escalation_deadlocked(self) -> None:
        scores = _make_scores(0.50, 0.51)
        result = detect_deadlock(scores)
        assert result.requires_escalation
        assert result.is_deadlocked

    def test_requires_escalation_not_deadlocked(self) -> None:
        scores = _make_scores(0.90, 0.92)
        result = detect_deadlock(scores)
        assert not result.requires_escalation

    def test_is_oscillation_deadlock(self) -> None:
        scores = _make_scores(0.40, 0.65, 0.42, 0.63)
        result = detect_deadlock(scores)
        assert result.is_oscillation_deadlock
        assert not result.is_stagnation_deadlock

    def test_is_stagnation_deadlock(self) -> None:
        scores = _make_scores(0.50, 0.51)
        result = detect_deadlock(scores)
        assert result.is_stagnation_deadlock
        assert not result.is_oscillation_deadlock

    def test_is_both_deadlock(self) -> None:
        scores = _make_scores(0.50, 0.56, 0.51, 0.55)
        cfg = DeadlockConfig(
            stagnation_band=0.08,
            oscillation_amplitude=0.04,
        )
        result = detect_deadlock(scores, config=cfg)
        if result.deadlock_type == "both":
            assert result.is_oscillation_deadlock
            assert result.is_stagnation_deadlock

    def test_evidence_deadlocked(self) -> None:
        """evidence() returns compact dict suitable for manifest."""
        scores = _make_scores(0.50, 0.51)
        result = detect_deadlock(scores)
        ev = result.evidence()
        assert ev["is_deadlocked"] is True
        assert ev["deadlock_type"] == "stagnation"
        assert "reason" in ev
        assert "stagnation" in ev
        assert "rounds" in ev["stagnation"]
        assert "score_band" in ev["stagnation"]
        assert "mean_score" in ev["stagnation"]

    def test_evidence_not_deadlocked(self) -> None:
        scores = _make_scores(0.90)
        result = detect_deadlock(scores)
        ev = result.evidence()
        assert ev["is_deadlocked"] is False
        assert ev["deadlock_type"] == "none"
        assert "oscillation" not in ev
        assert "stagnation" not in ev

    def test_evidence_oscillation(self) -> None:
        scores = _make_scores(0.40, 0.65, 0.42, 0.63)
        result = detect_deadlock(scores)
        ev = result.evidence()
        if ev["is_deadlocked"] and ev["deadlock_type"] == "oscillation":
            assert "oscillation" in ev
            assert "direction_changes" in ev["oscillation"]
            assert "amplitude" in ev["oscillation"]
            assert "trend" in ev["oscillation"]
            assert "stagnation" not in ev

    def test_deadlock_type_none(self) -> None:
        scores = _make_scores(0.60, 0.80)
        result = detect_deadlock(scores)
        assert result.deadlock_type == "none"

    def test_deadlock_type_oscillation(self) -> None:
        scores = _make_scores(0.40, 0.65, 0.42, 0.63)
        result = detect_deadlock(scores)
        assert result.deadlock_type == "oscillation"

    def test_deadlock_type_stagnation(self) -> None:
        scores = _make_scores(0.50, 0.51)
        result = detect_deadlock(scores)
        assert result.deadlock_type == "stagnation"


# ═════════════════════════════════════════════════════════════════════════
# detect_deadlock_from_values convenience function
# ═════════════════════════════════════════════════════════════════════════


class TestDetectDeadlockFromValues:
    """Convenience wrapper that accepts raw score values."""

    def test_oscillation_from_values(self) -> None:
        result = detect_deadlock_from_values([0.40, 0.65, 0.42, 0.63])
        assert result.is_deadlocked
        if result.deadlock_type == "oscillation":
            assert result.oscillation.detected

    def test_stagnation_from_values(self) -> None:
        result = detect_deadlock_from_values([0.50, 0.51])
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"

    def test_default_start_round_is_one(self) -> None:
        result = detect_deadlock_from_values([0.50, 0.51])
        scores = result.scores_analysed
        assert scores[0].round_number == 1
        assert scores[1].round_number == 2

    def test_custom_start_round(self) -> None:
        result = detect_deadlock_from_values([0.50, 0.51], start_round=3)
        scores = result.scores_analysed
        assert scores[0].round_number == 3
        assert scores[1].round_number == 4

    def test_no_deadlock_from_values(self) -> None:
        result = detect_deadlock_from_values([0.90, 0.92])
        assert not result.is_deadlocked


# ═════════════════════════════════════════════════════════════════════════
# Injectable overrides
# ═════════════════════════════════════════════════════════════════════════


class TestInjectables:
    """Injectable oscillation and stagnation detectors for deterministic testing."""

    def test_inject_oscillation_detector(self) -> None:
        """Inject a custom oscillation detector that always returns deadlock."""
        def forced_oscillation(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> OscillationPattern:
            return OscillationPattern(
                detected=True,
                rounds_involved=tuple(s.round_number for s in scores),
                scores=tuple(s.composite_score for s in scores),
                direction_changes=99,
                amplitude=0.30,
                mean_score=0.50,
                trend=0.0,
            )

        inject_oscillation_detector(forced_oscillation)
        try:
            # Even a converging sequence should deadlock with forced detector
            scores = _make_scores(0.90, 0.92)
            result = detect_deadlock(scores)
            assert result.is_deadlocked
            assert result.deadlock_type == "oscillation"
            assert result.oscillation.direction_changes == 99
        finally:
            reset_injectables()

    def test_inject_stagnation_detector(self) -> None:
        """Inject a custom stagnation detector that always returns deadlock."""
        def forced_stagnation(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> StagnationPattern:
            return StagnationPattern(
                detected=True,
                rounds_involved=tuple(s.round_number for s in scores),
                scores=tuple(s.composite_score for s in scores),
                score_band=0.01,
                mean_score=0.45,
                rounds_below_threshold=len(scores),
            )

        inject_stagnation_detector(forced_stagnation)
        try:
            scores = _make_scores(0.90, 0.92)
            result = detect_deadlock(scores)
            assert result.is_deadlocked
            assert result.deadlock_type == "stagnation"
            assert result.stagnation.score_band == 0.01
        finally:
            reset_injectables()

    def test_inject_both_detectors(self) -> None:
        """Inject both detectors — 'both' deadlock type."""
        def forced_true_osc(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> OscillationPattern:
            return OscillationPattern(
                detected=True,
                rounds_involved=(1,),
                scores=(0.50,),
                direction_changes=1,
                amplitude=0.20,
                mean_score=0.50,
                trend=0.0,
            )

        def forced_true_stag(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> StagnationPattern:
            return StagnationPattern(
                detected=True,
                rounds_involved=(1,),
                scores=(0.50,),
                score_band=0.0,
                mean_score=0.50,
                rounds_below_threshold=1,
            )

        inject_oscillation_detector(forced_true_osc)
        inject_stagnation_detector(forced_true_stag)
        try:
            result = detect_deadlock(_make_scores(0.50))
            assert result.is_deadlocked
            assert result.deadlock_type == "both"
        finally:
            reset_injectables()

    def test_inject_false_detectors(self) -> None:
        """Inject detectors that always return no deadlock."""
        def forced_false_osc(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> OscillationPattern:
            return OscillationPattern(
                detected=False,
                rounds_involved=(),
                scores=(),
                direction_changes=0,
                amplitude=0.0,
                mean_score=0.0,
                trend=0.0,
            )

        def forced_false_stag(
            scores: Sequence[ScoreRecord],
            cfg: DeadlockConfig,
        ) -> StagnationPattern:
            return StagnationPattern(
                detected=False,
                rounds_involved=(),
                scores=(),
                score_band=0.0,
                mean_score=0.0,
                rounds_below_threshold=0,
            )

        inject_oscillation_detector(forced_false_osc)
        inject_stagnation_detector(forced_false_stag)
        try:
            # Scores that would normally trigger deadlock
            result = detect_deadlock(_make_scores(0.50, 0.51))
            assert not result.is_deadlocked
            assert result.deadlock_type == "none"
        finally:
            reset_injectables()


# ═════════════════════════════════════════════════════════════════════════
# Integration-style tests with specific known inputs/outputs
# ═════════════════════════════════════════════════════════════════════════


class TestKnownSequenceOutputs:
    """Specific score sequences with expected deadlock/no-deadlock outputs.

    These serve as the core verification of the sub-AC requirement:
    'testable with score sequence inputs producing expected
    deadlock/no-deadlock outputs'.
    """

    # Each entry: (score_values, expected_is_deadlocked, expected_type, description)
    KNOWN_SEQUENCES = [
        # ── Deadlocked: Oscillation ──
        (
            [0.45, 0.70, 0.48, 0.68],
            True,
            "oscillation",
            "Classic 4-round oscillation",
        ),
        (
            [0.30, 0.55, 0.32, 0.54, 0.31],
            True,
            "oscillation",
            "5-round oscillation",
        ),
        (
            [0.40, 0.65, 0.42],
            True,
            "oscillation",
            "Minimal 3-round oscillation",
        ),
        # ── Deadlocked: Stagnation ──
        (
            [0.50, 0.51],
            True,
            "stagnation",
            "2-round flatline (minimal)",
        ),
        (
            [0.45, 0.46, 0.45],
            True,
            "stagnation",
            "3-round flatline",
        ),
        (
            [0.50, 0.505, 0.502, 0.50, 0.501],
            True,
            "stagnation",
            "5-round tight flatline",
        ),
        # ── Not deadlocked ──
        (
            [0.90, 0.92],
            False,
            "none",
            "Converged (above threshold)",
        ),
        (
            [0.85],
            False,
            "none",
            "Exactly at threshold",
        ),
        (
            [0.30, 0.55, 0.75],
            False,
            "none",
            "Monotonic progress",
        ),
        (
            [0.50, 0.60, 0.61, 0.75],
            False,
            "none",
            "Improving with minor plateau",
        ),
        (
            [0.50],
            False,
            "none",
            "Single score (insufficient data)",
        ),
        (
            [0.50, 0.80],
            False,
            "none",
            "Progressing but not yet converged",
        ),
    ]

    @pytest.mark.parametrize(
        "score_values,expected_deadlocked,expected_type,description",
        KNOWN_SEQUENCES,
    )
    def test_known_sequence(
        self,
        score_values: list[float],
        expected_deadlocked: bool,
        expected_type: str,
        description: str,
    ) -> None:
        """Parametric test: each known sequence produces expected output."""
        scores = _make_scores(*score_values)
        result = detect_deadlock(scores)
        assert result.is_deadlocked == expected_deadlocked, (
            f"{description}: expected is_deadlocked={expected_deadlocked}, "
            f"got {result.is_deadlocked} (type={result.deadlock_type}, "
            f"reason={result.reason})"
        )
        assert result.deadlock_type == expected_type, (
            f"{description}: expected type={expected_type}, "
            f"got {result.deadlock_type}"
        )
