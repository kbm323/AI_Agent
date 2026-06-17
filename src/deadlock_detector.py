"""Deadlock detection from multi-round convergence score history.

Sub-AC 5c-2: Given a history of convergence scores across consecutive
rounds, detect oscillation patterns or stagnation (score flatlining below
threshold for N consecutive rounds) and emit a deadlock declaration with
reason, testable with score sequence inputs producing expected
deadlock/no-deadlock outputs.

Architecture
------------
The deadlock detector sits late in the meeting lifecycle — after at least
2 rounds of convergence scores have been computed (via
``convergence_metric.compute_convergence``).  It receives the score
history and performs two independent analyses:

1. **Oscillation detection** — Identifies back-and-forth patterns where
   scores cycle between two levels without a net upward trend.  An
   oscillating meeting is one where personas keep revisiting the same
   disagreement without making progress.

2. **Stagnation detection** — Identifies flatlining where scores remain
   within a narrow band below the convergence threshold for N consecutive
   rounds.  A stagnated meeting has stopped making meaningful progress.

Either pattern (or both simultaneously) constitutes a deadlock.  The
module emits a ``DeadlockResult`` with the deadlock verdict, type, reason,
and supporting evidence.

The module is pure-in-memory (no filesystem I/O), fully testable with
hand-crafted score sequences, and follows the immutable dataclass patterns
of ``conflict_detector.py``, ``resolution_decision.py``, and
``convergence_metric.py``.

Usage::

    from src.deadlock_detector import (
        ScoreRecord,
        DeadlockConfig,
        DeadlockResult,
        detect_deadlock,
        DEFAULT_STAGNATION_ROUNDS,
        DEFAULT_CONVERGENCE_THRESHOLD,
    )

    scores = [
        ScoreRecord(round_number=1, composite_score=0.45),
        ScoreRecord(round_number=2, composite_score=0.48),
        ScoreRecord(round_number=3, composite_score=0.46),
    ]

    result = detect_deadlock(scores)
    print(f"Deadlocked: {result.is_deadlocked}")
    print(f"Type: {result.deadlock_type}")
    print(f"Reason: {result.reason}")
"""

from __future__ import annotations

import dataclasses
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

# ═════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════

DEFAULT_CONVERGENCE_THRESHOLD: float = 0.85
"""Default convergence threshold matching the exit conditions.

A score >= this threshold means the meeting has converged and is not
deadlocked regardless of other patterns."""

DEFAULT_STAGNATION_ROUNDS: int = 2
"""Default number of consecutive rounds below threshold for stagnation.

A sequence of scores that stays within a narrow band for this many
consecutive rounds while remaining below the convergence threshold
triggers a stagnation deadlock."""

DEFAULT_STAGNATION_BAND: float = 0.05
"""Default maximum difference between scores to consider them 'flat'.

When consecutive scores differ by at most this value, they are
considered part of the same flatline."""

DEFAULT_OSCILLATION_ROUNDS: int = 3
"""Default minimum number of rounds for oscillation detection.

An oscillation pattern requires at least this many rounds of alternating
score direction without a net upward trend."""

DEFAULT_OSCILLATION_AMPLITUDE: float = 0.10
"""Default minimum peak-to-trough amplitude to qualify as oscillation.

Smaller fluctuations are considered noise, not meaningful oscillation."""

# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ScoreRecord:
    """A single convergence score from one meeting round.

    Attributes:
        round_number: Which round this score belongs to (1-based).
        composite_score: The convergence score for this round (0.0-1.0).
        round_id: Optional unique round identifier (e.g. UUID).
        timestamp: Optional ISO-8601 timestamp when the score was recorded.
    """

    round_number: int
    """1-based round number."""

    composite_score: float
    """Convergence score in [0.0, 1.0]."""

    round_id: str = ""
    """Optional unique round identifier."""

    timestamp: str = ""
    """Optional ISO-8601 timestamp."""

    def __post_init__(self) -> None:
        """Validate field constraints."""
        if self.round_number < 1:
            raise ValueError(
                f"round_number must be >= 1, got {self.round_number}"
            )
        if not 0.0 <= self.composite_score <= 1.0:
            raise ValueError(
                f"composite_score must be in [0.0, 1.0], got {self.composite_score}"
            )


