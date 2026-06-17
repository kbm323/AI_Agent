"""Divergence metric computation — Sub-AC 6.3.1.

Computes a normalized divergence score (0.0–1.0) between two LLM
validation outputs — typically GLM-5.1 (primary) and Codex GPT-5.5
(conditional).  The score serves as a quantitative input to the
dual-validation conflict-resolution pipeline: low divergence
suggests alignment, while high divergence triggers escalation
heuristics.

Design
------
The divergence score is a **weighted composite** of five dimensions:

1. **Token similarity (0.25)** — Jaccard index on tokenised content
   words, cross-lingual (Korean + English).  Captures raw lexical
   overlap independent of structure.

2. **Bigram phrase similarity (0.15)** — Jaccard index on adjacent
   token bigrams.  Captures phrase-level agreement.

3. **Key concept overlap (0.25)** — overlap of extracted key noun
   phrases and domain terms.  Captures semantic alignment without
   requiring an embedding model.

4. **Structural divergence (0.15)** — comparison of output structure
   (section count, claim count, recommendation count).  Captures
   whether the two outputs are architecturally similar.

5. **Stance/conclusion alignment (0.20)** — heuristic comparison of
   verdicts, recommendations, and sentiment.  Captures whether the
   two validators reach the same actionable conclusion.

Each dimension produces a **similarity** score in [0.0, 1.0]; the
overall divergence is ``1.0 - weighted_similarity``.

Related modules
---------------
* ``cross_field_validator`` — Sub-AC 6.1.3 (manifest integrity)
* ``context_relevance_validator`` — Sub-AC 6.2b (context relevance)
* ``persona_consistency_validator`` — Sub-AC 6.2a (persona alignment)
* ``conflict_detector`` — Sub-AC 6.3.2 (inter-role conflict detection)

Testable with
-------------
* Identical outputs (expected divergence ≈ 0.0).
* Completely unrelated outputs (expected divergence ≈ 1.0).
* Partially overlapping outputs with known agreement levels.
* Korean-only, English-only, and mixed-language pairs.
* Empty strings (graceful degradation).
* Edge cases: single-word outputs, highly structured vs free-form.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


# ── Korean + English stop words shared with context_relevance_validator ──

_GENERAL_STOP_WORDS: frozenset[str] = frozenset({
    # Korean grammatical particles and general words
    "있다", "없다", "하다", "되다", "이다", "그", "이", "저",
    "것", "수", "년", "일", "월", "더", "때", "말", "위",
    "은", "는", "이", "가", "을", "를", "에", "의", "로",
    "에서", "으로", "에게", "한테", "하고", "와", "과",
    "그리고", "하지만", "그런데", "그래서", "그러나",
    "또는", "또한", "이런", "저런", "그런",
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

# ── Validation-specific keywords for stance detection ──────────────────

_VALIDATION_VERDICT_KEYWORDS: dict[str, frozenset[str]] = {
    "pass": frozenset({
        "pass", "passed", "통과", "합격", "승인", "approved",
        "acceptable", "meets", "satisfies", "충족", "적합",
    }),
    "conditional_pass": frozenset({
        "conditional", "conditionally", "조건부", "단,",
        "provided that", "if the following", "다음 조건",
    }),
    "revision_required": frozenset({
        "revision", "revise", "revised", "수정", "재검토",
        "rework", "수정 필요", "보완", "개선 필요",
    }),
    "escalate": frozenset({
        "escalate", "escalation", "에스컬레이션", "상위 검토",
        "human review", "manual review", "사람 확인",
    }),
    "fail": frozenset({
        "fail", "failed", "reject", "rejected", "거부",
        "불합격", "불가", "unacceptable", "부적합",
    }),
}

# ── Recommendation / action keywords for conclusion alignment ──────────

_RECOMMENDATION_KEYWORDS: frozenset[str] = frozenset({
    "recommend", "suggest", "propose", "제안", "추천", "권장",
    "should", "해야", "되어야", "하는 것이",
    "action item", "next step", "다음 단계", "조치 사항",
    "implement", "구현", "적용", "실행",
})


# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DivergenceDimension:
    """A single dimension's contribution to the divergence score.

    Attributes:
        name: Dimension name (e.g. ``token_similarity``).
        weight: Weight in the composite score (0.0–1.0).
        similarity: Raw similarity score for this dimension (0.0–1.0).
        divergence: 1.0 - similarity (0.0–1.0).
        weighted_divergence: weight * divergence (contribution to
            overall score).
        details: Optional human-readable description of what was
            compared.
    """

    name: str
    weight: float
    similarity: float
    divergence: float
    weighted_divergence: float
    details: str = ""


@dataclass(frozen=True)
class DivergenceReport:
    """Aggregated divergence analysis between two model outputs.

    ``overall_divergence`` is the weighted composite divergence score
    in [0.0, 1.0].  ``passed`` indicates whether the outputs are
    sufficiently aligned (divergence <= threshold, default 0.30).

    Attributes:
        overall_divergence: Weighted composite divergence 0.0–1.0
            (0.0 = identical, 1.0 = completely divergent).
        overall_similarity: 1.0 - overall_divergence.
        dimensions: Per-dimension breakdown.
        passed: True when divergence <= divergence_threshold.
        divergence_threshold: The threshold used for the pass/fail
            decision.
        primary_length: Character count of primary (GLM) output.
        secondary_length: Character count of secondary (Codex) output.
        primary_source: Label for the primary model
            (default ``"glm-5.1"``).
        secondary_source: Label for the secondary model
            (default ``"codex-gpt-5.5"``).
    """

    overall_divergence: float
    overall_similarity: float
    dimensions: tuple[DivergenceDimension, ...]
    passed: bool
    divergence_threshold: float
    primary_length: int
    secondary_length: int
    primary_source: str = "glm-5.1"
    secondary_source: str = "codex-gpt-5.5"

    @property
    def dimension_count(self) -> int:
        """Number of evaluation dimensions."""
        return len(self.dimensions)

    @property
    def highest_divergence_dimension(self) -> DivergenceDimension | None:
        """The dimension with the highest divergence score."""
        if not self.dimensions:
            return None
        return max(self.dimensions, key=lambda d: d.divergence)

    def dimensions_by_name(self) -> dict[str, DivergenceDimension]:
        """Index dimensions by name for targeted inspection."""
        return {d.name: d for d in self.dimensions}

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "overall_divergence": self.overall_divergence,
            "overall_similarity": self.overall_similarity,
            "passed": self.passed,
            "divergence_threshold": self.divergence_threshold,
            "primary_length": self.primary_length,
            "secondary_length": self.secondary_length,
            "primary_source": self.primary_source,
            "secondary_source": self.secondary_source,
            "dimensions": [
                {
                    "name": d.name,
                    "weight": d.weight,
                    "similarity": d.similarity,
                    "divergence": d.divergence,
                    "weighted_divergence": d.weighted_divergence,
                    "details": d.details,
                }
                for d in self.dimensions
            ],
        }


# ═════════════════════════════════════════════════════════════════════════
# Text tokenisation (shared bilingual logic)
# ═════════════════════════════════════════════════════════════════════════


def _normalise_token(token: str) -> str:
    """Normalise shallow inflection/synonyms before lexical comparison."""
    synonyms = {
        "합격": "통과",
        "passed": "pass",
        "approved": "pass",
        "acceptable": "pass",
    }
    token = synonyms.get(token, token)
    # Korean validation reports often differ only by polite endings or
    # attached object particles: "권장" vs "권장합니다", "실행" vs "실행을".
    for suffix in ("합니다", "합니다", "습니다", "입니다", "합니다", "됩니다", "합니다", "을", "를"):
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            token = token[: -len(suffix)]
            break
    if token.endswith("함") and len(token) > 2:
        token = token[:-1]
    return synonyms.get(token, token)


def _tokenise(text: str) -> list[str]:
    """Tokenise text into lowercase content words, stripping stop words.

    Korean Hangul words are kept whole (they are space-separated and
    carry meaning as units).  CJK ideographs are split into individual
    characters.  English/ASCII words are kept whole.
    """
    tokens: list[str] = []
    for chunk in re.findall(r"[\w가-힣一-龥]+", text.lower()):
        # CJK-only (Chinese characters without Korean Hangul)
        has_cjk_only = bool(
            re.search(r"[\u4E00-\u9FFF]", chunk)
            and not re.search(r"[\uAC00-\uD7A3]", chunk)
        )
        if has_cjk_only:
            tokens.extend(ch for ch in chunk if len(ch) >= 1)
        else:
            chunk = _normalise_token(chunk)
            if len(chunk) >= 2 and chunk not in _GENERAL_STOP_WORDS:
                tokens.append(chunk)
    return tokens


def _extract_bigrams(tokens: list[str]) -> set[str]:
    """Extract adjacent bigrams from a token list."""
    bigrams: set[str] = set()
    for i in range(len(tokens) - 1):
        bigrams.add(f"{tokens[i]} {tokens[i + 1]}")
    return bigrams


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets.

    Returns 1.0 when both sets are empty (considered identical).
    """
    if not set_a and not set_b:
        return 1.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


