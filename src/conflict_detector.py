"""Conflict detection for Round 1 multi-agent opinion packets.

Sub-AC 5b-1: Given Round 1 opinion packets from all personas, identify
conflicting position pairs (disagreeing on same topic/decision), output
structured conflict pairs with metadata; testable by asserting correct
conflict pairs are identified from known inputs.

Architecture
------------
The conflict detector sits between the round-1 packet assembly
(``round_packet_assembler``) and the consensus-building phase.  It
receives the assembled opinion packet set and performs a staged
analysis:

1. **Topic extraction** — each opinion packet's ``opinion_content`` is
   analysed to extract the key topics, decisions, and proposals it
   addresses.  The extractor is injectable via ``inject_topic_extractor``
   so tests can supply known topics without real LLM calls.

2. **Position inference** — for each topic-persona pair, the module
   determines the stance (position) the persona takes.  Similarly
   injectable via ``inject_position_analyzer``.

3. **Conflict identification** — all persona pairs are compared on each
   shared topic; conflicting positions are output with structured
   metadata (``ConflictPair``).

The module is pure-in-memory (no filesystem I/O), fully testable with
hand-crafted opinion packets, and returns immutable dataclass results
following the same patterns as ``round_packet_assembler`` and
``opinion_packet_validator``.

Usage::

    from src.conflict_detector import (
        ConflictPair,
        ConflictDetectionResult,
        detect_conflicts,
    )

    # After assemble_round_one_packets()...
    result = detect_conflicts(assembled.opinion_packets)

    for cp in result.conflict_pairs:
        print(f"Conflict: {cp.persona_a} vs {cp.persona_b}")
        print(f"  Topic: {cp.topic}")
        print(f"  {cp.persona_a}: {cp.position_a}")
        print(f"  {cp.persona_b}: {cp.position_b}")
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TopicExtraction:
    """A single topic extracted from a persona's opinion content.

    Attributes:
        topic_id: Short identifier for the topic (e.g. ``budget-allocation``).
        label: Human-readable topic label in the source language.
        key_terms: Key terms and phrases associated with this topic.
        excerpt: The relevant text excerpt from the opinion.
        character_offset: Where the excerpt starts in the opinion content.
    """

    topic_id: str
    """Short kebab-case topic identifier."""

    label: str
    """Human-readable topic label."""

    key_terms: tuple[str, ...]
    """Key terms and phrases associated with this topic."""

    excerpt: str
    """Relevant text excerpt from the opinion content."""

    character_offset: int
    """Character offset of the excerpt in the source opinion content (0-based)."""


@dataclass(frozen=True)
class TopicPosition:
    """A persona's position (stance) on a single topic.

    Attributes:
        persona_id: The persona who holds this position.
        topic_id: Which topic this position addresses.
        stance: The stance label (e.g. ``support``, ``oppose``, ``neutral``,
                ``conditional_support``, ``alternative_proposal``).
        summary: A short summary of the position (one sentence).
        supporting_points: Key arguments or evidence cited.
        confidence: The persona's own confidence score (from the opinion
                    packet, 0.0-1.0).
        recommendation_direction: Direction of the recommendation (e.g.
                                  ``increase``, ``decrease``, ``adopt``,
                                  ``reject``, ``defer``, ``explore``).
    """

    persona_id: str
    """The persona who holds this position."""

    topic_id: str
    """Which topic this position addresses."""

    stance: str
    """Stance label: support, oppose, neutral, conditional_support,
    alternative_proposal."""

    summary: str
    """One-sentence summary of the position."""

    supporting_points: tuple[str, ...]
    """Key arguments or evidence cited."""

    confidence: float
    """Persona's own confidence score (0.0-1.0)."""

    recommendation_direction: str
    """Direction: increase, decrease, adopt, reject, defer, explore, maintain."""


