"""Meeting session factory — Sub-AC 3.2.

Creates a validated ``MeetingManifest`` (MeetingSession record) from a
``MeetingCommandRequest`` (MeetingRequest), enforcing required fields,
defaults, and business rules.  The factory is **pure** — no filesystem I/O,
no side-effects — so it is independently testable and can be called before
the directory structure is created.

Design
------
The factory is a single public function ``create_meeting_session()`` that:

1. **Validates** the request against required fields and business rules.
2. **Applies defaults** from ``MeetingConfig`` (or system constants).
3. **Constructs** an immutable ``MeetingManifest`` with ``state='created'``.
4. **Returns** the manifest — the caller is responsible for persistence.

This separation of concerns lets the meeting creation pipeline
(``create_meeting()`` in ``meeting_trigger.py``) focus on I/O and directory
management, while the factory owns the pure logic of record creation.

Usage::

    from src.meeting_session_factory import (
        create_meeting_session,
        SessionFactoryResult,
    )

    request = MeetingCommandRequest(
        agenda="신규 캐릭터 '루나'의 비주얼 디자인 회의",
        user_id="discord_user_12345",
        channel_id="discord_channel_67890",
        priority="p1",
    )
    result = create_meeting_session(request)
    if result.success:
        manifest = result.session
        print(f"Created meeting: {manifest.meeting_id}")
    else:
        print(f"Validation failed: {result.error}")
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional, Tuple

from src.meeting_trigger import (
    CONDITIONAL_VALIDATOR_MODEL,
    MANIFEST_SCHEMA_VERSION,
    MAX_AGENTS_PER_MEETING,
    MAX_ROUNDS,
    PRIMARY_VALIDATOR_MODEL,
    TOKEN_LIMIT_CODEX,
    TOKEN_LIMIT_VALIDATOR,
    TOKEN_LIMIT_WORKER,
    MeetingCommandRequest,
    MeetingConfig,
    MeetingManifest,
)

# ── Valid priority levels ─────────────────────────────────────────────────

_VALID_PRIORITIES: frozenset[str] = frozenset({"p0", "p1", "p2", "p3"})
"""Recognised priority levels from the Seed contract."""

# ── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionFactoryResult:
    """Result of a ``create_meeting_session()`` call.

    When ``success`` is True, ``session`` contains the validated
    ``MeetingManifest`` and ``error`` is ``None``.  When ``success`` is
    False, ``session`` is ``None`` and ``error`` explains why creation
    was rejected.

    This follows the project pattern of structured result types
    (``DispatchResult``, ``TransitionResult``, ``LifecycleProgressionResult``).
    """

    success: bool
    """True when the session was successfully created."""

    session: Optional[MeetingManifest] = None
    """The validated meeting manifest (success only)."""

    error: Optional[str] = None
    """Human-readable rejection reason (failure only)."""

    def __post_init__(self) -> None:
        """Enforce the success/session/error invariant at construction."""
        if self.success:
            if self.session is None:
                raise ValueError(
                    "success=True requires session to be provided"
                )
        else:
            if self.error is None:
                raise ValueError(
                    "success=False requires error to be provided"
                )


# ── Internal helpers ──────────────────────────────────────────────────────


def _generate_meeting_id() -> str:
    """Generate a unique, sortable meeting ID.

    Format: ``meeting_YYYYMMDD_<short-uuid>``

    The date prefix enables chronological directory listings; the
    short UUID ensures global uniqueness.
    """
    date_part = datetime.now(UTC).strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:12]
    return f"meeting_{date_part}_{short_uuid}"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def _validate_request(request: MeetingCommandRequest) -> Optional[str]:
    """Validate that the request has all required fields.

    Returns:
        ``None`` when valid, or a human-readable error string explaining
        the first validation failure found (fail-fast).
    """
    # ── Required: agenda must be non-empty after stripping ────────────
    if not request.agenda or not request.agenda.strip():
        return (
            f"agenda must not be empty "
            f'(received: "{request.agenda}")'
        )

    # ── Required: user_id must be non-empty after stripping ───────────
    if not request.user_id or not request.user_id.strip():
        return (
            f"user_id must not be empty "
            f'(received: "{request.user_id}")'
        )

    # ── Required: channel_id must be non-empty after stripping ────────
    if not request.channel_id or not request.channel_id.strip():
        return (
            f"channel_id must not be empty "
            f'(received: "{request.channel_id}")'
        )

    # ── Required: priority must be p0–p3 ──────────────────────────────
    if request.priority not in _VALID_PRIORITIES:
        return (
            f"invalid priority '{request.priority}' — "
            f"must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
        )

    # ── Business rule: agenda length sanity check ─────────────────────
    if len(request.agenda.strip()) > 10_000:
        return (
            f"agenda exceeds maximum length of 10,000 characters "
            f"(received: {len(request.agenda.strip())})"
        )

    # ── Business rule: teams must reference known role IDs ────────────
    # (soft check — unknown teams produce a degraded but still valid session
    #  because the Qwen router may refine them later; we only reject
    #  clearly malformed inputs)
    for team_id in request.teams:
        if not team_id or not team_id.strip():
            return "teams contains an empty or whitespace-only entry"
        if " " in team_id.strip():
            return (
                f"team role ID must use kebab-case, found space in: "
                f"'{team_id}'"
            )

    # ── Business rule: suggested_roles must be valid identifiers ──────
    for role_id in request.suggested_roles:
        if not role_id or not role_id.strip():
            return (
                "suggested_roles contains an empty or "
                "whitespace-only entry"
            )
        if " " in role_id.strip():
            return (
                f"role ID must use kebab-case, found space in: "
                f"'{role_id}'"
            )

    return None


def _build_session(
    request: MeetingCommandRequest,
    meeting_id: str,
    config: MeetingConfig,
    now_iso: str,
) -> MeetingManifest:
    """Build a validated ``MeetingManifest`` from the request.

    All classification fields (tags, risk_tags, agenda_type) start
    empty — they are populated by the Qwen router during the routing
    phase (Sub-AC 3.1).

    The ``required_roles`` are seeded from ``request.teams`` (team-level
    selection) and ``optional_roles`` from ``request.suggested_roles``
    (role-level participant constraints).  These are refined by the
    routing system during the ready phase.

    Args:
        request: The validated command request.
        meeting_id: Generated unique meeting ID.
        config: System configuration (defaults source).
        now_iso: UTC timestamp in ISO 8601 format.

    Returns:
        An immutable ``MeetingManifest`` with ``state='created'``.
    """
    return MeetingManifest(
        # Identity
        meeting_id=meeting_id,
        # Lifecycle
        state="created",
        # Content
        priority=request.priority,
        agenda=request.agenda.strip(),
        agenda_type="",
        # Classification (populated later by Qwen router)
        tags=(),
        risk_tags=(),
        required_roles=request.teams,
        optional_roles=request.suggested_roles,
        # Progress
        round_count=0,
        # Validation
        validation_score=0.0,
        validation_verdict="",
        validator_required=True,
        codex_required=False,
        # Outcome
        consensus="",
        # Source
        user_id=request.user_id.strip(),
        channel_id=request.channel_id.strip(),
        thread_id=request.thread_id,
        guild_id=request.guild_id,
        # Errors
        error_log=(),
        # File system (manifest_path set by caller after directory creation)
        manifest_path="",
        meetings_root=str(config.meetings_root),
        # Configuration
        max_rounds=config.max_rounds,
        max_agents_per_meeting=config.max_agents_per_meeting,
        token_limit_worker=config.token_limit_worker,
        token_limit_validator=config.token_limit_validator,
        token_limit_codex=config.token_limit_codex,
        primary_validator_model=config.primary_validator_model,
        conditional_validator_model=config.conditional_validator_model,
        schema_version=config.manifest_schema_version,
        # Round / speaker state
        current_speaker="",
        speaker_queue=(),
        completed_step="",
        context_packets=(),
        decisions=(),
        tool_outputs=(),
        # Timestamps
        created_at=now_iso,
        updated_at=now_iso,
    )


# ── Public API ────────────────────────────────────────────────────────────


def create_meeting_session(
    request: MeetingCommandRequest,
    *,
    config: Optional[MeetingConfig] = None,
    meeting_id: Optional[str] = None,
) -> SessionFactoryResult:
    """Create a validated ``MeetingManifest`` from a ``MeetingCommandRequest``.

    This is the **single entry point** for Sub-AC 3.2.  It validates the
    request, applies defaults, and returns an immutable session record.
    No filesystem I/O is performed — the caller is responsible for
    persistence.

    Validation rules
    ----------------
    1. ``agenda`` must be non-empty after stripping (max 10k chars).
    2. ``user_id`` must be non-empty after stripping.
    3. ``channel_id`` must be non-empty after stripping.
    4. ``priority`` must be one of ``p0``, ``p1``, ``p2``, ``p3``.
    5. ``teams`` entries must be valid kebab-case identifiers (no spaces).
    6. ``suggested_roles`` entries must be valid kebab-case identifiers.

    Defaults applied
    ----------------
    - ``priority``: ``p2`` (when set on the request; the request itself
      defaults to p2, so this is typically already set).
    - ``state``: ``"created"``
    - ``round_count``: ``0``
    - ``validation_score``: ``0.0``
    - All classification fields (``tags``, ``risk_tags``, ``agenda_type``):
      empty — populated later by Qwen router.
    - Configuration parameters inherited from ``MeetingConfig``:
      ``max_rounds``, ``max_agents_per_meeting``, token limits, validator
      models, ``schema_version``.
    - Timestamps: current UTC time in ISO 8601.

    Business rules
    --------------
    - ``meeting_id`` is auto-generated (or provided for testability).
    - ``required_roles`` seeded from ``request.teams``.
    - ``optional_roles`` seeded from ``request.suggested_roles``.
    - ``validator_required`` always ``True`` on creation.
    - ``codex_required`` always ``False`` on creation (set later by routing).
    - ``manifest_path`` is empty — set by the caller after directory creation.

    Args:
        request: A validated ``MeetingCommandRequest`` with at minimum
                 ``agenda``, ``user_id``, and ``channel_id``.
        config: System-wide meeting configuration.  Uses defaults when
                ``None``.
        meeting_id: Pre-generated meeting ID for test determinism.
                    Auto-generated when ``None``.

    Returns:
        A ``SessionFactoryResult`` — inspect ``.success`` to branch;
        access ``.session`` when True, ``.error`` when False.

    Examples:
        Valid request — session created::

            >>> from src.meeting_trigger import MeetingCommandRequest
            >>> request = MeetingCommandRequest(
            ...     agenda="뮤직비디오 오프닝 아이디어 회의",
            ...     user_id="u1",
            ...     channel_id="c1",
            ... )
            >>> result = create_meeting_session(request)
            >>> result.success
            True
            >>> result.session.meeting_id  # doctest: +ELLIPSIS
            'meeting_...'
            >>> result.session.state
            'created'
            >>> result.session.priority
            'p2'

        Empty agenda rejected::

            >>> request = MeetingCommandRequest(
            ...     agenda="",
            ...     user_id="u1",
            ...     channel_id="c1",
            ... )
            >>> result = create_meeting_session(request)
            >>> result.success
            False
            >>> "agenda must not be empty" in result.error
            True

        Invalid priority rejected::

            >>> request = MeetingCommandRequest(
            ...     agenda="회의 주제",
            ...     user_id="u1",
            ...     channel_id="c1",
            ...     priority="p5",
            ... )
            >>> result = create_meeting_session(request)
            >>> result.success
            False
            >>> "invalid priority" in result.error
            True

        Custom config applied::

            >>> request = MeetingCommandRequest(
            ...     agenda="회의 주제",
            ...     user_id="u1",
            ...     channel_id="c1",
            ... )
            >>> custom_config = MeetingConfig(max_rounds=5)
            >>> result = create_meeting_session(request, config=custom_config)
            >>> result.session.max_rounds
            5

        Deterministic meeting_id for testing::

            >>> request = MeetingCommandRequest(
            ...     agenda="회의 주제",
            ...     user_id="u1",
            ...     channel_id="c1",
            ... )
            >>> result = create_meeting_session(
            ...     request,
            ...     meeting_id="meeting_20260611_aaaabbbbcccc",
            ... )
            >>> result.session.meeting_id
            'meeting_20260611_aaaabbbbcccc'
    """
    # ── Step 1: Validate input ────────────────────────────────────────
    error = _validate_request(request)
    if error is not None:
        return SessionFactoryResult(success=False, error=error)

    cfg = config if config is not None else MeetingConfig()

    # ── Step 2: Generate meeting ID ───────────────────────────────────
    _id = meeting_id if meeting_id is not None else _generate_meeting_id()
    now_iso = _utc_now_iso()

    # ── Step 3: Build the session record ──────────────────────────────
    session = _build_session(
        request=request,
        meeting_id=_id,
        config=cfg,
        now_iso=now_iso,
    )

    # ── Step 4: Return structured result ──────────────────────────────
    return SessionFactoryResult(success=True, session=session)


# ── Exports ───────────────────────────────────────────────────────────────

__all__ = [
    "SessionFactoryResult",
    "create_meeting_session",
]