def _overlap_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Blend Jaccard with containment so minor wording additions stay close."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    jaccard = _jaccard(set_a, set_b)
    containment = len(set_a & set_b) / min(len(set_a), len(set_b))
    return (jaccard * 0.4) + (containment * 0.6)


# ═════════════════════════════════════════════════════════════════════════
# Dimension 1: Token similarity (weight 0.25)
# ═════════════════════════════════════════════════════════════════════════


def _score_token_similarity(
    primary: str, secondary: str
) -> tuple[float, str]:
    """Compute Jaccard token similarity between the two outputs.

    Tokenises both texts with bilingual stop-word filtering and computes
    the Jaccard index on content-word token sets.

    Returns:
        (similarity, details) — similarity in [0.0, 1.0].
    """
    primary_tokens = _tokenise(primary)
    secondary_tokens = _tokenise(secondary)

    if not primary_tokens and not secondary_tokens:
        return 1.0, "both outputs have no analysable tokens"

    primary_set: set[str] = set(primary_tokens)
    secondary_set: set[str] = set(secondary_tokens)

    similarity = _overlap_similarity(primary_set, secondary_set)

    details = (
        f"primary_tokens={len(primary_set)}, "
        f"secondary_tokens={len(secondary_set)}, "
        f"overlap={len(primary_set & secondary_set)}"
    )
    return round(similarity, 4), details


