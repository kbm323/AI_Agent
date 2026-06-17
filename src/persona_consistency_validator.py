"""Persona consistency validator — Sub-AC 6.2a.

Evaluates an LLM response's alignment with an assigned team-leader persona
definition across four dimensions: tone, role vocabulary, behavioral
constraints, and forbidden patterns.  The validator is entirely rule-based
so it produces deterministic, reproducible results for every (response,
persona_spec) input pair — no LLM call required.

The module is designed to work across all 6–7 team-leader personas without
any meeting context, making it independently testable and reusable.

Core abstractions
-----------------
**PersonaSpec** — the extended persona definition carrying consistency-
relevant metadata (tone descriptors, expected vocabulary, behavioral
constraints, forbidden pattern lists).

**PersonaConsistencyReport** — the structured validation output with per-
category scores, an overall pass/fail verdict, and a human-readable
violation list.

Usage::

    from src.persona_consistency_validator import (
        PersonaConsistencyReport,
        PersonaSpec,
        validate_persona_consistency,
    )

    persona = PersonaSpec(
        role_id="art-director",
        display_name="아트 디렉터",
        team="art-design",
        role_type="leader",
        tone_profile={
            "formality": "professional",
            "assertiveness": "confident_measured",
            "emotional_valence": "neutral_positive",
            "style": "analytical_creative",
        },
        role_vocabulary={
            "keywords": ["visual direction", "art style", "palette",
                         "composition", "aesthetic", "design system"],
            "domain_terms": ["color grading", "typography", "layout",
                             "visual hierarchy", "brand identity"],
        },
        behavioral_constraints=[
            "stay_within_visual_design_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ],
        forbidden_patterns=[
            "contradict_art_director_authority",
            "make_engineering_decisions",
            "override_marketing_strategy",
        ],
    )

    result = validate_persona_consistency(response="...", persona_spec=persona)
    print(f"Score: {result.overall_score:.2f}, Passed: {result.passed}")
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# ═════════════════════════════════════════════════════════════════════════
# Korean formality markers used in tone heuristics
# ═════════════════════════════════════════════════════════════════════════

# Formal / polite endings (합니다체, 해요체)
_KOREAN_FORMAL_ENDINGS = re.compile(
    r"(?:합니[다까]|ㅂ니[다까]|습니[다까]|하십시오|"
    r"입니[다까]|입니다|입니다만|입니다|"
    r"어요|아요|여요|해요|되요|돼요|"
    r"세요|으세요|십시오|시지요|"
    r"군요|는군요|구나|는구나)"
)

# Casual / intimate endings (해체, 반말)
_KOREAN_CASUAL_ENDINGS = re.compile(
    r"(?:[가-힣]*[^요습시군][다라자거내][.?!\s]|[가-힣]*[지어줘봐할께][.?!\s])"
)

# Honorific particles / markers
_KOREAN_HONORIFIC_MARKERS = re.compile(
    r"(?:님|께서|께|시\b|으시|께서는|분|드리|올리)"
)

# ═════════════════════════════════════════════════════════════════════════
# Domain vocabulary categories for heuristic matching
# ═════════════════════════════════════════════════════════════════════════

_CONTENT_CREATION_TERMS: frozenset[str] = frozenset({
    "script", "story", "narrative", "plot", "dialogue", "scene",
    "production", "creative direction", "content strategy",
    "대본", "스토리", "내러티브", "플롯", "대사", "씬",
    "제작", "콘텐츠", "크리에이티브",
})

_ART_DESIGN_TERMS: frozenset[str] = frozenset({
    "visual", "design", "color", "palette", "composition",
    "aesthetic", "typography", "layout", "art style",
    "character design", "illustration", "graphic",
    "비주얼", "디자인", "색상", "팔레트", "구도",
    "미학", "타이포", "레이아웃", "아트", "캐릭터",
})

_TECH_ENGINEERING_TERMS: frozenset[str] = frozenset({
    "architecture", "system", "code", "infrastructure",
    "deployment", "api", "database", "server", "security",
    "performance", "scalability", "pipeline", "microservices",
    "아키텍처", "시스템", "코드", "인프라", "배포",
    "API", "데이터베이스", "서버", "보안", "성능", "확장",
})

_MARKETING_TERMS: frozenset[str] = frozenset({
    "marketing", "brand", "audience", "campaign", "growth",
    "market research", "conversion", "engagement", "SNS",
    "content strategy", "user acquisition",
    "마케팅", "브랜드", "타겟", "캠페인", "성장",
    "시장조사", "전환율", "참여", "획득",
})

_VALIDATION_TERMS: frozenset[str] = frozenset({
    "validate", "verify", "quality", "compliance", "risk",
    "standard", "audit", "review", "check", "assessment",
    "검증", "확인", "품질", "준수", "위험",
    "기준", "감사", "리뷰", "평가",
})

_EXECUTION_TERMS: frozenset[str] = frozenset({
    "execute", "deploy", "implement", "build", "run",
    "automation", "pipeline", "artifact", "tool",
    "실행", "배포", "구현", "빌드", "자동화",
})

_TEAM_VOCAB_MAP: dict[str, frozenset[str]] = {
    "content-production": _CONTENT_CREATION_TERMS,
    "art-design": _ART_DESIGN_TERMS,
    "tech-engineering": _TECH_ENGINEERING_TERMS,
    "marketing": _MARKETING_TERMS,
    "validation": _VALIDATION_TERMS,
    "execution": _EXECUTION_TERMS,
}

# ═════════════════════════════════════════════════════════════════════════
# Irrelevant / role-confusion patterns
# ═════════════════════════════════════════════════════════════════════════

# Patterns that suggest role-boundary violation (a persona speaking outside
# its domain with inappropriate authority).
_ROLE_BOUNDARY_MARKERS = re.compile(
    r"(?:"
    r"as\s+(?:the|a)\s+(?:different|other)\s+role|"
    r"I\s+will\s+override|"
    r"overruling|"
    r"speaking\s+for\s+(?:the\s+)?\w+\s+team"
    r")",
    re.IGNORECASE,
)

# Patterns suggesting the persona is uncertain about its own identity.
_IDENTITY_CONFUSION_PATTERNS = re.compile(
    r"(?:"
    r"I\s+don't\s+know\s+(?:my|what my)\s+role|"
    r"as\s+an?\s+(?:AI|language\s+model|assistant)|"
    r"I\s+am\s+(?:just|only|simply)\s+an?\s+(?:AI|language\s+model)|"
    r"not\s+sure\s+(?:what|if|how)\s+(?:my|I|this)\s+role|"
    r"what\s+team\s+(?:am\s+)?I|"
    r"저는\s*(?:그냥|단순한|단지)\s*(?:AI|인공지능)|"
    r"제\s*역할을\s*모르|"
    r"어떤\s*팀|무슨\s*역할"
    r")",
    re.IGNORECASE,
)


# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ToneProfile:
    """Expected tone characteristics for a persona.

    Attributes:
        formality: One of ``casual``, ``semi_formal``, ``professional``,
                   ``formal``.
        assertiveness: One of ``tentative``, ``measured``,
                       ``confident_measured``, ``authoritative``.
        emotional_valence: One of ``neutral``, ``neutral_positive``,
                           ``enthusiastic``, ``cautious``.
        style: One of ``analytical``, ``creative``, ``analytical_creative``,
               ``pragmatic``, ``collaborative``.
    """

    formality: str = "professional"
    assertiveness: str = "confident_measured"
    emotional_valence: str = "neutral_positive"
    style: str = "analytical"


@dataclass(frozen=True)
class PersonaSpec:
    """Complete persona specification for consistency validation.

    Extends the basic role definition with consistency-relevant metadata
    needed to evaluate LLM response alignment.

    Attributes:
        role_id: Kebab-case role identifier (e.g. ``art-director``).
        display_name: Human-readable role name (e.g. ``아트 디렉터``).
        team: Team affiliation (e.g. ``art-design``).
        role_type: ``leader``, ``worker``, ``validator``, or ``executor``.
        tone_profile: Expected tone characteristics.
        role_vocabulary: Dictionary with ``keywords`` and ``domain_terms``
                         lists of expected vocabulary.
        behavioral_constraints: List of behavioral rules the response must
                                obey (human-readable constraint IDs).
        forbidden_patterns: List of pattern IDs that must NOT appear in
                            the response.
    """

    role_id: str
    display_name: str
    team: str
    role_type: str = "worker"
    tone_profile: ToneProfile = field(default_factory=ToneProfile)
    role_vocabulary: dict[str, list[str]] = field(default_factory=dict)
    behavioral_constraints: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.role_id or not self.role_id.strip():
            raise ValueError("role_id must be a non-empty string")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("display_name must be a non-empty string")
        if not self.team or not self.team.strip():
            raise ValueError("team must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary."""
        return {
            "role_id": self.role_id,
            "display_name": self.display_name,
            "team": self.team,
            "role_type": self.role_type,
            "tone_profile": {
                "formality": self.tone_profile.formality,
                "assertiveness": self.tone_profile.assertiveness,
                "emotional_valence": self.tone_profile.emotional_valence,
                "style": self.tone_profile.style,
            },
            "role_vocabulary": {
                k: list(v) for k, v in self.role_vocabulary.items()
            },
            "behavioral_constraints": list(self.behavioral_constraints),
            "forbidden_patterns": list(self.forbidden_patterns),
        }


