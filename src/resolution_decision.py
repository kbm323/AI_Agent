"""Resolution-or-Escalation Decision for post-rebuttal conflict pairs.

Sub-AC 5b-3: Given post-rebuttal revised positions per conflict pair,
determine whether each conflict is resolved (consensus reached) or
escalated (deadlock, passed to Round 3/human); testable by asserting
correct resolved vs escalated classification from known outcomes.

Architecture
------------
The resolution decision engine sits between the rebuttal exchange
(Sub-AC 5b-2) and the Round 3 escalation path.  It receives the
``RebuttalExchangeResult`` and the original ``ConflictDetectionResult``
and performs a multi-factor classification:

1. **Stance convergence analysis** — do the post-rebuttal stances
   indicate agreement, compatibility, or persistent opposition?
2. **Revision tracking** — did either side revise their position?
   A revision toward compromise is a strong resolution signal.
3. **Severity re-evaluation** — has the conflict severity meaningfully
   decreased?  High-severity conflicts that remain high after rebuttal
   are candidates for escalation.
4. **Validity acknowledgement** — mutual acknowledgement of valid
   aspects suggests good-faith engagement and potential resolution.
5. **Deadlock detection** — if neither side moved and stances remain
   directly opposing, the conflict is deadlocked.

The module is pure-in-memory (no filesystem I/O), fully testable with
hand-crafted rebuttal data, and follows the immutable dataclass patterns
of ``conflict_detector.py`` and ``rebuttal_exchange.py``.

Usage::

    from src.conflict_detector import detect_conflicts
    from src.rebuttal_exchange import execute_rebuttal_exchange
    from src.resolution_decision import (
        ResolutionDecision,
        ConflictResolution,
        ResolutionResult,
        classify_resolutions,
    )

    # After execute_rebuttal_exchange()...
    resolutions = classify_resolutions(
        conflict_result=conflicts,
        exchange_result=exchange,
    )

    for cr in resolutions.conflict_resolutions:
        print(f"{cr.topic_id}: {cr.decision}")
        print(f"  Reason: {cr.rationale}")
        print(f"  Next action: {cr.next_action}")

    if resolutions.requires_escalation:
        print("Some conflicts need escalation to Round 3 or human review.")
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from src.conflict_detector import ConflictDetectionResult, ConflictPair
from src.rebuttal_exchange import RebuttalExchangeResult, RebuttalPacket, RevisionRecord


# ── Data types ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConflictResolution:
    """Resolution-or-escalation decision for a single conflict pair.

    Each conflict pair from Round 1 receives exactly one
    ``ConflictResolution`` after the rebuttal exchange has completed.
    The ``decision`` field is the key output: ``resolved`` or
    ``escalated``.

    Attributes:
        topic_id: The topic identifier (kebab-case).
        conflict_pair_index: Index into the original ``ConflictPair`` list.
        persona_a: First persona's role_id.
        persona_b: Second persona's role_id.
        original_stance_a: Persona A's Round 1 stance.
        original_stance_b: Persona B's Round 1 stance.
        post_rebuttal_stance_a: Persona A's stance after rebuttal.
        post_rebuttal_stance_b: Persona B's stance after rebuttal.
        revised_by_a: Whether persona A revised their position.
        revised_by_b: Whether persona B revised their position.
        acknowledged_a: Whether persona A acknowledged B's validity.
        acknowledged_b: Whether persona B acknowledged A's validity.
        original_severity: Severity from Round 1 conflict detection.
        decision: ``resolved`` or ``escalated``.
        rationale: Human-readable explanation of the decision.
        next_action: Recommended next action for the Coordinator:
            ``continue_consensus``, ``round_3_debate``,
            ``human_escalation``, ``accept_conditional``,
            ``tie_break_needed``.
        resolution_confidence: Confidence in the decision (0.0-1.0).
    """

    topic_id: str
    """The topic identifier."""

    conflict_pair_index: int
    """Index into the original ConflictPair list."""

    persona_a: str
    """First persona's role_id."""

    persona_b: str
    """Second persona's role_id."""

    original_stance_a: str
    """Persona A's Round 1 stance."""

    original_stance_b: str
    """Persona B's Round 1 stance."""

    post_rebuttal_stance_a: str
    """Persona A's stance after rebuttal."""

    post_rebuttal_stance_b: str
    """Persona B's stance after rebuttal."""

    revised_by_a: bool
    """Whether persona A revised their position."""

    revised_by_b: bool
    """Whether persona B revised their position."""

    acknowledged_a: bool
    """Whether persona A acknowledged B's valid aspects."""

    acknowledged_b: bool
    """Whether persona B acknowledged A's valid aspects."""

    original_severity: float
    """Severity from Round 1 conflict detection (0.0-1.0)."""

    decision: str
    """``resolved`` or ``escalated``."""

    rationale: str
    """Human-readable explanation of the decision."""

    next_action: str
    """Recommended next action:
    ``continue_consensus``, ``round_3_debate``,
    ``human_escalation``, ``accept_conditional``,
    ``tie_break_needed``."""

    resolution_confidence: float
    """Confidence in the decision (0.0-1.0)."""

    @property
    def is_resolved(self) -> bool:
        """True when the conflict is resolved."""
        return self.decision == "resolved"

    @property
    def is_escalated(self) -> bool:
        """True when the conflict is escalated."""
        return self.decision == "escalated"

    @property
    def stances_converged(self) -> bool:
        """True when post-rebuttal stances are compatible."""
        return not _are_directly_opposing(
            self.post_rebuttal_stance_a, self.post_rebuttal_stance_b
        )

    @property
    def either_revised(self) -> bool:
        """True when at least one side revised their position."""
        return self.revised_by_a or self.revised_by_b

    @property
    def both_revised(self) -> bool:
        """True when both sides revised their positions."""
        return self.revised_by_a and self.revised_by_b