# ═════════════════════════════════════════════════════════════════════════
# Dimension 2: Bigram phrase similarity (weight 0.15)
# ═════════════════════════════════════════════════════════════════════════


def _score_bigram_similarity(
    primary: str, secondary: str
) -> tuple[float, str]:
    """Compute Jaccard bigram similarity between the two outputs.

    Bigrams capture phrase-level agreement beyond single-token overlap.

    Returns:
        (similarity, details) — similarity in [0.0, 1.0].
    """
    primary_tokens = _tokenise(primary)
    secondary_tokens = _tokenise(secondary)

    primary_bigrams = _extract_bigrams(primary_tokens)
    secondary_bigrams = _extract_bigrams(secondary_tokens)

    similarity = _overlap_similarity(primary_bigrams, secondary_bigrams)

    details = (
        f"primary_bigrams={len(primary_bigrams)}, "
        f"secondary_bigrams={len(secondary_bigrams)}, "
        f"overlap={len(primary_bigrams & secondary_bigrams)}"
    )
    return round(similarity, 4), details


# ═════════════════════════════════════════════════════════════════════════
# Dimension 3: Key concept overlap (weight 0.25)
# ═════════════════════════════════════════════════════════════════════════


# Korean + English noun/domain indicators — words that are likely to
# carry key concept meaning in the entertainment-company domain.