@dataclass(frozen=True)
class ConsistencyViolation:
    """A single consistency violation detected in a persona response.

    Attributes:
        category: Dimension of violation — ``tone``, ``vocabulary``,
                  ``constraint``, ``forbidden_pattern``.
        severity: ``warning``, ``minor``, ``major``, ``critical``.
        message: Human-readable description of the violation.
        detail: Additional diagnostic context (e.g. matched text snippet).
    """

    category: str
    severity: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class PersonaConsistencyReport:
    """Aggregated result of persona consistency validation.

    Attributes:
        persona_id: The role_id that was validated.
        passed: True when overall_score >= threshold and no critical
                violations exist.
        overall_score: Weighted aggregate 0.0–1.0.
        tone_score: Tone alignment score 0.0–1.0.
        vocabulary_score: Role vocabulary alignment score 0.0–1.0.
        constraints_score: Behavioral constraint compliance 0.0–1.0.
        forbidden_score: Forbidden pattern absence score 0.0–1.0.
        violations: All detected consistency violations.
        response_length: Character count of the validated response.
        threshold: The pass threshold that was applied.
    """

    persona_id: str
    passed: bool
    overall_score: float
    tone_score: float
    vocabulary_score: float
    constraints_score: float
    forbidden_score: float
    violations: tuple[ConsistencyViolation, ...]
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

    def violations_by_category(
        self,
    ) -> dict[str, tuple[ConsistencyViolation, ...]]:
        """Group violations by category."""
        grouped: dict[str, list[ConsistencyViolation]] = {}
        for v in self.violations:
            grouped.setdefault(v.category, []).append(v)
        return {k: tuple(v) for k, v in grouped.items()}


# ═════════════════════════════════════════════════════════════════════════
# Tone heuristics
# ═════════════════════════════════════════════════════════════════════════


