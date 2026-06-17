"""Threshold gate evaluation — Sub-AC 6.3.2.

Accepts a divergence score and a configured threshold value, returns a
binary exceed/within decision.  This module sits between the divergence
metric (Sub-AC 6.3.1) and the downstream dual-validation conflict-
resolution pipeline.

Design
------
The gate is a **pure function** that compares a normalized score
against a threshold:

* **within** — score ≤ threshold (acceptable, no escalation needed)
* **exceeded** — score > threshold (divergence too high, escalation
  or Codex conditional validation triggered)

The inclusive boundary (``score == threshold`` → ``within``) is
intentional and matches the convention in ``DivergenceReport.passed``.

Edge-case contract
------------------
* **score == threshold** → within (boundary inclusive)
* **score == 0.0** with threshold 0.0 → within (both zero)
* **score == 1.0** with threshold 1.0 → within (ceiling inclusive)
* **score < 0.0** → clamped to 0.0 before evaluation
* **score > 1.0** → clamped to 1.0 before evaluation
* **threshold outside [0.0, 1.0]** → clamped to valid range

The ``GateResult`` dataclass carries the raw (original) score and the
effective (clamped) score so callers can detect out-of-bounds inputs.

Related modules
---------------
* ``divergence_metric`` — Sub-AC 6.3.1 (produces the score)
* ``conflict_detector`` — conflict identification (uses threshold)
* ``resolution_decision`` — final resolution (gate is a sub-step)

Testable with
-------------
* Known scores and thresholds at boundaries (0.0, 0.30, 1.0)
* Score == threshold (boundary-inclusive behaviour)
* Score marginally above/below threshold
* Out-of-range scores (negative, >1.0)
* Out-of-range thresholds
* Zero threshold and zero score
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Public constants ───────────────────────────────────────────────────

DEFAULT_THRESHOLD: float = 0.30
"""Default divergence threshold: scores ≤ 0.30 are considered 'within'."""

MIN_VALID_SCORE: float = 0.0
"""Minimum valid divergence/score value."""

MAX_VALID_SCORE: float = 1.0
"""Maximum valid divergence/score value."""


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateResult:
    """Result of a single threshold gate evaluation.

    ``exceeded`` and ``within`` are always logical complements
    (``exceeded == not within``).

    Attributes:
        score: The original (raw) divergence score provided.
        effective_score: The score actually used for evaluation
            (clamped to [0.0, 1.0]).
        threshold: The configured threshold value.
        effective_threshold: The threshold actually used (clamped to
            [0.0, 1.0]).
        exceeded: True when ``effective_score > effective_threshold``.
        within: True when ``effective_score <= effective_threshold``.
        margin: How far the effective score is from the threshold
            (``effective_score - effective_threshold``).  Positive =
            exceeded; negative/zero = within.
        was_clamped: True if the original score was outside [0.0, 1.0]
            or the threshold was outside [0.0, 1.0].
    """

    score: float
    """Original divergence score as passed by caller."""

    effective_score: float
    """Score after clamping to [0.0, 1.0]."""

    threshold: float
    """Original threshold as passed by caller."""

    effective_threshold: float
    """Threshold after clamping to [0.0, 1.0]."""

    exceeded: bool
    """True when effective_score > effective_threshold."""

    within: bool
    """True when effective_score <= effective_threshold."""

    margin: float
    """effective_score - effective_threshold (positive = exceeded region)."""

    was_clamped: bool
    """True if clamping was applied to score or threshold."""

    @property
    def is_boundary(self) -> bool:
        """True when the effective score equals the effective threshold
        exactly (within 1e-9 tolerance)."""
        return abs(self.effective_score - self.effective_threshold) < 1e-9

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "score": self.score,
            "effective_score": self.effective_score,
            "threshold": self.threshold,
            "effective_threshold": self.effective_threshold,
            "exceeded": self.exceeded,
            "within": self.within,
            "margin": self.margin,
            "was_clamped": self.was_clamped,
        }


# ── Core evaluation logic ──────────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the inclusive range [*lo*, *hi*]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def evaluate_threshold(
    score: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> GateResult:
    """Evaluate whether a divergence *score* exceeds a *threshold*.

    This is the primary public API for Sub-AC 6.3.2.

    Args:
        score: A divergence score, nominally in [0.0, 1.0].  Values
            outside this range are clamped before evaluation (and
            ``was_clamped`` is set to ``True``).
        threshold: The configured threshold value, nominally in
            [0.0, 1.0].  Defaults to ``DEFAULT_THRESHOLD`` (0.30).
            Values outside [0.0, 1.0] are clamped.

    Returns:
        A ``GateResult`` with the binary decision.

    Raises:
        TypeError: If *score* or *threshold* is not a real number.

    Examples:
        >>> evaluate_threshold(0.25, 0.30)
        GateResult(score=0.25, ..., exceeded=False, within=True, ...)

        >>> evaluate_threshold(0.35, 0.30)
        GateResult(score=0.35, ..., exceeded=True, within=False, ...)

        >>> evaluate_threshold(0.30, 0.30)  # boundary-inclusive
        GateResult(score=0.30, ..., exceeded=False, within=True, ...)

        >>> evaluate_threshold(-0.1, 0.30)  # clamped to 0.0
        GateResult(score=-0.1, effective_score=0.0, ..., within=True, was_clamped=True)
    """
    # Type validation
    if not isinstance(score, (int, float)):
        raise TypeError(
            f"score must be a number, got {type(score).__name__}"
        )
    if not isinstance(threshold, (int, float)):
        raise TypeError(
            f"threshold must be a number, got {type(threshold).__name__}"
        )
    if isinstance(score, bool) or isinstance(threshold, bool):
        # bool is a subclass of int — reject explicitly
        raise TypeError("score and threshold must be numbers, not bool")

    # Clamp to valid ranges
    effective_score = _clamp(float(score), MIN_VALID_SCORE, MAX_VALID_SCORE)
    effective_threshold = _clamp(float(threshold), MIN_VALID_SCORE, MAX_VALID_SCORE)

    was_clamped = (
        score != effective_score or threshold != effective_threshold
    )

    # Binary decision: inclusive boundary (≤) means "within"
    exceeded = effective_score > effective_threshold
    within = not exceeded
    margin = round(effective_score - effective_threshold, 10)

    return GateResult(
        score=float(score),
        effective_score=effective_score,
        threshold=float(threshold),
        effective_threshold=effective_threshold,
        exceeded=exceeded,
        within=within,
        margin=margin,
        was_clamped=was_clamped,
    )


# ── Convenience: divergence-aware gate ─────────────────────────────────


def gate_from_divergence_report(
    overall_divergence: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> GateResult:
    """Convenience wrapper that accepts a raw divergence score.

    This is functionally identical to ``evaluate_threshold(score, threshold)``
    but the parameter name ``overall_divergence`` makes the call-site intent
    explicit when used in the dual-validation pipeline.

    Args:
        overall_divergence: The ``overall_divergence`` field from a
            ``DivergenceReport`` (Sub-AC 6.3.1).
        threshold: The configured threshold (default 0.30).

    Returns:
        A ``GateResult`` with the binary decision.
    """
    return evaluate_threshold(overall_divergence, threshold)
