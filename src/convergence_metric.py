"""Convergence metric computation for multi-round meeting consensus tracking.

Sub-AC 5c-1: Given a set of positions from the current round and the
previous round, compute a numerical convergence score (e.g. position
delta norm, agreement ratio) and determine whether it meets the
configured threshold, testable with synthetic position sets producing
known convergence scores.

Architecture
------------
The convergence metric sits between the Round 2 rebuttal exchange
and the Round 3 convergence context packet assembly.  It receives
position sets from two consecutive rounds and computes:

1. **Position delta norm** — The normalized Euclidean distance between
   encoded position vectors across rounds.  Lower delta = more
   convergence.  Each position is encoded as a numeric vector capturing
   stance, recommendation direction, and confidence.

2. **Agreement ratio** — For each topic, the proportion of persona
   pairs whose positions are compatible (not directly opposing).
   Higher ratio = more agreement.

3. **Composite convergence score** — A weighted combination of the
   delta norm and agreement ratio, normalized to [0.0, 1.0] where
   1.0 = complete convergence.

4. **Threshold evaluation** — Compare the composite score against the
   configured threshold (default 0.85) and produce a ``has_converged``
   boolean.

The module is pure-in-memory (no filesystem I/O), fully testable with
hand-crafted position sets, and follows the immutable dataclass patterns
of ``conflict_detector.py`` and ``resolution_decision.py``.

Usage::

    from src.convergence_metric import (
        RoundPosition,
        ConvergenceResult,
        ConvergenceConfig,
        compute_convergence,
        DEFAULT_CONVERGENCE_THRESHOLD,
    )

    prev_round = [
        RoundPosition("art-director", "budget", "support", "adopt", 0.85),
        RoundPosition("tech-director", "budget", "oppose", "reject", 0.90),
    ]
    curr_round = [
        RoundPosition("art-director", "budget", "support", "adopt", 0.90),
        RoundPosition("tech-director", "budget", "conditional_support", "adopt", 0.75),
    ]

    result = compute_convergence(curr_round, prev_round)
    print(f"Score: {result.composite_score:.2f}")
    print(f"Converged: {result.has_converged}")
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoundPosition:
    """A single persona's position on a topic in a specific round.

    Attributes:
        persona_id: The persona's role_id (e.g. ``art-director``).
        topic_id: The topic identifier (kebab-case).
        stance: Stance label (``support``, ``oppose``, ``neutral``,
                ``conditional_support``, ``alternative_proposal``).
        recommendation: Direction label (``adopt``, ``reject``, ``defer``,
                        ``explore``, ``increase``, ``decrease``,
                        ``maintain``).
        confidence: Persona's confidence in their position (0.0-1.0).
        round_number: Which round this position belongs to (1, 2, 3, or 4
                      for tie-break).
    """

    persona_id: str
    """Persona role_id (e.g. ``art-director``)."""

    topic_id: str
    """Topic identifier in kebab-case."""

    stance: str
    """Stance label: support, oppose, neutral, conditional_support,
    alternative_proposal."""

    recommendation: str
    """Direction label: adopt, reject, defer, explore, increase, decrease,
    maintain."""

    confidence: float
    """Confidence in position (0.0-1.0)."""

    round_number: int = 1
    """Round number (1, 2, 3, or 4 for tie-break)."""

    def __post_init__(self) -> None:
        """Validate field constraints."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        if self.round_number < 1:
            raise ValueError(
                f"round_number must be >= 1, got {self.round_number}"
            )


