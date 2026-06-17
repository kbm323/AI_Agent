"""Meeting intent parser for the AI_Agent multi-agent meeting system.

Sub-AC 2b: Receives cleaned message text (output from the Discord mention
extractor, Sub-AC 2a) and returns structured meeting intent (meeting_type,
topic, participants, urgency) or a sentinel if no meeting intent is detected.

The parser uses keyword-based detection for Korean and English meeting
requests, urgency signals, and topic classification.  It is designed to
be testable with diverse natural-language input strings without requiring
any LLM call.

Pipeline position:
    Discord gateway event
        -> discord_mention_extractor (Sub-AC 2a) -> cleaned text
        -> meeting_intent_parser (Sub-AC 2b)     -> MeetingIntent | None
        -> meeting_trigger (Sub-AC 1c)           -> MeetingContext
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Meeting type constants ────────────────────────────────────────────────

MEETING_TYPE_CREATIVE = "creative_production"
MEETING_TYPE_TECHNICAL = "technical_development"
MEETING_TYPE_MARKETING = "marketing_strategy"
MEETING_TYPE_RISK = "risk_assessment"
MEETING_TYPE_PLANNING = "general_planning"
MEETING_TYPE_REVIEW = "project_review"

# ── Priority constants ────────────────────────────────────────────────────

PRIORITY_P0 = "p0"  # blocking / emergency
PRIORITY_P1 = "p1"  # high / urgent
PRIORITY_P2 = "p2"  # normal (default)
PRIORITY_P3 = "p3"  # low / when-available

# ── Structured output ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class MeetingIntent:
    """Structured meeting intent extracted from natural-language input.

    Fields map to the Seed ontology: meeting_type (agenda_type precursor),
    topic (agenda precursor), participants (suggested roles/teams), and
    priority (urgency level).

    All string fields are stripped.  ``participants`` is a tuple of
    normalised, deduplicated role/team identifiers (backward-compatible
    union of teams + suggested_roles).

    .. versionchanged:: 2.2
       Added ``teams`` and ``suggested_roles`` fields to separate
       team-level selection from role-level participant constraints.
       ``participants`` is now a computed union of both for backward
       compatibility.
    """

    meeting_type: str
    """Classified meeting type (one of the MEETING_TYPE_* constants)."""

    topic: str
    """Core meeting topic extracted from the user's message."""

    participants: tuple[str, ...] = ()
    """Suggested role or team identifiers mentioned in the message
    (backward-compatible union of teams + suggested_roles)."""

    teams: tuple[str, ...] = ()
    """Team-level selection: which teams should be convened.
    Values are team role-IDs (e.g. ``'content-pd'``, ``'art-director'``,
    ``'tech-director'``, ``'marketing-lead'``, ``'execution-lead'``)."""

    suggested_roles: tuple[str, ...] = ()
    """Specific role-level participant constraints mentioned by the user.
    Values are role-IDs (e.g. ``'concept-artist'``, ``'backend-dev'``).
    These are candidate roles that may be included in the meeting if the
    routing system determines they are relevant."""

    urgency: str = PRIORITY_P2
    """Priority mapped from urgency keywords: p0 | p1 | p2 | p3."""

    is_meeting: bool = True
    """Always True — this message contains a meeting request."""

    confidence: float = 1.0
    """Parser confidence 0.0–1.0 for this interpretation."""

    reasoning: str = ""
    """Brief explanation of why this classification was chosen."""


# ── Sentinel for non-meeting messages ─────────────────────────────────────


@dataclass(frozen=True)
class NoMeetingIntent:
    """Returned when the input does not contain a meeting request.

    This sentinel avoids None checks and mirrors the NoMentionResult
    pattern from the Discord mention extractor (Sub-AC 2a).
    """

    is_meeting: bool = False
    """Always False — this message is not a meeting request."""

    reason: str = ""
    """Why the message was rejected (e.g. 'no_meeting_keyword')."""


# ── Keyword registries ────────────────────────────────────────────────────