@dataclass(frozen=True)
class ConflictPair:
    """A conflicting position pair between two personas on the same topic.

    Two personas are in conflict when they take *opposing* stances
    (support vs oppose), propose *incompatible* recommendations, or
    disagree on *underlying facts/assumptions*.

    Attributes:
        topic: The topic being debated (``TopicExtraction.label``).
        topic_id: The topic identifier.
        persona_a: The first conflicting persona's ``role_id``.
        persona_b: The second conflicting persona's ``role_id``.
        position_a: Summary of persona A's position.
        position_b: Summary of persona B's position.
        stance_a: Persona A's stance label.
        stance_b: Persona B's stance label.
        conflict_type: One of ``direct_opposition``,
                       ``incompatible_recommendation``,
                       ``factual_disagreement``,
                       ``priority_divergence``,
                       ``methodological_difference``.
        severity: How severe the conflict is (0.0 = trivial,
                  1.0 = irreconcilable).
        confidence_a: Persona A's confidence.
        confidence_b: Persona B's confidence.
    """

    topic: str
    """Human-readable topic label."""

    topic_id: str
    """Topic identifier in kebab-case."""

    persona_a: str
    """First conflicting persona role_id."""

    persona_b: str
    """Second conflicting persona role_id."""

    position_a: str
    """Summary of persona A's position."""

    position_b: str
    """Summary of persona B's position."""

    stance_a: str
    """Persona A's stance label."""

    stance_b: str
    """Persona B's stance label."""

    conflict_type: str
    """One of: direct_opposition, incompatible_recommendation,
    factual_disagreement, priority_divergence, methodological_difference."""

    severity: float
    """Conflict severity (0.0 = trivial, 1.0 = irreconcilable)."""

    confidence_a: float
    """Persona A's confidence (0.0-1.0)."""

    confidence_b: float
    """Persona B's confidence (0.0-1.0)."""


@dataclass(frozen=True)
class ConflictDetectionResult:
    """Complete result of Round 1 conflict detection.

    Attributes:
        conflict_pairs: All detected conflict pairs (empty tuple when
                        consensus is achieved on all topics).
        conflict_count: Total number of conflict pairs detected.
        topics_identified: All topics identified across all opinions.
        personas_analysed: Set of persona IDs whose opinions were analysed.
        topic_persona_map: Map of topic_id → set of persona_ids that
                           addressed it.
        unanimous_topics: Topics where all participating personas agree
                          (no conflicts).
        conflict_severity_max: Highest severity score across all conflicts.
    """

    conflict_pairs: tuple[ConflictPair, ...]
    """All detected conflict pairs."""

    conflict_count: int
    """Total number of conflict pairs."""

    topics_identified: tuple[TopicExtraction, ...]
    """All unique topics identified across all opinions."""

    personas_analysed: tuple[str, ...]
    """Persona IDs whose opinions were analysed (sorted)."""

    topic_persona_map: dict[str, tuple[str, ...]]
    """Map of topic_id → persona_ids that addressed it."""

    unanimous_topics: tuple[str, ...]
    """Topic IDs where all participating personas agree."""

    conflict_severity_max: float
    """Highest severity score across all conflicts (0.0 if none)."""

    @property
    def has_conflicts(self) -> bool:
        """True when at least one conflict pair was detected."""
        return self.conflict_count > 0

    @property
    def requires_intervention(self) -> bool:
        """True when any conflict has severity >= 0.7 (requires
        Coordinator or validator intervention)."""
        return self.conflict_severity_max >= 0.7


# ── Callable type aliases (injectable for testing) ────────────────────


TopicExtractorFn = Callable[
    [str],  # opinion_content
    list[TopicExtraction],
]
"""Signature for a callable that extracts topics from opinion text."""

PositionAnalyzerFn = Callable[
    [str, TopicExtraction, float],  # opinion_content, topic, confidence
    TopicPosition | None,
]
"""Signature for a callable that analyses a persona's position on a topic."""


# ── Default topic extractor (Korean-aware) ─────────────────────────────


# Patterns for extracting structured points from opinion text.
# Korean opinion text typically contains numbered recommendations,
# bullet points, and decision-oriented sentences.

# Match numbered or bulleted points: "1.", "1)", "•", "-", "→"
_POINT_SPLIT_RE = re.compile(
    r"\n\s*(?:\d+[\.\)]\s*|[-•→]\s*|[①-⑩]\s*)"
)

# Match sentences that express a recommendation or decision
_DECISION_PATTERNS = [
    # Korean recommendation patterns
    r"(?:해야\s*합니|추천\s*합니|제안\s*합니|권고\s*합니|필요\s*합니)\w*",
    r"(?:해야\s*한\w*|추천\s*한\w*|제안\s*한\w*|권고\s*한\w*)\w*",
    r"(?:해야\s*됩니|추천\s*됩니|필요\s*됩니)\w*",
    r"(?:반대\s*합니|우려\s*됩니|위험\s*하\w*|신중\s*해)\w*",
    r"(?:채택|선택|결정|확정|진행|보류|검토)\w*(?:합니|해야|하겠)",
    # English patterns (for multi-lingual opinions)
    r"\b(?:should|must|recommend|suggest|propose|we need|it is (?:essential|crucial|important))\b",
    r"\b(?:I (?:recommend|suggest|propose|advise|oppose|disagree))\b",
    r"\b(?:we (?:must|should|need|ought|cannot|must not))\b",
]

