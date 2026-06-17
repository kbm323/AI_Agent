"""Trigger router for the AI_Agent multi-agent meeting system.

Sub-AC 2.3: Maps parsed command intent to the appropriate meeting workflow
entry point (initiate meeting, join meeting, cancel meeting, status query)
with validation.  Independently testable with parsed intent/parameter inputs.

Pipeline position:
    meeting_intent_parser (Sub-AC 2b) -> MeetingIntent
        -> trigger_router (Sub-AC 2.3) -> TriggerRoute
            -> [initiate] meeting_creation_dispatcher (Sub-AC 2c)
            -> [join]     (join workflow — future)
            -> [cancel]   (cancel workflow — future)
            -> [status]   (status workflow — future)

Design
------
The trigger router is a **pure-in-memory decision function** — no filesystem
I/O, no CLI calls, no LLM invocations.  It receives a parsed intent (or raw
parameter dict) and determines:

1. **Action type** — what the user wants to do: initiate a meeting, join one,
   cancel one, or query status.
2. **Parameter validation** — checks that the required parameters for the
   selected action are present and well-formed.
3. **Route production** — returns a ``TriggerRoute`` that the Coordinator
   uses to dispatch to the correct workflow entry point.

Action detection rules (deterministic, keyword-based):
- **initiate**: meeting_type is one of the six recognised types AND the
  intent is a meeting request (is_meeting=True).  This is the default when
  the parsed intent is a valid meeting.
- **join**: topic/agenda contains join keywords and a meeting_id is present.
- **cancel**: topic/agenda contains cancel keywords and a meeting_id is present.
- **status**: topic/agenda contains status/query keywords, optional meeting_id.

The module is fully testable with mock intents and parameter dicts — no
integration dependencies required.

Data types
----------
- ``TriggerAction`` — enum of the four workflow entry points.
- ``TriggerRoute`` — validated route result (action + validated params).
- ``TriggerRoutingResult`` — union of success (TriggerRoute) or failure
  (error message), following the ``DispatchResult`` pattern from
  ``meeting_creation_dispatcher.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Optional, Union

# ── Action type enum ────────────────────────────────────────────────────────

@unique
class TriggerAction(StrEnum):
    """The four meeting workflow entry points.

    Values are identical to member names — the enum serves as both
    a symbolic constant set and a string-comparable value.
    """

    INITIATE = "initiate"
    """Create a new meeting and begin the multi-agent pipeline."""

    JOIN = "join"
    """Join an existing meeting as an additional participant."""

    CANCEL = "cancel"
    """Cancel a running meeting and preserve outputs to disk."""

    STATUS = "status"
    """Query the status of one or all meetings."""


# ── Valid action set for fast membership checks ─────────────────────────────

_VALID_ACTIONS: frozenset[str] = frozenset(a.value for a in TriggerAction)


# ── Action detection keyword registries ─────────────────────────────────────

# Join keywords — signal the user wants to join an existing meeting.
_JOIN_KEYWORDS: tuple[str, ...] = (
    "참여", "참가", "join", "들어가", "참석",
    "합류", "enter", "attend",
)

# Cancel keywords — signal the user wants to cancel a meeting.
_CANCEL_KEYWORDS: tuple[str, ...] = (
    "취소", "cancel", "중단", "stop", "멈춰",
    "그만", "abort", "철회", "폐기", "종료",
)

# Status keywords — signal the user wants to query meeting status.
_STATUS_KEYWORDS: tuple[str, ...] = (
    "상태", "status", "현황", "진행", "어디까지",
    "어떻게", "보고", "report", "progress", "list",
    "목록", "조회", "query", "check", "확인",
)

# Initiate keywords — signal the user wants to start a NEW meeting.
# These are broader; initiate is also the fallback when none of the
# other action keywords match but the intent IS a meeting.
_INITIATE_KEYWORDS: tuple[str, ...] = (
    "시작", "start", "개최", "열어", "생성",
    "create", "new", "새로", "소집", "소환",
    "launch", "begin", "kickoff", "착수",
)


# ── Recognised meeting types (mirrors meeting_intent_parser constants) ──────

_VALID_MEETING_TYPES: frozenset[str] = frozenset({
    "creative_production",
    "technical_development",
    "marketing_strategy",
    "risk_assessment",
    "general_planning",
    "project_review",
})


# ── Valid priorities ────────────────────────────────────────────────────────

_VALID_PRIORITIES: frozenset[str] = frozenset({"p0", "p1", "p2", "p3"})


# ── Valid meeting_id pattern ────────────────────────────────────────────────

import re as _re

# meeting_id format: meeting_YYYYMMDD_<12-hex-chars>
# Unanchored pattern for extraction from text
_MEETING_ID_PATTERN = r"meeting_\d{8}_[0-9a-f]{12}"
_MEETING_ID_RE = _re.compile(_MEETING_ID_PATTERN)
_MEETING_ID_FULL_RE = _re.compile(r"^" + _MEETING_ID_PATTERN + r"$")


# ── Parsed intent / parameter input ─────────────────────────────────────────


@dataclass(frozen=True)
class ParsedTriggerInput:
    """Normalised input to the trigger router.

    Accepts either a ``MeetingIntent`` from the intent parser (Sub-AC 2b)
    or a raw parameter dict.  The router uses this as its sole input
    contract, making it independently testable.

    All string fields are stripped.  At minimum, ``topic`` must be
    non-empty for routing to succeed.

    Attributes:
        topic: The meeting topic / agenda text (may include action signals).
        meeting_type: Classified meeting type if available.
        priority: Priority level if known.
        participants: Suggested role/team identifiers.
        meeting_id: Explicit meeting ID from user input (for join/cancel/status).
        is_meeting: Whether the intent parser detected a meeting request.
        raw_params: Additional raw parameters from the command parser.
    """

    topic: str
    meeting_type: str = ""
    priority: str = "p2"
    participants: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    suggested_roles: tuple[str, ...] = ()
    meeting_id: str = ""
    is_meeting: bool = True
    raw_params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure tuples
        for attr in ("participants", "teams", "suggested_roles"):
            val = getattr(self, attr)
            if not isinstance(val, tuple):
                object.__setattr__(self, attr, tuple(val))


# ── Route result dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class TriggerRoute:
    """A validated route to a specific workflow entry point.

    Produced by ``route_trigger()`` on success.  The Coordinator
    inspects ``action`` to determine which workflow to invoke and
    uses ``params`` as the validated input.

    Attributes:
        action: The workflow entry point (initiate, join, cancel, status).
        topic: Core meeting topic / agenda (may be empty for status-queries).
        meeting_type: Classified meeting type (initiate only).
        priority: Priority level (normalised lowercase).
        meeting_id: Explicit meeting ID (join/cancel/status).
        participants: Suggested participants (initiate only).
        teams: Team-level selection (initiate only).
        suggested_roles: Role-level selection (initiate only).
        confidence: Routing confidence 0.0–1.0.
        reasoning: Human-readable explanation of the routing decision.
        source: How the action was determined (keyword, explicit, default).
    """

    action: TriggerAction
    topic: str = ""
    meeting_type: str = ""
    priority: str = "p2"
    meeting_id: str = ""
    participants: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    suggested_roles: tuple[str, ...] = ()
    confidence: float = 1.0
    reasoning: str = ""
    source: str = "keyword"

    def to_dict(self) -> dict:
        """Serialize to a plain dict for logging/storage."""
        return {
            "action": self.action.value,
            "topic": self.topic,
            "meeting_type": self.meeting_type,
            "priority": self.priority,
            "meeting_id": self.meeting_id,
            "participants": list(self.participants),
            "teams": list(self.teams),
            "suggested_roles": list(self.suggested_roles),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "source": self.source,
        }


@dataclass(frozen=True)
class TriggerRoutingResult:
    """Immutable result from ``route_trigger()``.

    When ``success`` is True, ``route`` carries the validated
    ``TriggerRoute`` and ``error`` is ``None``.  When ``success``
    is False, ``route`` is ``None`` and ``error`` explains why
    routing was rejected.

    This mirrors the ``DispatchResult`` pattern from
    ``meeting_creation_dispatcher.py`` — every routing decision,
    whether successful or not, returns a structured, inspectable result.
    """

    success: bool
    """True when the trigger was successfully routed."""

    route: Optional[TriggerRoute] = None
    """The validated route (success only)."""

    error: Optional[str] = None
    """Human-readable rejection reason (failure only)."""


# ── Public API ──────────────────────────────────────────────────────────────


def route_trigger(
    input: ParsedTriggerInput,
    *,
    default_action: TriggerAction = TriggerAction.INITIATE,
) -> TriggerRoutingResult:
    """Map parsed command intent to the appropriate workflow entry point.

    This is the **single entry point** for Sub-AC 2.3.  It receives a
    ``ParsedTriggerInput`` (from the intent parser or command parser),
    determines the action type, validates the parameters, and returns a
    ``TriggerRoutingResult``.

    **Action detection** (first-match wins):
    1. If ``input.meeting_id`` is provided AND cancel keywords detected → cancel.
    2. If ``input.meeting_id`` is provided AND join keywords detected → join.
    3. If status keywords detected → status.
    4. If cancel keywords detected (with or without meeting_id) → cancel.
    5. If join keywords detected → join.
    6. If initiate keywords detected → initiate.
    7. If ``input.is_meeting`` is True → initiate (default).
    8. Otherwise → failure (not a meeting request).

    **Parameter validation** (per action):
    - **initiate**: topic must be non-empty, meeting_type must be valid
      (if present), priority must be p0–p3.
    - **join**: meeting_id must be present and valid format.
    - **cancel**: meeting_id must be present and valid format (when known).
    - **status**: meeting_id is optional; if present must be valid format.

    Args:
        input: Parsed trigger input (from intent parser or command parser).
        default_action: Fallback action when no keywords match but the
                        input is a meeting request (default: initiate).

    Returns:
        ``TriggerRoutingResult`` — inspect ``.success`` to branch; access
        ``.route`` when True, ``.error`` when False.

    Examples:
        >>> result = route_trigger(ParsedTriggerInput(
        ...     topic="뮤직비디오 오프닝 아이디어 회의",
        ...     meeting_type="creative_production",
        ... ))
        >>> result.success
        True
        >>> result.route.action == TriggerAction.INITIATE
        True

        >>> result = route_trigger(ParsedTriggerInput(
        ...     topic="회의 취소해줘",
        ...     meeting_id="meeting_20260610_a1b2c3d4e5f6",
        ... ))
        >>> result.success
        True
        >>> result.route.action == TriggerAction.CANCEL
        True

        >>> result = route_trigger(ParsedTriggerInput(
        ...     topic="meeting_20260610_a1b2c3d4e5f6 상태 알려줘",
        ... ))
        >>> result.success
        True
        >>> result.route.action == TriggerAction.STATUS
        True
    """
    # ── Step 1: Input validation ─────────────────────────────────────
    if not input.topic or not input.topic.strip():
        # An empty topic can still be valid for status queries
        # if meeting_id is provided.
        if input.meeting_id and _is_valid_meeting_id(input.meeting_id):
            return _build_success(
                action=TriggerAction.STATUS,
                input=input,
                effective_meeting_id=input.meeting_id,
                reasoning="Empty topic but valid meeting_id — defaulting to status query",
                source="default",
                confidence=0.8,
            )
        return TriggerRoutingResult(
            success=False,
            error="topic must not be empty (and no valid meeting_id provided)",
        )

    topic = input.topic.strip()
    topic_lower = topic.lower()

    # Extract any meeting_id embedded in the topic text
    extracted_meeting_id = _extract_meeting_id(topic)
    effective_meeting_id = input.meeting_id or extracted_meeting_id

    # ── Step 2: Action detection (first-match wins) ──────────────────
    action: TriggerAction | None = None
    source: str = "keyword"
    reasoning: str = ""
    confidence: float = 1.0

    # 1. Explicit meeting_id + cancel keywords → cancel
    if effective_meeting_id and _has_keyword(topic_lower, _CANCEL_KEYWORDS):
        action = TriggerAction.CANCEL
        source = "keyword"
        reasoning = "cancel keyword detected with meeting_id"

    # 2. Explicit meeting_id + join keywords → join
    elif effective_meeting_id and _has_keyword(topic_lower, _JOIN_KEYWORDS):
        action = TriggerAction.JOIN
        source = "keyword"
        reasoning = "join keyword detected with meeting_id"

    # 3. Status keywords → status
    elif _has_keyword(topic_lower, _STATUS_KEYWORDS):
        action = TriggerAction.STATUS
        source = "keyword"
        reasoning = "status keyword detected"
        # Status queries can be confident even without meeting_id
        confidence = 0.9 if effective_meeting_id else 0.85

    # 4. Cancel keywords (even without explicit meeting_id) → cancel
    elif _has_keyword(topic_lower, _CANCEL_KEYWORDS):
        action = TriggerAction.CANCEL
        source = "keyword"
        reasoning = "cancel keyword detected"
        # Lower confidence without explicit meeting_id
        confidence = 0.9 if effective_meeting_id else 0.7

    # 5. Join keywords → join
    elif _has_keyword(topic_lower, _JOIN_KEYWORDS):
        action = TriggerAction.JOIN
        source = "keyword"
        reasoning = "join keyword detected"
        confidence = 0.85 if effective_meeting_id else 0.65

    # 6. Initiate keywords → initiate
    elif _has_keyword(topic_lower, _INITIATE_KEYWORDS):
        action = TriggerAction.INITIATE
        source = "keyword"
        reasoning = "initiate keyword detected"
        confidence = 0.95

    # 7. Fallback: if is_meeting → initiate
    elif input.is_meeting:
        action = default_action if default_action == TriggerAction.INITIATE else TriggerAction.INITIATE
        source = "default"
        reasoning = "meeting intent detected — defaulting to initiate"
        confidence = 0.85

    # 8. Not a meeting request
    else:
        # Check if this could be a status query with meeting_id in topic
        if effective_meeting_id:
            action = TriggerAction.STATUS
            source = "default"
            reasoning = "meeting_id found but no meeting keywords — defaulting to status"
            confidence = 0.7
        else:
            return TriggerRoutingResult(
                success=False,
                error="not a meeting request — no meeting keywords, no meeting_id",
            )

    # At this point action is guaranteed to be set
    assert action is not None, "action must be set by now"

    # ── Step 3: Parameter validation per action ──────────────────────
    validation_error = _validate_action_params(
        action=action,
        input=input,
        effective_meeting_id=effective_meeting_id,
    )
    if validation_error is not None:
        return TriggerRoutingResult(success=False, error=validation_error)

    # ── Step 4: Build and return route ───────────────────────────────
    return _build_success(
        action=action,
        input=input,
        effective_meeting_id=effective_meeting_id,
        reasoning=reasoning,
        source=source,
        confidence=confidence,
    )


# ── Validation helpers ──────────────────────────────────────────────────────


def _validate_action_params(
    action: TriggerAction,
    input: ParsedTriggerInput,
    effective_meeting_id: str,
) -> str | None:
    """Validate that the required parameters for *action* are present.

    Returns:
        None if valid, or a human-readable error string.
    """
    # ── Initiate ──
    if action == TriggerAction.INITIATE:
        if not input.topic or not input.topic.strip():
            return "initiate action requires a non-empty topic"
        if input.meeting_type and input.meeting_type not in _VALID_MEETING_TYPES:
            return (
                f"invalid meeting_type '{input.meeting_type}' — "
                f"must be one of: {', '.join(sorted(_VALID_MEETING_TYPES))}"
            )
        if input.priority.lower() not in _VALID_PRIORITIES:
            return (
                f"invalid priority '{input.priority}' — "
                f"must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
            )
        return None

    # ── Join ──
    if action == TriggerAction.JOIN:
        if effective_meeting_id and not _is_valid_meeting_id(effective_meeting_id):
            return (
                f"invalid meeting_id format '{effective_meeting_id}' — "
                f"expected format: meeting_YYYYMMDD_<12-hex-chars>"
            )
        # Join without meeting_id is allowed (lower confidence) — the
        # Coordinator will prompt the user to specify which meeting.
        return None

    # ── Cancel ──
    if action == TriggerAction.CANCEL:
        if effective_meeting_id and not _is_valid_meeting_id(effective_meeting_id):
            return (
                f"invalid meeting_id format '{effective_meeting_id}' — "
                f"expected format: meeting_YYYYMMDD_<12-hex-chars>"
            )
        # Cancel without meeting_id is allowed (lower confidence) — the
        # Coordinator will prompt the user to specify which meeting.
        return None

    # ── Status ──
    if action == TriggerAction.STATUS:
        # meeting_id is optional for status queries
        if effective_meeting_id and not _is_valid_meeting_id(effective_meeting_id):
            return (
                f"invalid meeting_id format '{effective_meeting_id}' — "
                f"expected format: meeting_YYYYMMDD_<12-hex-chars>"
            )
        return None

    return f"unknown action '{action}'"


# ── Internal helpers ────────────────────────────────────────────────────────


def _has_keyword(text_lower: str, keywords: tuple[str, ...]) -> bool:
    """Check if any keyword appears as a word in the lowercased text.

    Uses word-boundary matching so '상태' matches '회의 상태' but not
    '상태보고서' (since 상태 is already a complete word in Korean).
    For Korean text, does simple substring match because word boundaries
    are less reliable with Hangul.
    """
    for kw in keywords:
        if kw in text_lower:
            return True
    return False


def _extract_meeting_id(text: str) -> str:
    """Extract a meeting_id from text if present.

    Searches for the pattern ``meeting_YYYYMMDD_<12-hex-chars>``.

    Returns:
        The meeting_id string, or empty string if not found.
    """
    match = _MEETING_ID_RE.search(text)
    return match.group(0) if match else ""


def _is_valid_meeting_id(meeting_id: str) -> bool:
    """Check whether *meeting_id* matches the expected format."""
    return bool(_MEETING_ID_FULL_RE.fullmatch(meeting_id))


def _build_success(
    action: TriggerAction,
    input: ParsedTriggerInput,
    effective_meeting_id: str = "",
    reasoning: str = "",
    source: str = "keyword",
    confidence: float = 1.0,
) -> TriggerRoutingResult:
    """Build a successful TriggerRoutingResult."""
    return TriggerRoutingResult(
        success=True,
        route=TriggerRoute(
            action=action,
            topic=input.topic.strip() if input.topic else "",
            meeting_type=input.meeting_type,
            priority=input.priority.lower(),
            meeting_id=effective_meeting_id,
            participants=input.participants,
            teams=input.teams,
            suggested_roles=input.suggested_roles,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            source=source,
        ),
    )


# ── Convenience: build ParsedTriggerInput from MeetingIntent ────────────────


def input_from_intent(
    intent,  # MeetingIntent from meeting_intent_parser
    *,
    meeting_id: str = "",
    raw_params: dict[str, str] | None = None,
) -> ParsedTriggerInput:
    """Build a ``ParsedTriggerInput`` from a ``MeetingIntent``.

    This is a convenience bridge between Sub-AC 2b (intent parser) and
    Sub-AC 2.3 (trigger router).  It maps the ``MeetingIntent`` fields
    to the ``ParsedTriggerInput`` contract.

    Args:
        intent: A ``MeetingIntent`` from ``parse_meeting_intent()``.
        meeting_id: Optional explicit meeting ID from the user.
        raw_params: Optional raw parameter dict from the command parser.

    Returns:
        A ``ParsedTriggerInput`` ready for ``route_trigger()``.
    """
    return ParsedTriggerInput(
        topic=getattr(intent, "topic", ""),
        meeting_type=getattr(intent, "meeting_type", ""),
        priority=getattr(intent, "urgency", "p2"),
        participants=getattr(intent, "participants", ()),
        teams=getattr(intent, "teams", ()),
        suggested_roles=getattr(intent, "suggested_roles", ()),
        meeting_id=meeting_id,
        is_meeting=getattr(intent, "is_meeting", True),
        raw_params=raw_params or {},
    )


# ── Exports ─────────────────────────────────────────────────────────────────

__all__ = [
    "TriggerAction",
    "TriggerRoute",
    "TriggerRoutingResult",
    "ParsedTriggerInput",
    "route_trigger",
    "input_from_intent",
]