@dataclass(frozen=True)
class PositionDelta:
    """The measured change in a persona's position between two rounds.

    Attributes:
        persona_id: The persona who holds this position.
        topic_id: The topic identifier.
        stance_changed: Whether the stance label changed.
        recommendation_changed: Whether the recommendation direction changed.
        confidence_delta: Change in confidence (curr - prev, range [-1.0, 1.0]).
        vector_delta: Euclidean distance between encoded position vectors
                      (0.0 = identical, 2.0 = maximally different).
        normalized_delta: ``vector_delta`` normalized to [0.0, 1.0].
    """

    persona_id: str
    """Persona role_id."""

    topic_id: str
    """Topic identifier."""

    stance_changed: bool
    """Whether the stance label changed between rounds."""

    recommendation_changed: bool
    """Whether the recommendation direction changed between rounds."""

    confidence_delta: float
    """Change in confidence (curr - prev), range [-1.0, 1.0]."""

    vector_delta: float
    """Euclidean distance between encoded position vectors (0.0-2.0)."""

    normalized_delta: float
    """Vector delta normalized to [0.0, 1.0]."""


@dataclass(frozen=True)
class TopicAgreement:
    """Agreement analysis for a single topic across all personas.

    Attributes:
        topic_id: The topic identifier.
        total_personas: Number of personas with positions on this topic.
        agreeing_pairs: Number of persona pairs whose positions are compatible.
        total_pairs: Total number of distinct persona pairs (n*(n-1)/2).
        agreement_ratio: ``agreeing_pairs / total_pairs`` (1.0 when all agree).
        opposing_pairs: List of (persona_a, persona_b) tuples that are in
                        direct opposition.
    """

    topic_id: str
    """Topic identifier."""

    total_personas: int
    """Number of personas addressing this topic."""

    agreeing_pairs: int
    """Number of compatible persona pairs."""

    total_pairs: int
    """Total distinct persona pairs."""

    agreement_ratio: float
    """Agreement ratio (1.0 = unanimous, 0.0 = all opposing)."""

    opposing_pairs: tuple[tuple[str, str], ...]
    """Persona pairs in direct opposition."""


@dataclass(frozen=True)
class ConvergenceConfig:
    """Configuration for convergence threshold evaluation.

    Attributes:
        threshold: Minimum composite score to declare convergence
                   (default 0.85, per exit conditions).
        delta_weight: Weight for position delta norm in composite score
                      (default 0.4).
        agreement_weight: Weight for agreement ratio in composite score
                          (default 0.6).
        min_agreement_ratio: Minimum agreement ratio to allow convergence
                             even if delta norm is low (default 0.5).
    """

    threshold: float = 0.85
    """Minimum composite score for convergence."""

    delta_weight: float = 0.4
    """Weight of position delta norm in composite."""

    agreement_weight: float = 0.6
    """Weight of agreement ratio in composite."""

    min_agreement_ratio: float = 0.5
    """Minimum agreement ratio for convergence."""

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(
                f"threshold must be in [0.0, 1.0], got {self.threshold}"
            )
        total_w = self.delta_weight + self.agreement_weight
        if not math.isclose(total_w, 1.0, rel_tol=1e-9):
            raise ValueError(
                f"weights must sum to 1.0, got delta={self.delta_weight} "
                f"+ agreement={self.agreement_weight} = {total_w}"
            )


# Default configuration, per exit conditions requirement of overall >= 0.85
DEFAULT_CONVERGENCE_THRESHOLD: float = 0.85
"""Default convergence threshold matching the exit conditions."""

# ── Position vector encoding ────────────────────────────────────────────

# Stance → numeric value (ordered from most opposing to most supporting)
_STANCE_ENCODING: dict[str, float] = {
    "oppose": -1.0,
    "alternative_proposal": -0.25,
    "neutral": 0.0,
    "conditional_support": 0.5,
    "support": 1.0,
}

# Recommendation direction → numeric value
# Grouped by semantic proximity: reject < maintain/defer < explore < adopt
_RECOMMENDATION_ENCODING: dict[str, float] = {
    "reject": -1.0,
    "decrease": -0.75,
    "defer": -0.25,
    "maintain": 0.0,
    "explore": 0.25,
    "increase": 0.75,
    "adopt": 1.0,
}

# Directly opposing stance pairs (i.e., cannot be reconciled without revision)
_DIRECT_OPPOSITION: frozenset[tuple[str, str]] = frozenset({
    ("support", "oppose"),
    ("oppose", "support"),
    ("adopt", "reject"),
    ("reject", "adopt"),
    ("increase", "decrease"),
    ("decrease", "increase"),
})