_DOMAIN_INDICATORS: frozenset[str] = frozenset({
    # Korean domain terms
    "컨셉", "디자인", "기획", "전략", "분석", "평가", "리뷰",
    "개발", "구현", "테스트", "배포", "운영", "관리", "검증",
    "뮤직비디오", "비주얼", "타이포그래피", "색감", "구도",
    "브랜드", "아이덴티티", "마케팅", "콘텐츠", "스토리",
    "리스크", "위험", "보안", "데이터", "품질", "성능",
    # English domain terms
    "concept", "design", "strategy", "analysis", "review",
    "development", "implementation", "test", "deployment",
    "operation", "validation", "verification", "assessment",
    "music-video", "visual", "typography", "palette", "composition",
    "brand", "identity", "marketing", "content", "story",
    "risk", "security", "data", "quality", "performance",
    "architecture", "infrastructure", "workflow", "pipeline",
    "budget", "timeline", "schedule", "resource",
    "recommendation", "conclusion", "verdict", "decision",
})


def _extract_concepts(text: str) -> set[str]:
    """Extract key concept tokens from text.

    A token qualifies as a concept if it is:
    1. A content word (non-stop-word) AND
    2. Either (a) in the domain-indicator set, OR
       (b) at least 4 characters long (likely a compound noun or
       significant term).

    This biases toward meaningful domain-specific terms without
    requiring an NLP pipeline.
    """
    tokens = _tokenise(text)
    concepts: set[str] = set()
    for token in tokens:
        if token in _DOMAIN_INDICATORS:
            concepts.add(token)
        elif len(token) >= 4:
            # Longer words are more likely to be meaningful
            # (works for both Korean compounds and English multi-syllable)
            concepts.add(token)
    return concepts


def _score_concept_overlap(
    primary: str, secondary: str
) -> tuple[float, str]:
    """Compute key concept overlap between the two outputs.

    Extracts domain-relevant concept tokens and computes Jaccard
    similarity.

    Returns:
        (similarity, details) — similarity in [0.0, 1.0].
    """
    primary_concepts = _extract_concepts(primary)
    secondary_concepts = _extract_concepts(secondary)

    similarity = _jaccard(primary_concepts, secondary_concepts)

    overlap = primary_concepts & secondary_concepts
    details = (
        f"primary_concepts={len(primary_concepts)}, "
        f"secondary_concepts={len(secondary_concepts)}, "
        f"overlap={len(overlap)}"
    )
    if overlap:
        details += f", shared={sorted(overlap)[:5]}"
    return round(similarity, 4), details


# ═════════════════════════════════════════════════════════════════════════
# Dimension 4: Structural divergence (weight 0.15)
# ═════════════════════════════════════════════════════════════════════════


def _count_sections(text: str) -> int:
    """Count apparent sections in text.

    Heuristic: counts markdown-style headings (##, ###), numbered
    sections (1., 2.), and double-newline-separated paragraphs as
    section proxies.
    """
    # Markdown headings
    heading_count = len(re.findall(r"^#{1,4}\s", text, re.MULTILINE))
    if heading_count > 0:
        return heading_count

    # Numbered sections (e.g. "1. ", "1) ", "1- ")
    numbered = len(re.findall(r"^\d+[\.\)\-]\s", text, re.MULTILINE))
    if numbered >= 2:
        return numbered

    # Paragraph count as fallback
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return len(paragraphs)


def _count_claims(text: str) -> int:
    """Count apparent claims/assertions in text.

    Heuristic: counts sentence-ending punctuation and
    claim-indicator patterns (e.g. "because", "therefore", "왜냐하면").
    """
    # Sentence-ending punctuation
    sentences_kr = len(re.findall(r"[.!?다까요][\s\n]", text))
    sentences_en = len(re.findall(r"[.!?][\s\n]", text))
    total = max(sentences_kr, sentences_en, 1)

    # Claim indicators
    claim_patterns = re.findall(
        r"(because|therefore|thus|hence|왜냐하면|따라서|그러므로|고로)",
        text.lower(),
    )
    total += len(claim_patterns) * 0.5  # dampened boost

    return max(1, int(total))


def _count_recommendations(text: str) -> int:
    """Count apparent recommendations/action-items in text."""
    count = 0
    text_lower = text.lower()
    for kw in _RECOMMENDATION_KEYWORDS:
        count += len(re.findall(re.escape(kw), text_lower))
    return max(0, count)