def _analyze_tone(
    response: str, profile: ToneProfile
) -> tuple[float, list[ConsistencyViolation]]:
    """Heuristic tone analysis of the response text.

    Uses rule-based markers (sentence length, formality endings, emotional
    adjectives) to estimate tone alignment with the expected profile.

    Returns:
        (score, violations) where score is in [0.0, 1.0].
    """
    violations: list[ConsistencyViolation] = []
    score = 1.0
    text = response.strip()

    if not text:
        return 0.0, [
            ConsistencyViolation(
                category="tone",
                severity="critical",
                message="Response is empty — cannot evaluate tone.",
                detail="(empty response)",
            )
        ]

    # ── 1. Sentence count and average length ──
    sentences = re.split(r"[.!?\n]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_count = len(sentences)
    avg_len = sum(len(s) for s in sentences) / max(sentence_count, 1)

    # Very short responses (< 50 chars) are suspicious
    if len(text) < 50:
        score -= 0.15
        violations.append(
            ConsistencyViolation(
                category="tone",
                severity="warning",
                message="Response is very short (< 50 chars); may lack "
                "sufficient depth for a team-leader persona.",
                detail=f"response length = {len(text)} chars",
            )
        )

    # Extremely long sentences may indicate rambling
    if avg_len > 500:
        score -= 0.05
        violations.append(
            ConsistencyViolation(
                category="tone",
                severity="warning",
                message=f"Average sentence length ({avg_len:.0f} chars) is "
                f"very high; may indicate unstructured output.",
                detail=f"avg sentence length = {avg_len:.0f}",
            )
        )

    # ── 2. Formality heuristics (Korean-aware) ──
    # Count formal markers
    formal_matches = len(_KOREAN_FORMAL_ENDINGS.findall(text))
    casual_matches = len(_KOREAN_CASUAL_ENDINGS.findall(text))

    expect_formal = profile.formality in ("professional", "formal", "semi_formal")

    # For Korean text with sufficient markers
    total_kr_markers = formal_matches + casual_matches
    if total_kr_markers > 0:
        formal_ratio = formal_matches / max(total_kr_markers, 1)
        if expect_formal and formal_ratio < 0.5:
            score -= 0.10
            violations.append(
                ConsistencyViolation(
                    category="tone",
                    severity="minor",
                    message=f"Expected {profile.formality} tone but only "
                    f"{formal_ratio:.0%} of sentence endings are formal.",
                    detail=(
                        f"formal={formal_matches}, casual={casual_matches}"
                    ),
                )
            )
        elif not expect_formal and formal_ratio > 0.9:
            score -= 0.05
            violations.append(
                ConsistencyViolation(
                    category="tone",
                    severity="warning",
                    message=(
                        f"Response is highly formal ({formal_ratio:.0%}) but "
                        f"persona expects {profile.formality} tone."
                    ),
                    detail=(
                        f"formal={formal_matches}, casual={casual_matches}"
                    ),
                )
            )

    # ── 3. Assertiveness heuristics ──
    hedge_words = re.compile(
        r"\b(?:maybe|perhaps|possibly|might|could be|I think|"
        r"아마|아마도|~일 수도|~것 같|~일지도)\b",
        re.IGNORECASE,
    )
    confident_words = re.compile(
        r"\b(?:clearly|definitely|certainly|must|should|will|"
        r"분명|확실|반드시|틀림없이|해야 합니다|해야 한다)\b",
        re.IGNORECASE,
    )
    hedge_count = len(hedge_words.findall(text))
    confident_count = len(confident_words.findall(text))

    if profile.assertiveness in ("authoritative", "confident_measured"):
        if hedge_count > 3 and confident_count < 2:
            score -= 0.10
            violations.append(
                ConsistencyViolation(
                    category="tone",
                    severity="minor",
                    message=(
                        f"Expected {profile.assertiveness} tone but found "
                        f"{hedge_count} hedging markers and only "
                        f"{confident_count} confident markers."
                    ),
                    detail=f"hedge={hedge_count}, confident={confident_count}",
                )
            )
    elif profile.assertiveness == "tentative" and (
        confident_count > 5 and hedge_count < 1
    ):
        score -= 0.05
        violations.append(
            ConsistencyViolation(
                category="tone",
                severity="warning",
                message=(
                    f"Expected tentative tone ({profile.assertiveness}) "
                    f"but found {confident_count} confident markers."
                ),
                detail=f"confident={confident_count}, hedge={hedge_count}",
            )
        )

    # ── 4. Emotional valence heuristics ──
    positive_words = re.compile(
        r"\b(?:excellent|great|good|wonderful|fantastic|promising|"
        r"excited|happy|optimistic|"
        r"훌륭|좋|멋지|기대|희망|긍정적|만족)\b",
        re.IGNORECASE,
    )
    negative_words = re.compile(
        r"\b(?:terrible|awful|bad|disastrous|worried|concerned|"
        r"pessimistic|fear|danger|failure|fail|failed|wrong|"
        r"mistake|error|problem|risk|threat|severe|critical|"
        r"끔찍|나쁘|걱정|우려|위험|비관|실패|문제|심각)\b",
        re.IGNORECASE,
    )
    pos_count = len(positive_words.findall(text))
    neg_count = len(negative_words.findall(text))

    if profile.emotional_valence in ("neutral", "cautious", "neutral_positive"):
        if pos_count > 5:
            score -= 0.05
            violations.append(
                ConsistencyViolation(
                    category="tone",
                    severity="warning",
                    message=(
                        f"Expected {profile.emotional_valence} tone but "
                        f"found {pos_count} strongly positive markers."
                    ),
                    detail=f"positive markers = {pos_count}",
                )
            )
        if neg_count > 5:
            score -= 0.10
            violations.append(
                ConsistencyViolation(
                    category="tone",
                    severity="minor",
                    message=(
                        f"Expected {profile.emotional_valence} tone but "
                        f"found {neg_count} strongly negative markers."
                    ),
                    detail=f"negative markers = {neg_count}",
                )
            )

    # ── 5. Style heuristics ──
    if profile.style == "analytical" and sentence_count < 3:
        score -= 0.10
        violations.append(
            ConsistencyViolation(
                category="tone",
                severity="minor",
                message=(
                    "Expected analytical style but response has "
                    f"only {sentence_count} sentence(s)."
                ),
                detail=f"sentence_count={sentence_count}",
            )
        )

    return max(score, 0.0), violations


# ═════════════════════════════════════════════════════════════════════════
# Vocabulary heuristics
# ═════════════════════════════════════════════════════════════════════════


def _analyze_vocabulary(
    response: str,
    role_vocabulary: dict[str, list[str]],
    team: str,
    role_type: str,
) -> tuple[float, list[ConsistencyViolation]]:
    """Check whether the response uses role-appropriate vocabulary.

    Returns:
        (score, violations) where score is in [0.0, 1.0].
    """
    violations: list[ConsistencyViolation] = []
    text_lower = response.lower()
    score = 1.0

    # ── 1. Check explicit role vocabulary keywords ──
    keywords: list[str] = role_vocabulary.get("keywords", [])
    domain_terms: list[str] = role_vocabulary.get("domain_terms", [])

    all_expected = list(keywords) + list(domain_terms)

    if all_expected:
        found_terms: list[str] = []
        for term in all_expected:
            if term.lower() in text_lower:
                found_terms.append(term)

        coverage = len(found_terms) / len(all_expected)

        if coverage < 0.15 and all_expected:
            score -= 0.30
            violations.append(
                ConsistencyViolation(
                    category="vocabulary",
                    severity="major",
                    message=(
                        f"Very low role vocabulary coverage "
                        f"({coverage:.0%}). Found only: "
                        f"{found_terms[:5] or '(none)'}"
                    ),
                    detail=f"expected={len(all_expected)}, found={len(found_terms)}",
                )
            )
        elif coverage < 0.30:
            score -= 0.15
            violations.append(
                ConsistencyViolation(
                    category="vocabulary",
                    severity="minor",
                    message=(
                        f"Low role vocabulary coverage ({coverage:.0%})."
                    ),
                    detail=f"found {len(found_terms)}/{len(all_expected)} terms",
                )
            )

    # ── 2. Check team-level domain vocabulary ──
    team_terms = _TEAM_VOCAB_MAP.get(team)
    if team_terms:
        found_team = [t for t in team_terms if t.lower() in text_lower]
        team_coverage = len(found_team) / len(team_terms)

        if team_coverage < 0.05:
            score -= 0.20
            violations.append(
                ConsistencyViolation(
                    category="vocabulary",
                    severity="major",
                    message=(
                        f"Response contains almost no {team}-domain "
                        f"vocabulary ({team_coverage:.0%}). The persona "
                        f"may not be speaking from its assigned domain."
                    ),
                    detail=f"found {len(found_team)}/{len(team_terms)} team terms",
                )
            )
        elif team_coverage < 0.10:
            score -= 0.10
            violations.append(
                ConsistencyViolation(
                    category="vocabulary",
                    severity="warning",
                    message=(
                        f"Low {team}-domain vocabulary ({team_coverage:.0%})."
                    ),
                    detail=f"found {len(found_team)}/{len(team_terms)} team terms",
                )
            )

    # ── 3. Leader-specific vocabulary expectations ──
    if role_type == "leader":
        leadership_terms = re.compile(
            r"\b(?:recommend|decide|direct|oversee|approve|guide|"
            r"prioritize|strategy|direction|"
            r"추천|결정|지시|지휘|승인|안내|지도|"
            r"우선순위|전략|방향)\b",
            re.IGNORECASE,
        )
        leader_count = len(leadership_terms.findall(text_lower))
        if leader_count < 2:
            score -= 0.10
            violations.append(
                ConsistencyViolation(
                    category="vocabulary",
                    severity="minor",
                    message=(
                        f"Team leader persona should use leadership-oriented "
                        f"language. Found only {leader_count} leadership "
                        f"term(s)."
                    ),
                    detail=f"leader_terms_found={leader_count}",
                )
            )

    return max(score, 0.0), violations


# ═════════════════════════════════════════════════════════════════════════
# Behavioral constraint heuristics
# ═════════════════════════════════════════════════════════════════════════


# Map of constraint IDs → validation functions
# Each returns (passed: bool, detail: str)
def _check_stay_in_domain(response: str, team: str) -> tuple[bool, str]:
    """Constraint: persona stays within its assigned domain."""
    # Check for team-confusion markers
    for other_team, terms in _TEAM_VOCAB_MAP.items():
        if other_team == team:
            continue
        # Count cross-domain terms
        found = [t for t in terms if t.lower() in response.lower()]
        if len(found) > 8:
            return (
                False,
                f"Response contains {len(found)} terms from {other_team} "
                f"domain — possible role confusion.",
            )
    return True, ""


def _check_acknowledge_dependencies(response: str) -> tuple[bool, str]:
    """Constraint: persona acknowledges cross-team dependencies."""
    dep_markers = re.compile(
        r"\b(?:depends on|requires|needs input from|coordinated with|"
        r"collaborat|involves|cross-team|dependency|"
        r"의존|필요|협업|협력|연계|연관)\b",
        re.IGNORECASE,
    )
    if len(dep_markers.findall(response)) > 0:
        return True, ""
    return False, "No cross-team dependency acknowledgment found."


def _check_actionable_recommendations(response: str) -> tuple[bool, str]:
    """Constraint: persona provides actionable recommendations."""
    action_markers = re.compile(
        r"\b(?:recommend|propose|suggest|should|action item|next step|"
        r"implement|plan|"
        r"추천|제안|해야|액션|다음 단계|실행|계획)\b",
        re.IGNORECASE,
    )
    count = len(action_markers.findall(response))
    if count >= 2:
        return True, ""
    return (
        False,
        f"Only {count} actionable recommendation marker(s) found.",
    )


def _check_not_override_authority(response: str) -> tuple[bool, str]:
    """Constraint: persona does not override another leader's authority."""
    override_markers = re.compile(
        r"\b(?:override|overrule|veto|disregard|ignore|"
        r"I (?:will|am going to) decide for|"
        r"무시|덮어쓰|거부|묵살)\b",
        re.IGNORECASE,
    )
    if override_markers.search(response):
        return (
            False,
            "Response contains authority-override language.",
        )
    return True, ""


def _check_no_identity_confusion(response: str) -> tuple[bool, str]:
    """Constraint: persona does not express identity confusion."""
    match = _IDENTITY_CONFUSION_PATTERNS.search(response)
    if match:
        return (
            False,
            f"Identity confusion detected: '{match.group()[:80]}'",
        )
    match2 = _ROLE_BOUNDARY_MARKERS.search(response)
    if match2:
        return (
            False,
            f"Role-boundary violation detected: '{match2.group()[:80]}'",
        )
    return True, ""


_CONSTRAINT_CHECKS: dict[str, Any] = {
    "stay_within_domain": _check_stay_in_domain,
    "within_visual_design_domain": _check_stay_in_domain,
    "stay_within_visual_design_domain": _check_stay_in_domain,
    "within_tech_domain": _check_stay_in_domain,
    "stay_within_content_domain": _check_stay_in_domain,
    "within_marketing_domain": _check_stay_in_domain,
    "within_validation_domain": _check_stay_in_domain,
    "within_execution_domain": _check_stay_in_domain,
    "acknowledge_cross_team_dependencies": _check_acknowledge_dependencies,
    "provide_actionable_recommendations": _check_actionable_recommendations,
    "provide_risk_assessment": _check_actionable_recommendations,
    "no_override_authority": _check_not_override_authority,
    "not_override_other_leaders": _check_not_override_authority,
    "no_identity_confusion": _check_no_identity_confusion,
    "maintain_role_identity": _check_no_identity_confusion,
}


def _analyze_constraints(
    response: str,
    constraints: Sequence[str],
    team: str,
) -> tuple[float, list[ConsistencyViolation]]:
    """Check behavioral constraint compliance.

    Returns:
        (score, violations) where score is in [0.0, 1.0].
    """
    violations: list[ConsistencyViolation] = []

    if not constraints:
        return 1.0, violations

    passed_count = 0
    total = len(constraints)

    for constraint_id in constraints:
        # Resolve the constraint check function
        check_fn = _CONSTRAINT_CHECKS.get(constraint_id)

        if check_fn is None:
            # Unknown constraint — skip with a warning
            violations.append(
                ConsistencyViolation(
                    category="constraint",
                    severity="warning",
                    message=f"Unknown constraint '{constraint_id}' — "
                    f"could not validate.",
                    detail="constraint not in registry",
                )
            )
            continue

        # Call the check function; some need the team parameter
        try:
            if constraint_id.endswith("_domain") or "domain" in constraint_id:
                result = check_fn(response, team)
            else:
                result = check_fn(response)
        except TypeError:
            # Fallback: try with team
            try:
                result = check_fn(response, team)
            except TypeError:
                result = check_fn(response)

        ok, detail = result
        if ok:
            passed_count += 1
        else:
            severity = "major" if "authority" in constraint_id.lower() else "minor"
            violations.append(
                ConsistencyViolation(
                    category="constraint",
                    severity=severity,
                    message=(
                        f"Constraint '{constraint_id}' violated: {detail}"
                    ),
                    detail=detail,
                )
            )

    score = passed_count / total if total > 0 else 1.0
    return score, violations


# ═════════════════════════════════════════════════════════════════════════
# Forbidden pattern detection
# ═════════════════════════════════════════════════════════════════════════

# Mapping of forbidden pattern IDs → regex patterns (or check functions)
_FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "contradict_authority": re.compile(
        r"\b(?:I\s+(?:disagree|reject|oppose|refuse)\s+(?:with\s+)?"
        r"(?:the\s+)?(?:director|lead|manager|team\s+lead)|"
        r"that\s+(?:decision|direction)\s+is\s+wrong)",
        re.IGNORECASE,
    ),
    "make_engineering_decisions": re.compile(
        r"\b(?:the\s+(?:architecture|infrastructure|tech\s+stack|"
        r"database|server)\s+(?:should|must|will)\s+be|"
        r"we\s+(?:should|must|will)\s+(?:deploy|refactor|migrate|rewrite))",
        re.IGNORECASE,
    ),
    "make_financial_decisions": re.compile(
        r"\b(?:budget\s+(?:should|must|will)\s+be|"
        r"allocate\s+(?:\$\d+|\d+\s*(?:million|billion|천만|억|조))|"
        r"spend\s+(?:\$\d+|\d+\s*(?:million|billion)))",
        re.IGNORECASE,
    ),
    "override_marketing_strategy": re.compile(
        r"\b(?:marketing\s+(?:strategy|campaign|plan)\s+(?:should|must|will)\s+"
        r"(?:be|change|pivot)|"
        r"target\s+audience\s+(?:should|must)\s+(?:be|change))",
        re.IGNORECASE,
    ),
    "make_art_direction_decisions": re.compile(
        r"\b(?:art\s+(?:direction|style|concept)\s+(?:should|must|will)\s+"
        r"(?:be|change)|"
        r"visual\s+(?:identity|direction)\s+(?:should|must)\s+be)",
        re.IGNORECASE,
    ),
    "make_content_decisions": re.compile(
        r"\b(?:content\s+(?:strategy|direction|plan)\s+(?:should|must)\s+"
        r"(?:be|change)|"
        r"story\s+(?:direction|arc)\s+(?:should|must)\s+be)",
        re.IGNORECASE,
    ),
    "contradict_art_director_authority": re.compile(
        r"\b(?:art\s+(?:director|team)\s+(?:is|are)\s+wrong|"
        r"visual\s+(?:direction|style)\s+(?:is|are)\s+(?:wrong|incorrect|"
        r"misguided))",
        re.IGNORECASE,
    ),
    "contradict_content_director_authority": re.compile(
        r"\b(?:content\s+(?:director|team)\s+(?:is|are)\s+wrong|"
        r"creative\s+direction\s+(?:is|are)\s+(?:wrong|misguided))",
        re.IGNORECASE,
    ),
    "contradict_tech_director_authority": re.compile(
        r"\b(?:tech\s+(?:director|team|lead)\s+(?:is|are)\s+wrong|"
        r"technical\s+(?:direction|architecture)\s+(?:is|are)\s+"
        r"(?:wrong|incorrect|misguided))",
        re.IGNORECASE,
    ),
    "self_deprecation_as_ai": re.compile(
        r"\b(?:I\s+(?:am|'m)\s+(?:just|only)\s+an?\s+(?:AI|language\s+model|"
        r"assistant|bot)|"
        r"as\s+an?\s+(?:AI|language\s+model)\s+I\s+(?:cannot|can't|"
        r"don't|am\s+not))",
        re.IGNORECASE,
    ),
    "role_abdication": re.compile(
        r"\b(?:I\s+(?:defer|leave|hand\s+over)\s+(?:this|the\s+decision)\s+"
        r"(?:to|completely)|"
        r"I\s+(?:cannot|can't|won't)\s+(?:make|give|provide)\s+(?:this|a|my|"
        r"any)\s+(?:decision|opinion|recommendation))",
        re.IGNORECASE,
    ),
    "use_personal_pronouns_inappropriately": re.compile(
        r"\b(?:in\s+my\s+(?:personal|own)\s+(?:opinion|view|experience)|"
        r"personally,?\s+I\s+(?:think|believe|feel)|"
        r"개인적으로|제\s*개인적인|내\s*생각에는)",
        re.IGNORECASE,
    ),
}