@dataclass(frozen=True)
class ResolutionResult:
    """Complete classification result for all conflict pairs.

    Aggregates the per-pair ``ConflictResolution`` decisions into a
    single result the Coordinator can use to determine the next meeting
    phase: consensus building (all resolved), Round 3 escalation (some
    unresolved), or human-in-the-loop (deadlocked).

    Attributes:
        conflict_resolutions: Per-conflict-pair resolution decisions.
        total_conflicts: Total number of conflict pairs analysed.
        resolved_count: Number of conflict pairs classified as resolved.
        escalated_count: Number of conflict pairs classified as
            escalated.
        requires_escalation: True when at least one conflict is
            escalated (Coordinator must take further action).
        requires_human: True when at least one escalated conflict has
            severity >= 0.8 and requires human-in-the-loop.
        tie_break_needed: True when deadlock on any pair requires
            tie-breaking (Round 3+1 or human chair decision).
        overall_consensus_score: Aggregate score 0.0-1.0 reflecting
            how close the meeting is to full consensus.
    """

    conflict_resolutions: tuple[ConflictResolution, ...]
    """Per-conflict-pair resolution decisions."""

    total_conflicts: int
    """Total number of conflict pairs analysed."""

    resolved_count: int
    """Number of conflict pairs classified as resolved."""

    escalated_count: int
    """Number of conflict pairs classified as escalated."""

    requires_escalation: bool
    """True when at least one conflict is escalated."""

    requires_human: bool
    """True when at least one escalated conflict needs human review."""

    tie_break_needed: bool
    """True when deadlock requires tie-breaking."""

    overall_consensus_score: float
    """0.0 (no consensus) to 1.0 (full consensus)."""

    @property
    def all_resolved(self) -> bool:
        """True when every conflict pair is resolved."""
        return self.escalated_count == 0

    @property
    def consensus_ratio(self) -> float:
        """Ratio of resolved conflicts to total (0.0-1.0)."""
        if self.total_conflicts == 0:
            return 1.0
        return self.resolved_count / self.total_conflicts

    def get_by_topic(self, topic_id: str) -> ConflictResolution | None:
        """Retrieve a resolution by topic_id."""
        for cr in self.conflict_resolutions:
            if cr.topic_id == topic_id:
                return cr
        return None

    def get_escalated(self) -> tuple[ConflictResolution, ...]:
        """Return only escalated conflict resolutions."""
        return tuple(cr for cr in self.conflict_resolutions if cr.is_escalated)

    def get_resolved(self) -> tuple[ConflictResolution, ...]:
        """Return only resolved conflict resolutions."""
        return tuple(cr for cr in self.conflict_resolutions if cr.is_resolved)