_DECISION_RE = re.compile(
    "|".join(_DECISION_PATTERNS), re.IGNORECASE
)

# Match sentences that introduce a topic or domain
_TOPIC_INTRO_PATTERNS = [
    r"(?:관점|측면|입장|견해|의견)\w*\s*(?:에서|으로|:\s*)",
    r"(?:관련|대해|대한|대하여)\s*.*?(?:의견|판단|분석|평가)",
    r"(?:문제|이슈|리스크|위험)\w*\s*(?:는|은|에\s*대해)",
    r"\b(?:regarding|concerning|with respect to|in terms of|from the perspective of)\b",
]

_TOPIC_INTRO_RE = re.compile(
    "|".join(_TOPIC_INTRO_PATTERNS), re.IGNORECASE
)


def _default_extract_topics(opinion_content: str) -> list[TopicExtraction]:
    """Default topic extractor: splits opinion into point-sections,
    identifies topic cues, and returns structured ``TopicExtraction`` objects.

    The extractor works on structured opinion text (typically LLM-generated
    with numbered points or clear paragraph breaks):

    1. Splits the text at numbered/bulleted points and paragraph breaks.
    2. For each segment, attempts to identify a topic label.
    3. Extracts key terms from the segment.
    4. Returns a list of ``TopicExtraction`` objects.

    Args:
        opinion_content: The full ``opinion_content`` field from an
                         opinion packet.

    Returns:
        List of ``TopicExtraction`` objects (empty if no topics found).
    """
    if not opinion_content or not opinion_content.strip():
        return []

    text = opinion_content.strip()
    extractions: list[TopicExtraction] = []
    topic_counter: dict[str, int] = {}

    # Step 1: Split into logical segments
    # Try point-split first; if that yields too few segments, fall back
    # to paragraph splitting.
    segments = _POINT_SPLIT_RE.split("\n" + text)
    # Remove empty leading segment from the split artifact
    if segments and segments[0].strip() == "":
        segments = segments[1:]

    if len(segments) < 2:
        # Not point-structured — split by double newlines (paragraphs)
        segments = [s.strip() for s in re.split(r"\n\s*\n", text) if s.strip()]

    if len(segments) < 2:
        # Single block — try sentence splitting on decision cues
        segments = _DECISION_RE.split(text)
        segments = [s.strip() for s in segments if s.strip()]
        if len(segments) > 8:
            # Too granular; collapse back
            segments = [text]

    offset = 0
    for i, segment in enumerate(segments):
        seg_stripped = segment.strip()
        if not seg_stripped or len(seg_stripped) < 15:
            offset += len(segment) + 1
            continue

        # Step 2: Derive topic label from segment content
        label = _derive_topic_label(seg_stripped, i)

        # Step 3: Extract key terms
        key_terms = _extract_key_terms(seg_stripped)

        # Step 4: Ensure unique topic_id
        base_id = _label_to_topic_id(label)
        count = topic_counter.get(base_id, 0)
        topic_counter[base_id] = count + 1
        topic_id = f"{base_id}-{count}" if count > 0 else base_id

        # Find actual offset in the original text
        char_offset = text.find(seg_stripped, offset)
        if char_offset == -1:
            char_offset = offset

        extractions.append(
            TopicExtraction(
                topic_id=topic_id,
                label=label,
                key_terms=tuple(key_terms),
                excerpt=seg_stripped[:300],
                character_offset=char_offset,
            )
        )

        offset = char_offset + len(seg_stripped)

    return extractions


def _derive_topic_label(segment: str, index: int) -> str:
    """Derive a human-readable topic label from a text segment.

    Uses topic-intro patterns, first-sentence extraction, and
    fallback to positional labelling.
    """
    # Try topic-intro pattern match
    intro_match = _TOPIC_INTRO_RE.search(segment)
    if intro_match:
        # Use the sentence containing the intro pattern
        start = max(0, intro_match.start() - 30)
        end = min(len(segment), intro_match.end() + 80)
        snippet = segment[start:end].strip()
        # Clean up
        snippet = re.sub(r"\s+", " ", snippet)
        if len(snippet) > 80:
            snippet = snippet[:80] + "..."
        return snippet

    # Use first sentence (up to 80 chars) as label
    first_sentence = re.split(r"[.。!?\n]", segment)[0].strip()
    if first_sentence and len(first_sentence) >= 10:
        if len(first_sentence) > 80:
            first_sentence = first_sentence[:80] + "..."
        return first_sentence

    # Fallback: positional label
    return f"Topic {index + 1}"