def _encode_position_vector(stance: str, recommendation: str, confidence: float) -> tuple[float, float, float]:
    """Encode a position as a 3-dimensional numeric vector.

    Args:
        stance: The stance label.
        recommendation: The recommendation direction.
        confidence: The confidence value (0.0-1.0).

    Returns:
        A tuple of (stance_val, recommendation_val, confidence_val).
        Unknown labels map to 0.0 (neutral).
    """
    stance_val = _STANCE_ENCODING.get(stance, 0.0)
    rec_val = _RECOMMENDATION_ENCODING.get(recommendation, 0.0)
    return (stance_val, rec_val, confidence)


def _vector_euclidean_distance(
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
) -> float:
    """Compute Euclidean distance between two 3D position vectors.

    The theoretical maximum distance between two encoded vectors is
    sqrt((1 - (-1))^2 + (1 - (-1))^2 + (1 - 0)^2) = sqrt(4 + 4 + 1) = sqrt(9) = 3.
    We divide by 3 to normalize to [0.0, 1.0].

    Args:
        v1: First position vector.
        v2: Second position vector.

    Returns:
        Euclidean distance normalized to [0.0, 1.0].
    """
    d_stance = v1[0] - v2[0]
    d_rec = v1[1] - v2[1]
    d_conf = v1[2] - v2[2]
    raw = math.sqrt(d_stance * d_stance + d_rec * d_rec + d_conf * d_conf)
    return raw / 3.0  # normalize to [0, 1]


def _are_stances_compatible(stance_a: str, stance_b: str) -> bool:
    """Check whether two stances are compatible (not directly opposing).

    Compatible stances include: same stance, support+conditional_support,
    neutral+support, neutral+oppose (neutral is compatible with anything),
    alternative_proposal+anything (it's a different angle, not opposition).

    Args:
        stance_a: First stance label.
        stance_b: Second stance label.

    Returns:
        True if the stances are not in direct opposition.
    """
    pair = (stance_a, stance_b)
    if pair in _DIRECT_OPPOSITION:
        return False
    # Also check recommendation pairs
    return True


def _are_recommendations_compatible(rec_a: str, rec_b: str) -> bool:
    """Check whether two recommendation directions are compatible.

    Compatible: same direction, adopt+explore, increase+maintain,
    decrease+maintain, etc. Not compatible: adopt+reject, increase+decrease.

    Args:
        rec_a: First recommendation.
        rec_b: Second recommendation.

    Returns:
        True if the directions are not in direct opposition.
    """
    pair = (rec_a, rec_b)
    if pair in _DIRECT_OPPOSITION:
        return False
    return True


def _are_positions_compatible(pos_a: RoundPosition, pos_b: RoundPosition) -> bool:
    """Check whether two positions on the same topic are compatible.

    Positions are compatible when both stances and recommendations are
    not directly opposing.

    Args:
        pos_a: First position.
        pos_b: Second position.

    Returns:
        True when compatible.
    """
    return (
        _are_stances_compatible(pos_a.stance, pos_b.stance)
        and _are_recommendations_compatible(pos_a.recommendation, pos_b.recommendation)
    )


# ── Sub-metric computations ─────────────────────────────────────────────