# ── Callable type aliases (injectable for testing) ────────────────────

ResolutionClassifierFn = Callable[
    [
        ConflictPair,             # Original conflict pair
        list[RebuttalPacket],     # Rebuttal packets for this pair
        list[RevisionRecord],     # Revisions for this pair
    ],
    tuple[str, str, str, float],
    # Returns: (decision, rationale, next_action, confidence)
]
"""Signature for a callable that classifies a single conflict pair."""


# ── Default resolution classifier ──────────────────────────────────────


def _default_classify_resolution(
    conflict: ConflictPair,
    pair_rebuttals: list[RebuttalPacket],
    pair_revisions: list[RevisionRecord],
) -> tuple[str, str, str, float]:
    """Classify a conflict pair as resolved or escalated.

    This is the default implementation that uses structured rule-based
    analysis without LLM calls.  The classification follows a
    decision tree:

    1. **Complete convergence**: Both stances compatible → resolved.
    2. **Revision toward consensus**: At least one side revised and
       post-rebuttal stances are compatible → resolved.
    3. **Mutual acknowledgement**: Both acknowledged validity and
       moved to non-opposing stances → resolved (conditional_pass).
    4. **Persistent opposition**: Both maintain directly opposing
       stances with no revision → escalated.
    5. **High-severity deadlock**: Direct opposition persists with
       severity >= 0.8 → escalated, human-in-the-loop.
    6. **Partial movement**: One side moved but stances still
       incompatible → escalated (needs Round 3 debate).
    7. **Low-severity priority divergence**: Both support-like stances
       but different urgency → resolved (conditional).

    Args:
        conflict: The original ``ConflictPair``.
        pair_rebuttals: All rebuttal packets for this conflict pair.
        pair_revisions: All revision records for this conflict pair.

    Returns:
        Tuple of (decision, rationale, next_action, confidence).
    """
    # ── Extract post-rebuttal stances ──────────────────────────────
    # Default to original stances if no rebuttal data
    stance_a_after = conflict.stance_a
    stance_b_after = conflict.stance_b
    revised_a = False
    revised_b = False
    ack_a = False
    ack_b = False

    # Find rebuttal from persona A (rebutting persona B)
    for rb in pair_rebuttals:
        if rb.rebutting_persona == conflict.persona_a:
            stance_a_after = rb.stance_after
            ack_a = rb.acknowledges_validity
        elif rb.rebutting_persona == conflict.persona_b:
            stance_b_after = rb.stance_after
            ack_b = rb.acknowledges_validity

    # Check revisions
    for rev in pair_revisions:
        if rev.persona_id == conflict.persona_a:
            revised_a = True
            # Use revised stance from revision record if available
            if rev.revised_stance:
                stance_a_after = rev.revised_stance
        elif rev.persona_id == conflict.persona_b:
            revised_b = True
            if rev.revised_stance:
                stance_b_after = rev.revised_stance

    opposing_after = _are_directly_opposing(stance_a_after, stance_b_after)
    severity = conflict.severity

    # ── Decision tree ──────────────────────────────────────────────

    # CASE 1: Both stances are compatible (not directly opposing)
    # AND at least one indicator of good-faith engagement
    if not opposing_after:
        if revised_a or revised_b:
            # Revision led to convergence — strong resolution signal
            reviser = (
                conflict.persona_a if revised_a else conflict.persona_b
            )
            return (
                "resolved",
                (
                    f"Stances converged: {stance_a_after} vs {stance_b_after}. "
                    f"{reviser} revised position after rebuttal exchange."
                ),
                "continue_consensus",
                0.90,
            )
        elif ack_a and ack_b:
            # Mutual acknowledgement without formal revision
            return (
                "resolved",
                (
                    f"Both personas acknowledged valid aspects. "
                    f"Stances ({stance_a_after} vs {stance_b_after}) are compatible."
                ),
                "accept_conditional",
                0.80,
            )
        elif stance_a_after == stance_b_after:
            # Same stance naturally emerged
            return (
                "resolved",
                (
                    f"Both personas converged to '{stance_a_after}' stance "
                    f"organically through the exchange."
                ),
                "continue_consensus",
                0.85,
            )
        else:
            # Compatible but not fully aligned — conditional resolution
            return (
                "resolved",
                (
                    f"Stances ({stance_a_after} vs {stance_b_after}) are "
                    f"compatible though not identical. Conditional consensus reached."
                ),
                "accept_conditional",
                0.75,
            )

    # CASE 2: Stances still directly opposing
    if opposing_after:
        if severity >= 0.8 and not (revised_a or revised_b):
            # High-severity deadlock with no movement → human escalation
            return (
                "escalated",
                (
                    f"Direct opposition persists ({stance_a_after} vs {stance_b_after}) "
                    f"with high severity ({severity:.2f}) and no revision from either side. "
                    f"This requires human review."
                ),
                "human_escalation",
                0.95,
            )

        if revised_a and revised_b:
            # Both tried to move but still opposing — intractable
            return (
                "escalated",
                (
                    f"Both personas attempted revision but stances remain "
                    f"directly opposing ({stance_a_after} vs {stance_b_after}). "
                    f"Conflict is intractable within current round structure."
                ),
                "tie_break_needed",
                0.90,
            )

        if revised_a or revised_b:
            # One side moved but opposition persists — needs more debate
            mover = conflict.persona_a if revised_a else conflict.persona_b
            non_mover = conflict.persona_b if revised_a else conflict.persona_a
            return (
                "escalated",
                (
                    f"{mover} revised position but {non_mover} maintained "
                    f"opposition ({stance_a_after} vs {stance_b_after}). "
                    f"Additional debate or mediator needed."
                ),
                "round_3_debate",
                0.80,
            )

        # Neither revised, severity moderate — needs Round 3 debate
        return (
            "escalated",
            (
                f"Neither persona revised their position. Stances remain "
                f"directly opposing ({stance_a_after} vs {stance_b_after}) "
                f"with severity {severity:.2f}. Escalating to Round 3."
            ),
            "round_3_debate",
            0.85,
        )

    # CASE 3: Edge cases — one stance is neutral (unusual after rebuttal)
    if stance_a_after == "neutral" or stance_b_after == "neutral":
        return (
            "resolved",
            (
                f"At least one persona adopted a neutral stance "
                f"({stance_a_after} vs {stance_b_after}), removing active opposition."
            ),
            "continue_consensus",
            0.70,
        )

    # Default: escalatory (should not reach here normally)
    return (
        "escalated",
        (
            f"Unable to determine clear resolution path. "
            f"Stances: {stance_a_after} vs {stance_b_after}. "
            f"Escalating for review."
        ),
        "human_escalation",
        0.50,
    )