def _analyze_forbidden_patterns(
    response: str,
    forbidden_ids: Sequence[str],
) -> tuple[float, list[ConsistencyViolation]]:
    """Check for forbidden patterns in the response.

    Returns:
        (score, violations) where score is in [0.0, 1.0].
        1.0 means no forbidden patterns detected.
    """
    violations: list[ConsistencyViolation] = []

    if not forbidden_ids:
        return 1.0, violations

    # ── Always check identity confusion regardless of explicit list ──
    identity_match = _IDENTITY_CONFUSION_PATTERNS.search(response)
    if identity_match:
        violations.append(
            ConsistencyViolation(
                category="forbidden_pattern",
                severity="critical",
                message=(
                    f"Forbidden: identity confusion detected — "
                    f"'{identity_match.group()[:80]}'"
                ),
                detail=f"matched: {identity_match.group()[:100]}",
            )
        )

    role_boundary_match = _ROLE_BOUNDARY_MARKERS.search(response)
    if role_boundary_match:
        violations.append(
            ConsistencyViolation(
                category="forbidden_pattern",
                severity="major",
                message=(
                    f"Forbidden: role-boundary violation — "
                    f"'{role_boundary_match.group()[:80]}'"
                ),
                detail=f"matched: {role_boundary_match.group()[:100]}",
            )
        )

    # ── Check explicit forbidden patterns ──
    for pattern_id in forbidden_ids:
        pattern = _FORBIDDEN_PATTERNS.get(pattern_id)
        if pattern is None:
            # Unknown pattern — skip gracefully
            continue

        matches = pattern.findall(response)
        if matches:
            severity = "critical" if "authority" in pattern_id.lower() else "major"
            violations.append(
                ConsistencyViolation(
                    category="forbidden_pattern",
                    severity=severity,
                    message=(
                        f"Forbidden pattern '{pattern_id}' detected "
                        f"({len(matches)} match(es))."
                    ),
                    detail=f"matches: {matches[:3]}",
                )
            )

    # Score: 1.0 if zero forbidden violations, dropping by 0.25 per violation
    score = max(1.0 - (0.25 * len(violations)), 0.0)
    return score, violations


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


