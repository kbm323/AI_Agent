"""Context relevance validator — Sub-AC 6.2b.

Evaluates LLM response coherence against meeting context across four
dimensions: agenda relevance, topic alignment, off-topic detection,
and reference consistency.  The validator is entirely rule-based
(keyword heuristics + co-occurrence analysis) and requires no LLM
calls, making it deterministic, independently testable, and suitable
for the Coordinator's validation pipeline.

Design
------
This module mirrors the architecture of ``persona_consistency_validator``
(Sub-AC 6.2a) with a different input shape: ``(response, meeting_context)``
instead of ``(response, persona_spec)``.  No persona definitions are
required — the evaluation is purely content-driven.

Dimensions
---------
* **agenda_relevance** — keyword and semantic overlap between response
  and meeting agenda.
* **topic_alignment** — coverage of expected topic tags in the response.
* **off_topic_detection** — detection of off-domain language and
  irrelevant subject areas (higher score = less off-topic).
* **reference_consistency** — validation of references to meeting
  context artifacts (previous rounds, decisions, participant roles).

Testable with
-------------
* Fully relevant responses (baseline pass).
* Responses totally unrelated to the agenda (off-topic).
* Responses with partial tag coverage only.
* Responses referencing non-existent decisions/rounds.
* Edge cases: empty responses, empty contexts, mixed-language content.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# ── Off-topic detection: general-language stop-words that do not
# count as topic words (Korean + English) ───────────────────────────

_GENERAL_STOP_WORDS: frozenset[str] = frozenset({
    # Korean general/grammatical words (keep as whole-word tokens)
    "있다", "없다", "하다", "되다", "이다", "그", "이", "저",
    "것", "수", "년", "일", "월", "더", "때", "말", "위",
    "은", "는", "이", "가", "을", "를", "에", "의", "로",
    "에서", "으로", "에게", "한테", "하고", "와", "과",
    "그리고", "하지만", "그런데", "그래서", "그러나",
    "또는", "또한", "이런", "저런", "그런",
    "있다", "없다", "있다", "한다", "된다", "있는", "없는",
    "하는", "되는", "위해", "위한", "대한", "대해",
    "같은", "같이", "처럼", "부터", "까지", "조차", "마저",
    "만", "도", "라도", "나", "이나", "든지", "든가",
    "합니", "습니", "입니", "하겠", "되겠", "있겠",
    "합니다", "입니다", "습니다", "했습", "됐습",
    # English general words
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "may", "might", "shall", "should",
    "this", "that", "these", "those", "it", "its",
    "and", "or", "but", "if", "then", "else", "when", "where",
    "which", "who", "whom", "what", "why", "how",
    "in", "on", "at", "to", "for", "of", "from", "by", "with",
    "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "under",
    "not", "no", "nor", "so", "very", "just", "also", "only",
    "than", "too", "as", "well", "now", "here", "there",
    "i", "we", "you", "they", "he", "she", "me", "us", "him", "her",
    "my", "our", "your", "their", "his",
})

# ── Off-topic domain fingerprint: words that signal potentially
# irrelevant subject areas ─────────────────────────────────────────

_OFF_TOPIC_DOMAINS: dict[str, frozenset[str]] = {
    "cooking_food": frozenset({
        "recipe", "cooking", "bake", "fry", "ingredient", "delicious",
        "요리", "레시피", "음식", "맛있", "요리법",
    }),
    "sports": frozenset({
        "football", "soccer", "basketball", "baseball", "score",
        "championship", "tournament", "athlete", "sport",
        "축구", "야구", "농구", "경기", "스포츠",
    }),
    "medical_health": frozenset({
        "diagnosis", "prescription", "surgery", "patient", "symptom",
        "disease", "treatment", "clinical", "medical",
        "진단", "처방", "수술", "환자", "증상", "의학",
    }),
    "politics": frozenset({
        "election", "president", "congress", "senate", "parliament",
        "vote", "democracy", "political", "government",
        "선거", "대통령", "국회", "정치", "투표",
    }),
}


# ── Data types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RelevanceViolation:
    """A single context-relevance violation.

    Attributes:
        dimension: The evaluation dimension — ``agenda_relevance``,
                   ``topic_alignment``, ``off_topic``,
                   or ``reference_consistency``.
        severity: ``warning``, ``minor``, ``major``, ``critical``.
        message: Human-readable description of the violation.
        detail: Additional diagnostic context (matched text snippet,
                missing reference, off-topic signal word).
    """

    dimension: str
    severity: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class ContextRelevanceReport:
    """Aggregated result of context relevance validation.

    Attributes:
        passed: True when overall_score >= threshold and no critical
                violations exist.
        overall_score: Weighted aggregate 0.0–1.0.
        agenda_relevance_score: Agenda keyword overlap score 0.0–1.0.
        topic_alignment_score: Topic tag coverage score 0.0–1.0.
        off_topic_score: Off-topic severity score 0.0–1.0
                         (higher = less off-topic).
        reference_consistency_score: Reference consistency 0.0–1.0.
        violations: All detected relevance violations.
        response_length: Character count of the validated response.
        threshold: The pass threshold that was applied.
    """

    passed: bool
    overall_score: float
    agenda_relevance_score: float
    topic_alignment_score: float
    off_topic_score: float
    reference_consistency_score: float
    violations: tuple[RelevanceViolation, ...]
    response_length: int
    threshold: float = 0.70

    @property
    def violation_count(self) -> int:
        """Total number of violations."""
        return len(self.violations)

    @property
    def critical_violations(self) -> int:
        """Number of critical-severity violations."""
        return sum(1 for v in self.violations if v.severity == "critical")

    def violations_by_dimension(
        self,
    ) -> dict[str, tuple[RelevanceViolation, ...]]:
        """Group violations by evaluation dimension."""
        grouped: dict[str, list[RelevanceViolation]] = {}
        for v in self.violations:
            grouped.setdefault(v.dimension, []).append(v)
        return {k: tuple(v) for k, v in grouped.items()}

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "passed": self.passed,
            "overall_score": self.overall_score,
            "agenda_relevance_score": self.agenda_relevance_score,
            "topic_alignment_score": self.topic_alignment_score,
            "off_topic_score": self.off_topic_score,
            "reference_consistency_score": self.reference_consistency_score,
            "violations": [
                {
                    "dimension": v.dimension,
                    "severity": v.severity,
                    "message": v.message,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
            "response_length": self.response_length,
            "threshold": self.threshold,
        }


# ═══════════════════════════════════════════════════════════════════
# Helper: text tokenisation
# ═══════════════════════════════════════════════════════════════════


def _tokenise(text: str) -> list[str]:
    """Tokenise text into lowercase content words, stripping stop words.

    Korean words (space-separated Hangul) are kept as whole tokens since
    each word carries meaning.  Chinese characters (CJK ideographs) are
    split into individual characters since they are not space-separated.
    English/ASCII words are kept whole.
    """
    tokens: list[str] = []
    # Split into word-like chunks: alphanumeric + Korean + CJK runs
    for chunk in re.findall(r"[\w가-힣一-龥]+", text.lower()):
        # Only split Chinese (CJK ideographs), NOT Korean Hangul.
        # Korean words are space-separated and carry meaning as whole
        # units; splitting them into syllables creates noise.
        has_cjk_only = bool(
            re.search(r"[\u4E00-\u9FFF]", chunk)
            and not re.search(r"[\uAC00-\uD7A3]", chunk)
        )
        if has_cjk_only:
            tokens.extend(ch for ch in chunk if len(ch) >= 1)
        elif len(chunk) >= 2 and chunk not in _GENERAL_STOP_WORDS:
            tokens.append(chunk)
    return tokens


def _extract_bigrams(tokens: list[str]) -> set[str]:
    """Extract bigrams from a token list."""
    bigrams: set[str] = set()
    for i in range(len(tokens) - 1):
        bigrams.add(f"{tokens[i]} {tokens[i+1]}")
    return bigrams


# ── Cross-lingual tag mapping: common English tags → Korean
# equivalents that may appear in Korean responses ──────────────────

_EN_TO_KO_TAG_MAP: dict[str, list[str]] = {
    "music-video": ["뮤직비디오", "뮤비", "music video"],
    "visual-concept": ["비주얼컨셉", "비주얼 컨셉", "visual concept"],
    "opening-sequence": ["오프닝시퀀스", "오프닝 시퀀스", "opening sequence"],
    "teaser-content": ["티저콘텐츠", "티저 콘텐츠", "teaser content"],
    "brand-identity": ["브랜드아이덴티티", "브랜드 아이덴티티", "brand identity"],
    "creative-production": ["크리에이티브", "제작"],
    "technical-development": ["기술개발", "테크니컬", "개발"],
    "marketing-strategy": ["마케팅전략", "마케팅 전략"],
    "risk-assessment": ["리스크평가", "위험평가", "리스크 평가"],
    "general-planning": ["일반기획", "기획"],
    "project-review": ["프로젝트리뷰", "리뷰"],
}


def _fuzzy_token_match(
    tokens_a: set[str], tokens_b: set[str], min_stem_len: int = 2
) -> set[str]:
    """Fuzzy match between two token sets, handling Korean agglutination.

    For each token in ``tokens_a``, finds any token in ``tokens_b``
    where one is a prefix (stem) of the other.  This handles cases like
    ``컨셉`` ↔ ``컨셉을`` (Korean object-marker suffix) and
    ``비주얼`` ↔ ``비주얼컨셉`` (compound noun).

    Tokens shorter than ``min_stem_len`` are excluded to avoid
    spurious matches on grammatical particles.
    """
    matches: set[str] = set()
    for a in tokens_a:
        if len(a) < min_stem_len:
            continue
        for b in tokens_b:
            if len(b) < min_stem_len:
                continue
            if a == b:
                matches.add(a)
                break
            if a.startswith(b) or b.startswith(a):
                matches.add(a)
                break
    return matches


# ── Korean-English tag matching ──────────────────────────────────

def _tag_matches_response(tag: str, response_lower: str) -> bool:
    """Check if a tag (English kebab-case) matches the response text.

    Tries exact substring match first, then cross-lingual Korean
    equivalents, then individual English word matching.
    """
    if tag in response_lower:
        return True
    # Cross-lingual: check Korean equivalents
    ko_forms = _EN_TO_KO_TAG_MAP.get(tag, [])
    for ko_form in ko_forms:
        if ko_form in response_lower:
            return True
    return False


def _tag_partial_matches_response(tag: str, response_lower: str) -> bool:
    """Check if at least one content word of the tag appears in the response.

    Strips the tag into constituent words and checks each against the
    response (including Korean transliterations).
    """
    tag_words = [
        w for w in re.findall(r"[\w가-힣]+", tag.lower())
        if w not in _GENERAL_STOP_WORDS and len(w) >= 2
    ]
    for word in tag_words:
        if word in response_lower:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Dimension 1: Agenda Relevance
# ═══════════════════════════════════════════════════════════════════


def _score_agenda_relevance(
    response: str, meeting_context: dict[str, Any]
) -> tuple[float, list[RelevanceViolation]]:
    """Score response relevance to the meeting agenda.

    Uses keyword overlap (unigram + bigram) between the response and
    the agenda text.  Also checks for thematic keyword sets derived
    from ``agenda_type`` and ``tags``.

    Returns:
        (score, violations) — score in [0.0, 1.0].
    """
    violations: list[RelevanceViolation] = []

    agenda: str = str(meeting_context.get("agenda", "")).strip()
    if not agenda:
        return 0.5, [
            RelevanceViolation(
                dimension="agenda_relevance",
                severity="warning",
                message="No agenda provided in meeting context — cannot "
                "evaluate agenda relevance.",
                detail="agenda field is empty or missing",
            )
        ]

    agenda_tokens = _tokenise(agenda)
    response_tokens = _tokenise(response)

    if not response_tokens:
        return 0.0, [
            RelevanceViolation(
                dimension="agenda_relevance",
                severity="critical",
                message="Response contains no analysable tokens; "
                "cannot establish agenda relevance.",
                detail="response token set empty",
            )
        ]

    # ── Unigram overlap (fuzzy to handle Korean morphology) ──
    agenda_set: set[str] = set(agenda_tokens)
    response_set: set[str] = set(response_tokens)
    # Exact overlap
    exact_overlap = agenda_set & response_set
    # Fuzzy overlap: handles Korean morphological variation
    fuzzy_overlap = _fuzzy_token_match(agenda_set, response_set)
    effective_overlap = exact_overlap | fuzzy_overlap
    unigram_score = len(effective_overlap) / max(len(agenda_set), 1)

    # ── Bigram overlap (captures phrase-level relevance) ──
    agenda_bigrams = _extract_bigrams(agenda_tokens)
    response_bigrams = _extract_bigrams(response_tokens)
    bigram_overlap = agenda_bigrams & response_bigrams
    bigram_score = len(bigram_overlap) / max(len(agenda_bigrams), 1)

    # ── Tag-informed boost (cross-lingual) ──
    tags: list[str] = _safe_str_list(meeting_context.get("tags", []))
    response_lower = response.lower()
    tag_hits = sum(
        1 for tag in tags
        if _tag_matches_response(tag, response_lower)
        or _tag_partial_matches_response(tag, response_lower)
    )
    tag_boost = min(tag_hits / max(len(tags), 1) * 0.4, 0.3)

    # ── Composite score ──
    score = min(unigram_score * 0.4 + bigram_score * 0.3 + tag_boost, 1.0)

    # ── Cross-lingual bridge: when no direct token overlap exists
    # but tags match well (>= 60% of tags hit), the response is
    # indirectly relevant — tags are derived from the agenda after all.
    if not effective_overlap and tag_hits >= max(len(tags) * 0.6, 1):
        score = max(score, 0.25)  # floor at warning threshold

    # ── Violation generation ──
    if effective_overlap:
        pass  # some relevance established
    else:
        violations.append(
            RelevanceViolation(
                dimension="agenda_relevance",
                severity="major" if tag_hits >= max(len(tags) * 0.6, 1) else "minor",
                message="Response shares no unigram keywords with the "
                "meeting agenda — likely off-topic.",
                detail=f"agenda keywords: {sorted(agenda_set)[:10]}",
            )
        )
        # Only apply score penalty if tags don't bridge the gap
        if tag_hits < max(len(tags) * 0.6, 1):
            score = max(score - 0.30, 0.0)

    if score < 0.25:
        violations.append(
            RelevanceViolation(
                dimension="agenda_relevance",
                severity="critical",
                message=f"Agenda relevance score ({score:.2f}) is far "
                "below acceptable threshold.",
                detail="response does not address the meeting agenda",
            )
        )
    elif score < 0.50:
        violations.append(
            RelevanceViolation(
                dimension="agenda_relevance",
                severity="minor",
                message=f"Agenda relevance score ({score:.2f}) is below "
                "the ideal range; response may only loosely address "
                "the agenda.",
                detail="consider strengthening agenda-aligned content",
            )
        )

    return round(score, 4), violations


# ═══════════════════════════════════════════════════════════════════
# Dimension 2: Topic Alignment
# ═══════════════════════════════════════════════════════════════════


def _score_topic_alignment(
    response: str, meeting_context: dict[str, Any]
) -> tuple[float, list[RelevanceViolation]]:
    """Score how well the response covers expected topic tags.

    Checks each tag from the meeting context against the response
    text.  Partial matches (substring, stem overlap) count as partial
    hits.  Also checks against ``agenda_type``-derived expected
    vocabulary.

    Returns:
        (score, violations) — score in [0.0, 1.0].
    """
    violations: list[RelevanceViolation] = []

    tags: list[str] = _safe_str_list(meeting_context.get("tags", []))
    if not tags:
        return 0.5, [
            RelevanceViolation(
                dimension="topic_alignment",
                severity="warning",
                message="No topic tags provided in meeting context — "
                "cannot evaluate topic alignment.",
                detail="tags field is empty or missing",
            )
        ]

    response_lower = response.lower()

    hit_count = 0
    partial_hit_count = 0
    tag_details: list[str] = []

    for tag in tags:
        tag_lower = tag.lower().strip()
        if not tag_lower:
            continue
        # Full match: tag or its Korean equivalent appears
        if _tag_matches_response(tag_lower, response_lower):
            hit_count += 1
            continue
        # Partial match: at least one content word appears
        if _tag_partial_matches_response(tag_lower, response_lower):
            partial_hit_count += 1
            continue
        tag_details.append(tag)

    total_valid_tags = max(len([t for t in tags if t.strip()]), 1)
    score = (hit_count + partial_hit_count * 0.5) / total_valid_tags
    score = max(0.0, min(score, 1.0))

    if hit_count == 0 and partial_hit_count == 0:
        score = 0.0
        violations.append(
            RelevanceViolation(
                dimension="topic_alignment",
                severity="major",
                message="Response covers none of the expected meeting "
                "topic tags — topic alignment is missing.",
                detail=f"expected tags: {tags}",
            )
        )
    elif score < 0.40:
        violations.append(
            RelevanceViolation(
                dimension="topic_alignment",
                severity="minor",
                message=f"Topic alignment score ({score:.2f}) is low; "
                "response covers few expected topics.",
                detail=f"missed tags: {tag_details}",
            )
        )

    return round(score, 4), violations


# ═══════════════════════════════════════════════════════════════════
# Dimension 3: Off-Topic Detection
# ═══════════════════════════════════════════════════════════════════


def _score_off_topic(
    response: str, meeting_context: dict[str, Any]
) -> tuple[float, list[RelevanceViolation]]:
    """Detect off-topic content in the response.

    Scans for domain-inappropriate vocabulary from known off-topic
    domains (cooking, sports, medical, politics).  Higher score
    means LESS off-topic content.

    Also penalises responses that contain no meeting-domain vocabulary
    (generic / vacuous content).

    Returns:
        (score, violations) — score in [0.0, 1.0] (higher = better).
    """
    violations: list[RelevanceViolation] = []
    response_lower = response.lower()

    # ── Check known off-topic domains ──
    off_topic_hits: dict[str, list[str]] = {}
    for domain_name, keywords in _OFF_TOPIC_DOMAINS.items():
        hits_for_domain = [kw for kw in keywords if kw in response_lower]
        if hits_for_domain:
            off_topic_hits[domain_name] = hits_for_domain

    score = 1.0
    penalty_per_domain = 0.20

    for domain_name, hits in off_topic_hits.items():
        score = max(0.0, score - penalty_per_domain)
        violations.append(
            RelevanceViolation(
                dimension="off_topic",
                severity="major",
                message=f"Response contains off-topic vocabulary from "
                f"domain '{domain_name}'.",
                detail=f"off-topic keywords: {sorted(hits)}",
            )
        )

    # ── Check for generic/vacuous content ──
    # A response dominated by stop words and lacking domain terms
    # is likely vacuous/generic rather than actively off-topic.
    response_tokens = _tokenise(response)
    if len(response_tokens) < 5:
        score = max(0.0, score - 0.15)
        violations.append(
            RelevanceViolation(
                dimension="off_topic",
                severity="minor",
                message="Response has very few content-bearing tokens; "
                "may be too generic to be useful.",
                detail=f"content token count = {len(response_tokens)}",
            )
        )

    # ── Check for uncharacteristically long responses that may
    #     contain auto-generated boilerplate ──
    if len(response) > 5000 and len(off_topic_hits) == 0:
        # Long response is not necessarily off-topic but merits
        # a warning for the Coordinator.
        pass

    return round(score, 4), violations


# ═══════════════════════════════════════════════════════════════════
# Dimension 4: Reference Consistency
# ═══════════════════════════════════════════════════════════════════


def _score_reference_consistency(
    response: str, meeting_context: dict[str, Any]
) -> tuple[float, list[RelevanceViolation]]:
    """Validate references made in the response against meeting context.

    Checks:
    * References to round numbers that exceed ``round_count``.
    * References to decisions/artifacts that do not exist in the
      meeting's ``decisions`` list.
    * References to role IDs that do not exist in ``required_roles``
      or ``optional_roles``.
    * References to previous rounds when none exist.

    Returns:
        (score, violations) — score in [0.0, 1.0].
    """
    violations: list[RelevanceViolation] = []
    score = 1.0

    round_count: int = _safe_int(meeting_context.get("round_count"), default=0)
    required_roles: list[str] = _safe_str_list(
        meeting_context.get("required_roles", [])
    )
    optional_roles: list[str] = _safe_str_list(
        meeting_context.get("optional_roles", [])
    )
    all_roles: set[str] = set(required_roles) | set(optional_roles)
    decisions: list[dict[str, Any]] = _safe_list_of_dicts(
        meeting_context.get("decisions", [])
    )
    decision_ids: set[str] = {
        str(d.get("decision_id", "")).strip()
        for d in decisions
        if str(d.get("decision_id", "")).strip()
    }

    # ── Check round number references ──
    # Pattern: "Round N", "round N", "라운드 N", "제 N 라운드"
    round_refs = re.findall(
        r"(?:round|라운드|제)\s*(\d+)", response, re.IGNORECASE
    )
    for ref_str in round_refs:
        ref_num = int(ref_str)
        if ref_num > round_count:
            score -= 0.15
            violations.append(
                RelevanceViolation(
                    dimension="reference_consistency",
                    severity="major",
                    message=f"Response references Round {ref_num} but "
                    f"round_count is only {round_count}.",
                    detail=f"round reference = '{ref_str}'",
                )
            )
            break  # one violation per round reference category

    # ── Check references to non-existent previous rounds ──
    if round_count == 0:
        prev_round_refs = re.findall(
            r"(?:previous\s*round|이전\s*라운드|지난\s*라운드|앞\s*라운드)",
            response,
            re.IGNORECASE,
        )
        if prev_round_refs:
            score -= 0.10
            violations.append(
                RelevanceViolation(
                    dimension="reference_consistency",
                    severity="minor",
                    message="Response references previous rounds but "
                    "round_count is 0 — no previous rounds exist.",
                    detail=f"references found: {prev_round_refs}",
                )
            )

    # ── Check for hallucinated role IDs ──
    if all_roles:
        # Collect known tags to avoid flagging tag strings as roles
        known_tags: set[str] = set(
            _safe_str_list(meeting_context.get("tags", []))
        )
        # Find potential role references (kebab-case identifiers).
        # Use lookarounds instead of \\b because \\b treats Hangul as
        # word characters, breaking boundary detection in Korean text.
        potential_roles = re.findall(
            r"(?<![a-z0-9-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![a-z0-9-])",
            response,
        )
        for role_ref in potential_roles:
            if role_ref in all_roles:
                continue
            if role_ref in known_tags:
                continue  # this is a tag, not a role reference
            # Check if this could be a role ID but isn't in the list
            if re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", role_ref) and len(role_ref) > 5:
                score -= 0.05
                violations.append(
                    RelevanceViolation(
                        dimension="reference_consistency",
                        severity="minor",
                        message=f"Response references role '{role_ref}' "
                        "which is not in required_roles or optional_roles.",
                        detail=f"available roles: {sorted(all_roles)}",
                    )
                )
                break  # one violation for unknown roles

    # ── Check for references to non-existent decisions ──
    if decision_ids:
        for did in decision_ids:
            if did and did in response:
                pass  # valid reference
        # If response references decisions but none match, warn
        dec_refs = re.findall(r"decision[_\s]*([a-zA-Z0-9_-]+)", response, re.IGNORECASE)
        for ref in dec_refs:
            ref_clean = ref.strip()
            if ref_clean and ref_clean not in decision_ids:
                score -= 0.10
                violations.append(
                    RelevanceViolation(
                        dimension="reference_consistency",
                        severity="major",
                        message=f"Response references decision "
                        f"'{ref_clean}' which does not exist.",
                        detail=f"existing decisions: {sorted(decision_ids)}",
                    )
                )
                break

    score = max(0.0, min(score, 1.0))
    return round(score, 4), violations


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _safe_str_list(value: object) -> list[str]:
    """Safely coerce a value to a list of strings."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item.strip())
        elif item is not None:
            result.append(str(item))
    return result