# ── Stance compatibility helpers ───────────────────────────────────────


def _are_directly_opposing(a: str, b: str) -> bool:
    """Check if two stances are directly opposing.

    Direct opposition means no middle ground exists; these pairs
    cannot be resolved without one side conceding or a mediator
    imposing a decision.
    """
    opposing_pairs = {
        ("support", "oppose"),
        ("oppose", "support"),
        ("alternative_proposal", "oppose"),
        ("oppose", "alternative_proposal"),
    }
    return (a, b) in opposing_pairs


def _compute_resolution_confidence(
    converged: bool,
    revised: bool,
    severity: float,
    opposing: bool,
) -> float:
    """Compute confidence in the resolution classification.

    Higher confidence when:
    - Stances have converged
    - Revisions occurred
    - Severity is low
    - Not opposing
    """
    confidence = 0.5  # Baseline

    if converged:
        confidence += 0.25
    if revised:
        confidence += 0.10
    if not opposing:
        confidence += 0.10
    if severity < 0.5:
        confidence += 0.05
    elif severity >= 0.8:
        confidence -= 0.05

    return max(0.10, min(1.0, confidence))


# ── Thread-local injectable overrides ─────────────────────────────────

_resolution_classifier_store: threading.local = threading.local()
"""Thread-local storage for the active resolution classifier."""