def validate_persona_consistency(
    response: str,
    persona_spec: PersonaSpec,
    *,
    threshold: float = 0.70,
) -> PersonaConsistencyReport:
    """Validate an LLM response against a persona specification.

    This is the main entry point for **Sub-AC 6.2a**.  It evaluates the
    given response text across four dimensions — tone, role vocabulary,
    behavioral constraints, and forbidden patterns — and returns a
    structured report with per-category scores and an overall verdict.

    The validator is **entirely rule-based** (regex + keyword heuristics)
    and requires no LLM calls, making it deterministic and independently
    testable with any (response, persona_spec) pair.

    Args:
        response: The LLM response text to validate.  For opinion packets
                  use the ``opinion_content`` field.
        persona_spec: The ``PersonaSpec`` defining expected tone,
                      vocabulary, constraints, and forbidden patterns.
        threshold: Overall score threshold for ``passed=True``
                   (default: 0.70).

    Returns:
        ``PersonaConsistencyReport`` with per-category scores, violation
        details, and a pass/fail verdict.

    Examples:
        >>> spec = PersonaSpec(
        ...     role_id="art-director",
        ...     display_name="아트 디렉터",
        ...     team="art-design",
        ...     role_type="leader",
        ...     tone_profile=ToneProfile(formality="professional"),
        ...     role_vocabulary={"keywords": ["visual", "design", "palette"]},
        ...     behavioral_constraints=("stay_within_visual_design_domain",),
        ...     forbidden_patterns=("make_engineering_decisions",),
        ... )
        >>> result = validate_persona_consistency(
        ...     "I recommend a neon-noir visual palette with high-contrast...",
        ...     spec,
        ... )
        >>> result.passed
        True
    """
    # ── Guard: empty / whitespace-only response ──
    if not response or not response.strip():
        return PersonaConsistencyReport(
            persona_id=persona_spec.role_id,
            passed=False,
            overall_score=0.0,
            tone_score=0.0,
            vocabulary_score=0.0,
            constraints_score=0.0,
            forbidden_score=0.0,
            violations=(
                ConsistencyViolation(
                    category="tone",
                    severity="critical",
                    message="Response is empty — cannot validate consistency.",
                    detail="(empty response)",
                ),
            ),
            response_length=0,
            threshold=threshold,
        )

    response = response.strip()

    # ── Run each dimension ──
    tone_score, tone_violations = _analyze_tone(response, persona_spec.tone_profile)
    vocab_score, vocab_violations = _analyze_vocabulary(
        response,
        persona_spec.role_vocabulary,
        persona_spec.team,
        persona_spec.role_type,
    )
    constraints_score, constraint_violations = _analyze_constraints(
        response,
        persona_spec.behavioral_constraints,
        persona_spec.team,
    )
    forbidden_score, forbidden_violations = _analyze_forbidden_patterns(
        response,
        persona_spec.forbidden_patterns,
    )

    # ── Aggregate ──
    all_violations: list[ConsistencyViolation] = []
    all_violations.extend(tone_violations)
    all_violations.extend(vocab_violations)
    all_violations.extend(constraint_violations)
    all_violations.extend(forbidden_violations)

    # Weighted overall score (tone=0.25, vocab=0.25, constraints=0.25,
    # forbidden=0.25)
    overall = (
        tone_score * 0.25
        + vocab_score * 0.25
        + constraints_score * 0.25
        + forbidden_score * 0.25
    )

    # Critical violations are an automatic fail
    has_critical = any(v.severity == "critical" for v in all_violations)
    passed = overall >= threshold and not has_critical

    return PersonaConsistencyReport(
        persona_id=persona_spec.role_id,
        passed=passed,
        overall_score=round(overall, 4),
        tone_score=round(tone_score, 4),
        vocabulary_score=round(vocab_score, 4),
        constraints_score=round(constraints_score, 4),
        forbidden_score=round(forbidden_score, 4),
        violations=tuple(all_violations),
        response_length=len(response),
        threshold=threshold,
    )