# Meeting initiation keywords — any one of these triggers intent detection.
# Grouped by language for clarity; the matcher normalises input to lowercase.
_MEETING_KEYWORDS: tuple[tuple[str, str], ...] = (
    # Korean meeting verbs / nouns
    ("회의", "meeting"),
    ("미팅", "meeting"),
    ("논의", "discuss"),
    ("검토", "review"),
    ("상의", "consult"),
    ("협의", "confer"),
    ("토론", "debate"),
    ("브레인스토밍", "brainstorm"),
    ("브레인스토밍", "brainstorm"),
    ("의논", "discuss"),
    ("심의", "deliberate"),
    ("회고", "retrospective"),
    ("소집", "summon"),
    ("모여", "gather"),
    # English meeting keywords
    ("meeting", "meeting"),
    ("meet", "meeting"),
    ("discuss", "discuss"),
    ("review", "review"),
    ("brainstorm", "brainstorm"),
    ("consult", "consult"),
    ("sync", "sync"),
    ("standup", "standup"),
    ("planning", "planning"),
)


# Meeting-type classification rules: (keyword, meeting_type)
# Ordered by specificity — more specific matches take priority.
_TYPE_RULES: tuple[tuple[str, str], ...] = (
    # ── risk_assessment ──
    ("보안", MEETING_TYPE_RISK),
    ("취약점", MEETING_TYPE_RISK),
    ("침해", MEETING_TYPE_RISK),
    ("사고", MEETING_TYPE_RISK),
    ("장애", MEETING_TYPE_RISK),
    ("법률", MEETING_TYPE_RISK),
    ("법적", MEETING_TYPE_RISK),
    ("저작권", MEETING_TYPE_RISK),
    ("라이선스", MEETING_TYPE_RISK),
    ("규정", MEETING_TYPE_RISK),
    ("컴플라이언스", MEETING_TYPE_RISK),
    ("예산", MEETING_TYPE_RISK),
    ("비용", MEETING_TYPE_RISK),
    ("security", MEETING_TYPE_RISK),
    ("vulnerability", MEETING_TYPE_RISK),
    ("incident", MEETING_TYPE_RISK),
    ("compliance", MEETING_TYPE_RISK),
    ("legal", MEETING_TYPE_RISK),
    ("budget", MEETING_TYPE_RISK),
    # ── creative_production ──
    ("버추얼", MEETING_TYPE_CREATIVE),
    ("버츄얼", MEETING_TYPE_CREATIVE),
    ("버류얼", MEETING_TYPE_CREATIVE),
    ("vtuber", MEETING_TYPE_CREATIVE),
    ("v-tuber", MEETING_TYPE_CREATIVE),
    ("유튜버", MEETING_TYPE_CREATIVE),
    ("2d", MEETING_TYPE_CREATIVE),
    ("3d", MEETING_TYPE_CREATIVE),
    ("모델링", MEETING_TYPE_CREATIVE),
    ("캐릭터", MEETING_TYPE_CREATIVE),
    ("캐릭", MEETING_TYPE_CREATIVE),
    ("일러스트", MEETING_TYPE_CREATIVE),
    ("일러스트", MEETING_TYPE_CREATIVE),
    ("비주얼", MEETING_TYPE_CREATIVE),
    ("디자인", MEETING_TYPE_CREATIVE),
    ("디자이너", MEETING_TYPE_CREATIVE),
    ("ui", MEETING_TYPE_CREATIVE),
    ("ux", MEETING_TYPE_CREATIVE),
    ("컨셉", MEETING_TYPE_CREATIVE),
    ("콘셉", MEETING_TYPE_CREATIVE),
    ("스크립트", MEETING_TYPE_CREATIVE),
    ("대본", MEETING_TYPE_CREATIVE),
    ("스토리보드", MEETING_TYPE_CREATIVE),
    ("뮤직비디오", MEETING_TYPE_CREATIVE),
    ("뮤비", MEETING_TYPE_CREATIVE),
    ("음악", MEETING_TYPE_CREATIVE),
    ("사운드", MEETING_TYPE_CREATIVE),
    ("bgm", MEETING_TYPE_CREATIVE),
    ("보이스", MEETING_TYPE_CREATIVE),
    ("더빙", MEETING_TYPE_CREATIVE),
    ("영상", MEETING_TYPE_CREATIVE),
    ("편집", MEETING_TYPE_CREATIVE),
    ("애니메이션", MEETING_TYPE_CREATIVE),
    ("vfx", MEETING_TYPE_CREATIVE),
    ("character", MEETING_TYPE_CREATIVE),
    ("illustration", MEETING_TYPE_CREATIVE),
    ("visual", MEETING_TYPE_CREATIVE),
    ("concept art", MEETING_TYPE_CREATIVE),
    ("storyboard", MEETING_TYPE_CREATIVE),
    ("script", MEETING_TYPE_CREATIVE),
    ("animation", MEETING_TYPE_CREATIVE),
    ("music", MEETING_TYPE_CREATIVE),
    ("sound", MEETING_TYPE_CREATIVE),
    # ── technical_development ──
    ("코드", MEETING_TYPE_TECHNICAL),
    ("개발", MEETING_TYPE_TECHNICAL),
    ("아키텍처", MEETING_TYPE_TECHNICAL),
    ("아키텍쳐", MEETING_TYPE_TECHNICAL),
    ("인프라", MEETING_TYPE_TECHNICAL),
    ("서버", MEETING_TYPE_TECHNICAL),
    ("백엔드", MEETING_TYPE_TECHNICAL),
    ("프론트엔드", MEETING_TYPE_TECHNICAL),
    ("api", MEETING_TYPE_TECHNICAL),
    ("데이터베이스", MEETING_TYPE_TECHNICAL),
    ("db", MEETING_TYPE_TECHNICAL),
    ("데브옵스", MEETING_TYPE_TECHNICAL),
    ("ci/cd", MEETING_TYPE_TECHNICAL),
    ("배포", MEETING_TYPE_TECHNICAL),
    ("리팩토링", MEETING_TYPE_TECHNICAL),
    ("테스트", MEETING_TYPE_TECHNICAL),
    ("버그", MEETING_TYPE_TECHNICAL),
    ("툴", MEETING_TYPE_TECHNICAL),
    ("파이프라인", MEETING_TYPE_TECHNICAL),
    ("개발환경", MEETING_TYPE_TECHNICAL),
    ("코드리뷰", MEETING_TYPE_TECHNICAL),
    ("git", MEETING_TYPE_TECHNICAL),
    ("code", MEETING_TYPE_TECHNICAL),
    ("architecture", MEETING_TYPE_TECHNICAL),
    ("infrastructure", MEETING_TYPE_TECHNICAL),
    ("backend", MEETING_TYPE_TECHNICAL),
    ("frontend", MEETING_TYPE_TECHNICAL),
    ("database", MEETING_TYPE_TECHNICAL),
    ("devops", MEETING_TYPE_TECHNICAL),
    ("refactor", MEETING_TYPE_TECHNICAL),
    ("deploy", MEETING_TYPE_TECHNICAL),
    ("pipeline", MEETING_TYPE_TECHNICAL),
    # ── marketing_strategy ──
    ("마케팅", MEETING_TYPE_MARKETING),
    ("홍보", MEETING_TYPE_MARKETING),
    ("sns", MEETING_TYPE_MARKETING),
    ("트위터", MEETING_TYPE_MARKETING),
    ("인스타", MEETING_TYPE_MARKETING),
    ("티톡", MEETING_TYPE_MARKETING),
    ("유튜브", MEETING_TYPE_MARKETING),
    ("커뮤니티", MEETING_TYPE_MARKETING),
    ("팬", MEETING_TYPE_MARKETING),
    ("브랜드", MEETING_TYPE_MARKETING),
    ("pr", MEETING_TYPE_MARKETING),
    ("보도자료", MEETING_TYPE_MARKETING),
    ("캠페인", MEETING_TYPE_MARKETING),
    ("시장", MEETING_TYPE_MARKETING),
    ("경쟁사", MEETING_TYPE_MARKETING),
    ("트렌드", MEETING_TYPE_MARKETING),
    ("marketing", MEETING_TYPE_MARKETING),
    ("brand", MEETING_TYPE_MARKETING),
    ("community", MEETING_TYPE_MARKETING),
    ("campaign", MEETING_TYPE_MARKETING),
    ("trend", MEETING_TYPE_MARKETING),
    # ── project_review ──
    ("진행상황", MEETING_TYPE_REVIEW),
    ("상태보고", MEETING_TYPE_REVIEW),
    ("마일스톤", MEETING_TYPE_REVIEW),
    ("회고", MEETING_TYPE_REVIEW),
    ("레트로", MEETING_TYPE_REVIEW),
    ("현황", MEETING_TYPE_REVIEW),
    ("경과", MEETING_TYPE_REVIEW),
    ("중간점검", MEETING_TYPE_REVIEW),
    ("status", MEETING_TYPE_REVIEW),
    ("milestone", MEETING_TYPE_REVIEW),
    ("retrospective", MEETING_TYPE_REVIEW),
    ("progress", MEETING_TYPE_REVIEW),
)