def _compute_position_deltas(
    curr_positions: Sequence[RoundPosition],
    prev_positions: Sequence[RoundPosition],
) -> tuple[PositionDelta, ...]:
    """Compute position deltas between current and previous round positions.

    For each (persona_id, topic_id) pair present in both rounds, compute
    the change in encoded position vector.  Positions that appear in only
    one round are skipped (no delta to compute).

    Args:
        curr_positions: Position set from the current round.
        prev_positions: Position set from the previous round.

    Returns:
        Tuple of ``PositionDelta`` objects, one per matched position.
    """
    # Build lookup: (persona_id, topic_id) → RoundPosition for previous round
    prev_lookup: dict[tuple[str, str], RoundPosition] = {}
    for pos in prev_positions:
        key = (pos.persona_id, pos.topic_id)
        prev_lookup[key] = pos

    deltas: list[PositionDelta] = []
    for curr in curr_positions:
        key = (curr.persona_id, curr.topic_id)
        prev = prev_lookup.get(key)
        if prev is None:
            continue

        curr_vec = _encode_position_vector(curr.stance, curr.recommendation, curr.confidence)
        prev_vec = _encode_position_vector(prev.stance, prev.recommendation, prev.confidence)

        vector_delta = _vector_euclidean_distance(curr_vec, prev_vec)
        # Normalize: sqrt(3) = 1.732... maximum, so cap at 1.0
        normalized = min(vector_delta, 1.0)

        stance_changed = curr.stance != prev.stance
        rec_changed = curr.recommendation != prev.recommendation
        conf_delta = curr.confidence - prev.confidence

        deltas.append(
            PositionDelta(
                persona_id=curr.persona_id,
                topic_id=curr.topic_id,
                stance_changed=stance_changed,
                recommendation_changed=rec_changed,
                confidence_delta=conf_delta,
                vector_delta=vector_delta,
                normalized_delta=normalized,
            )
        )

    return tuple(deltas)


def _compute_delta_norm(deltas: Sequence[PositionDelta]) -> float:
    """Compute the position delta norm from a set of ``PositionDelta`` objects.

    The delta norm is the RMS (root mean square) of the normalized vector
    deltas.  Lower values indicate less change between rounds, meaning
    more stability / convergence.

    When there are no deltas (no matched positions), returns 1.0
    (maximum divergence — cannot assess convergence).

    Args:
        deltas: Computed position deltas.

    Returns:
        Delta norm in [0.0, 1.0].  0.0 = identical positions,
        1.0 = maximum divergence.
    """
    if not deltas:
        return 1.0
    sum_sq = sum(d.normalized_delta ** 2 for d in deltas)
    rms = math.sqrt(sum_sq / len(deltas))
    # Invert: convergence = 1 - divergence
    return 1.0 - rms


def _compute_topic_agreements(
    current_positions: Sequence[RoundPosition],
) -> tuple[TopicAgreement, ...]:
    """Compute agreement ratios for all topics across the current round.

    For each topic, compute how many persona pairs have compatible
    positions (neither directly opposing stances nor directly opposing
    recommendations).

    Args:
        current_positions: Position set from the current round.

    Returns:
        Tuple of ``TopicAgreement`` objects, one per topic.
    """
    # Group positions by topic_id
    topic_groups: dict[str, list[RoundPosition]] = {}
    for pos in current_positions:
        if pos.topic_id not in topic_groups:
            topic_groups[pos.topic_id] = []
        topic_groups[pos.topic_id].append(pos)

    agreements: list[TopicAgreement] = []
    for topic_id, positions in sorted(topic_groups.items()):
        n = len(positions)
        if n < 2:
            # Single persona on this topic — trivially agreed
            agreements.append(
                TopicAgreement(
                    topic_id=topic_id,
                    total_personas=n,
                    agreeing_pairs=0,
                    total_pairs=0,
                    agreement_ratio=1.0,
                    opposing_pairs=(),
                )
            )
            continue

        total_pairs = n * (n - 1) // 2
        agreeing = 0
        opposing: list[tuple[str, str]] = []

        for i in range(n):
            for j in range(i + 1, n):
                pos_a = positions[i]
                pos_b = positions[j]
                if _are_positions_compatible(pos_a, pos_b):
                    agreeing += 1
                else:
                    opposing.append((pos_a.persona_id, pos_b.persona_id))

        ratio = agreeing / total_pairs if total_pairs > 0 else 1.0

        agreements.append(
            TopicAgreement(
                topic_id=topic_id,
                total_personas=n,
                agreeing_pairs=agreeing,
                total_pairs=total_pairs,
                agreement_ratio=ratio,
                opposing_pairs=tuple(opposing),
            )
        )

    return tuple(agreements)


