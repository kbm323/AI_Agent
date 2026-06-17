"""Trigger input parsing and normalization — Sub-AC 3.1.

Parse raw trigger payloads from Discord commands, bot mentions, webhooks,
or direct API calls into a structured, validated ``MeetingRequest`` object.

This module is the **first normalization layer** in the trigger pipeline.
It sits upstream of ``meeting_intent_parser`` (Sub-AC 2b) and the
``trigger_router`` (Sub-AC 2.3)::

    Raw trigger payload (any source)
        → trigger_input_parser.parse_meeting_request()
        → MeetingRequest (canonical validated representation)
        → meeting_intent_parser / trigger_router (downstream)

Design
------
- **Source-agnostic** — accepts payloads from Discord slash commands,
  bot mentions, HTTP webhooks, and direct API calls via a single entry
  point ``parse_meeting_request()``.
- **Payload-validated** — every field is validated against the Seed
  ontology constraints before downstream consumers see the data.
- **Pure-in-memory** — no filesystem I/O, no CLI calls.  The module is
  fully testable with mock payloads.
- **Structured errors** — all validation failures return a
  ``ParseResult`` with ``success=False`` and a descriptive error;
  exceptions are never raised for validation failures.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Optional

# ── Trigger source enum ─────────────────────────────────────────────────

@unique
class TriggerSource(StrEnum):
    """Identifies the origin of a meeting trigger.

    The source determines which fields are required and how the raw
    payload is interpreted.
    """

    DISCORD_SLASH = "discord_slash"
    """Discord APPLICATION_COMMAND interaction (slash command)."""

    DISCORD_MENTION = "discord_mention"
    """Discord @bot mention (MESSAGE_CREATE gateway event)."""

    WEBHOOK = "webhook"
    """HTTP webhook payload (e.g. Zapier, n8n, external service)."""

    DIRECT_API = "direct_api"
    """Direct programmatic API call (Hermes skill, cron job, test)."""


# ── Valid priorities ────────────────────────────────────────────────────

_VALID_PRIORITIES: frozenset[str] = frozenset({"p0", "p1", "p2", "p3"})
"""Accepted priority levels per Seed constraint."""

# ── Valid meeting types ─────────────────────────────────────────────────

_VALID_MEETING_TYPES: frozenset[str] = frozenset(
    {
        "creative_production",
        "technical_development",
        "marketing_strategy",
        "risk_assessment",
        "general_planning",
        "project_review",
    }
)
"""Recognised meeting types from the Seed ontology."""

# ── Discord snowflake ID pattern ────────────────────────────────────────

# Discord snowflakes are 17-20 decimal digits (up to 64-bit unsigned).
_DISCORD_SNOWFLAKE_RE = _re.compile(r"^[0-9]{17,20}$")


def _is_valid_snowflake(value: str) -> bool:
    """Return True when *value* looks like a valid Discord snowflake ID."""
    return bool(value and _DISCORD_SNOWFLAKE_RE.match(value))


# ── Max field lengths (guard against oversized inputs) ──────────────────

_MAX_AGENDA_LENGTH = 4_000
"""Maximum characters for a meeting agenda (Discord message limit is 2000,
but we allow longer for combined webhook / direct API sources)."""

_MAX_USER_ID_LENGTH = 64
_MAX_CHANNEL_ID_LENGTH = 64
_MAX_THREAD_ID_LENGTH = 64
_MAX_GUILD_ID_LENGTH = 64
_MAX_PARTICIPANT_ID_LENGTH = 128


# ═════════════════════════════════════════════════════════════════════════
# MeetingRequest — canonical validated trigger representation
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeetingRequest:
    """Canonical, validated meeting trigger representation.

    Produced by ``parse_meeting_request()`` after parsing and validating
    the raw trigger payload.  All downstream consumers (meeting intent
    parser, trigger router, meeting creation dispatcher) receive this
    object as their input contract.

    Required fields (must be present in every valid request):
        - **source** — identifies the origin of the trigger
        - **agenda** — the meeting topic / objectives
        - **user_id** — the Discord user ID who initiated
        - **channel_id** — the Discord channel ID

    Optional fields (populated when available from the trigger source):
        - **meeting_type** — classified meeting type (may be empty)
        - **priority** — urgency level (defaults to ``p2``)
        - **thread_id** — Discord thread ID
        - **guild_id** — Discord guild/server ID
        - **message_id** — original Discord message snowflake
        - **participants** — suggested role/team identifiers
        - **teams** — team-level selection
        - **suggested_roles** — role-level participant constraints
        - **raw_tags** — explicit topic tags from the user
        - **confidence** — parser confidence 0.0–1.0
        - **reasoning** — human-readable parse trace
    """

    # ── Identity ──
    source: TriggerSource
    """Trigger origin (discord_slash, discord_mention, webhook, direct_api)."""

    # ── Core content ──
    agenda: str
    """Meeting topic and objectives (stripped, non-empty)."""

    user_id: str
    """Discord user ID who initiated the meeting."""

    channel_id: str
    """Discord channel ID where the command was issued."""

    # ── Classification (populated by intent parser downstream) ──
    meeting_type: str = ""
    """Classified meeting type (empty when not yet classified)."""

    # ── Priority ──
    priority: str = "p2"
    """Priority level: p0 | p1 | p2 | p3."""

    # ── Discord source fields ──
    thread_id: str = ""
    """Discord thread ID (created by Coordinator)."""

    guild_id: str = ""
    """Discord guild/server ID (empty for DMs)."""

    message_id: str = ""
    """Original Discord message snowflake ID."""

    # ── Participant selection ──
    participants: tuple[str, ...] = ()
    """Suggested role or team identifiers (union of teams + suggested_roles)."""

    teams: tuple[str, ...] = ()
    """Team-level selection: which teams should be convened."""

    suggested_roles: tuple[str, ...] = ()
    """Role-level participant constraints mentioned by the user."""

    # ── Tagging ──
    raw_tags: tuple[str, ...] = ()
    """Explicit topic tags provided by the user."""

    # ── Metadata ──
    confidence: float = 1.0
    """Parse confidence 0.0–1.0."""

    reasoning: str = ""
    """Human-readable trace of the parsing decisions."""

    # ── Raw payload (original, unmodified) ──
    raw_payload: dict[str, object] = field(default_factory=dict)
    """The original raw payload for audit/debugging."""

    def __post_init__(self) -> None:
        """Validate field constraints on construction."""
        # Core fields
        if not self.agenda or not self.agenda.strip():
            raise ValueError("agenda must not be empty")
        if len(self.agenda) > _MAX_AGENDA_LENGTH:
            raise ValueError(
                f"agenda exceeds {_MAX_AGENDA_LENGTH} characters "
                f"({len(self.agenda)})"
            )
        if not self.user_id or not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if not self.channel_id or not self.channel_id.strip():
            raise ValueError("channel_id must not be empty")

        # Priority
        if self.priority not in _VALID_PRIORITIES:
            raise ValueError(
                f"invalid priority '{self.priority}' — "
                f"must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
            )

        # Meeting type (when provided must be valid)
        if self.meeting_type and self.meeting_type not in _VALID_MEETING_TYPES:
            raise ValueError(
                f"invalid meeting_type '{self.meeting_type}' — "
                f"must be one of: {', '.join(sorted(_VALID_MEETING_TYPES))}"
            )

        # Confidence bounds
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )

        # Ensure tuples
        for attr in ("participants", "teams", "suggested_roles", "raw_tags"):
            val = getattr(self, attr)
            if not isinstance(val, tuple):
                object.__setattr__(self, attr, tuple(val))

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "source": self.source.value,
            "agenda": self.agenda,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "meeting_type": self.meeting_type,
            "priority": self.priority,
            "thread_id": self.thread_id,
            "guild_id": self.guild_id,
            "message_id": self.message_id,
            "participants": list(self.participants),
            "teams": list(self.teams),
            "suggested_roles": list(self.suggested_roles),
            "raw_tags": list(self.raw_tags),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }

    def to_command_request(self) -> "MeetingCommandRequest":
        """Convert to a ``MeetingCommandRequest`` for ``create_meeting()``.

        This is the bridge from the parsed trigger to the meeting
        creation pipeline (Sub-AC 1c).
        """
        # Import deferred to avoid circular dependency at module level.
        from src.meeting_trigger import MeetingCommandRequest  # noqa: PLC0415

        return MeetingCommandRequest(
            agenda=self.agenda,
            user_id=self.user_id,
            channel_id=self.channel_id,
            priority=self.priority,
            thread_id=self.thread_id,
            guild_id=self.guild_id,
            teams=self.teams,
            suggested_roles=self.suggested_roles,
        )


# ═════════════════════════════════════════════════════════════════════════
# Parse result — success or structured error
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ParseResult:
    """Result of ``parse_meeting_request()``.

    Mirrors the ``DispatchResult`` / ``TriggerRoutingResult`` pattern —
    every parse, whether successful or not, returns a structured,
    inspectable result.

    Attributes:
        success: True when parsing and validation passed.
        request: The validated ``MeetingRequest`` (success only).
        error: Human-readable rejection reason (failure only).
    """

    success: bool
    """True when the trigger was successfully parsed and validated."""

    request: Optional[MeetingRequest] = None
    """The validated MeetingRequest (success only)."""

    error: Optional[str] = None
    """Human-readable rejection reason (failure only)."""


# ═════════════════════════════════════════════════════════════════════════
# Public API — parse_meeting_request()
# ═════════════════════════════════════════════════════════════════════════


def parse_meeting_request(
    payload: dict[str, object],
    *,
    source: TriggerSource,
    **overrides: object,
) -> ParseResult:
    """Parse raw trigger payload into a validated ``MeetingRequest``.

    This is the **single entry point** for Sub-AC 3.1.  It receives a
    raw trigger payload from any supported source, extracts and
    validates all required fields, and returns a ``ParseResult``.

    **Source dispatch**:
    - ``discord_slash`` → ``_parse_discord_slash()``
    - ``discord_mention`` → ``_parse_discord_mention()``
    - ``webhook`` → ``_parse_webhook()``
    - ``direct_api`` → ``_parse_direct_api()``

    **Overrides**: Additional keyword arguments can override or
    supplement fields extracted from the payload.  This is useful
    when the caller has side-channel information (e.g. the Discord
    gateway adapter knows the channel_id even when the payload is
    ambiguous).

    Args:
        payload: Raw trigger payload (structure varies by source).
        source: The trigger source type.
        **overrides: Field overrides (agenda, user_id, channel_id, etc.).

    Returns:
        ``ParseResult`` — inspect ``.success`` to branch; access
        ``.request`` when True, ``.error`` when False.

    Raises:
        ValueError: If ``source`` is not a recognised ``TriggerSource``.

    Examples:
        >>> result = parse_meeting_request(
        ...     payload={
        ...         "agenda": "신규 캐릭터 디자인 검토",
        ...         "user_id": "discord_user_12345",
        ...         "channel_id": "discord_channel_67890",
        ...         "priority": "p1",
        ...     },
        ...     source=TriggerSource.DIRECT_API,
        ... )
        >>> result.success
        True
        >>> result.request.agenda
        '신규 캐릭터 디자인 검토'
    """
    # ── Validate source ──
    if not isinstance(source, TriggerSource):
        raise ValueError(
            f"source must be a TriggerSource, got {type(source).__name__}"
        )

    # ── Dispatch to source-specific parser ──
    parser = _SOURCE_PARSERS.get(source)
    if parser is None:
        return ParseResult(
            success=False,
            error=f"unsupported trigger source: {source.value}",
        )

    parsed = parser(payload)

    # ── Apply overrides ──
    if overrides:
        parsed = _apply_overrides(parsed, overrides)

    # ── Normalise and validate ──
    return _normalise_and_validate(parsed, source)


# ═════════════════════════════════════════════════════════════════════════
# Source-specific parsers
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _RawParsed:
    """Intermediate representation from source-specific parsing.

    Fields are extracted from the raw payload but not yet fully
    validated or normalised.  ``_normalise_and_validate()`` produces
    the final ``MeetingRequest`` from this.
    """

    agenda: str = ""
    user_id: str = ""
    channel_id: str = ""
    meeting_type: str = ""
    priority: str = "p2"
    thread_id: str = ""
    guild_id: str = ""
    message_id: str = ""
    participants: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    suggested_roles: tuple[str, ...] = ()
    raw_tags: tuple[str, ...] = ()
    confidence: float = 1.0
    reasoning: str = ""
    raw_payload: dict[str, object] = field(default_factory=dict)


def _parse_discord_slash(payload: dict[str, object]) -> _RawParsed:
    """Parse a Discord APPLICATION_COMMAND (slash command) payload.

    Expected payload structure (from Discord interaction webhook)::

        {
            "data": {
                "name": "meeting",
                "options": [
                    {"name": "agenda", "value": "..."},
                    {"name": "priority", "value": "p1"},
                    ...
                ]
            },
            "member": {"user": {"id": "..."}},
            "channel_id": "...",
            "guild_id": "...",
        }
    """
    data = _safe_get(payload, "data", default={})
    options_list = _safe_get(data, "options", default=[])
    member = _safe_get(payload, "member", default={})
    user_obj = _safe_get(member, "user", default={})

    # Extract options into a lookup dict
    options: dict[str, str] = {}
    for opt in options_list if isinstance(options_list, list) else []:
        if isinstance(opt, dict):
            name = str(opt.get("name", ""))
            value = str(opt.get("value", ""))
            if name:
                options[name] = value

    agenda = options.get("agenda", "")
    priority_raw = options.get("priority", "p2").lower()
    priority = priority_raw if priority_raw in _VALID_PRIORITIES else "p2"
    meeting_type = options.get("meeting_type", "")

    user_id = str(_safe_get(user_obj, "id", default=""))
    channel_id = str(_safe_get(payload, "channel_id", default=""))
    guild_id = str(_safe_get(payload, "guild_id", default=""))

    # Slash commands don't carry participants directly — those come
    # from the meeting_type / intent parser downstream.
    reasoning_parts = ["Parsed Discord slash command"]
    if meeting_type:
        reasoning_parts.append(f"meeting_type={meeting_type}")
    if priority != "p2":
        reasoning_parts.append(f"priority={priority}")

    return _RawParsed(
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        meeting_type=meeting_type,
        priority=priority,
        guild_id=guild_id,
        confidence=0.95,
        reasoning=" ".join(reasoning_parts),
        raw_payload=payload,
    )


def _parse_discord_mention(payload: dict[str, object]) -> _RawParsed:
    """Parse a Discord @bot mention trigger payload.

    Expected payload structure (from ``ExtractedMentionCommand``)::

        {
            "content": "...",          # cleaned text with mention removed
            "author_id": "...",
            "author_name": "...",
            "channel_id": "...",
            "guild_id": "...",
            "message_id": "...",
            "is_command": True,
        }

    The ``content`` field becomes ``agenda`` after the intent parser
    processes it downstream.  Here we pass it through as-is.
    """
    agenda = str(_safe_get(payload, "content", default=""))
    user_id = str(_safe_get(payload, "author_id", default=""))
    channel_id = str(_safe_get(payload, "channel_id", default=""))
    guild_id = str(_safe_get(payload, "guild_id", default=""))
    message_id = str(_safe_get(payload, "message_id", default=""))

    return _RawParsed(
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        guild_id=guild_id,
        message_id=message_id,
        confidence=0.90,
        reasoning="Parsed Discord @bot mention",
        raw_payload=payload,
    )


def _parse_webhook(payload: dict[str, object]) -> _RawParsed:
    """Parse an HTTP webhook trigger payload.

    Expected payload structure (generic webhook — field names are
    normalised to the ontology)::

        {
            "agenda": "...",           # or "topic", "text"
            "user_id": "...",          # or "user"
            "channel_id": "...",       # optional for webhooks
            "priority": "p2",          # optional
            "meeting_type": "...",     # optional
            "tags": [...],             # optional
            "participants": [...],     # optional
            "teams": [...],            # optional
            "suggested_roles": [...],  # optional
        }
    """
    # Agenda: try "agenda" first, then common aliases
    agenda = _first_non_empty(
        payload,
        "agenda",
        "topic",
        "text",
        "message",
        "content",
    )

    # User ID: try "user_id" then "user", "author_id", "author"
    user_id = _first_non_empty(
        payload,
        "user_id",
        "user",
        "author_id",
        "author",
    )

    channel_id = str(_safe_get(payload, "channel_id", default=""))

    priority_raw = str(_safe_get(payload, "priority", default="p2")).lower()
    priority = priority_raw if priority_raw in _VALID_PRIORITIES else "p2"

    meeting_type = str(_safe_get(payload, "meeting_type", default=""))

    raw_tags = _extract_string_list(payload, "tags")
    participants = _extract_string_list(payload, "participants")
    teams = _extract_string_list(payload, "teams")
    suggested_roles = _extract_string_list(payload, "suggested_roles")

    reasoning_parts = ["Parsed webhook payload"]
    if meeting_type:
        reasoning_parts.append(f"meeting_type={meeting_type}")

    return _RawParsed(
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        meeting_type=meeting_type,
        priority=priority,
        participants=tuple(participants),
        teams=tuple(teams),
        suggested_roles=tuple(suggested_roles),
        raw_tags=tuple(raw_tags),
        confidence=0.85,
        reasoning=" ".join(reasoning_parts),
        raw_payload=payload,
    )


def _parse_direct_api(payload: dict[str, object]) -> _RawParsed:
    """Parse a direct programmatic API call payload.

    Expected payload structure — fields use the canonical ontology names::

        {
            "agenda": "...",           # required
            "user_id": "...",          # required
            "channel_id": "...",       # required
            "meeting_type": "...",     # optional
            "priority": "p2",          # optional
            "thread_id": "...",        # optional
            "guild_id": "...",         # optional
            "message_id": "...",       # optional
            "participants": [...],     # optional
            "teams": [...],            # optional
            "suggested_roles": [...],  # optional
            "tags": [...],             # optional
        }

    This is the most permissive source — it expects fields to already
    use the canonical names.
    """
    agenda = str(_safe_get(payload, "agenda", default=""))
    user_id = str(_safe_get(payload, "user_id", default=""))
    channel_id = str(_safe_get(payload, "channel_id", default=""))
    meeting_type = str(_safe_get(payload, "meeting_type", default=""))

    priority_raw = str(_safe_get(payload, "priority", default="p2")).lower()
    priority = priority_raw if priority_raw in _VALID_PRIORITIES else "p2"

    thread_id = str(_safe_get(payload, "thread_id", default=""))
    guild_id = str(_safe_get(payload, "guild_id", default=""))
    message_id = str(_safe_get(payload, "message_id", default=""))

    participants = _extract_string_list(payload, "participants")
    teams = _extract_string_list(payload, "teams")
    suggested_roles = _extract_string_list(payload, "suggested_roles")
    raw_tags = _extract_string_list(payload, "tags")

    reasoning_parts = ["Parsed direct API payload"]
    if meeting_type:
        reasoning_parts.append(f"meeting_type={meeting_type}")

    return _RawParsed(
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        meeting_type=meeting_type,
        priority=priority,
        thread_id=thread_id,
        guild_id=guild_id,
        message_id=message_id,
        participants=tuple(participants),
        teams=tuple(teams),
        suggested_roles=tuple(suggested_roles),
        raw_tags=tuple(raw_tags),
        confidence=0.98,
        reasoning=" ".join(reasoning_parts),
        raw_payload=payload,
    )


# Source parser callable type
from typing import Callable as _Callable

_SourceParserFn = _Callable[[dict[str, object]], _RawParsed]

# Source parser registry
_SOURCE_PARSERS: dict[TriggerSource, _SourceParserFn] = {
    TriggerSource.DISCORD_SLASH: _parse_discord_slash,
    TriggerSource.DISCORD_MENTION: _parse_discord_mention,
    TriggerSource.WEBHOOK: _parse_webhook,
    TriggerSource.DIRECT_API: _parse_direct_api,
}


# ═════════════════════════════════════════════════════════════════════════
# Normalisation and validation
# ═════════════════════════════════════════════════════════════════════════


def _normalise_and_validate(
    raw: _RawParsed,
    source: TriggerSource,
) -> ParseResult:
    """Normalise and validate a ``_RawParsed`` into a ``MeetingRequest``.

    Performs field-level validation: non-empty agenda, valid user_id,
    valid channel_id, valid meeting_type (if provided), valid priority,
    and source-specific required fields.
    """
    # ── Agenda ──
    agenda = raw.agenda.strip() if raw.agenda else ""
    if not agenda:
        return ParseResult(
            success=False,
            error="agenda must not be empty",
        )
    if len(agenda) > _MAX_AGENDA_LENGTH:
        return ParseResult(
            success=False,
            error=f"agenda exceeds {_MAX_AGENDA_LENGTH} characters "
            f"({len(agenda)})",
        )

    # ── User ID ──
    user_id = raw.user_id.strip() if raw.user_id else ""
    if not user_id:
        return _source_specific_error(
            source,
            "user_id must not be empty",
        )
    if len(user_id) > _MAX_USER_ID_LENGTH:
        return ParseResult(
            success=False,
            error=f"user_id exceeds {_MAX_USER_ID_LENGTH} characters",
        )
    # For Discord sources, validate snowflake format
    if source in (TriggerSource.DISCORD_SLASH, TriggerSource.DISCORD_MENTION):
        if not _is_valid_snowflake(user_id):
            return ParseResult(
                success=False,
                error=f"user_id '{user_id}' is not a valid Discord snowflake",
            )

    # ── Channel ID ──
    channel_id = raw.channel_id.strip() if raw.channel_id else ""
    if not channel_id:
        return _source_specific_error(
            source,
            "channel_id must not be empty",
        )
    if len(channel_id) > _MAX_CHANNEL_ID_LENGTH:
        return ParseResult(
            success=False,
            error=f"channel_id exceeds {_MAX_CHANNEL_ID_LENGTH} characters",
        )
    if source in (TriggerSource.DISCORD_SLASH, TriggerSource.DISCORD_MENTION):
        if not _is_valid_snowflake(channel_id):
            return ParseResult(
                success=False,
                error=f"channel_id '{channel_id}' is not a valid Discord snowflake",
            )

    # ── Meeting type ──
    meeting_type = raw.meeting_type.strip() if raw.meeting_type else ""
    if meeting_type and meeting_type not in _VALID_MEETING_TYPES:
        return ParseResult(
            success=False,
            error=f"invalid meeting_type '{meeting_type}' — "
            f"must be one of: {', '.join(sorted(_VALID_MEETING_TYPES))}",
        )

    # ── Priority ──
    priority = raw.priority.lower() if raw.priority else "p2"
    if priority not in _VALID_PRIORITIES:
        return ParseResult(
            success=False,
            error=f"invalid priority '{priority}' — "
            f"must be one of: {', '.join(sorted(_VALID_PRIORITIES))}",
        )

    # ── Optional Discord fields ──
    thread_id = raw.thread_id.strip() if raw.thread_id else ""
    if thread_id and source in (
        TriggerSource.DISCORD_SLASH,
        TriggerSource.DISCORD_MENTION,
    ):
        if not _is_valid_snowflake(thread_id):
            return ParseResult(
                success=False,
                error=f"thread_id '{thread_id}' is not a valid Discord snowflake",
            )

    guild_id = raw.guild_id.strip() if raw.guild_id else ""
    if guild_id and source in (
        TriggerSource.DISCORD_SLASH,
        TriggerSource.DISCORD_MENTION,
    ):
        if not _is_valid_snowflake(guild_id):
            return ParseResult(
                success=False,
                error=f"guild_id '{guild_id}' is not a valid Discord snowflake",
            )

    message_id = raw.message_id.strip() if raw.message_id else ""
    if message_id and source in (
        TriggerSource.DISCORD_SLASH,
        TriggerSource.DISCORD_MENTION,
    ):
        if not _is_valid_snowflake(message_id):
            return ParseResult(
                success=False,
                error=f"message_id '{message_id}' is not a valid Discord snowflake",
            )

    # ── Build MeetingRequest ──
    try:
        request = MeetingRequest(
            source=source,
            agenda=agenda,
            user_id=user_id,
            channel_id=channel_id,
            meeting_type=meeting_type,
            priority=priority,
            thread_id=thread_id,
            guild_id=guild_id,
            message_id=message_id,
            participants=raw.participants,
            teams=raw.teams,
            suggested_roles=raw.suggested_roles,
            raw_tags=raw.raw_tags,
            confidence=raw.confidence,
            reasoning=raw.reasoning,
            raw_payload=raw.raw_payload,
        )
    except ValueError as exc:
        return ParseResult(success=False, error=str(exc))

    return ParseResult(success=True, request=request)


def _source_specific_error(
    source: TriggerSource,
    generic_message: str,
) -> ParseResult:
    """Produce a source-specific error message.

    Different trigger sources may need different guidance for the
    same validation failure.
    """
    hints: dict[TriggerSource, str] = {
        TriggerSource.DISCORD_SLASH: (
            f"{generic_message}. For Discord slash commands, ensure "
            "the interaction payload includes the required user and "
            "channel fields."
        ),
        TriggerSource.DISCORD_MENTION: (
            f"{generic_message}. For Discord @mentions, ensure "
            "author_id and channel_id are extracted from the "
            "MESSAGE_CREATE event."
        ),
        TriggerSource.WEBHOOK: (
            f"{generic_message}. For webhook payloads, provide "
            "user_id/author and channel_id, or pass them as overrides."
        ),
        TriggerSource.DIRECT_API: (
            f"{generic_message}. For direct API calls, provide "
            "user_id and channel_id in the payload dict."
        ),
    }
    error = hints.get(source, generic_message)
    return ParseResult(success=False, error=error)


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _safe_get(
    obj: object,
    key: str,
    *,
    default: object = "",
) -> object:
    """Safely get a value from a dict, returning *default* on any error.

    Never raises — always returns *default* when *obj* is not a dict,
    *key* is missing, or any other exception occurs.
    """
    if not isinstance(obj, dict):
        return default
    try:
        return obj.get(key, default)
    except Exception:
        return default


def _first_non_empty(
    payload: dict[str, object],
    *keys: str,
) -> str:
    """Return the first non-empty value for any of *keys* in *payload*.

    Returns empty string when no key yields a non-empty value.
    """
    for key in keys:
        val = _safe_get(payload, key, default="")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_string_list(
    payload: dict[str, object],
    key: str,
) -> list[str]:
    """Extract a list of strings from a payload key.

    Handles both actual lists and comma-separated strings.
    Returns an empty list when the key is missing or invalid.
    """
    val = _safe_get(payload, key, default=None)
    if val is None:
        return []

    if isinstance(val, list):
        result: list[str] = []
        for item in val:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    result.append(stripped)
            elif isinstance(item, (int, float)):
                result.append(str(item))
        return result

    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]

    return []


def _apply_overrides(
    raw: _RawParsed,
    overrides: dict[str, object],
) -> _RawParsed:
    """Apply field overrides to a ``_RawParsed``.

    Only overrides for fields that exist on ``_RawParsed`` are applied.
    Unknown keys are silently ignored.
    """
    allowed = {
        "agenda",
        "user_id",
        "channel_id",
        "meeting_type",
        "priority",
        "thread_id",
        "guild_id",
        "message_id",
        "participants",
        "teams",
        "suggested_roles",
        "raw_tags",
        "confidence",
        "reasoning",
    }

    updates: dict[str, object] = {}
    for key, value in overrides.items():
        if key in allowed:
            updates[key] = value

    if not updates:
        return raw

    return _RawParsed(
        agenda=str(updates.get("agenda", raw.agenda)),
        user_id=str(updates.get("user_id", raw.user_id)),
        channel_id=str(updates.get("channel_id", raw.channel_id)),
        meeting_type=str(updates.get("meeting_type", raw.meeting_type)),
        priority=str(updates.get("priority", raw.priority)),
        thread_id=str(updates.get("thread_id", raw.thread_id)),
        guild_id=str(updates.get("guild_id", raw.guild_id)),
        message_id=str(updates.get("message_id", raw.message_id)),
        participants=_coerce_tuple(updates.get("participants", raw.participants)),
        teams=_coerce_tuple(updates.get("teams", raw.teams)),
        suggested_roles=_coerce_tuple(
            updates.get("suggested_roles", raw.suggested_roles)
        ),
        raw_tags=_coerce_tuple(updates.get("raw_tags", raw.raw_tags)),
        confidence=_coerce_float(updates.get("confidence", raw.confidence)),
        reasoning=str(updates.get("reasoning", raw.reasoning)),
        raw_payload=raw.raw_payload,
    )


def _coerce_tuple(value: object) -> tuple[str, ...]:
    """Coerce any reasonable iterable/string into a tuple of strings."""
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(str(v) for v in value if v)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        return tuple(s.strip() for s in stripped.split(",") if s.strip())
    return ()


def _coerce_float(value: object) -> float:
    """Coerce a value to float, clamping to [0.0, 1.0]."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


# ═════════════════════════════════════════════════════════════════════════
# Bridge to existing pipeline
# ═════════════════════════════════════════════════════════════════════════


def meeting_request_to_parsed_trigger_input(
    request: MeetingRequest,
) -> "ParsedTriggerInput":
    """Convert a ``MeetingRequest`` to a ``ParsedTriggerInput``.

    This bridges Sub-AC 3.1 output into the Sub-AC 2.3
    ``trigger_router`` pipeline.

    The ``is_meeting`` flag is always True because ``MeetingRequest``
    is only produced for meeting triggers.
    """
    from src.trigger_router import ParsedTriggerInput  # noqa: PLC0415

    return ParsedTriggerInput(
        topic=request.agenda,
        meeting_type=request.meeting_type,
        priority=request.priority,
        participants=request.participants,
        teams=request.teams,
        suggested_roles=request.suggested_roles,
        meeting_id="",
        is_meeting=True,
    )


# ── Exports ────────────────────────────────────────────────────────────

__all__ = [
    "MeetingRequest",
    "ParseResult",
    "TriggerSource",
    "parse_meeting_request",
    "meeting_request_to_parsed_trigger_input",
]