# Urgency keywords mapped to priority levels.
# Ordered from highest priority — first match wins.
_URGENCY_RULES: tuple[tuple[str, str, float], ...] = (
    # (keyword, priority, confidence_modifier)
    # ── P0: blocking ──
    ("서버다운", PRIORITY_P0, 1.0),
    ("서버 다운", PRIORITY_P0, 1.0),
    ("시스템다운", PRIORITY_P0, 1.0),
    ("장애발생", PRIORITY_P0, 1.0),
    ("장애 발생", PRIORITY_P0, 1.0),
    ("긴급", PRIORITY_P0, 1.0),
    ("위급", PRIORITY_P0, 1.0),
    ("emergency", PRIORITY_P0, 1.0),
    ("incident", PRIORITY_P0, 1.0),
    ("outage", PRIORITY_P0, 1.0),
    ("down", PRIORITY_P0, 1.0),
    ("asap", PRIORITY_P0, 0.9),
    ("바로", PRIORITY_P0, 0.85),
    ("즉시", PRIORITY_P0, 0.85),
    # ── P1: high ──
    ("중요", PRIORITY_P1, 1.0),
    ("시급", PRIORITY_P1, 1.0),
    ("심각", PRIORITY_P1, 1.0),
    ("urgent", PRIORITY_P1, 1.0),
    ("critical", PRIORITY_P1, 1.0),
    ("high priority", PRIORITY_P1, 1.0),
    ("빨리", PRIORITY_P1, 0.9),
    ("가능한 빨리", PRIORITY_P1, 0.9),
    ("최우선", PRIORITY_P1, 0.95),
    # ── P3: low ──
    ("시간 있을 때", PRIORITY_P3, 1.0),
    ("시간날 때", PRIORITY_P3, 1.0),
    ("천천히", PRIORITY_P3, 1.0),
    ("여유 있을 때", PRIORITY_P3, 1.0),
    ("나중에", PRIORITY_P3, 0.9),
    ("when available", PRIORITY_P3, 1.0),
    ("low priority", PRIORITY_P3, 1.0),
    ("not urgent", PRIORITY_P3, 1.0),
)