def _compute_agreement_ratio(topic_agreements: Sequence[TopicAgreement]) -> float:
    """Compute the overall agreement ratio across all topics.

    The overall agreement ratio is the mean of per-topic agreement ratios.
    When no topics are present, returns 0.0.

    Args:
        topic_agreements: Per-topic agreement analyses.

    Returns:
        Overall agreement ratio in [0.0, 1.0].
    """
    if not topic_agreements:
        return 0.0
    return sum(ta.agreement_ratio for ta in topic_agreements) / len(topic_agreements)


def _compute_composite_score(
    delta_norm: float,
    agreement_ratio: float,
    config: ConvergenceConfig,
) -> float:
    """Compute the composite convergence score from sub-metrics.

    Composite = delta_weight * delta_norm + agreement_weight * agreement_ratio.

    Args:
        delta_norm: The position delta norm (convergence through stability).
        agreement_ratio: The overall agreement ratio.
        config: Convergence configuration with weights.

    Returns:
        Composite score in [0.0, 1.0].
    """
    return (config.delta_weight * delta_norm) + (config.agreement_weight * agreement_ratio)


# ── Main result type ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConvergenceResult:
    """Complete result of convergence metric computation between two rounds.

    Attributes:
        position_deltas: All computed position deltas.
        delta_norm: Normalised position delta norm (higher = more stable).
        topic_agreements: Per-topic agreement analyses.
        agreement_ratio: Overall agreement ratio across all topics.
        composite_score: Weighted composite convergence score.
        threshold: The threshold that was evaluated against.
        has_converged: True when ``composite_score >= threshold`` AND
                       ``agreement_ratio >= config.min_agreement_ratio``.
        convergence_status: Human-readable verdict.
        matched_position_count: Number of positions matched between rounds.
        total_current_positions: Number of positions in the current round.
        config: The configuration used for this computation.
        detail: Optional free-text analysis of convergence status.
    """

    position_deltas: tuple[PositionDelta, ...]
    """All computed position deltas."""

    delta_norm: float
    """Normalised position delta norm (higher = more stable/converged)."""

    topic_agreements: tuple[TopicAgreement, ...]
    """Per-topic agreement analyses."""

    agreement_ratio: float
    """Overall agreement ratio across all topics."""

    composite_score: float
    """Weighted composite convergence score."""

    threshold: float
    """The threshold evaluated against."""

    has_converged: bool
    """True when convergence meets the threshold."""

    convergence_status: str
    """Human-readable verdict: ``converged``, ``near_convergence``,
    ``diverging``, ``deadlocked``, or ``insufficient_data``."""

    matched_position_count: int
    """Number of (persona, topic) pairs matched between rounds."""

    total_current_positions: int
    """Total number of positions in the current round."""

    config: ConvergenceConfig
    """Configuration used for this computation."""

    detail: str = ""
    """Optional free-text analysis."""


# ── Injectable callable types ───────────────────────────────────────────

DeltaComputerFn = Callable[
    [Sequence[RoundPosition], Sequence[RoundPosition]],
    tuple[PositionDelta, ...],
]
"""Signature: (curr_positions, prev_positions) -> tuple[PositionDelta, ...]"""

AgreementComputerFn = Callable[
    [Sequence[RoundPosition]],
    tuple[TopicAgreement, ...],
]
"""Signature: (current_positions) -> tuple[TopicAgreement, ...]"""

ScoreComputerFn = Callable[
    [float, float, ConvergenceConfig],
    float,
]
"""Signature: (delta_norm, agreement_ratio, config) -> float"""

# Thread-safe injectable overrides (for testing without LLM calls)
_delta_computer: DeltaComputerFn = _compute_position_deltas
_agreement_computer: AgreementComputerFn = _compute_topic_agreements
_score_computer: ScoreComputerFn = _compute_composite_score
_lock: threading.Lock = threading.Lock()