def _extract_key_terms(segment: str) -> list[str]:
    """Extract key terms from a text segment.

    Identifies:
    - Proper noun-like sequences (consecutive capitalized or 한글 terms)
    - Domain-specific terminology patterns
    - Numeric references (budget, timeline)
    """
    terms: list[str] = []

    # Extract Korean compound nouns (2+ consecutive Hangul words)
    hangul_compounds = re.findall(r"[가-힣]{2,}(?:\s+[가-힣]{2,})+", segment)
    for compound in hangul_compounds:
        if len(compound) <= 20:
            terms.append(compound.strip())

    # Also extract individual significant Korean nouns (4+ chars)
    # as fallback when no compound nouns found
    if not terms:
        single_nouns = re.findall(r"[가-힣]{4,}", segment)
        for noun in single_nouns:
            terms.append(noun)
            if len(terms) >= 3:
                break

    # Extract English compounds (2+ words starting with uppercase)
    eng_compounds = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", segment)
    for compound in eng_compounds:
        if len(compound) <= 30:
            terms.append(compound.strip())

    # Extract key-value patterns (e.g., "예산: 50억", "Budget: $5M")
    kv_patterns = re.findall(
        r"(?:예산|비용|기간|인력|일정|budget|cost|timeline|staff)\s*[:：]\s*\S+",
        segment,
        re.IGNORECASE,
    )
    terms.extend(kv_patterns)

    # Limit and deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
            if len(unique) >= 5:
                break

    return unique


def _label_to_topic_id(label: str) -> str:
    """Convert a human-readable label to a kebab-case topic_id."""
    # Take first 10 significant words, transliterate Korean to romanised
    # or use a hash-based approach for Korean labels
    cleaned = re.sub(r"[^\w\s가-힣-]", "", label).strip().lower()

    # For Korean text, use the first few meaningful characters
    # to create a stable but readable ID
    korean_chars = re.findall(r"[가-힣]+", cleaned)
    if korean_chars:
        # Use first Korean word + first English/romanised word
        parts = [korean_chars[0]]
        eng_parts = re.findall(r"[a-z]+", cleaned)
        if eng_parts:
            parts.append(eng_parts[0])
        return "-".join(parts[:3])[:40]

    # English/latin text: standard kebab-case
    words = re.findall(r"[a-z0-9]+", cleaned)[:5]
    if words:
        return "-".join(words)[:40]

    # Fallback
    return f"topic-{hash(label) % 10000:04d}"


# ── Default position analyser (Korean-aware) ──────────────────────────


# Stance-indicating patterns in Korean and English
_STANCE_PATTERNS: dict[str, re.Pattern] = {
    "support": re.compile(
        r"(?:찬성|동의|지지|좋은\s*방향|적절|타당|올바른|공감|추천|채택|"
        r"agree|support|endorse|good\s*idea|right\s*approach|recommend)",
        re.IGNORECASE,
    ),
    "oppose": re.compile(
        r"(?:반대|부적절|옳지\s*않|문제\s*있|위험|우려|"
        r"disagree|oppose|reject|problematic|risky|concerning)",
        re.IGNORECASE,
    ),
    "conditional_support": re.compile(
        r"(?:조건\s*부|전제\s*로|경우\에\s*만|한다면|가정\s*하|"
        r"if\s*.*\s*then|provided\s*that|on\s*condition|conditional)",
        re.IGNORECASE,
    ),
    "alternative_proposal": re.compile(
        r"(?:대안|다른\s*방법|차라리|대신\s*에|변경|수정|"
        r"alternative|instead|rather|replace|modify|revise)",
        re.IGNORECASE,
    ),
}

# Recommendation direction patterns
_DIRECTION_PATTERNS: dict[str, re.Pattern] = {
    "adopt": re.compile(
        r"(?:채택|도입|시작|실행|적용|진행|"
        r"adopt|implement|start|proceed|apply)",
        re.IGNORECASE,
    ),
    "reject": re.compile(
        r"(?:거부|폐기|중단|포기|취소|"
        r"reject|abandon|stop|cancel|discard)",
        re.IGNORECASE,
    ),
    "increase": re.compile(
        r"(?:증가|증액|확대|강화|늘리|높이|올리|투자|"
        r"increase|expand|scale\s*up|invest|raise)",
        re.IGNORECASE,
    ),
    "decrease": re.compile(
        r"(?:감소|축소|줄이|낮추|절감|삭감|"
        r"decrease|reduce|cut|lower|minim[i|s]e)",
        re.IGNORECASE,
    ),
    "defer": re.compile(
        r"(?:보류|연기|미루|나중|추후|다음\s*단계|"
        r"defer|postpone|delay|later|next\s*phase)",
        re.IGNORECASE,
    ),
    "explore": re.compile(
        r"(?:탐색|검토|조사|연구|분석|확인|"
        r"explore|investigate|research|study|analyse|assess)",
        re.IGNORECASE,
    ),
}