# ═════════════════════════════════════════════════════════════════════════
# Convenience: build PersonaSpec for the 6 team leaders
# ═════════════════════════════════════════════════════════════════════════


def make_art_director_spec() -> PersonaSpec:
    """Return the standard Art Director persona spec."""
    return PersonaSpec(
        role_id="art-director",
        display_name="아트 디렉터",
        team="art-design",
        role_type="leader",
        tone_profile=ToneProfile(
            formality="professional",
            assertiveness="confident_measured",
            emotional_valence="neutral_positive",
            style="analytical_creative",
        ),
        role_vocabulary={
            "keywords": [
                "visual direction", "art style", "design system",
                "color palette", "composition", "aesthetic",
                "character design", "typography", "layout",
                "brand identity", "motion graphics",
            ],
            "domain_terms": [
                "color grading", "visual hierarchy", "design language",
                "style guide", "mood board", "concept art",
                "illustration", "rendering", "shader",
                "비주얼", "디자인", "아트", "색감", "구도",
            ],
        },
        behavioral_constraints=(
            "stay_within_visual_design_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_engineering_decisions",
            "make_financial_decisions",
            "override_marketing_strategy",
        ),
    )


def make_content_director_spec() -> PersonaSpec:
    """Return the standard Content Director persona spec."""
    return PersonaSpec(
        role_id="content-director",
        display_name="콘텐츠 디렉터",
        team="content-production",
        role_type="leader",
        tone_profile=ToneProfile(
            formality="professional",
            assertiveness="confident_measured",
            emotional_valence="enthusiastic",
            style="creative",
        ),
        role_vocabulary={
            "keywords": [
                "creative direction", "content strategy", "storytelling",
                "narrative", "script", "production", "audience engagement",
                "entertainment", "character development", "plot",
            ],
            "domain_terms": [
                "시나리오", "대본", "스토리", "연출", "제작",
                "콘텐츠", "크리에이티브", "기획", "캐릭터",
                "세계관", "내러티브", "에피소드",
            ],
        },
        behavioral_constraints=(
            "stay_within_content_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_engineering_decisions",
            "make_financial_decisions",
            "make_art_direction_decisions",
        ),
    )