def inject_delta_computer(fn: DeltaComputerFn) -> None:
    """Inject a custom delta computer for deterministic testing.

    Args:
        fn: A callable matching ``DeltaComputerFn`` signature.
    """
    global _delta_computer
    with _lock:
        _delta_computer = fn


def inject_agreement_computer(fn: AgreementComputerFn) -> None:
    """Inject a custom agreement computer for deterministic testing.

    Args:
        fn: A callable matching ``AgreementComputerFn`` signature.
    """
    global _agreement_computer
    with _lock:
        _agreement_computer = fn


def inject_score_computer(fn: ScoreComputerFn) -> None:
    """Inject a custom score computer for deterministic testing.

    Args:
        fn: A callable matching ``ScoreComputerFn`` signature.
    """
    global _score_computer
    with _lock:
        _score_computer = fn


def reset_injectables() -> None:
    """Reset all injectable functions to their default implementations."""
    global _delta_computer, _agreement_computer, _score_computer
    with _lock:
        _delta_computer = _compute_position_deltas
        _agreement_computer = _compute_topic_agreements
        _score_computer = _compute_composite_score


# ── Main API ────────────────────────────────────────────────────────────


def _determine_convergence_status(
    composite_score: float,
    agreement_ratio: float,
    has_converged: bool,
    config: ConvergenceConfig,
) -> str:
    """Determine a human-readable convergence status label.

    Args:
        composite_score: The composite convergence score.
        agreement_ratio: The overall agreement ratio.
        has_converged: Whether convergence passes the threshold.
        config: The convergence configuration.

    Returns:
        One of: ``converged``, ``near_convergence``, ``diverging``,
        ``deadlocked``, ``insufficient_data``.
    """
    if has_converged:
        return "converged"

    if agreement_ratio == 0.0 and composite_score == 0.0:
        return "insufficient_data"

    if agreement_ratio < config.min_agreement_ratio:
        # Low agreement ratio suggests deadlock
        if agreement_ratio < 0.2:
            return "deadlocked"
        return "diverging"

    if composite_score >= config.threshold * 0.85:
        return "near_convergence"

    return "diverging"


def compute_convergence(
    current_positions: Sequence[RoundPosition],
    previous_positions: Sequence[RoundPosition] | None = None,
    threshold: float | None = None,
    config: ConvergenceConfig | None = None,
) -> ConvergenceResult:
    """Compute convergence metrics between two rounds of positions.

    This is the main entry point for the convergence metric computation.
    It measures how much the multi-agent discussion has converged between
    consecutive rounds.

    Metrics computed:
    1. **Position delta norm** — How much positions changed between
       rounds (lower = more stable = better convergence).
    2. **Agreement ratio** — How much personas agree with each other
       on each topic in the current round.
    3. **Composite score** — Weighted combination of the above.

    Args:
        current_positions: Position set from the current round.
        previous_positions: Position set from the previous round.
                            If None, only the agreement ratio is used
                            (delta norm defaults to 1.0).
        threshold: Override convergence threshold (default: 0.85).
        config: Override convergence configuration.

    Returns:
        ``ConvergenceResult`` with full metrics and convergence verdict.

    Raises:
        ValueError: If ``current_positions`` is empty.
        TypeError: If any position is not a ``RoundPosition``.
    """
    if not current_positions:
        raise ValueError("current_positions must not be empty")

    for pos in current_positions:
        if not isinstance(pos, RoundPosition):
            raise TypeError(
                f"All positions must be RoundPosition, got {type(pos).__name__}"
            )

    if previous_positions:
        for pos in previous_positions:
            if not isinstance(pos, RoundPosition):
                raise TypeError(
                    f"All positions must be RoundPosition, got {type(pos).__name__}"
                )

    cfg = config or ConvergenceConfig()
    if threshold is not None:
        # Create a new config with the override threshold
        cfg = ConvergenceConfig(
            threshold=threshold,
            delta_weight=cfg.delta_weight,
            agreement_weight=cfg.agreement_weight,
            min_agreement_ratio=cfg.min_agreement_ratio,
        )

    # Compute position deltas
    if previous_positions:
        deltas = _delta_computer(current_positions, previous_positions)
        delta_norm = _compute_delta_norm(deltas)
    else:
        deltas = ()
        delta_norm = 1.0  # no previous round to compare against

    # Compute topic agreements from current round
    topic_agreements = _agreement_computer(current_positions)
    agreement_ratio = _compute_agreement_ratio(topic_agreements)

    # Compute composite score
    composite_score = _score_computer(delta_norm, agreement_ratio, cfg)

    # Evaluate convergence
    has_converged = (
        composite_score >= cfg.threshold
        and agreement_ratio >= cfg.min_agreement_ratio
    )

    status = _determine_convergence_status(
        composite_score, agreement_ratio, has_converged, cfg
    )

    # Build detail
    detail_parts: list[str] = [
        f"delta_norm={delta_norm:.3f}",
        f"agreement_ratio={agreement_ratio:.3f}",
        f"composite={composite_score:.3f}",
        f"threshold={cfg.threshold:.2f}",
    ]
    if previous_positions:
        detail_parts.append(f"matched_positions={len(deltas)}")
    if topic_agreements:
        topic_details = ", ".join(
            f"{ta.topic_id}:{ta.agreement_ratio:.2f}" for ta in topic_agreements
        )
        detail_parts.append(f"per_topic=[{topic_details}]")

    return ConvergenceResult(
        position_deltas=deltas,
        delta_norm=delta_norm,
        topic_agreements=topic_agreements,
        agreement_ratio=agreement_ratio,
        composite_score=composite_score,
        threshold=cfg.threshold,
        has_converged=has_converged,
        convergence_status=status,
        matched_position_count=len(deltas),
        total_current_positions=len(current_positions),
        config=cfg,
        detail="; ".join(detail_parts),
    )