def _default_analyse_position(
    opinion_content: str,
    topic: TopicExtraction,
    confidence: float,
) -> TopicPosition | None:
    """Default position analyser: determines a persona's stance on a topic.

    Analyses the opinion content around the topic excerpt to identify:
    - The stance (support/oppose/conditional/alternative)
    - The recommendation direction
    - Supporting arguments

    Args:
        opinion_content: The full opinion text.
        topic: The topic extraction to analyse the position on.
        confidence: The persona's confidence score.

    Returns:
        ``TopicPosition`` or ``None`` if position cannot be determined.
    """
    # Work with the excerpt and surrounding context
    excerpt = topic.excerpt
    if not excerpt:
        return None

    # Combine excerpt with its surrounding context (±200 chars)
    start = max(0, topic.character_offset - 100)
    end = min(len(opinion_content), topic.character_offset + len(excerpt) + 200)
    context = opinion_content[start:end]

    # Determine stance
    stance = _determine_stance(context)
    if stance == "neutral":
        # Try again with just the excerpt
        stance = _determine_stance(excerpt)

    # Determine recommendation direction
    direction = _determine_direction(context)
    if direction == "maintain":
        direction = _determine_direction(excerpt)

    # Extract supporting points (sentences containing evidence/rationale)
    supporting = _extract_supporting_points(context)

    # Generate a one-sentence position summary
    summary = _generate_position_summary(
        stance=stance,
        direction=direction,
        excerpt=excerpt,
        topic_label=topic.label,
    )

    return TopicPosition(
        persona_id="",  # filled by caller
        topic_id=topic.topic_id,
        stance=stance,
        summary=summary,
        supporting_points=tuple(supporting[:3]),
        confidence=confidence,
        recommendation_direction=direction,
    )


def _determine_stance(text: str) -> str:
    """Determine the dominant stance from text.

    Returns one of: ``support``, ``oppose``, ``conditional_support``,
    ``alternative_proposal``, ``neutral``.
    """
    scores: dict[str, int] = {}
    for stance, pattern in _STANCE_PATTERNS.items():
        matches = pattern.findall(text)
        scores[stance] = len(matches)

    # Remove neutral (not a pattern match — it's the fallback)
    if not scores or max(scores.values()) == 0:
        return "neutral"

    # Conditional support overrides regular support
    if scores.get("conditional_support", 0) > 0:
        return "conditional_support"

    # Oppose overrides support (stronger signal)
    oppose_score = scores.get("oppose", 0)
    support_score = scores.get("support", 0)

    if oppose_score > support_score:
        return "oppose"
    if support_score > oppose_score:
        return "support"
    if scores.get("alternative_proposal", 0) > 0:
        return "alternative_proposal"

    return "neutral"


def _determine_direction(text: str) -> str:
    """Determine the recommendation direction from text.

    Returns one of: ``adopt``, ``reject``, ``increase``, ``decrease``,
    ``defer``, ``explore``, ``maintain``.
    """
    scores: dict[str, int] = {}
    for direction, pattern in _DIRECTION_PATTERNS.items():
        matches = pattern.findall(text)
        scores[direction] = len(matches)

    if not scores or max(scores.values()) == 0:
        return "maintain"

    # Return the direction with the most matches
    best = max(scores, key=lambda k: scores[k])  # type: ignore[arg-type]
    if scores[best] == 0:
        return "maintain"
    return best


def _extract_supporting_points(text: str) -> list[str]:
    """Extract sentences that contain supporting arguments or evidence."""
    sentences = re.split(r"(?<=[.!?。])\s+", text)
    supporting: list[str] = []

    # Patterns that indicate reasoning/evidence
    reason_patterns = [
        r"(?:때문|이유|근거|증거|사례|예\s*를\s*들|예시|데이터|통계|"
        r"because|reason|evidence|example|data|statistics|case\s*study)",
        r"(?:따라서|그러므로|결과|영향|효과|"
        r"therefore|thus|consequence|impact|effect)",
    ]
    reason_re = re.compile("|".join(reason_patterns), re.IGNORECASE)

    for sent in sentences:
        sent = sent.strip()
        if not sent or len(sent) < 15:
            continue
        if reason_re.search(sent):
            supporting.append(sent[:200])
        if len(supporting) >= 3:
            break

    return supporting