def _score_structural_divergence(
    primary: str, secondary: str
) -> tuple[float, str]:
    """Compare structural similarity of the two outputs.

    Compares section counts, claim counts, and recommendation counts.
    Uses a ratio-based similarity for each metric and averages them.

    Returns:
        (similarity, details) — similarity in [0.0, 1.0].
    """
    p_sections = _count_sections(primary)
    s_sections = _count_sections(secondary)
    p_claims = _count_claims(primary)
    s_claims = _count_claims(secondary)
    p_recs = _count_recommendations(primary)
    s_recs = _count_recommendations(secondary)

    # Ratio-based similarity: min/max for each metric
    def ratio_sim(a: int, b: int) -> float:
        if a == 0 and b == 0:
            return 1.0
        if a == 0 or b == 0:
            return 0.0
        return min(a, b) / max(a, b)

    section_sim = ratio_sim(p_sections, s_sections)
    claim_sim = ratio_sim(p_claims, s_claims)
    rec_sim = ratio_sim(p_recs, s_recs)

    # Average of the three ratios
    similarity = round((section_sim + claim_sim + rec_sim) / 3.0, 4)

    details = (
        f"sections=({p_sections},{s_sections})->{section_sim:.2f}, "
        f"claims=({p_claims},{s_claims})->{claim_sim:.2f}, "
        f"recommendations=({p_recs},{s_recs})->{rec_sim:.2f}"
    )
    return similarity, details


# ═════════════════════════════════════════════════════════════════════════
# Dimension 5: Stance / conclusion alignment (weight 0.20)
# ═════════════════════════════════════════════════════════════════════════


def _detect_stance(text: str) -> str | None:
    """Detect the dominant validation stance from text.

    Searches for verdict keywords and returns the strongest detected
    stance.  ``None`` if no stance is detectable.

    Priority: fail > escalate > revision_required > conditional_pass >
    pass (a stricter stance overrides a lenient one).
    """
    text_lower = text.lower()
    detected: set[str] = set()

    if "revision_required" in text_lower or "revision required" in text_lower:
        detected.add("revision_required")
    else:
        for kw in _VALIDATION_VERDICT_KEYWORDS["revision_required"]:
            # Bare "revision" in phrases like "approved after revision" is a
            # historical-edit note, not a stricter validator stance.
            if kw in {"revision", "revised"}:
                continue
            if kw in text_lower:
                detected.add("revision_required")
                break

    for stance, keywords in _VALIDATION_VERDICT_KEYWORDS.items():
        if stance == "revision_required":
            continue
        for kw in keywords:
            if kw in text_lower:
                detected.add(stance)
                break

    if not detected:
        return None

    # Priority ordering: more severe overrides
    priority_order = (
        "fail", "escalate", "revision_required",
        "conditional_pass", "pass",
    )
    for stance in priority_order:
        if stance in detected:
            return stance
    return None


def _score_stance_alignment(
    primary: str, secondary: str
) -> tuple[float, str]:
    """Compare verdict/recommendation stances between the two outputs.

    Heuristic approach:
    - If both detect the same stance → 1.0
    - If both detect stances but they differ → 0.2
    - If one detects a stance and the other does not → 0.4
    - If neither detects a stance → 0.5 (neutral)

    Additionally checks for recommendation presence agreement.

    Returns:
        (similarity, details) — similarity in [0.0, 1.0].
    """
    p_stance = _detect_stance(primary)
    s_stance = _detect_stance(secondary)

    p_has_recs = _count_recommendations(primary) > 0
    s_has_recs = _count_recommendations(secondary) > 0

    # Stance similarity
    if p_stance is None and s_stance is None:
        if not _tokenise(primary) and not _tokenise(secondary):
            # Empty/whitespace-only pairs have no evidence of divergence.
            stance_sim = 1.0
        else:
            # Non-empty outputs without verdicts are neutral, not equivalent.
            stance_sim = 0.5
    elif p_stance == s_stance:
        stance_sim = 1.0
    elif {p_stance, s_stance} == {"pass", "conditional_pass"}:
        # Conditional pass and pass are adjacent outcomes, not a hard conflict.
        stance_sim = 0.8
    elif p_stance is not None and s_stance is not None:
        # Both have stances but they differ
        stance_sim = 0.2
    else:
        # One has stance, other does not
        stance_sim = 0.4

    # Recommendation presence agreement
    rec_sim = 1.0 if p_has_recs == s_has_recs else 0.3

    # Composite: 0.6 stance + 0.4 recommendation
    similarity = round(stance_sim * 0.6 + rec_sim * 0.4, 4)

    details = (
        f"primary_stance={p_stance or 'none'}, "
        f"secondary_stance={s_stance or 'none'}, "
        f"primary_recs={'yes' if p_has_recs else 'no'}, "
        f"secondary_recs={'yes' if s_has_recs else 'no'}"
    )
    return similarity, details