@dataclass(frozen=True)
class OscillationPattern:
    """A detected oscillation pattern in convergence scores.

    Oscillation means scores alternate up and down (like a sine wave)
    without a net upward trend toward convergence.  This indicates the
    meeting is cycling through the same disagreements without making
    progress.

    Attributes:
        detected: Whether an oscillation pattern was detected.
        rounds_involved: The round numbers exhibiting oscillation.
        scores: The scores for those rounds.
        direction_changes: Number of times the score direction changed.
        amplitude: Peak-to-trough amplitude of the oscillation.
        mean_score: Mean score across the oscillating rounds.
        trend: Net trend (positive = improving, negative = worsening,
              near-zero = no trend).
    """

    detected: bool
    """Whether oscillation was detected."""

    rounds_involved: tuple[int, ...]
    """Round numbers exhibiting oscillation."""

    scores: tuple[float, ...]
    """Convergence scores for oscillating rounds."""

    direction_changes: int
    """Number of direction changes in the score sequence."""

    amplitude: float
    """Peak-to-trough amplitude (max - min)."""

    mean_score: float
    """Mean score across oscillating rounds."""

    trend: float
    """Net trend: (last - first) / (n - 1), positive = improving."""


@dataclass(frozen=True)
class StagnationPattern:
    """A detected stagnation pattern in convergence scores.

    Stagnation means scores flatline below the convergence threshold for
    N consecutive rounds — the meeting has stopped making meaningful
    progress and is stuck at a sub-threshold score level.

    Attributes:
        detected: Whether stagnation was detected.
        rounds_involved: The consecutive rounds exhibiting stagnation.
        scores: The scores for those rounds.
        score_band: max(scores) - min(scores) — how flat the line is.
        mean_score: Mean score across stagnating rounds.
        rounds_below_threshold: How many consecutive rounds below threshold.
    """

    detected: bool
    """Whether stagnation was detected."""

    rounds_involved: tuple[int, ...]
    """Consecutive round numbers exhibiting stagnation."""

    scores: tuple[float, ...]
    """Convergence scores for stagnating rounds."""

    score_band: float
    """max - min of scores (0.0 = perfectly flat)."""

    mean_score: float
    """Mean score across stagnating rounds."""

    rounds_below_threshold: int
    """Number of consecutive rounds below the convergence threshold."""


@dataclass(frozen=True)
class DeadlockConfig:
    """Configuration for deadlock detection.

    Attributes:
        convergence_threshold: Score threshold for convergence.  Scores
            >= this value are considered converged (not deadlocked).
        stagnation_rounds: Minimum consecutive rounds below threshold
            within a narrow band to trigger stagnation deadlock.
        stagnation_band: Maximum score spread (max - min) to consider
            scores as 'flat' for stagnation detection.
        oscillation_rounds: Minimum total rounds needed for oscillation
            detection.
        oscillation_amplitude: Minimum peak-to-trough amplitude for
            oscillation (smaller = noise).
    """

    convergence_threshold: float = DEFAULT_CONVERGENCE_THRESHOLD
    """Score threshold for convergence."""

    stagnation_rounds: int = DEFAULT_STAGNATION_ROUNDS
    """Consecutive rounds needed for stagnation deadlock."""

    stagnation_band: float = DEFAULT_STAGNATION_BAND
    """Maximum score spread to consider scores 'flat'."""

    oscillation_rounds: int = DEFAULT_OSCILLATION_ROUNDS
    """Minimum rounds for oscillation detection."""

    oscillation_amplitude: float = DEFAULT_OSCILLATION_AMPLITUDE
    """Minimum amplitude to qualify as oscillation."""

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not 0.0 <= self.convergence_threshold <= 1.0:
            raise ValueError(
                f"convergence_threshold must be in [0.0, 1.0], "
                f"got {self.convergence_threshold}"
            )
        if self.stagnation_rounds < 2:
            raise ValueError(
                f"stagnation_rounds must be >= 2, got {self.stagnation_rounds}"
            )
        if self.stagnation_band <= 0.0:
            raise ValueError(
                f"stagnation_band must be > 0.0, got {self.stagnation_band}"
            )
        if self.oscillation_rounds < 3:
            raise ValueError(
                f"oscillation_rounds must be >= 3, got {self.oscillation_rounds}"
            )
        if not 0.0 < self.oscillation_amplitude <= 1.0:
            raise ValueError(
                f"oscillation_amplitude must be in (0.0, 1.0], "
                f"got {self.oscillation_amplitude}"
            )