def _generate_position_summary(
    stance: str,
    direction: str,
    excerpt: str,
    topic_label: str,
) -> str:
    """Generate a one-sentence position summary."""
    # Use the excerpt as the basis, trimmed
    short = excerpt.split("\n")[0].strip()
    if len(short) > 120:
        short = short[:120] + "..."

    stance_labels: dict[str, str] = {
        "support": "지지 (Support)",
        "oppose": "반대 (Oppose)",
        "conditional_support": "조건부 지지 (Conditional Support)",
        "alternative_proposal": "대안 제시 (Alternative Proposal)",
        "neutral": "중립 (Neutral)",
    }

    direction_labels: dict[str, str] = {
        "adopt": "→ 채택",
        "reject": "→ 거부",
        "increase": "→ 확대",
        "decrease": "→ 축소",
        "defer": "→ 보류",
        "explore": "→ 검토",
        "maintain": "→ 유지",
    }

    stance_str = stance_labels.get(stance, stance)
    dir_str = direction_labels.get(direction, "")

    return f"[{stance_str}{dir_str}] {short}"


# ── Core conflict identification ──────────────────────────────────────


def _identify_conflicts(
    positions_by_topic: dict[str, list[TopicPosition]],
) -> tuple[ConflictPair, ...]:
    """Identify conflict pairs from positions grouped by topic.

    Two positions are in conflict when they have opposing stances,
    incompatible recommendations, or both.

    Args:
        positions_by_topic: Dict mapping topic_id → list of
                            ``TopicPosition`` from different personas.

    Returns:
        Tuple of ``ConflictPair`` objects.
    """
    conflicts: list[ConflictPair] = []

    for topic_id, positions in positions_by_topic.items():
        if len(positions) < 2:
            continue  # Only one persona addressed this topic

        topic_label = positions[0].summary.split("] ")[-1][:60] if positions else topic_id

        # Compare every pair of positions
        n = len(positions)
        for i in range(n):
            for j in range(i + 1, n):
                pos_a = positions[i]
                pos_b = positions[j]

                conflict = _evaluate_pair(
                    topic_id=topic_id,
                    topic_label=topic_label,
                    pos_a=pos_a,
                    pos_b=pos_b,
                )
                if conflict is not None:
                    conflicts.append(conflict)

    # Sort by severity (highest first)
    conflicts.sort(key=lambda c: c.severity, reverse=True)
    return tuple(conflicts)


def _evaluate_pair(
    topic_id: str,
    topic_label: str,
    pos_a: TopicPosition,
    pos_b: TopicPosition,
) -> ConflictPair | None:
    """Evaluate whether two positions on the same topic conflict.

    Returns a ``ConflictPair`` if a conflict is detected, or ``None``
    if the positions are compatible.
    """
    stance_a = pos_a.stance
    stance_b = pos_b.stance
    direction_a = pos_a.recommendation_direction
    direction_b = pos_b.recommendation_direction

    conflict_type = ""
    severity = 0.0

    # Check 1: Direct opposition (support vs oppose)
    if _are_opposing_stances(stance_a, stance_b):
        conflict_type = "direct_opposition"
        severity = 0.9
    # Check 2: Incompatible recommendations
    elif _are_incompatible_directions(direction_a, direction_b):
        conflict_type = "incompatible_recommendation"
        severity = 0.7
    # Check 3: Alternative proposal vs adopt (someone wants to do
    #           something different than what another proposes)
    elif stance_a == "alternative_proposal" and stance_b in ("support", "neutral"):
        conflict_type = "methodological_difference"
        severity = 0.5
    elif stance_b == "alternative_proposal" and stance_a in ("support", "neutral"):
        conflict_type = "methodological_difference"
        severity = 0.5
    # Check 4: Priority divergence (both support but different urgency)
    elif stance_a == "support" and stance_b == "conditional_support":
        conflict_type = "priority_divergence"
        severity = 0.4
    elif stance_b == "support" and stance_a == "conditional_support":
        conflict_type = "priority_divergence"
        severity = 0.4
    # Check 5: Both neutral but different directions
    elif stance_a == "neutral" and stance_b == "neutral":
        if _are_incompatible_directions(direction_a, direction_b):
            conflict_type = "incompatible_recommendation"
            severity = 0.5
        else:
            return None  # Not a meaningful conflict
    else:
        return None  # No conflict detected

    # Adjust severity by confidence: high-confidence conflicts are
    # more severe (harder to resolve)
    avg_confidence = (pos_a.confidence + pos_b.confidence) / 2
    severity = round(severity * (0.7 + 0.3 * avg_confidence), 2)
    severity = min(1.0, max(0.0, severity))

    return ConflictPair(
        topic=topic_label,
        topic_id=topic_id,
        persona_a=pos_a.persona_id,
        persona_b=pos_b.persona_id,
        position_a=pos_a.summary,
        position_b=pos_b.summary,
        stance_a=stance_a,
        stance_b=stance_b,
        conflict_type=conflict_type,
        severity=severity,
        confidence_a=pos_a.confidence,
        confidence_b=pos_b.confidence,
    )