# ═════════════════════════════════════════════════════════════════════════
# Main public API
# ═════════════════════════════════════════════════════════════════════════

# Dimension weights (must sum to 1.0)
_DIMENSION_WEIGHTS: tuple[
    tuple[str, float, Callable[[str, str], tuple[float, str]]], ...
] = (
    ("token_similarity", 0.25, _score_token_similarity),
    ("bigram_similarity", 0.15, _score_bigram_similarity),
    ("concept_overlap", 0.25, _score_concept_overlap),
    ("structural_similarity", 0.15, _score_structural_divergence),
    ("stance_alignment", 0.20, _score_stance_alignment),
)

DEFAULT_DIVERGENCE_THRESHOLD: float = 0.30
"""Default threshold: divergence <= 0.30 is considered 'aligned'."""


def compute_divergence(
    primary_output: str,
    secondary_output: str,
    *,
    divergence_threshold: float = DEFAULT_DIVERGENCE_THRESHOLD,
    primary_source: str = "glm-5.1",
    secondary_source: str = "codex-gpt-5.5",
) -> DivergenceReport:
    """Compute the normalized divergence score between two model outputs.

    Evaluates five dimensions (token similarity, bigram similarity,
    concept overlap, structural similarity, stance alignment) and
    combines them into a weighted composite score.

    Args:
        primary_output: The primary validator's output text
            (typically GLM-5.1).
        secondary_output: The secondary validator's output text
            (typically Codex GPT-5.5).
        divergence_threshold: Maximum divergence for a ``passed``
            verdict (default 0.30).
        primary_source: Label for the primary model.
        secondary_source: Label for the secondary model.

    Returns:
        A ``DivergenceReport`` with the overall divergence score,
        per-dimension breakdown, and pass/fail verdict.

    Raises:
        TypeError: If either output is not a string.

    Examples:
        >>> report = compute_divergence("pass. 통과.", "pass. 통과.")
        >>> report.overall_divergence
        0.0
        >>> report.passed
        True
    """
    if not isinstance(primary_output, str):
        raise TypeError(
            f"primary_output must be str, got {type(primary_output).__name__}"
        )
    if not isinstance(secondary_output, str):
        raise TypeError(
            f"secondary_output must be str, got "
            f"{type(secondary_output).__name__}"
        )

    dimensions: list[DivergenceDimension] = []
    weighted_sum = 0.0

    for name, weight, scorer in _DIMENSION_WEIGHTS:
        similarity, details = scorer(primary_output, secondary_output)
        divergence = round(1.0 - similarity, 4)
        w_divergence = round(weight * divergence, 4)
        weighted_sum += w_divergence

        dimensions.append(
            DivergenceDimension(
                name=name,
                weight=weight,
                similarity=similarity,
                divergence=divergence,
                weighted_divergence=w_divergence,
                details=details,
            )
        )

    overall_divergence = round(weighted_sum, 4)
    overall_similarity = round(1.0 - overall_divergence, 4)
    passed = overall_divergence <= divergence_threshold

    return DivergenceReport(
        overall_divergence=overall_divergence,
        overall_similarity=overall_similarity,
        dimensions=tuple(dimensions),
        passed=passed,
        divergence_threshold=divergence_threshold,
        primary_length=len(primary_output),
        secondary_length=len(secondary_output),
        primary_source=primary_source,
        secondary_source=secondary_source,
    )