def make_tech_director_spec() -> PersonaSpec:
    """Return the standard Tech Director persona spec."""
    return PersonaSpec(
        role_id="tech-director",
        display_name="테크 디렉터",
        team="tech-engineering",
        role_type="leader",
        tone_profile=ToneProfile(
            formality="professional",
            assertiveness="authoritative",
            emotional_valence="neutral",
            style="analytical",
        ),
        role_vocabulary={
            "keywords": [
                "technical architecture", "system design", "infrastructure",
                "code quality", "scalability", "performance",
                "security", "deployment", "pipeline", "api design",
            ],
            "domain_terms": [
                "microservices", "database", "cloud", "CI/CD",
                "containerization", "monitoring", "load balancing",
                "아키텍처", "시스템", "인프라", "배포", "보안",
            ],
        },
        behavioral_constraints=(
            "stay_within_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_art_direction_decisions",
            "make_content_decisions",
            "make_financial_decisions",
        ),
    )


def make_marketing_lead_spec() -> PersonaSpec:
    """Return the standard Marketing Lead persona spec."""
    return PersonaSpec(
        role_id="marketing-lead",
        display_name="마케팅 리드",
        team="marketing",
        role_type="leader",
        tone_profile=ToneProfile(
            formality="professional",
            assertiveness="confident_measured",
            emotional_valence="enthusiastic",
            style="pragmatic",
        ),
        role_vocabulary={
            "keywords": [
                "marketing strategy", "brand management", "campaign",
                "audience growth", "market research", "engagement",
                "conversion", "SNS strategy", "content marketing",
            ],
            "domain_terms": [
                "마케팅", "브랜드", "타겟", "캠페인", "성장",
                "시장", "전략", "고객", "홍보", "바이럴",
            ],
        },
        behavioral_constraints=(
            "stay_within_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_engineering_decisions",
            "make_art_direction_decisions",
            "make_content_decisions",
        ),
    )