@dataclass(frozen=True)
class DeadlockResult:
    """Complete result of deadlock detection across a score history.

    Aggregates both oscillation and stagnation analyses into a single
    verdict the Coordinator can use to determine whether the meeting
    has deadlocked and needs escalation.

    Attributes:
        is_deadlocked: True when oscillation or stagnation (or both)
                       is detected.
        deadlock_type: ``oscillation``, ``stagnation``, ``both``, or
                       ``none`` (when not deadlocked).
        reason: Human-readable explanation of the deadlock verdict.
        oscillation: The oscillation analysis result.
        stagnation: The stagnation analysis result.
        scores_analysed: The score records that were analysed.
        config: The configuration used.
    """

    is_deadlocked: bool
    """True when deadlock is detected (oscillation or stagnation)."""

    deadlock_type: str
    """``oscillation``, ``stagnation``, ``both``, or ``none``."""

    reason: str
    """Human-readable explanation."""

    oscillation: OscillationPattern
    """Oscillation analysis result."""

    stagnation: StagnationPattern
    """Stagnation analysis result."""

    scores_analysed: tuple[ScoreRecord, ...]
    """Score records that were analysed."""

    config: DeadlockConfig
    """Configuration used."""

    @property
    def requires_escalation(self) -> bool:
        """True when deadlock requires escalation (alias for is_deadlocked)."""
        return self.is_deadlocked

    @property
    def is_oscillation_deadlock(self) -> bool:
        """True when the deadlock is specifically oscillation-driven."""
        return self.deadlock_type in ("oscillation", "both")

    @property
    def is_stagnation_deadlock(self) -> bool:
        """True when the deadlock is specifically stagnation-driven."""
        return self.deadlock_type in ("stagnation", "both")

    def evidence(self) -> dict[str, Any]:
        """Return compact evidence dict suitable for logging/manifest.

        Returns:
            Dict with deadlock verdict, type, reason, and pattern specifics.
        """
        result: dict[str, Any] = {
            "is_deadlocked": self.is_deadlocked,
            "deadlock_type": self.deadlock_type,
            "reason": self.reason,
        }
        if self.oscillation.detected:
            result["oscillation"] = {
                "rounds": list(self.oscillation.rounds_involved),
                "amplitude": round(self.oscillation.amplitude, 4),
                "direction_changes": self.oscillation.direction_changes,
                "mean_score": round(self.oscillation.mean_score, 4),
                "trend": round(self.oscillation.trend, 4),
            }
        if self.stagnation.detected:
            result["stagnation"] = {
                "rounds": list(self.stagnation.rounds_involved),
                "score_band": round(self.stagnation.score_band, 4),
                "mean_score": round(self.stagnation.mean_score, 4),
                "rounds_below_threshold": self.stagnation.rounds_below_threshold,
            }
        return result


# ═════════════════════════════════════════════════════════════════════════
# Oscillation detection
# ═════════════════════════════════════════════════════════════════════════