# Team name patterns — detect team-level selection requests.
# Maps display-name fragments to the team leader role_id.
# Ordered by specificity; first match per team wins.
_TEAM_PATTERNS: tuple[tuple[str, str], ...] = (
    # Content team
    ("콘텐츠팀", "content-pd"),
    ("콘텐츠 팀", "content-pd"),
    ("content team", "content-pd"),
    ("콘텐pd", "content-pd"),
    # Art team
    ("아트팀", "art-director"),
    ("아트 팀", "art-director"),
    ("art team", "art-director"),
    ("디자인팀", "art-director"),
    ("디자인 팀", "art-director"),
    # Tech team
    ("기술팀", "tech-director"),
    ("기술 팀", "tech-director"),
    ("tech team", "tech-director"),
    ("개발팀", "tech-director"),
    ("개발 팀", "tech-director"),
    # Marketing team
    ("마케팅팀", "marketing-lead"),
    ("마케팅 팀", "marketing-lead"),
    ("marketing team", "marketing-lead"),
    ("홍보팀", "marketing-lead"),
    ("홍보 팀", "marketing-lead"),
    # Execution team
    ("실행팀", "execution-lead"),
    ("실행 팀", "execution-lead"),
    ("execution team", "execution-lead"),
    ("운영팀", "execution-lead"),
    ("운영 팀", "execution-lead"),
    # Risk/legal team (maps to execution-lead for now)
    ("법무팀", "execution-lead"),
    ("법무 팀", "execution-lead"),
    ("legal team", "execution-lead"),
)