# ── Convenience helpers ─────────────────────────────────────────────────


def convergence_from_conflict_resolutions(
    resolved_count: int,
    total_conflicts: int,
    avg_resolution_confidence: float = 1.0,
    threshold: float | None = None,
) -> ConvergenceResult:
    """Build a convergence result from conflict resolution statistics.

    This is a convenience helper for the Coordinator to derive a
    convergence verdict from the post-rebuttal resolution decisions
    without requiring full position tracking.  It maps the
    ``ConflictResolution`` outcomes to a convergence score.

    The resolved ratio (resolved / total) is treated as the agreement
    ratio, and the delta norm is set to 1.0 (no position-level tracking).

    Args:
        resolved_count: Number of resolved conflicts.
        total_conflicts: Total number of conflict pairs.
        avg_resolution_confidence: Average confidence across resolutions.
        threshold: Override convergence threshold.

    Returns:
        ``ConvergenceResult`` with derived metrics.

    Raises:
        ValueError: If ``total_conflicts`` is zero.
    """
    if total_conflicts <= 0:
        raise ValueError("total_conflicts must be positive")

    resolved_ratio = resolved_count / total_conflicts
    # Composite: blend resolution ratio with confidence
    composite_score = (resolved_ratio * 0.7) + (avg_resolution_confidence * 0.3)
    cfg = ConvergenceConfig(threshold=threshold or DEFAULT_CONVERGENCE_THRESHOLD)

    has_converged = composite_score >= cfg.threshold

    status = "converged" if has_converged else "diverging"

    return ConvergenceResult(
        position_deltas=(),
        delta_norm=1.0,
        topic_agreements=(),
        agreement_ratio=resolved_ratio,
        composite_score=composite_score,
        threshold=cfg.threshold,
        has_converged=has_converged,
        convergence_status=status,
        matched_position_count=0,
        total_current_positions=total_conflicts,
        config=cfg,
        detail=(
            f"Derived from conflict resolutions: {resolved_count}/{total_conflicts} "
            f"resolved (ratio={resolved_ratio:.3f}), "
            f"avg_confidence={avg_resolution_confidence:.3f}, "
            f"composite={composite_score:.3f}"
        ),
    )