def _are_opposing_stances(a: str, b: str) -> bool:
    """Check if two stances are directly opposing."""
    opposing_pairs = {
        ("support", "oppose"),
        ("oppose", "support"),
        ("alternative_proposal", "oppose"),
        ("oppose", "alternative_proposal"),
    }
    return (a, b) in opposing_pairs


def _are_incompatible_directions(a: str, b: str) -> bool:
    """Check if two recommendation directions are incompatible."""
    incompatible_pairs = {
        ("adopt", "reject"),
        ("reject", "adopt"),
        ("increase", "decrease"),
        ("decrease", "increase"),
        ("adopt", "defer"),
        ("defer", "adopt"),
        ("adopt", "explore"),
        ("explore", "reject"),
        ("increase", "maintain"),
        ("decrease", "maintain"),
    }
    return (a, b) in incompatible_pairs


# ── Thread-local injectable overrides ─────────────────────────────────

_topic_extractor_store: threading.local = threading.local()
"""Thread-local storage for the active topic extractor."""

_position_analyzer_store: threading.local = threading.local()
"""Thread-local storage for the active position analyser."""


def _get_topic_extractor() -> TopicExtractorFn:
    """Return the currently active topic extractor for this thread."""
    try:
        return _topic_extractor_store.value  # type: ignore[no-any-return]
    except AttributeError:
        return _default_extract_topics


def _get_position_analyzer() -> PositionAnalyzerFn:
    """Return the currently active position analyser for this thread."""
    try:
        return _position_analyzer_store.value  # type: ignore[no-any-return]
    except AttributeError:
        return _default_analyse_position


def inject_topic_extractor(extractor: TopicExtractorFn | None) -> None:
    """Inject a custom topic extractor for testing.

    Pass ``None`` to restore the default extractor.
    Thread-safe — each thread maintains its own extractor.
    """
    if extractor is None:
        try:
            del _topic_extractor_store.value
        except AttributeError:
            pass
    else:
        _topic_extractor_store.value = extractor


def inject_position_analyzer(analyzer: PositionAnalyzerFn | None) -> None:
    """Inject a custom position analyser for testing.

    Pass ``None`` to restore the default analyser.
    Thread-safe — each thread maintains its own analyser.
    """
    if analyzer is None:
        try:
            del _position_analyzer_store.value
        except AttributeError:
            pass
    else:
        _position_analyzer_store.value = analyzer


# ── Public API ────────────────────────────────────────────────────────