# Role name patterns — detect specific participant role mentions.
# Maps display-name fragments to their role_id.
# Excludes team-leader role-IDs (those are in _TEAM_PATTERNS).
_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    # ── Content team roles ──
    ("scriptwriter", "scriptwriter"),
    ("스크립트라이터", "scriptwriter"),
    ("작가", "scriptwriter"),
    ("storyboard-artist", "storyboard-artist"),
    ("스토리보드", "storyboard-artist"),
    ("music-director", "music-director"),
    ("뮤직 디렉터", "music-director"),
    ("뮤직디렉터", "music-director"),
    ("voice-director", "voice-director"),
    ("보이스 디렉터", "voice-director"),
    ("보이스디렉터", "voice-director"),
    ("video-editor", "video-editor"),
    ("비디오 에디터", "video-editor"),
    ("영상 편집자", "video-editor"),
    # ── Art team roles ──
    ("concept-artist", "concept-artist"),
    ("컨셉 아티스트", "concept-artist"),
    ("컨셉아티스트", "concept-artist"),
    ("illustrator", "illustrator"),
    ("일러스트레이터", "illustrator"),
    ("ui-designer", "ui-designer"),
    ("ui 디자이너", "ui-designer"),
    ("ui디자이너", "ui-designer"),
    ("vfx-artist", "vfx-artist"),
    ("vfx 아티스트", "vfx-artist"),
    ("vfx아티스트", "vfx-artist"),
    ("animator", "animator"),
    ("애니메이터", "animator"),
    # ── Tech team roles ──
    ("game-engine-dev", "game-engine-dev"),
    ("게임엔진", "game-engine-dev"),
    ("backend-dev", "backend-dev"),
    ("백엔드 개발자", "backend-dev"),
    ("백엔드", "backend-dev"),
    ("frontend-dev", "frontend-dev"),
    ("프론트엔드", "frontend-dev"),
    ("devops-engineer", "devops-engineer"),
    ("데브옵스", "devops-engineer"),
    ("security-engineer", "security-engineer"),
    ("보안 엔지니어", "security-engineer"),
    ("data-engineer", "data-engineer"),
    ("데이터 엔지니어", "data-engineer"),
    ("데이터엔지니어", "data-engineer"),
    # ── Marketing team roles ──
    ("sns-strategist", "sns-strategist"),
    ("sns 전략가", "sns-strategist"),
    ("sns전략가", "sns-strategist"),
    ("pr-specialist", "pr-specialist"),
    ("pr 전문가", "pr-specialist"),
    ("pr전문가", "pr-specialist"),
    ("community-manager", "community-manager"),
    ("커뮤니티 매니저", "community-manager"),
    ("커뮤니티매니저", "community-manager"),
    ("market-analyst", "market-analyst"),
    ("시장 분석가", "market-analyst"),
    ("시장분석가", "market-analyst"),
    # ── Executor roles ──
    ("code-executor", "code-executor"),
    ("코드 실행자", "code-executor"),
    ("asset-executor", "asset-executor"),
    ("에셋 실행자", "asset-executor"),
    # ── Cross-team: individual leader mentions (when mentioned by role not team) ──
    ("content pd", "content-pd"),
    ("콘텐츠 pd", "content-pd"),
    ("아트 디렉터", "art-director"),
    ("아트디렉터", "art-director"),
    ("art director", "art-director"),
    ("테크 디렉터", "tech-director"),
    ("테크디렉터", "tech-director"),
    ("tech director", "tech-director"),
    ("마케팅 리드", "marketing-lead"),
    ("마케팅리드", "marketing-lead"),
    ("marketing lead", "marketing-lead"),
    ("실행 리드", "execution-lead"),
    ("실행리드", "execution-lead"),
    ("execution lead", "execution-lead"),
)