def _detect_oscillation(
    scores: Sequence[ScoreRecord],
    config: DeadlockConfig,
) -> OscillationPattern:
    """Detect oscillation patterns in the score history.

    Oscillation is detected when:
    1. There are at least ``config.oscillation_rounds`` rounds.
    2. All scores are below the convergence threshold (otherwise they'd
       have converged).
    3. The score direction (up/down from the previous round) changes
       frequently — at least ``ceil(n/3)`` direction changes or 3,
       whichever is smaller.
    4. The peak-to-trough amplitude meets the minimum.
    5. There is no net upward trend toward convergence.

    Args:
        scores: Score records sorted by round_number ascending.
        config: Deadlock detection configuration.

    Returns:
        ``OscillationPattern`` with detection verdict and details.
    """
    n = len(scores)
    if n < config.oscillation_rounds:
        return OscillationPattern(
            detected=False,
            rounds_involved=(),
            scores=(),
            direction_changes=0,
            amplitude=0.0,
            mean_score=0.0,
            trend=0.0,
        )

    # Extract scores in round order
    score_values = [s.composite_score for s in scores]
    round_numbers = [s.round_number for s in scores]

    # If any score is at or above threshold, meeting converged — not oscillation
    if any(s >= config.convergence_threshold for s in score_values):
        return OscillationPattern(
            detected=False,
            rounds_involved=tuple(round_numbers),
            scores=tuple(score_values),
            direction_changes=0,
            amplitude=0.0,
            mean_score=0.0,
            trend=0.0,
        )

    score_min = min(score_values)
    score_max = max(score_values)
    amplitude = score_max - score_min

    if amplitude < config.oscillation_amplitude:
        # Fluctuations too small — more like stagnation, not oscillation
        return OscillationPattern(
            detected=False,
            rounds_involved=tuple(round_numbers),
            scores=tuple(score_values),
            direction_changes=0,
            amplitude=amplitude,
            mean_score=sum(score_values) / n,
            trend=0.0,
        )

    # Count direction changes
    direction_changes = 0
    prev_direction: int | None = None
    for i in range(1, n):
        diff = score_values[i] - score_values[i - 1]
        if abs(diff) < 0.005:
            # Essentially flat — not a direction change
            continue
        current_direction = 1 if diff > 0 else -1
        if prev_direction is not None and current_direction != prev_direction:
            direction_changes += 1
        prev_direction = current_direction

    # Compute net trend as centreline drift rather than raw endpoint drift.
    # Endpoint-only trend misclassifies symmetric oscillations that start on a
    # trough and end on a peak (e.g. 0.45, 0.60, 0.45, 0.60) as improving.
    # Comparing first-half vs second-half averages captures whether the whole
    # oscillation envelope is moving upward toward convergence.
    if n >= 2:
        midpoint = n // 2
        first_half = score_values[:midpoint]
        second_half = score_values[-midpoint:]
        first_mean = sum(first_half) / len(first_half)
        second_mean = sum(second_half) / len(second_half)
        trend = (second_mean - first_mean) / max(1, n - 1)
    else:
        trend = 0.0

    mean_score = sum(score_values) / n

    # Oscillation requires frequent direction changes relative to sequence length.
    # Need at least ceil((n-1)/2) direction changes — roughly half the possible
    # transitions must be flips.  This ensures the pattern is truly oscillatory
    # rather than a single peak or valley.
    #   n=3 → ceil(2/2)=1  (single peak in 3 rounds = oscillation)
    #   n=4 → ceil(3/2)=2  (up-down-up or down-up-down in 4 rounds)
    #   n=5 → ceil(4/2)=2  (at least 2 flips in 4 transitions)
    #   n=6 → ceil(5/2)=3  (at least 3 flips in 5 transitions)
    min_changes = max(1, math.ceil((n - 1) / 2))
    is_oscillating = direction_changes >= min_changes and trend <= 0.02

    # If trend is strongly positive, oscillation may be resolving
    if trend > 0.05:
        is_oscillating = False

    return OscillationPattern(
        detected=is_oscillating,
        rounds_involved=tuple(round_numbers),
        scores=tuple(score_values),
        direction_changes=direction_changes,
        amplitude=amplitude,
        mean_score=mean_score,
        trend=trend,
    )


# ═════════════════════════════════════════════════════════════════════════
# Stagnation detection
# ═════════════════════════════════════════════════════════════════════════


def _detect_stagnation(
    scores: Sequence[ScoreRecord],
    config: DeadlockConfig,
) -> StagnationPattern:
    """Detect stagnation patterns in the score history.

    Stagnation is detected when:
    1. There are at least ``config.stagnation_rounds`` consecutive rounds
       where scores all fall within ``config.stagnation_band`` of each
       other AND are all below the convergence threshold.
    2. The flatline window is identified by scanning for the longest
       consecutive streak meeting these conditions.

    Args:
        scores: Score records sorted by round_number ascending.
        config: Deadlock detection configuration.

    Returns:
        ``StagnationPattern`` with detection verdict and details.
    """
    n = len(scores)
    if n < config.stagnation_rounds:
        return StagnationPattern(
            detected=False,
            rounds_involved=(),
            scores=(),
            score_band=0.0,
            mean_score=0.0,
            rounds_below_threshold=0,
        )

    score_values = [s.composite_score for s in scores]
    round_numbers = [s.round_number for s in scores]

    # Stagnation is only actionable when the *current tail* is flat below the
    # convergence threshold.  Historical plateaus followed by renewed progress
    # must not deadlock the meeting.
    tail_start = n
    for i in range(n - 1, -1, -1):
        if score_values[i] < config.convergence_threshold:
            tail_start = i
        else:
            break

    tail_scores = score_values[tail_start:]
    tail_rounds = round_numbers[tail_start:]
    if len(tail_scores) < config.stagnation_rounds:
        return StagnationPattern(
            detected=False,
            rounds_involved=(),
            scores=(),
            score_band=0.0,
            mean_score=0.0,
            rounds_below_threshold=len(tail_scores),
        )

    tail_min = min(tail_scores)
    tail_max = max(tail_scores)
    tail_band = tail_max - tail_min
    if tail_band > config.stagnation_band:
        return StagnationPattern(
            detected=False,
            rounds_involved=(),
            scores=(),
            score_band=tail_band,
            mean_score=sum(tail_scores) / len(tail_scores),
            rounds_below_threshold=len(tail_scores),
        )

    return StagnationPattern(
        detected=True,
        rounds_involved=tuple(tail_rounds),
        scores=tuple(tail_scores),
        score_band=tail_band,
        mean_score=sum(tail_scores) / len(tail_scores),
        rounds_below_threshold=len(tail_scores),
    )