def detect_conflicts(
    opinion_packets: Sequence[dict[str, Any]],
    *,
    _inject_extractor: TopicExtractorFn | None = None,
    _inject_analyzer: PositionAnalyzerFn | None = None,
) -> ConflictDetectionResult:
    """Analyse Round 1 opinion packets and detect conflicting position pairs.

    This is the main entry point for **Sub-AC 5b-1**.

    Steps:
    1. Extract topics from each opinion packet's ``opinion_content``.
    2. For each (persona, topic) pair, analyse the position/stance.
    3. Group positions by topic and compare persona pairs.
    4. Identify conflict pairs and return a ``ConflictDetectionResult``.

    Args:
        opinion_packets: A sequence of validated opinion packet dicts.
            Each dict must contain at minimum: ``persona_id``,
            ``opinion_content``, ``confidence``.
        _inject_extractor: Per-call topic extractor override (for testing).
        _inject_analyzer: Per-call position analyser override (for testing).

    Returns:
        ``ConflictDetectionResult`` — inspect ``result.conflict_pairs``
        and ``result.has_conflicts``.

    Raises:
        ValueError: If ``opinion_packets`` is empty.
        TypeError: If any packet is not a dict.

    Examples:
        >>> packets = [
        ...     {"persona_id": "art-director", "opinion_content": "Use neon palette.", "confidence": 0.9},
        ...     {"persona_id": "tech-director", "opinion_content": "Avoid neon; use pastel.", "confidence": 0.85},
        ... ]
        >>> result = detect_conflicts(packets)
        >>> result.has_conflicts
        True
        >>> len(result.conflict_pairs)
        1
    """
    if not opinion_packets:
        raise ValueError("opinion_packets must be a non-empty sequence")

    # Choose extractor and analyser
    extractor = _inject_extractor or _get_topic_extractor()
    analyzer = _inject_analyzer or _get_position_analyzer()

    # Phase 1: Extract topics from every opinion
    persona_topics: dict[str, list[TopicExtraction]] = {}
    all_topics: dict[str, TopicExtraction] = {}

    for packet in opinion_packets:
        if not isinstance(packet, dict):
            raise TypeError(
                f"Each packet must be a dict, got {type(packet).__name__}"
            )

        persona_id: str = packet.get("persona_id", "")
        if not persona_id:
            continue

        opinion_content: str = packet.get("opinion_content", "")
        if not opinion_content:
            continue

        topics = extractor(opinion_content)
        persona_topics[persona_id] = topics

        for t in topics:
            if t.topic_id not in all_topics:
                all_topics[t.topic_id] = t

    if not all_topics:
        return ConflictDetectionResult(
            conflict_pairs=(),
            conflict_count=0,
            topics_identified=(),
            personas_analysed=tuple(
                sorted(p.get("persona_id", "") for p in opinion_packets if p)
            ),
            topic_persona_map={},
            unanimous_topics=(),
            conflict_severity_max=0.0,
        )

    # Phase 2: Analyse positions for each (persona, topic)
    positions_by_topic: dict[str, list[TopicPosition]] = defaultdict(list)
    topic_personas: dict[str, set[str]] = defaultdict(set)

    for persona_id, topics in persona_topics.items():
        confidence = 0.5
        # Find the persona's confidence from the opinion packet
        for packet in opinion_packets:
            if isinstance(packet, dict) and packet.get("persona_id") == persona_id:
                conf = packet.get("confidence", 0.5)
                if isinstance(conf, (int, float)):
                    confidence = float(conf)
                break

        for topic in topics:
            opinion_content_full = _get_opinion_content(
                opinion_packets, persona_id
            )
            position = analyzer(opinion_content_full, topic, confidence)
            if position is not None:
                # Attach persona_id
                position = TopicPosition(
                    persona_id=persona_id,
                    topic_id=position.topic_id,
                    stance=position.stance,
                    summary=position.summary,
                    supporting_points=position.supporting_points,
                    confidence=position.confidence,
                    recommendation_direction=position.recommendation_direction,
                )
                positions_by_topic[topic.topic_id].append(position)
                topic_personas[topic.topic_id].add(persona_id)

    # Phase 3: Identify conflicts
    conflict_pairs = _identify_conflicts(dict(positions_by_topic))

    # Compute unanimous topics (all personas who addressed it agree)
    unanimous: list[str] = []
    for topic_id, positions in positions_by_topic.items():
        if len(positions) >= 2:
            # Check if all stances are compatible
            stances = {p.stance for p in positions}
            if len(stances) == 1 and "neutral" not in stances:
                # Same stance across all personas — check directions too
                directions = {p.recommendation_direction for p in positions}
                if len(directions) == 1 or all(
                    not _are_incompatible_directions(d1, d2)
                    for d1 in directions
                    for d2 in directions
                ):
                    unanimous.append(topic_id)

    # Compute severity max
    severity_max = 0.0
    for cp in conflict_pairs:
        if cp.severity > severity_max:
            severity_max = cp.severity

    # Build topic_persona_map as tuple values
    topic_persona_map: dict[str, tuple[str, ...]] = {
        tid: tuple(sorted(pids)) for tid, pids in topic_personas.items()
    }

    return ConflictDetectionResult(
        conflict_pairs=conflict_pairs,
        conflict_count=len(conflict_pairs),
        topics_identified=tuple(all_topics.values()),
        personas_analysed=tuple(sorted(persona_topics.keys())),
        topic_persona_map=topic_persona_map,
        unanimous_topics=tuple(sorted(unanimous)),
        conflict_severity_max=round(severity_max, 2),
    )


def _get_opinion_content(
    packets: Sequence[dict[str, Any]], persona_id: str
) -> str:
    """Retrieve the opinion_content for a given persona from the packet list."""
    for packet in packets:
        if isinstance(packet, dict) and packet.get("persona_id") == persona_id:
            return str(packet.get("opinion_content", ""))
    return ""