# ── Public API ────────────────────────────────────────────────────────────


def parse_meeting_intent(
    cleaned_text: str,
    *,
    default_meeting_type: str = MEETING_TYPE_PLANNING,
    force_meeting: bool = False,
) -> MeetingIntent | NoMeetingIntent:
    """Parse cleaned message text into a structured meeting intent.

    Receives the output of the Discord mention extractor (Sub-AC 2a)
    and determines whether the message contains a meeting request.
    If yes, extracts the meeting type, topic, suggested participants,
    and urgency level.

    The parser is deterministic and does not require any LLM call.
    It uses keyword-based heuristics tuned for Korean and English
    natural-language inputs in the virtual entertainment company domain.

    Args:
        cleaned_text: Cleaned message text with bot mention already
                      removed (output from extract_mention_command).
        default_meeting_type: Fallback meeting type when no specific
                              domain keywords match.

    Returns:
        * ``MeetingIntent`` when a meeting request is detected.
        * ``NoMeetingIntent`` when the message is not a meeting request.

    Detection logic:
        1. Scan for meeting-initiation keywords (회의, 미팅, 논의, etc.)
        2. Classify meeting type from domain keywords
        3. Detect urgency level from priority signals
        4. Extract suggested participants from role/team mentions
        5. Extract core topic by removing meeting boilerplate

    Examples:
        >>> result = parse_meeting_intent(
        ...     "뮤직비디오 오프닝 아이디어 회의해줘"
        ... )
        >>> assert result.is_meeting  # type: ignore[union-attr]
        >>> assert result.meeting_type == "creative_production"  # type: ignore[union-attr]
        >>> assert "뮤직비디오" in result.topic  # type: ignore[union-attr]

        >>> result = parse_meeting_intent("오늘 점심 뭐 먹지?")
        >>> assert not result.is_meeting  # type: ignore[union-attr]
    """
    if not cleaned_text or not cleaned_text.strip():
        return NoMeetingIntent(
            is_meeting=False,
            reason="empty_input",
        )

    text = cleaned_text.strip()
    text_lower = text.lower()

    # ── Step 1: detect meeting intent ─────────────────────────────────
    meeting_keyword = _find_meeting_keyword(text_lower)
    if meeting_keyword is None and not force_meeting:
        return NoMeetingIntent(
            is_meeting=False,
            reason="no_meeting_keyword",
        )
    if meeting_keyword is None:
        meeting_keyword = ""

    # ── Step 2: classify meeting type ─────────────────────────────────
    meeting_type, type_confidence, type_reasoning = _classify_type(
        text_lower, default_meeting_type
    )

    # ── Step 3: detect urgency ────────────────────────────────────────
    urgency, urgency_confidence = _detect_urgency(text_lower)

    # ── Step 4: extract participants (teams + roles separately) ──────
    teams = _extract_teams(text_lower)
    suggested_roles = _extract_suggested_roles(text_lower)
    participants = _extract_participants(text_lower)  # backward-compatible union

    # ── Step 5: extract topic ─────────────────────────────────────────
    topic = _extract_topic(text, meeting_keyword)

    # ── Step 6: build reasoning ───────────────────────────────────────
    reasoning_parts: list[str] = []
    if type_reasoning:
        reasoning_parts.append(type_reasoning)
    if urgency != PRIORITY_P2:
        reasoning_parts.append(
            f"urgency={urgency} from priority keywords"
        )
    if teams:
        reasoning_parts.append(
            f"teams={','.join(teams)} from team mentions"
        )
    if suggested_roles:
        reasoning_parts.append(
            f"suggested_roles={','.join(suggested_roles)} from role mentions"
        )
    elif participants:
        reasoning_parts.append(
            f"participants={','.join(participants)} from mentions"
        )

    reasoning = "; ".join(reasoning_parts) if reasoning_parts else (
        "meeting keyword detected, default classification"
    )

    # ── Compute overall confidence ────────────────────────────────────
    confidence = round(
        type_confidence * urgency_confidence
        * (0.95 if participants else 1.0),
        2,
    )

    return MeetingIntent(
        meeting_type=meeting_type,
        topic=topic,
        participants=participants,
        teams=teams,
        suggested_roles=suggested_roles,
        urgency=urgency,
        confidence=confidence,
        reasoning=reasoning,
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _find_meeting_keyword(text_lower: str) -> str | None:
    """Return the first meeting keyword found, or None.

    The returned string is the matched keyword (Korean or English),
    used later for topic extraction to know what to strip.
    """
    for keyword, _kind in _MEETING_KEYWORDS:
        if keyword in text_lower:
            return keyword
    return None


def _classify_type(
    text_lower: str,
    default: str,
) -> tuple[str, float, str]:
    """Classify meeting type via keyword matching.

    Returns (meeting_type, confidence, reasoning).
    First match wins — type rules are ordered by specificity.
    """
    matches: dict[str, int] = {}

    for keyword, mtype in _TYPE_RULES:
        if keyword in text_lower:
            matches[mtype] = matches.get(mtype, 0) + 1

    if not matches:
        return default, 0.6, "no domain keywords — default to general_planning"

    # Pick the type with the most keyword matches
    best_type = max(matches, key=lambda k: matches[k])
    match_count = matches[best_type]
    total_matches = sum(matches.values())

    # Confidence: ratio of best-type keywords to total matching keywords
    # Single match = 0.85, multiple strong matches = up to 0.95
    confidence = round(0.7 + (0.25 * match_count / max(total_matches, 1)), 2)
    reasoning = (
        f"matched {match_count}/{total_matches} keywords for "
        f"'{best_type}'"
    )

    return best_type, confidence, reasoning


def _detect_urgency(text_lower: str) -> tuple[str, float]:
    """Detect urgency level from priority keywords.

    Returns (priority, confidence).
    Default is P2 with confidence 1.0 when no urgency signals.
    Upgrade signals (P0/P1) and downgrade signals (P3) are tracked
    independently.  When both are present, the upgrade wins.
    """
    upgrade_priority: str | None = None
    upgrade_confidence: float = 0.0
    downgrade_detected: bool = False

    for keyword, priority, conf in _URGENCY_RULES:
        if keyword in text_lower:
            rank = _priority_rank(priority)
            if rank < 2:  # P0 or P1 — upgrade from default
                if upgrade_priority is None or rank < _priority_rank(upgrade_priority):
                    upgrade_priority = priority
                    upgrade_confidence = conf
                elif rank == _priority_rank(upgrade_priority or PRIORITY_P2):
                    upgrade_confidence = max(upgrade_confidence, conf)
            elif rank > 2:  # P3 — downgrade from default
                downgrade_detected = True

    if upgrade_priority is not None:
        return upgrade_priority, upgrade_confidence
    if downgrade_detected:
        return PRIORITY_P3, 1.0
    return PRIORITY_P2, 1.0


def _priority_rank(priority: str) -> int:
    """Return numeric rank for priority comparison (lower = higher priority)."""
    return {"p0": 0, "p1": 1, "p2": 2, "p3": 3}.get(priority, 99)


def _extract_participants(text_lower: str) -> tuple[str, ...]:
    """Extract suggested participant role IDs from the text.

    Matches role/team names mentioned in the message and returns
    deduplicated, sorted role IDs.  This is the backward-compatible
    union of ``_extract_teams`` and ``_extract_suggested_roles``.
    """
    found: set[str] = set()
    for pattern, role_id in _TEAM_PATTERNS:
        if pattern in text_lower:
            found.add(role_id)
    for pattern, role_id in _ROLE_PATTERNS:
        if pattern in text_lower:
            found.add(role_id)
    return tuple(sorted(found))


def _extract_teams(text_lower: str) -> tuple[str, ...]:
    """Extract team-level selection from the text.

    Matches team name patterns (e.g. ``'아트팀'``, ``'기술팀'``)
    and returns deduplicated, sorted team-leader role-IDs.

    These are the teams the user explicitly wants to convene.
    """
    found: set[str] = set()
    for pattern, role_id in _TEAM_PATTERNS:
        if pattern in text_lower:
            found.add(role_id)
    return tuple(sorted(found))


def _extract_suggested_roles(text_lower: str) -> tuple[str, ...]:
    """Extract specific role-level participant mentions from the text.

    Matches individual role patterns (e.g. ``'concept-artist'``,
    ``'backend-dev'``) and returns deduplicated, sorted role-IDs.

    These are specific roles the user mentioned — they become
    candidates for inclusion if the routing system determines
    relevance.
    """
    found: set[str] = set()
    for pattern, role_id in _ROLE_PATTERNS:
        if pattern in text_lower:
            found.add(role_id)
    return tuple(sorted(found))


def _extract_topic(text: str, meeting_keyword: str) -> str:
    """Extract the core meeting topic by stripping boilerplate.

    Removes the matched meeting keyword, common Korean sentence-ending
    forms (해줘, 해주세요, 하자, etc.), and leading/trailing whitespace.

    If the result is empty, returns the original topic description
    derived from the full text.
    """
    # Remove the meeting keyword and common endings
    topic = text

    # Strip the meeting keyword when natural text contains one.  Slash-command
    # topics can be forced as meetings without adding boilerplate keywords.
    if meeting_keyword:
        topic = re.sub(re.escape(meeting_keyword), "", topic, flags=re.IGNORECASE)

    # Strip common Korean request suffixes
    _REQUEST_SUFFIXES = (
        r"해줘\b",
        r"해주세요\b",
        r"해 줘\b",
        r"해 주세요\b",
        r"하자\b",
        r"합시다\b",
        r"부탁해요\b",
        r"부탁드려요\b",
        r"부탁드립니다\b",
        r"해봐\b",
        r"해봐요\b",
        r"해보자\b",
        r"검토해줘\b",
        r"검토해 주세요\b",
        r"논의하자\b",
        r"논의합시다\b",
        r"상의하자\b",
        r"상의해요\b",
        r"please\b",
        r"plz\b",
        r"pls\b",
    )
    for suffix in _REQUEST_SUFFIXES:
        topic = re.sub(suffix, "", topic, flags=re.IGNORECASE)

    # Strip common meeting-form words that surround the topic
    _MEETING_FORM_WORDS = (
        r"\b(?:에 대해|에 대해서|에 대한|에 관해|에 관한|에 관해서)\b",
        r"\babout\b",
        r"\bregarding\b",
        r"\b(?:관련|관한)\b",
        r"\b(?:대한|대해)\b",
    )
    for pattern in _MEETING_FORM_WORDS:
        topic = re.sub(pattern, "", topic, flags=re.IGNORECASE)

    # Strip leading/trailing punctuation common in requests
    topic = re.sub(r"^[,.\s~!?]+", "", topic)
    topic = re.sub(r"[,.\s~!?]+$", "", topic)

    # Collapse whitespace
    topic = re.sub(r"\s{2,}", " ", topic).strip()

    # If stripping removed everything, return a best-effort from the original
    if not topic:
        # Remove just the meeting keyword and common endings from original
        topic = re.sub(
            r"\b(?:회의|미팅|논의|검토|상의|협의|토론|논의하자|검토해줘)\b",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if not topic:
            topic = text.strip()

    return topic
