"""Meeting creation dispatcher for the AI_Agent multi-agent meeting system.

Sub-AC 2c: Receives structured meeting intent, validates required fields,
and calls the meeting orchestrator with a normalized creation request.
Testable with mock orchestrator and valid/invalid intent payloads.

Pipeline position:
    meeting_intent_parser (Sub-AC 2b) -> MeetingIntent
        -> meeting_creation_dispatcher (Sub-AC 2c) -> MeetingCommandRequest
            -> create_meeting (Sub-AC 1c) -> MeetingContext

Design
------
The dispatcher is a thin validation-and-normalization layer.  It does
*not* create meetings itself — it delegates to an injectable orchestrator
callable (``create_meeting`` by default).  The injectable design makes
the module fully testable with a mock orchestrator, as required by the
acceptance criterion.

Every validation failure produces a ``DispatchResult`` with
``success=False`` and a descriptive ``error`` string — no silent
exceptions.  The caller always gets a structured result it can branch on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from src.meeting_intent_parser import (
    MEETING_TYPE_CREATIVE,
    MEETING_TYPE_MARKETING,
    MEETING_TYPE_PLANNING,
    MEETING_TYPE_REVIEW,
    MEETING_TYPE_RISK,
    MEETING_TYPE_TECHNICAL,
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    MeetingIntent,
)
from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    create_meeting as _default_create_meeting,
)

# ── Recognised meeting-type constants (set for fast validation) ────────────

_VALID_MEETING_TYPES: frozenset[str] = frozenset(
    {
        MEETING_TYPE_CREATIVE,
        MEETING_TYPE_TECHNICAL,
        MEETING_TYPE_MARKETING,
        MEETING_TYPE_RISK,
        MEETING_TYPE_PLANNING,
        MEETING_TYPE_REVIEW,
    }
)

_VALID_PRIORITIES: frozenset[str] = frozenset(
    {PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3}
)


# ── Orchestrator protocol (for dependency injection) ──────────────────────


class OrchestratorCallable(Protocol):
    """Protocol for the meeting orchestrator callable.

    Concrete implementations include ``create_meeting`` (production)
    and any mock / spy for testing.  The protocol ensures that the
    dispatcher can work with either without a hard dependency.

    The callable receives a ``MeetingCommandRequest`` and an optional
    ``MeetingConfig``, and returns a ``MeetingContext``.
    """

    def __call__(
        self,
        request: MeetingCommandRequest,
        *,
        meetings_root: Optional[str] = None,
        config: Optional[MeetingConfig] = None,
    ) -> MeetingContext: ...


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchResult:
    """Immutable result from ``dispatch_meeting()``.

    When ``success`` is True, ``context`` carries the
    ``MeetingContext`` returned by the orchestrator and ``error`` is
    ``None``.  When ``success`` is False, ``context`` is ``None`` and
    ``error`` explains why dispatch was rejected.

    This mirrors the ``TransitionResult`` pattern from the transition
    engine — every dispatch, whether successful or not, returns a
    structured, inspectable result.
    """

    success: bool
    """True when the meeting was successfully dispatched and created."""

    context: Optional[MeetingContext] = None
    """The ``MeetingContext`` returned by the orchestrator (success only)."""

    error: Optional[str] = None
    """Human-readable rejection reason (failure only)."""

    intent: Optional[MeetingIntent] = None
    """The original validated intent that was dispatched."""


# ── Public API ────────────────────────────────────────────────────────────


def dispatch_meeting(
    intent: MeetingIntent,
    *,
    user_id: str,
    channel_id: str,
    thread_id: str = "",
    guild_id: str = "",
    meetings_root: Optional[str] = None,
    config: Optional[MeetingConfig] = None,
    orchestrator: Optional[OrchestratorCallable] = None,
) -> DispatchResult:
    """Validate a ``MeetingIntent`` and dispatch to the meeting orchestrator.

    Steps
    -----
    1. **Validate required fields** — topic must be non-empty, meeting_type
       must be one of the six recognised types, priority must be p0–p3.
    2. **Normalise the intent** into a ``MeetingCommandRequest`` — maps
       intent fields to the request contract expected by ``create_meeting``.
    3. **Call the orchestrator** — delegates to ``create_meeting`` by
       default, or the injectable ``orchestrator`` callable in tests.
    4. **Wrap the result** — returns a ``DispatchResult`` with the
       ``MeetingContext`` on success, or a descriptive error on failure.

    Args:
        intent: Structured meeting intent from ``parse_meeting_intent()``.
        user_id: Discord user ID who initiated the meeting.
        channel_id: Discord channel ID where the command was issued.
        thread_id: Optional Discord thread ID (created by Coordinator).
        guild_id: Optional Discord guild/server ID.
        meetings_root: Optional root directory for meeting storage.
        config: Optional ``MeetingConfig`` overrides.
        orchestrator: Injectable orchestrator callable (default:
                      ``create_meeting``).  Use a mock/spy for testing.

    Returns:
        A ``DispatchResult`` — inspect ``.success`` to branch; access
        ``.context`` when True, ``.error`` when False.

    Examples:
        Valid intent — meeting created:

        >>> from src.meeting_intent_parser import MeetingIntent
        >>> intent = MeetingIntent(
        ...     meeting_type="creative_production",
        ...     topic="뮤직비디오 오프닝 아이디어",
        ... )
        >>> result = dispatch_meeting(
        ...     intent,
        ...     user_id="u1",
        ...     channel_id="c1",
        ...     meetings_root="/tmp/test-dispatcher",
        ... )
        >>> result.success
        True
        >>> result.context is not None
        True

        Invalid intent — empty topic rejected:

        >>> intent = MeetingIntent(
        ...     meeting_type="creative_production",
        ...     topic="",
        ... )
        >>> result = dispatch_meeting(
        ...     intent, user_id="u1", channel_id="c1"
        ... )
        >>> result.success
        False
        >>> result.error
        'topic must not be empty (received: "")'
    """
    # ── Step 1: Validate the intent ───────────────────────────────────
    error = _validate_intent(intent)
    if error is not None:
        return DispatchResult(success=False, error=error, intent=intent)

    # ── Step 2: Normalise into MeetingCommandRequest ───────────────────
    request = _normalise_intent(
        intent,
        user_id=user_id,
        channel_id=channel_id,
        thread_id=thread_id,
        guild_id=guild_id,
    )

    # ── Step 3: Call the orchestrator ──────────────────────────────────
    _orchestrator: OrchestratorCallable = (
        orchestrator if orchestrator is not None else _default_create_meeting
    )

    try:
        context = _orchestrator(
            request,
            meetings_root=meetings_root,
            config=config,
        )
    except Exception as exc:
        # The orchestrator may raise (e.g. ValueError from create_meeting
        # for empty fields, OSError for disk failures).  Catch and return
        # as a structured error rather than letting the exception
        # propagate — the caller should never have to catch.
        return DispatchResult(
            success=False,
            error=f"orchestrator raised {type(exc).__name__}: {exc}",
            intent=intent,
        )

    # ── Step 4: Wrap result ───────────────────────────────────────────
    return DispatchResult(success=True, context=context, intent=intent)


# ── Validation helper ────────────────────────────────────────────────────


def _validate_intent(intent: MeetingIntent) -> Optional[str]:
    """Validate that *intent* has all required fields.

    Returns
        ``None`` when the intent is valid, or a human-readable error
        string explaining the first validation failure found.
    """
    # Required: topic must be non-empty after stripping
    if not intent.topic or not intent.topic.strip():
        return f'topic must not be empty (received: "{intent.topic}")'

    # Required: meeting_type must be one of the six recognised types
    if intent.meeting_type not in _VALID_MEETING_TYPES:
        return (
            f"invalid meeting_type '{intent.meeting_type}' — "
            f"must be one of: {', '.join(sorted(_VALID_MEETING_TYPES))}"
        )

    # Required: priority must be p0–p3
    if intent.urgency not in _VALID_PRIORITIES:
        return (
            f"invalid priority '{intent.urgency}' — "
            f"must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
        )

    return None


# ── Normalisation helper ─────────────────────────────────────────────────


def _normalise_intent(
    intent: MeetingIntent,
    *,
    user_id: str,
    channel_id: str,
    thread_id: str = "",
    guild_id: str = "",
) -> MeetingCommandRequest:
    """Convert a validated ``MeetingIntent`` into a ``MeetingCommandRequest``.

    Mapping rules
    -------------
    - ``intent.topic`` → ``request.agenda`` (the meeting's core topic)
    - ``intent.meeting_type`` is stored for later use (routing phase)
    - ``intent.urgency`` → ``request.priority`` (normalised lowercase)
    - ``intent.teams`` → ``request.teams`` (team-level selection)
    - ``intent.suggested_roles`` → ``request.suggested_roles`` (role constraints)

    The command request is the contract that ``create_meeting`` consumes.
    """
    return MeetingCommandRequest(
        agenda=intent.topic.strip(),
        user_id=user_id,
        channel_id=channel_id,
        priority=intent.urgency.lower(),  # normalise: P1 → p1
        thread_id=thread_id,
        guild_id=guild_id,
        teams=intent.teams,
        suggested_roles=intent.suggested_roles,
    )


# ── Exports ────────────────────────────────────────────────────────────────

__all__ = [
    "DispatchResult",
    "OrchestratorCallable",
    "dispatch_meeting",
]