def _safe_list_of_dicts(value: object) -> list[dict[str, Any]]:
    """Safely coerce a value to a list of dicts."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_int(value: object, *, default: int = 0) -> int:
    """Safely coerce a value to int."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (ValueError, TypeError):
            return default
    return default


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════


def validate_context_relevance(
    response: str,
    meeting_context: dict[str, Any],
    *,
    threshold: float = 0.70,
) -> ContextRelevanceReport:
    """Validate LLM response relevance against meeting context.

    This is the main entry point for **Sub-AC 6.2b**.  It evaluates
    the given response text across four dimensions — agenda relevance,
    topic alignment, off-topic detection, and reference consistency —
    and returns a structured report with per-dimension scores and an
    overall verdict.

    The validator is **entirely rule-based** (keyword heuristics,
    co-occurrence analysis) and requires no LLM calls, making it
    deterministic and independently testable with any
    ``(response, meeting_context)`` input pair.

    **No persona definitions are required** — the evaluation is
    purely content-driven, unlike the persona consistency validator
    (Sub-AC 6.2a).

    Args:
        response: The LLM response text to validate.  For opinion
                  packets use the ``opinion_content`` field.
        meeting_context: A dict with at minimum:
            * ``agenda`` (str) — the meeting agenda.
            * ``tags`` (list[str]) — topic tags.
            * ``agenda_type`` (str) — classified meeting type.
            * ``round_context`` (str) — current round context.
            * ``round_count`` (int) — number of completed rounds.
            * ``required_roles`` (list[str]) — required role IDs.
            * ``optional_roles`` (list[str]) — optional role IDs.
            * ``decisions`` (list[dict]) — past decisions.
        threshold: Overall score threshold for ``passed=True``
                   (default: 0.70).

    Returns:
        ``ContextRelevanceReport`` with per-dimension scores,
        violation details, and a pass/fail verdict.

    Examples:
        >>> ctx = {
        ...     "agenda": "뮤직비디오 오프닝 아이디어 회의",
        ...     "tags": ["mv", "visual-concept", "opening-sequence"],
        ...     "round_count": 1,
        ...     "required_roles": ["art-director"],
        ...     "optional_roles": [],
        ...     "decisions": [],
        ... }
        >>> result = validate_context_relevance(
        ...     "네온 느와르 스타일의 비주얼 컨셉을 제안합니다. "
        ...     "고대비 색감과 실루엣 중심의 오프닝 시퀀스...",
        ...     ctx,
        ... )
        >>> result.passed
        True
    """
    # ── Guard: None / non-dict context ──
    if meeting_context is None:
        return ContextRelevanceReport(
            passed=False,
            overall_score=0.0,
            agenda_relevance_score=0.0,
            topic_alignment_score=0.0,
            off_topic_score=0.0,
            reference_consistency_score=0.0,
            violations=(
                RelevanceViolation(
                    dimension="agenda_relevance",
                    severity="critical",
                    message="Meeting context is None — cannot validate.",
                    detail="meeting_context=None",
                ),
            ),
            response_length=len(response) if response else 0,
            threshold=threshold,
        )

    if not isinstance(meeting_context, dict):
        return ContextRelevanceReport(
            passed=False,
            overall_score=0.0,
            agenda_relevance_score=0.0,
            topic_alignment_score=0.0,
            off_topic_score=0.0,
            reference_consistency_score=0.0,
            violations=(
                RelevanceViolation(
                    dimension="agenda_relevance",
                    severity="critical",
                    message=f"Meeting context must be a dict, "
                    f"got {type(meeting_context).__name__}.",
                    detail=f"type={type(meeting_context).__name__}",
                ),
            ),
            response_length=len(response) if response else 0,
            threshold=threshold,
        )

    # ── Guard: empty / whitespace-only response ──
    if not response or not response.strip():
        return ContextRelevanceReport(
            passed=False,
            overall_score=0.0,
            agenda_relevance_score=0.0,
            topic_alignment_score=0.0,
            off_topic_score=0.0,
            reference_consistency_score=0.0,
            violations=(
                RelevanceViolation(
                    dimension="agenda_relevance",
                    severity="critical",
                    message="Response is empty — cannot validate "
                    "context relevance.",
                    detail="(empty response)",
                ),
            ),
            response_length=0,
            threshold=threshold,
        )

    response = response.strip()

    # ── Run each dimension ──
    agenda_score, agenda_violations = _score_agenda_relevance(
        response, meeting_context
    )
    topic_score, topic_violations = _score_topic_alignment(
        response, meeting_context
    )
    off_topic_score, off_topic_violations = _score_off_topic(
        response, meeting_context
    )
    ref_score, ref_violations = _score_reference_consistency(
        response, meeting_context
    )

    # ── Aggregate ──
    all_violations: list[RelevanceViolation] = []
    all_violations.extend(agenda_violations)
    all_violations.extend(topic_violations)
    all_violations.extend(off_topic_violations)
    all_violations.extend(ref_violations)

    # Weighted overall score
    overall = (
        agenda_score * 0.30
        + topic_score * 0.25
        + off_topic_score * 0.25
        + ref_score * 0.20
    )

    # Critical violations are an automatic fail
    has_critical = any(v.severity == "critical" for v in all_violations)
    passed = overall >= threshold and not has_critical

    return ContextRelevanceReport(
        passed=passed,
        overall_score=round(overall, 4),
        agenda_relevance_score=round(agenda_score, 4),
        topic_alignment_score=round(topic_score, 4),
        off_topic_score=round(off_topic_score, 4),
        reference_consistency_score=round(ref_score, 4),
        violations=tuple(all_violations),
        response_length=len(response),
        threshold=threshold,
    )
