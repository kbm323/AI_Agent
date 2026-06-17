"""Slash Command Interaction Orchestrator — Sub-AC 1b-iii.

Composes the Discord interaction payload parser (Sub-AC 1b-i) and meeting
parameter validator (Sub-AC 1b-ii), calls meeting initiation with validated
``MeetingParams``, and returns a Discord interaction response dict.

Design
------
- **Single entry point** — ``orchestrate_slash_command()`` receives a raw
  Discord interaction payload (dict, JSON string, or bytes), extracts
  command options, validates them, and dispatches to the meeting creation
  pipeline.
- **Dependency injection** — the meeting initiation callable is injectable
  (defaults to ``create_meeting``), making the orchestrator fully testable
  with mock interaction payloads and a mocked meeting initiation function.
- **Structured response** — every call returns an ``OrchestratorResult``
  with a Discord-compatible interaction response dict, never raising
  exceptions for invalid inputs.
- **Follows project patterns** — discriminated result type
  (``OrchestratorResult``), error-as-value, and the existing
  ``InteractionParseResult`` / ``MeetingParamsResult`` conventions.

Orchestration pipeline::

    Raw Discord payload
        │
        ▼
    parse_interaction_command()      ← Sub-AC 1b-i
        │  InteractionParseResult
        ▼
    extract Discord user metadata
    + extract option values (agenda, team_selection, urgency)
        │
        ▼
    validate_meeting_params()        ← Sub-AC 1b-ii
        │  MeetingParamsResult
        ▼
    build MeetingCommandRequest
        │
        ▼
    create_meeting()                 ← injectable for testing
        │  MeetingContext
        ▼
    format Discord interaction response
        │
        ▼
    OrchestratorResult
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from src.discord_interaction_parser import (
    InteractionParseResult,
    parse_interaction_command,
)
from src.meeting_params_validator import (
    MeetingParamsResult,
    format_params_errors,
    validate_meeting_params,
)
from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    create_meeting as _default_create_meeting,
)

# ── Discord Interaction Response Types ────────────────────────────────────
# Mirror of discord_webhook_handler.DiscordResponseType for self-contained use.

_IR_PONG = 1
_IR_CHANNEL_MESSAGE_WITH_SOURCE = 4
_IR_DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5

# ── Discord extra payload fields we extract ───────────────────────────────

_DEFAULT_USER_ID = "unknown"
_DEFAULT_CHANNEL_ID = "unknown"
_DEFAULT_GUILD_ID = ""


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class OrchestratorResult:
    """The result of orchestrating a slash command interaction.

    When ``success`` is True:
      - ``interaction_response`` carries the Discord callback payload dict.
      - ``meeting_context`` carries the ``MeetingContext`` (if a meeting
        was created — may be None for non-meeting responses like PONG).

    When ``success`` is False:
      - ``interaction_response`` carries an error message response to
        send back to Discord.
      - ``error`` holds a human-readable description.
      - ``error_code`` holds a machine-readable code.
    """

    success: bool
    """True when the slash command was successfully processed."""

    interaction_response: dict[str, Any]
    """Discord-compatible interaction response dict — always populated."""

    meeting_context: Optional[MeetingContext] = None
    """The created meeting context (success only)."""

    error: str = ""
    """Human-readable error description (failure only)."""

    error_code: str = ""
    """Machine-readable error code (failure only)."""


# ── Meeting creation callable protocol ────────────────────────────────────


class MeetingCreatorFn(Protocol):
    """Protocol for the injectable meeting creation callable.

    Concrete implementations include ``create_meeting`` (production)
    and any mock / spy for testing.  The callable receives a
    ``MeetingCommandRequest``, an optional meetings root path, and an
    optional ``MeetingConfig``, and returns a ``MeetingContext``.
    """

    def __call__(
        self,
        request: MeetingCommandRequest,
        *,
        meetings_root: Optional[str] = None,
        config: Optional[MeetingConfig] = None,
    ) -> MeetingContext: ...


# ── Public API ────────────────────────────────────────────────────────────


def orchestrate_slash_command(
    payload: dict[str, Any] | str | bytes,
    *,
    meetings_root: Optional[str] = None,
    config: Optional[MeetingConfig] = None,
    create_meeting_fn: Optional[MeetingCreatorFn] = None,
    expected_command_name: str = "meeting",
) -> OrchestratorResult:
    """Orchestrate a Discord slash command interaction end-to-end.

    This is the **single entry point** for Sub-AC 1b-iii.  It composes
    the payload parser, parameter validator, and meeting creation into a
    single pipeline that takes a raw Discord interaction payload and
    returns a Discord-compatible interaction response.

    Pipeline
    --------
    1. **Parse** the interaction payload via ``parse_interaction_command``.
    2. **Extract** Discord user/channel metadata from the raw payload.
    3. **Extract** slash command option values (agenda, team_selection,
       urgency) from the parsed options.
    4. **Validate** parameters via ``validate_meeting_params``.
    5. **Build** a ``MeetingCommandRequest`` from validated params +
       Discord metadata.
    6. **Call** ``create_meeting_fn`` (injectable for testing).
    7. **Format** a Discord interaction response dict.

    Args:
        payload: Raw Discord interaction payload — dict, JSON string,
                 or UTF-8 bytes.
        meetings_root: Optional root directory for meeting storage.
        config: Optional ``MeetingConfig`` overrides.
        create_meeting_fn: Injectable meeting creation callable.
                           Defaults to ``create_meeting`` from
                           ``meeting_trigger``.
        expected_command_name: Expected slash command name.
                               Defaults to ``"meeting"``.

    Returns:
        ``OrchestratorResult`` — always populated.  Inspect ``success``,
        then read ``interaction_response`` for the Discord callback payload
        and ``meeting_context`` for the created meeting (if successful).

    Examples:
        Successful orchestration::

            >>> payload = _make_meeting_payload(agenda="Design Review")
            >>> result = orchestrate_slash_command(
            ...     payload,
            ...     create_meeting_fn=_mock_create_meeting,
            ... )
            >>> result.success
            True
            >>> result.interaction_response["type"]
            4
            >>> result.meeting_context is not None
            True

        Invalid payload — missing agenda::

            >>> payload = _make_meeting_payload(agenda="")
            >>> result = orchestrate_slash_command(
            ...     payload,
            ...     create_meeting_fn=_mock_create_meeting,
            ... )
            >>> result.success
            False
            >>> result.error_code
            'VALIDATION_FAILED'
            >>> result.interaction_response["type"]
            4

        Not a meeting command::

            >>> payload = _make_meeting_payload(
            ...     command_name="status",
            ...     agenda="check",
            ... )
            >>> result = orchestrate_slash_command(
            ...     payload,
            ...     create_meeting_fn=_mock_create_meeting,
            ... )
            >>> result.success
            False
            >>> result.error_code
            'PARSE_FAILED'
    """
    # ── Step 1: Parse the interaction payload ─────────────────────────
    parse_result: InteractionParseResult = parse_interaction_command(
        payload,
        expected_command_name=expected_command_name,
    )

    if not parse_result.success:
        return _build_error_response(
            error_code=parse_result.error_code or "PARSE_FAILED",
            error_message=parse_result.error or "Failed to parse interaction payload",
        )

    parsed = parse_result.parsed
    assert parsed is not None  # guaranteed by success=True invariant

    # ── Step 2: Extract Discord metadata ──────────────────────────────
    raw_payload = parsed.raw_payload
    user_id = _extract_user_id(raw_payload)
    channel_id = _extract_channel_id(raw_payload)
    guild_id = _extract_guild_id(raw_payload)

    # ── Step 3: Extract option values ─────────────────────────────────
    options = parsed.options

    topic = _extract_topic(options)
    team_selection = _extract_team_selection(options)
    urgency = _extract_urgency(options)

    # ── Step 4: Validate parameters ───────────────────────────────────
    params_result: MeetingParamsResult = validate_meeting_params(
        topic=topic,
        team_selection=team_selection,
        urgency=urgency,
    )

    if not params_result.success:
        formatted_errors = format_params_errors(params_result)
        return _build_error_response(
            error_code="VALIDATION_FAILED",
            error_message=f"Parameter validation failed:\n{formatted_errors}",
        )

    validated = params_result.params
    assert validated is not None  # guaranteed by success=True

    # ── Step 5: Build MeetingCommandRequest ───────────────────────────
    # Map MeetingParams fields to MeetingCommandRequest fields.
    # team_selection from the validator is a tuple of team IDs (like
    # "art-design") — these are team-level identifiers per the Seed
    # ontology and map to request.teams.
    request = MeetingCommandRequest(
        agenda=validated.topic,
        user_id=user_id,
        channel_id=channel_id,
        priority=validated.urgency,
        guild_id=guild_id,
        teams=validated.team_selection,
        suggested_roles=(),  # no role-level constraints from slash command
    )

    # ── Step 6: Call meeting creation ─────────────────────────────────
    creator: MeetingCreatorFn = (
        create_meeting_fn if create_meeting_fn is not None else _default_create_meeting
    )

    try:
        meeting_context = creator(
            request,
            meetings_root=meetings_root,
            config=config,
        )
    except Exception as exc:
        return _build_error_response(
            error_code="MEETING_CREATION_FAILED",
            error_message=(
                f"Meeting creation failed: {type(exc).__name__}: {exc}"
            ),
        )

    # ── Step 7: Format success response ───────────────────────────────
    meeting_id = meeting_context.meeting_id
    response_content = (
        f"✅ **Meeting created!**\n"
        f"**ID:** `{meeting_id}`\n"
        f"**Topic:** {validated.topic}\n"
        f"**Priority:** {validated.urgency.upper()}\n"
        f"**Teams:** {', '.join(validated.team_selection) if validated.team_selection else 'All teams'}\n"
        f"\nThe Coordinator will begin the meeting pipeline shortly."
    )

    interaction_response: dict[str, Any] = {
        "type": _IR_CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {
            "content": response_content,
        },
    }

    return OrchestratorResult(
        success=True,
        interaction_response=interaction_response,
        meeting_context=meeting_context,
    )


# ── Extraction helpers ────────────────────────────────────────────────────


def _extract_user_id(raw_payload: dict[str, Any]) -> str:
    """Extract the Discord user ID from the interaction payload.

    Discord structures: ``member.user.id`` (guild) or ``user.id`` (DM).
    """
    member = raw_payload.get("member")
    if isinstance(member, dict):
        user_obj = member.get("user")
        if isinstance(user_obj, dict):
            uid = user_obj.get("id")
            if isinstance(uid, str) and uid:
                return uid

    user_obj = raw_payload.get("user")
    if isinstance(user_obj, dict):
        uid = user_obj.get("id")
        if isinstance(uid, str) and uid:
            return uid

    return _DEFAULT_USER_ID


def _extract_channel_id(raw_payload: dict[str, Any]) -> str:
    """Extract the Discord channel ID from the interaction payload."""
    cid = raw_payload.get("channel_id")
    if isinstance(cid, str) and cid:
        return cid
    return _DEFAULT_CHANNEL_ID


def _extract_guild_id(raw_payload: dict[str, Any]) -> str:
    """Extract the Discord guild ID from the interaction payload.

    Returns empty string for DMs (no guild).
    """
    gid = raw_payload.get("guild_id")
    if isinstance(gid, str) and gid:
        return gid
    return _DEFAULT_GUILD_ID


def _extract_topic(options: dict[str, Any]) -> str:
    """Extract the meeting topic (agenda) from parsed command options.

    The primary option name is ``agenda``, with ``topic`` as a fallback.
    Both are common in Discord slash command registrations.
    """
    value = options.get("agenda")
    if value is None:
        value = options.get("topic", "")
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    return value


def _extract_team_selection(options: dict[str, Any]) -> list[str]:
    """Extract team selection from parsed command options.

    Discord can deliver this as:
    - A single string (type 3, STRING) — split on commas.
    - A list of strings (if using multi-select or collected options).
    """
    raw = options.get("team_selection")
    if raw is None:
        return []

    if isinstance(raw, list):
        result: list[str] = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif item is not None:
                result.append(str(item))
        return result

    if isinstance(raw, str):
        # Split on commas for comma-separated team lists
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts

    # Unexpected type — try string conversion
    try:
        return [str(raw)]
    except Exception:
        return []


def _extract_urgency(options: dict[str, Any]) -> str:
    """Extract urgency/priority from parsed command options.

    The option name is ``urgency`` with ``priority`` as a fallback.
    Returns ``"p2"`` (default) when the option is not present.
    """
    value = options.get("urgency")
    if value is None:
        value = options.get("priority")
    if value is None:
        return "p2"
    if not isinstance(value, str):
        value = str(value)
    return value if value else "p2"


# ── Response builders ─────────────────────────────────────────────────────


def _build_error_response(
    *,
    error_code: str,
    error_message: str,
) -> OrchestratorResult:
    """Build a failed OrchestratorResult with a user-facing error message.

    The interaction_response still uses type 4 (CHANNEL_MESSAGE_WITH_SOURCE)
    so the user sees the error inline in Discord.  The content is formatted
    for readability.
    """
    formatted_message = f"❌ **Error** [`{error_code}`]:\n{error_message}"

    interaction_response: dict[str, Any] = {
        "type": _IR_CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {
            "content": formatted_message,
        },
    }

    return OrchestratorResult(
        success=False,
        interaction_response=interaction_response,
        error=error_message,
        error_code=error_code,
    )


# ── Exports ───────────────────────────────────────────────────────────────

__all__ = [
    "OrchestratorResult",
    "MeetingCreatorFn",
    "orchestrate_slash_command",
]