# ═════════════════════════════════════════════════════════════════════════
# Main API
# ═════════════════════════════════════════════════════════════════════════


def detect_deadlock(
    scores: Sequence[ScoreRecord],
    config: DeadlockConfig | None = None,
) -> DeadlockResult:
    """Detect deadlock from a history of convergence scores.

    Analyses the score history for two deadlock patterns:

    1. **Oscillation** — Scores cycle up and down without net progress.
       Detected when score direction changes frequently and there is no
       net upward trend toward convergence.
    2. **Stagnation** — Scores flatline below the convergence threshold
       for N consecutive rounds.  Detected when scores remain within a
       narrow band while staying below threshold.

    If either pattern is detected (or both), the meeting is declared
    deadlocked and requires escalation.

    Args:
        scores: Convergence score records, sorted by round_number
                ascending.  Must have at least 2 rounds for meaningful
                analysis.
        config: Detection configuration.  Uses ``DeadlockConfig()``
                defaults when omitted.

    Returns:
        ``DeadlockResult`` with deadlock verdict, type, reason, and
        pattern details.

    Raises:
        ValueError: If ``scores`` is empty.
        TypeError: If any entry is not a ``ScoreRecord``.

    Example::

        # Oscillation deadlock
        scores = [
            ScoreRecord(1, 0.45),
            ScoreRecord(2, 0.70),
            ScoreRecord(3, 0.48),
            ScoreRecord(4, 0.68),
        ]
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "oscillation"

        # Stagnation deadlock
        scores = [
            ScoreRecord(1, 0.50),
            ScoreRecord(2, 0.51),
            ScoreRecord(3, 0.50),
        ]
        result = detect_deadlock(scores)
        assert result.is_deadlocked
        assert result.deadlock_type == "stagnation"

        # Converged — not deadlocked
        scores = [
            ScoreRecord(1, 0.60),
            ScoreRecord(2, 0.88),
        ]
        result = detect_deadlock(scores)
        assert not result.is_deadlocked
    """
    if not scores:
        raise ValueError("scores must not be empty")

    for s in scores:
        if not isinstance(s, ScoreRecord):
            raise TypeError(
                f"All scores must be ScoreRecord, got {type(s).__name__}"
            )

    cfg = config or DeadlockConfig()

    # Sort by round number to ensure correct analysis order
    sorted_scores = sorted(scores, key=lambda s: s.round_number)

    # Run both analyses via injectable detector functions.  Tests and runtime
    # harnesses can replace either detector deterministically without patching
    # module internals.
    with _lock:
        oscillation_detector = _oscillation_detector
        stagnation_detector = _stagnation_detector
    oscillation = oscillation_detector(sorted_scores, cfg)
    stagnation = stagnation_detector(sorted_scores, cfg)

    # ── Determine deadlock verdict ──────────────────────────────────
    if oscillation.detected and stagnation.detected:
        deadlock_type = "both"
        reason = (
            f"Deadlock detected: both oscillation and stagnation patterns present. "
            f"Oscillation: scores cycle between {min(oscillation.scores):.2f} and "
            f"{max(oscillation.scores):.2f} with {oscillation.direction_changes} "
            f"direction changes. "
            f"Stagnation: scores flatline at ~{stagnation.mean_score:.2f} for "
            f"{len(stagnation.rounds_involved)} consecutive rounds "
            f"(band={stagnation.score_band:.3f}). "
            f"Convergence threshold={cfg.convergence_threshold:.2f}."
        )
        is_deadlocked = True
    elif oscillation.detected:
        deadlock_type = "oscillation"
        reason = (
            f"Deadlock detected: oscillation pattern — convergence scores cycle "
            f"between {min(oscillation.scores):.2f} and {max(oscillation.scores):.2f} "
            f"across {len(oscillation.rounds_involved)} rounds with "
            f"{oscillation.direction_changes} direction changes and no net upward "
            f"trend (trend={oscillation.trend:+.3f}/round). "
            f"Convergence threshold={cfg.convergence_threshold:.2f}."
        )
        is_deadlocked = True
    elif stagnation.detected:
        deadlock_type = "stagnation"
        reason = (
            f"Deadlock detected: stagnation pattern — convergence scores flatline at "
            f"~{stagnation.mean_score:.2f} for {len(stagnation.rounds_involved)} "
            f"consecutive rounds (band={stagnation.score_band:.3f}), well below "
            f"convergence threshold of {cfg.convergence_threshold:.2f}. "
            f"Total {stagnation.rounds_below_threshold} consecutive rounds below "
            f"threshold."
        )
        is_deadlocked = True
    else:
        deadlock_type = "none"
        # Find the most recent score for context
        latest_score = sorted_scores[-1].composite_score
        if latest_score >= cfg.convergence_threshold:
            reason = (
                f"No deadlock: latest score ({latest_score:.3f}) meets or exceeds "
                f"convergence threshold ({cfg.convergence_threshold:.2f}). "
                f"Meeting has converged."
            )
        else:
            reason = (
                f"No deadlock detected: scores show neither oscillation nor "
                f"stagnation patterns. Latest score: {latest_score:.3f}. "
                f"Threshold: {cfg.convergence_threshold:.2f}. "
                f"Score direction changes: {oscillation.direction_changes}, "
                f"max flatline band: {stagnation.score_band:.3f}."
            )
        is_deadlocked = False

    return DeadlockResult(
        is_deadlocked=is_deadlocked,
        deadlock_type=deadlock_type,
        reason=reason,
        oscillation=oscillation,
        stagnation=stagnation,
        scores_analysed=tuple(sorted_scores),
        config=cfg,
    )