def make_validator_spec() -> PersonaSpec:
    """Return the standard Validator persona spec."""
    return PersonaSpec(
        role_id="validator",
        display_name="검증자",
        team="validation",
        role_type="validator",
        tone_profile=ToneProfile(
            formality="formal",
            assertiveness="authoritative",
            emotional_valence="cautious",
            style="analytical",
        ),
        role_vocabulary={
            "keywords": [
                "validate", "verify", "compliance", "quality assurance",
                "risk assessment", "audit", "standards", "benchmark",
                "criteria", "threshold", "evidence",
            ],
            "domain_terms": [
                "검증", "확인", "준수", "품질", "위험", "기준",
                "평가", "리뷰", "오류", "결함",
            ],
        },
        behavioral_constraints=(
            "provide_risk_assessment",
            "maintain_role_identity",
            "not_override_other_leaders",
        ),
        forbidden_patterns=(
            "make_engineering_decisions",
            "make_art_direction_decisions",
            "make_content_decisions",
            "make_financial_decisions",
            "role_abdication",
        ),
    )


def make_executor_spec() -> PersonaSpec:
    """Return the standard Executor persona spec."""
    return PersonaSpec(
        role_id="executor",
        display_name="실행자",
        team="execution",
        role_type="executor",
        tone_profile=ToneProfile(
            formality="semi_formal",
            assertiveness="confident_measured",
            emotional_valence="neutral_positive",
            style="pragmatic",
        ),
        role_vocabulary={
            "keywords": [
                "execute", "deploy", "implement", "build", "automation",
                "pipeline", "artifact", "tool", "workflow",
            ],
            "domain_terms": [
                "실행", "배포", "구현", "빌드", "자동화", "파이프라인",
                "스크립트", "훅", "트리거",
            ],
        },
        behavioral_constraints=(
            "stay_within_domain",
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_art_direction_decisions",
            "make_content_decisions",
            "make_financial_decisions",
            "role_abdication",
        ),
    )


def make_coordinator_spec() -> PersonaSpec:
    """Return the standard Coordinator persona spec."""
    return PersonaSpec(
        role_id="coordinator",
        display_name="코디네이터",
        team="execution",
        role_type="leader",
        tone_profile=ToneProfile(
            formality="professional",
            assertiveness="authoritative",
            emotional_valence="neutral",
            style="collaborative",
        ),
        role_vocabulary={
            "keywords": [
                "coordinate", "facilitate", "agenda", "consensus",
                "round", "summary", "decision", "action items",
                "next steps", "priority",
            ],
            "domain_terms": [
                "조정", "진행", "의제", "합의", "라운드", "요약",
                "결정", "다음", "우선순위",
            ],
        },
        behavioral_constraints=(
            "acknowledge_cross_team_dependencies",
            "provide_actionable_recommendations",
        ),
        forbidden_patterns=(
            "make_engineering_decisions",
            "make_art_direction_decisions",
            "make_content_decisions",
            "make_financial_decisions",
            "role_abdication",
        ),
    )


# Registry of all standard team-leader PersonaSpec factories
_STANDARD_SPECS: dict[str, Any] = {
    "art-director": make_art_director_spec,
    "content-director": make_content_director_spec,
    "tech-director": make_tech_director_spec,
    "marketing-lead": make_marketing_lead_spec,
    "validator": make_validator_spec,
    "executor": make_executor_spec,
    "coordinator": make_coordinator_spec,
}


def get_standard_spec(role_id: str) -> PersonaSpec:
    """Return the standard ``PersonaSpec`` for a known team-leader role.

    Args:
        role_id: One of ``art-director``, ``content-director``,
                 ``tech-director``, ``marketing-lead``, ``validator``,
                 ``executor``, ``coordinator``.

    Returns:
        A fully configured ``PersonaSpec``.

    Raises:
        KeyError: If ``role_id`` is not a recognized standard persona.
    """
    factory = _STANDARD_SPECS.get(role_id)
    if factory is None:
        raise KeyError(
            f"Unknown standard role_id '{role_id}'. "
            f"Available: {list(_STANDARD_SPECS)}"
        )
    return factory()