def _get_classifier() -> ResolutionClassifierFn:
    """Return the currently active resolution classifier for this thread."""
    try:
        return _resolution_classifier_store.value  # type: ignore[no-any-return]
    except AttributeError:
        return _default_classify_resolution


def inject_classifier(classifier: ResolutionClassifierFn | None) -> None:
    """Inject a custom resolution classifier for testing.

    Pass ``None`` to restore the default classifier.
    Thread-safe — each thread maintains its own classifier.
    """
    if classifier is None:
        try:
            del _resolution_classifier_store.value
        except AttributeError:
            pass
    else:
        _resolution_classifier_store.value = classifier


# ── Public API ────────────────────────────────────────────────────────


def classify_resolutions(
    conflict_result: ConflictDetectionResult,
    exchange_result: RebuttalExchangeResult,
    *,
    _injected_classifier: ResolutionClassifierFn | None = None,
) -> ResolutionResult:
    """Classify each conflict pair as resolved or escalated.

    This is the main entry point for **Sub-AC 5b-3**.

    Steps:
    1. For each ``ConflictPair`` in ``conflict_result``, gather the
       associated rebuttal packets and revision records from
       ``exchange_result``.
    2. Run the classification logic on each pair.
    3. Produce a ``ResolutionResult`` with per-pair decisions and
       aggregate summary.

    Args:
        conflict_result: The ``ConflictDetectionResult`` from
            ``detect_conflicts()`` (Sub-AC 5b-1).
        exchange_result: The ``RebuttalExchangeResult`` from
            ``execute_rebuttal_exchange()`` (Sub-AC 5b-2).
        _injected_classifier: Per-call classifier override (for testing).

    Returns:
        ``ResolutionResult`` — inspect ``result.conflict_resolutions``,
        ``result.requires_escalation``, and ``result.overall_consensus_score``.

    Raises:
        TypeError: If ``conflict_result`` or ``exchange_result`` are
            not the expected types.
        ValueError: If the rebuttal exchange was not completed or
            conflict pairs in the results don't match.

    Examples:
        >>> from src.conflict_detector import detect_conflicts
        >>> from src.rebuttal_exchange import execute_rebuttal_exchange
        >>> packets = [
        ...     {"persona_id": "art-director", "opinion_content": "Use neon.",
        ...      "confidence": 0.9},
        ...     {"persona_id": "tech-director", "opinion_content": "Avoid neon.",
        ...      "confidence": 0.85},
        ... ]
        >>> conflicts = detect_conflicts(packets)
        >>> exchange = execute_rebuttal_exchange(conflicts, packets)
        >>> result = classify_resolutions(conflicts, exchange)
        >>> result.total_conflicts
        1
        >>> result.conflict_resolutions[0].decision in ("resolved", "escalated")
        True
    """
    # ── Type validation ────────────────────────────────────────────
    if not isinstance(conflict_result, ConflictDetectionResult):
        raise TypeError(
            f"conflict_result must be a ConflictDetectionResult, "
            f"got {type(conflict_result).__name__}"
        )
    if not isinstance(exchange_result, RebuttalExchangeResult):
        raise TypeError(
            f"exchange_result must be a RebuttalExchangeResult, "
            f"got {type(exchange_result).__name__}"
        )
    if not exchange_result.exchange_round_complete:
        raise ValueError(
            "The rebuttal exchange round must be complete before "
            "classifying resolutions."
        )

    # ── Choose classifier ──────────────────────────────────────────
    classifier = _injected_classifier or _get_classifier()

    # ── Group rebuttals and revisions by conflict pair index ───────
    rebuttals_by_pair: dict[int, list[RebuttalPacket]] = {}
    revisions_by_pair: dict[int, list[RevisionRecord]] = {}

    for rb in exchange_result.rebuttal_packets:
        idx = rb.conflict_pair_index
        rebuttals_by_pair.setdefault(idx, []).append(rb)

    for rev in exchange_result.revisions:
        # Map revision to conflict pair by matching persona and topic
        for idx, conflict in enumerate(conflict_result.conflict_pairs):
            if (
                rev.topic_id == conflict.topic_id
                and rev.persona_id in (conflict.persona_a, conflict.persona_b)
            ):
                revisions_by_pair.setdefault(idx, []).append(rev)
                break

    # ── Classify each conflict pair ────────────────────────────────
    resolutions: list[ConflictResolution] = []
    resolved_count = 0
    escalated_count = 0
    tie_break_count = 0
    human_escalation_count = 0

    for pair_idx, conflict in enumerate(conflict_result.conflict_pairs):
        pair_rebuttals = rebuttals_by_pair.get(pair_idx, [])
        pair_revisions = revisions_by_pair.get(pair_idx, [])

        decision, rationale, next_action, confidence = classifier(
            conflict,
            pair_rebuttals,
            pair_revisions,
        )

        # Extract post-rebuttal stances from rebuttal data
        stance_a_after = conflict.stance_a
        stance_b_after = conflict.stance_b
        revised_a = False
        revised_b = False
        ack_a = False
        ack_b = False

        for rb in pair_rebuttals:
            if rb.rebutting_persona == conflict.persona_a:
                stance_a_after = rb.stance_after
                ack_a = rb.acknowledges_validity
            elif rb.rebutting_persona == conflict.persona_b:
                stance_b_after = rb.stance_after
                ack_b = rb.acknowledges_validity

        for rev in pair_revisions:
            if rev.persona_id == conflict.persona_a:
                revised_a = True
                if rev.revised_stance:
                    stance_a_after = rev.revised_stance
            elif rev.persona_id == conflict.persona_b:
                revised_b = True
                if rev.revised_stance:
                    stance_b_after = rev.revised_stance

        cr = ConflictResolution(
            topic_id=conflict.topic_id,
            conflict_pair_index=pair_idx,
            persona_a=conflict.persona_a,
            persona_b=conflict.persona_b,
            original_stance_a=conflict.stance_a,
            original_stance_b=conflict.stance_b,
            post_rebuttal_stance_a=stance_a_after,
            post_rebuttal_stance_b=stance_b_after,
            revised_by_a=revised_a,
            revised_by_b=revised_b,
            acknowledged_a=ack_a,
            acknowledged_b=ack_b,
            original_severity=conflict.severity,
            decision=decision,
            rationale=rationale,
            next_action=next_action,
            resolution_confidence=confidence,
        )
        resolutions.append(cr)

        if decision == "resolved":
            resolved_count += 1
        else:
            escalated_count += 1
            if next_action == "tie_break_needed":
                tie_break_count += 1
            if next_action == "human_escalation":
                human_escalation_count += 1

    # ── Compute aggregate score ────────────────────────────────────
    if len(resolutions) == 0:
        overall_score = 1.0  # No conflicts = full consensus
    else:
        total_confidence = sum(cr.resolution_confidence for cr in resolutions)
        avg_confidence = total_confidence / len(resolutions)
        consensus_ratio = resolved_count / len(resolutions)
        # Blend confidence and ratio for the overall score
        overall_score = round(consensus_ratio * 0.6 + avg_confidence * 0.4, 2)

    return ResolutionResult(
        conflict_resolutions=tuple(resolutions),
        total_conflicts=len(resolutions),
        resolved_count=resolved_count,
        escalated_count=escalated_count,
        requires_escalation=escalated_count > 0,
        requires_human=human_escalation_count > 0,
        tie_break_needed=tie_break_count > 0,
        overall_consensus_score=overall_score,
    )