# ═════════════════════════════════════════════════════════════════════════
# Convenience: detect deadlock from raw score values
# ═════════════════════════════════════════════════════════════════════════


def detect_deadlock_from_values(
    score_values: Sequence[float],
    start_round: int = 1,
    config: DeadlockConfig | None = None,
) -> DeadlockResult:
    """Convenience wrapper that accepts raw score values instead of
    ``ScoreRecord`` objects.

    Each score is assigned a sequential round number starting from
    ``start_round`` (default 1).

    Args:
        score_values: Convergence scores in round order.
        start_round: Round number for the first score.
        config: Detection configuration.

    Returns:
        ``DeadlockResult`` as from ``detect_deadlock``.

    Example::

        result = detect_deadlock_from_values([0.45, 0.70, 0.48, 0.68])
        assert result.is_deadlocked
    """
    records = [
        ScoreRecord(round_number=start_round + i, composite_score=s)
        for i, s in enumerate(score_values)
    ]
    return detect_deadlock(records, config=config)


# ═════════════════════════════════════════════════════════════════════════
# Injectable overrides (for deterministic testing)
# ═════════════════════════════════════════════════════════════════════════

_OscillationDetectorFn = Callable[
    [Sequence[ScoreRecord], DeadlockConfig],
    OscillationPattern,
]
"""Signature: (scores, config) -> OscillationPattern"""

_StagnationDetectorFn = Callable[
    [Sequence[ScoreRecord], DeadlockConfig],
    StagnationPattern,
]
"""Signature: (scores, config) -> StagnationPattern"""

_oscillation_detector: _OscillationDetectorFn = _detect_oscillation
_stagnation_detector: _StagnationDetectorFn = _detect_stagnation
_lock: threading.Lock = threading.Lock()


def inject_oscillation_detector(fn: _OscillationDetectorFn) -> None:
    """Inject a custom oscillation detector for deterministic testing.

    Args:
        fn: A callable matching ``_OscillationDetectorFn`` signature.
    """
    global _oscillation_detector
    with _lock:
        _oscillation_detector = fn


def inject_stagnation_detector(fn: _StagnationDetectorFn) -> None:
    """Inject a custom stagnation detector for deterministic testing.

    Args:
        fn: A callable matching ``_StagnationDetectorFn`` signature.
    """
    global _stagnation_detector
    with _lock:
        _stagnation_detector = fn


def reset_injectables() -> None:
    """Reset all injectable functions to their default implementations."""
    global _oscillation_detector, _stagnation_detector
    with _lock:
        _oscillation_detector = _detect_oscillation
        _stagnation_detector = _detect_stagnation
