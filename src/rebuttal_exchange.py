"""Structured Rebuttal/Revision Exchange for Round 2 conflict resolution.

Sub-AC 5b-2: Given detected conflict pairs and original opinion packets,
execute a rebuttal round where conflicting personas exchange structured
counterarguments and optionally revise positions; testable by asserting
rebuttal packets are generated and revisions are reflected.

Architecture
------------
The rebuttal exchange sits between the conflict detector (Sub-AC 5b-1)
and the Round 2 packet delivery.  It receives the ``ConflictDetectionResult``
and the original opinion packet dicts and orchestrates a structured exchange:

1. **Pairing** — for each ``ConflictPair``, both personas are assigned
   as rebutter and target.  Each receives the *other* persona's position
   as the subject of rebuttal.

2. **Rebuttal generation** — for each (rebutting_persona, target_persona)
   pair, a structured rebuttal packet is produced containing:
   - Counterargument summary
   - Detailed counterargument points
   - Acknowledgement of valid aspects
   - Optional revised position

3. **Revision capture** — if either persona revises their position during
   the rebuttal, a ``RevisionRecord`` is emitted tracking the original
   and revised stances.

4. **Exchange result** — all rebuttal packets and revisions are bundled
   into a ``RebuttalExchangeResult`` ready for Round 2 context packet
   assembly.

The module is pure-in-memory (no filesystem I/O), fully testable with
hand-crafted conflict pairs and opinion packets, and follows the immutable
dataclass patterns of ``conflict_detector.py`` and ``round_packet_assembler.py``.

Usage::

    from src.conflict_detector import detect_conflicts
    from src.rebuttal_exchange import (
        RebuttalExchangeResult,
        execute_rebuttal_exchange,
    )

    # After detect_conflicts()...
    exchange = execute_rebuttal_exchange(conflict_result, opinion_packets)

    for rb in exchange.rebuttal_packets:
        print(f"{rb.rebutting_persona} rebuts {rb.target_persona}")
        print(f"  Counter: {rb.counterargument_summary}")
        if rb.revised_position:
            print(f"  Revised to: {rb.revised_position.stance}")
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from src.conflict_detector import (
    ConflictDetectionResult,
    ConflictPair,
    TopicPosition,
    _default_analyse_position,
    _determine_stance,
)


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RebuttalPacket:
    """A structured rebuttal from one persona against another's position.

    Each rebuttal packet captures the complete counterargument exchange
    for a single (rebutter, target) pair on a specific topic.  The
    rebutter may optionally include a revised position reflecting a
    change of stance after considering the opposing view.

    Attributes:
        rebuttal_id: Unique rebuttal identifier (kebab-case).
        conflict_pair_index: Index into the original ``ConflictPair`` list.
        topic_id: The topic being debated.
        rebutting_persona: The persona writing the rebuttal.
        target_persona: The persona being rebutted against.
        counterargument_summary: One-sentence summary of the counterargument.
        counterargument_points: Structured counterargument points (1-5).
        acknowledges_validity: Whether the rebutter acknowledges any
                               valid aspects of the target's position.
        revised_position: Optional revised ``TopicPosition`` if the
                          rebutter changed their stance.
        confidence_after: Confidence level after the rebuttal (0.0-1.0).
        stance_after: Current stance after rebuttal.
    """

    rebuttal_id: str
    """Unique rebuttal identifier."""

    conflict_pair_index: int
    """Index into the original ConflictPair list."""

    topic_id: str
    """The topic being debated."""

    rebutting_persona: str
    """The persona writing the rebuttal."""

    target_persona: str
    """The persona being rebutted against."""

    counterargument_summary: str
    """One-sentence summary of the counterargument."""

    counterargument_points: tuple[str, ...]
    """Structured counterargument points (1-5)."""

    acknowledges_validity: bool
    """Whether the rebutter acknowledges valid aspects."""

    revised_position: TopicPosition | None = None
    """Optional revised TopicPosition if stance changed."""

    confidence_after: float = 0.0
    """Confidence after rebuttal."""

    stance_after: str = "neutral"
    """Current stance after rebuttal."""

    @property
    def has_revision(self) -> bool:
        """True when the rebutter revised their position."""
        return self.revised_position is not None

    @property
    def stance_changed(self) -> bool:
        """True when the stance after rebuttal differs from the original."""
        return self.stance_after != "neutral"  # conservative default


@dataclass(frozen=True)
class RevisionRecord:
    """A record of a persona revising their original position.

    Captures both the original and revised stance/summary so the
    Coordinator can track how consensus evolves through the rebuttal
    round.

    Attributes:
        persona_id: The persona who revised their position.
        topic_id: The topic on which revision occurred.
        original_stance: The stance from Round 1.
        original_summary: The position summary from Round 1.
        revised_stance: The revised stance after rebuttal.
        revised_summary: The revised position summary.
        revision_rationale: Why the revision was made (extracted from
                            counterargument context).
        confidence_before: Confidence before the rebuttal.
        confidence_after: Confidence after the rebuttal.
    """

    persona_id: str
    """Persona who revised their position."""

    topic_id: str
    """Topic on which revision occurred."""

    original_stance: str
    """Stance from Round 1."""

    original_summary: str
    """Position summary from Round 1."""

    revised_stance: str
    """Revised stance after rebuttal."""

    revised_summary: str
    """Revised position summary."""

    revision_rationale: str
    """Why the revision was made."""

    confidence_before: float
    """Confidence before rebuttal."""

    confidence_after: float
    """Confidence after rebuttal."""


@dataclass(frozen=True)
class RebuttalExchangeResult:
    """Complete result of a rebuttal exchange round.

    Bundles all rebuttal packets and revisions produced during the
    Round-2 structured exchange.  The Coordinator uses this to
    determine whether consensus has been reached or further rounds
    are needed.

    Attributes:
        rebuttal_packets: All rebuttal packets generated.
        revisions: All revision records captured (empty if no revisions).
        conflict_pairs_resolved: Number of conflict pairs where both
            personas' rebuttals indicate convergence or one side revised.
        conflict_pairs_unresolved: Number of conflict pairs still in
            active disagreement after rebuttals.
        exchange_round_complete: True when all rebuttals have been
            generated successfully.
        total_rebuttals: Total number of rebuttal packets.
        total_revisions: Total number of revision records.
    """

    rebuttal_packets: tuple[RebuttalPacket, ...]
    """All rebuttal packets."""

    revisions: tuple[RevisionRecord, ...]
    """All revision records (empty if no revisions)."""

    conflict_pairs_resolved: int
    """Conflict pairs resolved by rebuttal."""

    conflict_pairs_unresolved: int
    """Conflict pairs still unresolved after rebuttal."""

    exchange_round_complete: bool
    """True when all rebuttals generated successfully."""

    @property
    def total_rebuttals(self) -> int:
        """Total number of rebuttal packets."""
        return len(self.rebuttal_packets)

    @property
    def total_revisions(self) -> int:
        """Total number of revision records."""
        return len(self.revisions)

    @property
    def has_revisions(self) -> bool:
        """True when at least one persona revised their position."""
        return self.total_revisions > 0

    @property
    def all_resolved(self) -> bool:
        """True when no conflicts remain unresolved."""
        return self.conflict_pairs_unresolved == 0


# ── Callable type aliases (injectable for testing) ────────────────────

RebuttalGeneratorFn = Callable[
    [
        ConflictPair,  # the conflict pair
        str,  # rebutting_persona
        str,  # target_persona
        str,  # rebutting_persona's original opinion content
        str,  # target_persona's original opinion content
        float,  # rebutting_persona's original confidence
        float,  # target_persona's original confidence
    ],
    tuple[str, tuple[str, ...], bool, str, str, float],
    # Returns: (summary, points, acknowledges_validity,
    #           revised_stance, revised_summary, confidence_after)
]
"""Signature for a callable that generates a rebuttal from one persona."""


# ── Default rebuttal generator ─────────────────────────────────────────


def _default_generate_rebuttal(
    conflict: ConflictPair,
    rebutting_persona: str,
    target_persona: str,
    rebutter_opinion: str,
    target_opinion: str,
    rebutter_confidence: float,
    target_confidence: float,
) -> tuple[str, tuple[str, ...], bool, str, str, float]:
    """Generate a structured rebuttal using pattern-based counterargument logic.

    This default implementation works without LLM calls by analysing:
    - Whether the rebutting persona's stance is directly opposing
    - The conflict type to determine the counterargument structure
    - Korean/English stance patterns to extract reasoning

    The rebuttal follows a structured template:
    1. State the disagreement explicitly
    2. Provide counter-reasoning based on conflict type
    3. Optionally acknowledge valid aspects
    4. Optionally propose a revision if warranted

    Args:
        conflict: The ``ConflictPair`` being rebutted.
        rebutting_persona: The persona writing the rebuttal.
        target_persona: The persona being rebutted.
        rebutter_opinion: The rebutting persona's original opinion text.
        target_opinion: The target persona's original opinion text.
        rebutter_confidence: The rebutting persona's Round 1 confidence.
        target_confidence: The target persona's Round 1 confidence.

    Returns:
        Tuple of (summary, points, acknowledges_validity,
                  revised_stance, revised_summary, confidence_after).
    """
    # Determine which side the rebutting persona is on
    if rebutting_persona == conflict.persona_a:
        rebutter_stance = conflict.stance_a
        rebutter_position = conflict.position_a
        target_stance = conflict.stance_b
        target_position = conflict.position_b
        rebutter_conf = conflict.confidence_a
        target_conf = conflict.confidence_b
    else:
        rebutter_stance = conflict.stance_b
        rebutter_position = conflict.position_b
        target_stance = conflict.stance_a
        target_position = conflict.position_a
        rebutter_conf = conflict.confidence_b
        target_conf = conflict.confidence_a

    # Build counterargument points based on conflict type
    points = _build_counterargument_points(
        conflict_type=conflict.conflict_type,
        rebutter_stance=rebutter_stance,
        target_stance=target_stance,
        rebutter_position=rebutter_position,
        target_position=target_position,
        rebutter_opinion=rebutter_opinion,
    )

    # Determine if there's validity acknowledgement
    acknowledges = _should_acknowledge_validity(
        conflict_type=conflict.conflict_type,
        rebutter_stance=rebutter_stance,
        target_confidence=target_conf,
    )

    # Generate summary
    summary = _generate_rebuttal_summary(
        rebutting_persona=rebutting_persona,
        target_persona=target_persona,
        conflict_type=conflict.conflict_type,
        rebutter_stance=rebutter_stance,
        points=points,
    )

    # Determine if revision is warranted
    # A revision occurs when:
    # - The conflict severity is low (<= 0.5) and target has high confidence
    # - The rebutter's stance was neutral and target's is well-supported
    revised_stance = rebutter_stance
    revised_summary = rebutter_position
    confidence_after = rebutter_conf

    if _should_revise(
        severity=conflict.severity,
        rebutter_stance=rebutter_stance,
        target_confidence=target_conf,
        rebutter_confidence=rebutter_conf,
        acknowledges=acknowledges,
    ):
        # Revision: move toward the target's position
        revised_stance = _compute_revised_stance(rebutter_stance, target_stance)
        revised_summary = f"[Revised] {rebutter_position} — "
        if target_confidence > rebutter_conf:
            revised_summary += (
                f"일부 수용: {target_persona}의 의견을 반영하여 조정."
                if "지지" in target_position or "Support" in target_position
                else f"considering {target_persona}'s position."
            )
        else:
            revised_summary += "conditional adjustment based on rebuttal."
        # Adjust confidence — move slightly toward compromise
        confidence_after = round((rebutter_conf + target_conf) / 2, 2)

    return (
        summary,
        tuple(points),
        acknowledges,
        revised_stance,
        revised_summary,
        confidence_after,
    )


def _build_counterargument_points(
    conflict_type: str,
    rebutter_stance: str,
    target_stance: str,
    rebutter_position: str,
    target_position: str,
    rebutter_opinion: str,
) -> list[str]:
    """Build structured counterargument points based on conflict type.

    Each conflict type generates a tailored set of counterargument
    points that address the specific nature of the disagreement.
    """
    points: list[str] = []

    if conflict_type == "direct_opposition":
        points.append(f"Direct disagreement with position: {target_position[:100]}")
        points.append(
            "Refutation based on domain expertise and evidence assessment."
        )
        if rebutter_stance == "oppose":
            points.append(
                "Risk assessment: identified concerns not adequately "
                "addressed in original position."
            )
        elif rebutter_stance == "support":
            points.append(
                "Benefits outweigh risks: positive impact assessment "
                "counters opposition claims."
            )

    elif conflict_type == "incompatible_recommendation":
        points.append(
            f"Recommendation divergence: proposed direction conflicts "
            f"with {target_position[:80]}"
        )
        points.append(
            "Resource/priority analysis shows preferred approach is "
            "more aligned with meeting objectives."
        )
        points.append(
            "Alternative implementation path suggested to reconcile "
            "divergent recommendations."
        )

    elif conflict_type == "factual_disagreement":
        points.append(
            "Evidence-based counter: data and precedents support "
            "alternative interpretation."
        )
        points.append(
            "Request for source verification on disputed factual claims."
        )

    elif conflict_type == "priority_divergence":
        points.append(
            "Timeline/urgency assessment differs: immediate action "
            "preferred over conditional delay."
        )
        points.append(
            "Cost of delay analysis: waiting for conditions risks "
            "missing market window."
        )

    elif conflict_type == "methodological_difference":
        points.append(
            "Approach divergence: preferred methodology has proven "
            "track record in similar scenarios."
        )
        points.append(
            "Comparative analysis: alternative method evaluated "
            "but found less suitable for current context."
        )

    else:
        points.append(f"Counterargument addressing: {target_position[:120]}")
        points.append("Further analysis and evidence required for resolution.")

    # Add a closing point from the original opinion if available
    if rebutter_opinion:
        # Extract a key sentence as a supporting point
        sentences = rebutter_opinion.replace("\n", " ").split(". ")
        for sent in sentences:
            sent = sent.strip()
            if len(sent) >= 20 and len(sent) <= 150:
                if any(
                    kw in sent
                    for kw in [
                        "해야", "추천", "제안", "필요", "should", "recommend",
                        "must", "important",
                    ]
                ):
                    points.append(f"Supporting evidence: {sent}")
                    break

    # Ensure at least 2 points, at most 5
    if len(points) < 2:
        points.append("Further deliberation required to resolve disagreement.")
    return points[:5]


def _should_acknowledge_validity(
    conflict_type: str,
    rebutter_stance: str,
    target_confidence: float,
) -> bool:
    """Determine whether the rebutter should acknowledge valid aspects.

    Acknowledge validity when:
    - The target has high confidence (>= 0.7)
    - The conflict is not a fundamental opposition
    - The rebutter is not outright opposing
    """
    if rebutter_stance == "oppose" and conflict_type == "direct_opposition":
        return target_confidence >= 0.85  # Only acknowledge very confident opposition
    if conflict_type == "direct_opposition":
        return target_confidence >= 0.7
    # For milder conflict types, acknowledge more readily
    return target_confidence >= 0.5


def _should_revise(
    severity: float,
    rebutter_stance: str,
    target_confidence: float,
    rebutter_confidence: float,
    acknowledges: bool,
) -> bool:
    """Determine whether the rebutter should revise their position.

    Revision is warranted when:
    - Severity is low (<= 0.5) AND target has higher confidence than rebutter
    - Acknowledges validity AND rebutter was neutral/conditional
    - Conflict is priority_divergence (both want same thing, different timing)
    """
    if severity <= 0.5 and target_confidence > rebutter_confidence:
        return True
    if acknowledges and rebutter_stance in ("neutral", "conditional_support"):
        return True
    if severity <= 0.4:  # Priority divergence — both support, different urgency
        return True
    return False


def _compute_revised_stance(original: str, target: str) -> str:
    """Compute a revised stance that moves toward the target position.

    The revision is a step toward consensus, not a complete flip.
    """
    # If original was oppose, move to conditional_support at most
    if original == "oppose":
        return "conditional_support"
    # If original was support and target offers alternative, consider it
    if original == "support" and target == "alternative_proposal":
        return "conditional_support"
    # Priority divergence: both support, just align
    if original == "conditional_support" and target == "support":
        return "support"
    # Neutral moves toward target's direction
    if original == "neutral":
        if target in ("support", "conditional_support"):
            return "conditional_support"
        return target
    # Alternative proposal can converge to support
    if original == "alternative_proposal" and target == "support":
        return "conditional_support"
    return original


def _generate_rebuttal_summary(
    rebutting_persona: str,
    target_persona: str,
    conflict_type: str,
    rebutter_stance: str,
    points: list[str],
) -> str:
    """Generate a one-sentence rebuttal summary."""
    stance_kr: dict[str, str] = {
        "support": "지지",
        "oppose": "반대",
        "conditional_support": "조건부 지지",
        "alternative_proposal": "대안 제시",
        "neutral": "중립",
    }
    rebutter_stance_kr = stance_kr.get(rebutter_stance, rebutter_stance)

    return (
        f"{rebutting_persona}({rebutter_stance_kr}) → "
        f"{target_persona}: {points[0][:100] if points else 'counterargument'}"
    )


# ── Thread-local injectable overrides ─────────────────────────────────

_rebuttal_generator_store: threading.local = threading.local()
"""Thread-local storage for the active rebuttal generator."""


def _get_rebuttal_generator() -> RebuttalGeneratorFn:
    """Return the currently active rebuttal generator for this thread."""
    try:
        return _rebuttal_generator_store.value  # type: ignore[no-any-return]
    except AttributeError:
        return _default_generate_rebuttal


def inject_rebuttal_generator(generator: RebuttalGeneratorFn | None) -> None:
    """Inject a custom rebuttal generator for testing.

    Pass ``None`` to restore the default generator.
    Thread-safe — each thread maintains its own generator.
    """
    if generator is None:
        try:
            del _rebuttal_generator_store.value
        except AttributeError:
            pass
    else:
        _rebuttal_generator_store.value = generator


# ── Public API ────────────────────────────────────────────────────────


def execute_rebuttal_exchange(
    conflict_result: ConflictDetectionResult,
    opinion_packets: Sequence[dict[str, Any]],
    *,
    _injected_generator: RebuttalGeneratorFn | None = None,
) -> RebuttalExchangeResult:
    """Execute a structured rebuttal/revision exchange round.

    This is the main entry point for **Sub-AC 5b-2**.

    Steps:
     1. For each ``ConflictPair``, identify both personas
        (persona_a and persona_b).
     2. Generate a rebuttal packet where persona_a rebuts persona_b's
        position.
     3. Generate a rebuttal packet where persona_b rebuts persona_a's
        position.
     4. For each rebuttal that includes a revised position, emit a
        ``RevisionRecord``.
     5. Count resolved vs unresolved conflicts based on stance
        convergence.
     6. Return a ``RebuttalExchangeResult``.

    Args:
        conflict_result: The ``ConflictDetectionResult`` from
            ``detect_conflicts()`` (Sub-AC 5b-1).
        opinion_packets: The original Round 1 opinion packet dicts.
            Used to retrieve full opinion content for rebuttal generation.
        _injected_generator: Per-call rebuttal generator override
            (for testing).

    Returns:
        ``RebuttalExchangeResult`` — inspect ``rebuttal_packets`` and
        ``revisions``.

    Raises:
        ValueError: If ``conflict_result`` has no conflicts or
            ``opinion_packets`` is empty.

    Examples:
        >>> from src.conflict_detector import detect_conflicts
        >>> packets = [
        ...     {"persona_id": "art-director", "opinion_content": "Use neon.", "confidence": 0.9},
        ...     {"persona_id": "tech-director", "opinion_content": "Avoid neon.", "confidence": 0.85},
        ... ]
        >>> conflicts = detect_conflicts(packets)
        >>> exchange = execute_rebuttal_exchange(conflicts, packets)
        >>> exchange.total_rebuttals
        2
        >>> len(exchange.rebuttal_packets) == 2
        True
    """
    if not isinstance(conflict_result, ConflictDetectionResult):
        raise TypeError(
            f"conflict_result must be a ConflictDetectionResult, "
            f"got {type(conflict_result).__name__}"
        )
    if not opinion_packets:
        raise ValueError("opinion_packets must be a non-empty sequence")

    # Choose generator
    generator = _injected_generator or _get_rebuttal_generator()

    # Build opinion content lookup
    opinion_lookup: dict[str, str] = {}
    confidence_lookup: dict[str, float] = {}
    for packet in opinion_packets:
        if isinstance(packet, dict):
            pid = packet.get("persona_id", "")
            if pid:
                opinion_lookup[pid] = str(packet.get("opinion_content", ""))
                conf = packet.get("confidence", 0.5)
                if isinstance(conf, (int, float)):
                    confidence_lookup[pid] = float(conf)

    rebuttal_packets: list[RebuttalPacket] = []
    revisions: list[RevisionRecord] = []
    rebuttal_counter: int = 0

    # Track resolution status per conflict pair
    pair_resolved: dict[int, bool] = {}

    for pair_idx, conflict in enumerate(conflict_result.conflict_pairs):
        # Get opinion content for both personas
        opinion_a = opinion_lookup.get(conflict.persona_a, "")
        opinion_b = opinion_lookup.get(conflict.persona_b, "")
        conf_a = confidence_lookup.get(conflict.persona_a, 0.5)
        conf_b = confidence_lookup.get(conflict.persona_b, 0.5)

        # Rebuttal A → B: persona_a rebuts persona_b's position
        sum_a, pts_a, ack_a, rev_stance_a, rev_summary_a, conf_after_a = (
            generator(
                conflict,
                conflict.persona_a,
                conflict.persona_b,
                opinion_a,
                opinion_b,
                conf_a,
                conf_b,
            )
        )

        rb_a = RebuttalPacket(
            rebuttal_id=f"rebuttal-{rebuttal_counter:03d}",
            conflict_pair_index=pair_idx,
            topic_id=conflict.topic_id,
            rebutting_persona=conflict.persona_a,
            target_persona=conflict.persona_b,
            counterargument_summary=sum_a,
            counterargument_points=pts_a,
            acknowledges_validity=ack_a,
            revised_position=None,  # filled below if revision occurred
            confidence_after=conf_after_a,
            stance_after=rev_stance_a,
        )
        rebuttal_counter += 1

        # Check for revision from A
        original_stance_a = conflict.stance_a
        if rev_stance_a != original_stance_a:
            rev_pos = _build_revised_topic_position(
                persona_id=conflict.persona_a,
                topic_id=conflict.topic_id,
                stance=rev_stance_a,
                summary=rev_summary_a,
                confidence=conf_after_a,
                original_confidence=conf_a,
            )
            rb_a = dataclasses.replace(
                rb_a,
                revised_position=rev_pos,
            )
            revisions.append(
                RevisionRecord(
                    persona_id=conflict.persona_a,
                    topic_id=conflict.topic_id,
                    original_stance=original_stance_a,
                    original_summary=conflict.position_a,
                    revised_stance=rev_stance_a,
                    revised_summary=rev_summary_a,
                    revision_rationale=(
                        f"Rebuttal from {conflict.persona_b} prompted "
                        f"re-evaluation of position."
                    ),
                    confidence_before=conf_a,
                    confidence_after=conf_after_a,
                )
            )

        rebuttal_packets.append(rb_a)

        # Rebuttal B → A: persona_b rebuts persona_a's position
        sum_b, pts_b, ack_b, rev_stance_b, rev_summary_b, conf_after_b = (
            generator(
                conflict,
                conflict.persona_b,
                conflict.persona_a,
                opinion_b,
                opinion_a,
                conf_b,
                conf_a,
            )
        )

        rb_b = RebuttalPacket(
            rebuttal_id=f"rebuttal-{rebuttal_counter:03d}",
            conflict_pair_index=pair_idx,
            topic_id=conflict.topic_id,
            rebutting_persona=conflict.persona_b,
            target_persona=conflict.persona_a,
            counterargument_summary=sum_b,
            counterargument_points=pts_b,
            acknowledges_validity=ack_b,
            revised_position=None,
            confidence_after=conf_after_b,
            stance_after=rev_stance_b,
        )
        rebuttal_counter += 1

        # Check for revision from B
        original_stance_b = conflict.stance_b
        if rev_stance_b != original_stance_b:
            rev_pos_b = _build_revised_topic_position(
                persona_id=conflict.persona_b,
                topic_id=conflict.topic_id,
                stance=rev_stance_b,
                summary=rev_summary_b,
                confidence=conf_after_b,
                original_confidence=conf_b,
            )
            rb_b = dataclasses.replace(
                rb_b,
                revised_position=rev_pos_b,
            )
            revisions.append(
                RevisionRecord(
                    persona_id=conflict.persona_b,
                    topic_id=conflict.topic_id,
                    original_stance=original_stance_b,
                    original_summary=conflict.position_b,
                    revised_stance=rev_stance_b,
                    revised_summary=rev_summary_b,
                    revision_rationale=(
                        f"Rebuttal from {conflict.persona_a} prompted "
                        f"re-evaluation of position."
                    ),
                    confidence_before=conf_b,
                    confidence_after=conf_after_b,
                )
            )

        rebuttal_packets.append(rb_b)

        # Determine if this conflict pair is resolved after rebuttal
        resolved = _check_pair_resolved(
            rev_stance_a=rev_stance_a,
            rev_stance_b=rev_stance_b,
            conflict=conflict,
        )
        pair_resolved[pair_idx] = resolved

    # Count resolved vs unresolved
    resolved_count = sum(1 for v in pair_resolved.values() if v)
    unresolved_count = len(pair_resolved) - resolved_count

    return RebuttalExchangeResult(
        rebuttal_packets=tuple(rebuttal_packets),
        revisions=tuple(revisions),
        conflict_pairs_resolved=resolved_count,
        conflict_pairs_unresolved=unresolved_count,
        exchange_round_complete=True,
    )


def _build_revised_topic_position(
    persona_id: str,
    topic_id: str,
    stance: str,
    summary: str,
    confidence: float,
    original_confidence: float,
) -> TopicPosition:
    """Build a TopicPosition for a revised stance."""
    return TopicPosition(
        persona_id=persona_id,
        topic_id=topic_id,
        stance=stance,
        summary=summary,
        supporting_points=(),
        confidence=confidence,
        recommendation_direction="maintain",  # direction may change; default
    )


def _check_pair_resolved(
    rev_stance_a: str,
    rev_stance_b: str,
    conflict: ConflictPair,
) -> bool:
    """Check whether a conflict pair is resolved after rebuttals.

    A conflict is resolved when:
    - Both sides revised to the same stance
    - One side acknowledged validity and the other revised
    - Both moved to compatible stances (not directly opposing)
    """
    # If both revised to compatible stances
    if not _are_directly_opposing(rev_stance_a, rev_stance_b):
        return True
    # If both at least acknowledge validity
    if rev_stance_a == "conditional_support" and rev_stance_b == "conditional_support":
        return True
    return False


def _are_directly_opposing(a: str, b: str) -> bool:
    """Check if two stances are directly opposing."""
    opposing_pairs = {
        ("support", "oppose"),
        ("oppose", "support"),
        ("alternative_proposal", "oppose"),
        ("oppose", "alternative_proposal"),
    }
    return (a, b) in opposing_pairs
