"""Initiation orchestrator — Sub-AC 3.3.

Coordinates trigger parsing, session creation, routing, and persistence
into a single idempotent initiation workflow.  Returns a confirmed
session record (``MeetingContext``) or a structured error.

Pipeline
--------
::

    Raw trigger payload (any source)
        → trigger_input_parser.parse_meeting_request()   [Sub-AC 3.1]
        → MeetingRequest
        → MeetingRequest.to_command_request()
        → MeetingCommandRequest
        → meeting_session_factory.create_meeting_session() [Sub-AC 3.2]
        → MeetingManifest (state='created', in-memory)
        → trigger_router.route_trigger()                   [Sub-AC 2.3]
        → TriggerRoute (validated, action=initiate)
        → _persist_meeting()                               [this module]
        → MeetingContext (persisted manifest + directories)

Idempotency
-----------
If a meeting with the same ``meeting_id`` already exists on disk (manifest
found at the expected path), the orchestrator loads the existing manifest
and returns it as a confirmed session record rather than creating a
duplicate.  This makes the workflow safe for retry-after-crash scenarios.

The idempotency key is the ``meeting_id``.  When the caller provides
``meeting_id`` (e.g. from a deterministic seed or a crash-recovery token),
the orchestrator checks for an existing manifest before creating anything.

Design
------
- **Single entry point** — ``initiate_meeting()`` receives a raw trigger
  payload and a source type, then produces a ``InitiationResult``.
- **Error-as-value** — every path returns a structured result.  No
  exceptions for validation failures.
- **Persist-before-external-call** — the manifest is written to disk
  before any external system call (per Seed constraint).
- **Self-contained persistence** — the module handles directory creation
  and manifest writing directly rather than delegating to
  ``create_meeting()``, which generates its own meeting_id internally.
  This ensures the idempotency key matches what goes to disk.
- **Follows project patterns** — ``InitiationResult`` mirrors
  ``DispatchResult`` / ``SessionFactoryResult`` conventions.
"""

from __future__ import annotations

import json as _json
import os as _os
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC as _UTC, datetime as _datetime
from pathlib import Path as _Path
from typing import Optional as _Optional

from src.trigger_input_parser import (
    MeetingRequest,
    ParseResult,
    TriggerSource,
    parse_meeting_request,
)
from src.meeting_session_factory import (
    SessionFactoryResult,
    create_meeting_session,
)
from src.trigger_router import (
    ParsedTriggerInput,
    TriggerAction,
    TriggerRoutingResult,
    route_trigger,
)
from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    MeetingManifest,
    load_manifest,
)
from src.shared.lifecycle import LifecycleState

# ── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InitiationResult:
    """Result of an ``initiate_meeting()`` call.

    When ``success`` is True:
      - ``context`` carries the ``MeetingContext`` (persisted session).
      - ``idempotent`` indicates whether the session already existed
        (True = existing manifest loaded, False = new session created).

    When ``success`` is False:
      - ``error`` holds a human-readable description.
      - ``error_code`` holds a machine-readable code for programmatic
        handling.
      - ``phase`` identifies which pipeline phase failed (parse, factory,
        route, persist).
    """

    success: bool
    """True when initiation succeeded (new or existing session)."""

    context: _Optional[MeetingContext] = None
    """The confirmed meeting context (success only)."""

    idempotent: bool = False
    """True when the session was *already* persisted (existing manifest loaded)."""

    error: str = ""
    """Human-readable error description (failure only)."""

    error_code: str = ""
    """Machine-readable error code (failure only)."""

    phase: str = ""
    """Pipeline phase where the failure occurred (failure only)."""

    def __post_init__(self) -> None:
        """Enforce success/context/error invariants at construction."""
        if self.success:
            if self.context is None:
                raise ValueError(
                    "success=True requires context to be provided"
                )
        else:
            if not self.error:
                raise ValueError(
                    "success=False requires error to be provided"
                )


# ── Error codes ────────────────────────────────────────────────────────────


class InitiationErrorCode:
    """Well-known error codes for programmatic handling."""

    PARSE_FAILED = "PARSE_FAILED"
    """Trigger payload parsing failed (Sub-AC 3.1)."""

    FACTORY_FAILED = "FACTORY_FAILED"
    """Session factory validation failed (Sub-AC 3.2)."""

    ROUTE_FAILED = "ROUTE_FAILED"
    """Trigger routing rejected the action (Sub-AC 2.3)."""

    NOT_INITIATE_ACTION = "NOT_INITIATE_ACTION"
    """The routed action is not INITIATE (e.g. join/cancel/status)."""

    PERSIST_FAILED = "PERSIST_FAILED"
    """Manifest persistence or directory creation failed."""

    MANIFEST_CORRUPTED = "MANIFEST_CORRUPTED"
    """Existing manifest could not be loaded (idempotent check)."""

    DISK_ERROR = "DISK_ERROR"
    """Filesystem error during directory creation or manifest write."""


# ── Public API ─────────────────────────────────────────────────────────────


def initiate_meeting(
    payload: dict[str, object],
    *,
    source: TriggerSource,
    meetings_root: _Optional[str] = None,
    config: _Optional[MeetingConfig] = None,
    meeting_id: _Optional[str] = None,
    **overrides: object,
) -> InitiationResult:
    """Coordinate trigger parsing, session creation, and persistence.

    This is the **single entry point** for Sub-AC 3.3.  It receives a raw
    trigger payload from any supported source, parses and validates it,
    creates a session record, routes to the initiate workflow, and persists
    the manifest to disk with an isolated directory structure.

    Pipeline
    --------
    1. **Parse** the raw trigger payload via ``parse_meeting_request``
       (Sub-AC 3.1) → ``MeetingRequest``
    2. **Idempotency check** — if ``meeting_id`` is provided, check
       whether a manifest already exists at the expected path.  If so,
       load and return the existing session.
    3. **Convert** ``MeetingRequest`` to ``MeetingCommandRequest``.
    4. **Create session** via ``create_meeting_session`` (Sub-AC 3.2)
       → ``MeetingManifest`` (state='created', in-memory).
    5. **Route** the trigger via ``route_trigger`` (Sub-AC 2.3) to
       validate that this is an INITIATE action.
    6. **Persist** — create isolated directory structure, build the
       manifest, write manifest.json atomically, return MeetingContext.
    7. **Return** a confirmed ``InitiationResult`` with the
       ``MeetingContext``.

    Idempotency
    -----------
    When ``meeting_id`` is provided, the orchestrator checks if a manifest
    already exists at ``{meetings_root}/{meeting_id}/manifest.json``.  If
    it does, the existing manifest is loaded and returned with
    ``idempotent=True``.  If the existing manifest is corrupted, a
    ``MANIFEST_CORRUPTED`` error is returned.

    When ``meeting_id`` is **not** provided, a new unique ID is
    auto-generated — idempotency relies on the caller repeating the exact
    same ``meeting_id`` on retry.

    Args:
        payload: Raw trigger payload (structure varies by source).
        source: Trigger source type (discord_slash, discord_mention,
                webhook, direct_api).
        meetings_root: Optional root directory for meeting storage.
                       Defaults to ``./meetings/``.
        config: Optional ``MeetingConfig`` overrides.
        meeting_id: Pre-generated meeting ID for deterministic testing
                    and idempotent retry.  Auto-generated when ``None``.
        **overrides: Field overrides passed through to the trigger input
                     parser (agenda, user_id, channel_id, etc.).

    Returns:
        ``InitiationResult`` — inspect ``.success`` to branch; access
        ``.context`` when True, ``.error`` / ``.error_code`` / ``.phase``
        when False.

    Examples:
        Successful initiation::

            >>> from src.trigger_input_parser import TriggerSource
            >>> result = initiate_meeting(
            ...     payload={
            ...         "agenda": "신규 캐릭터 디자인 검토",
            ...         "user_id": "u1",
            ...         "channel_id": "c1",
            ...         "priority": "p1",
            ...     },
            ...     source=TriggerSource.DIRECT_API,
            ...     meetings_root="/tmp/test-init-orch",
            ... )
            >>> result.success
            True
            >>> result.context is not None
            True
            >>> result.idempotent
            False

        Empty agenda rejected::

            >>> result = initiate_meeting(
            ...     payload={"agenda": "", "user_id": "u1", "channel_id": "c1"},
            ...     source=TriggerSource.DIRECT_API,
            ... )
            >>> result.success
            False
            >>> result.error_code
            'PARSE_FAILED'

        Idempotent retry::

            >>> result1 = initiate_meeting(
            ...     payload={"agenda": "test", "user_id": "u1", "channel_id": "c1"},
            ...     source=TriggerSource.DIRECT_API,
            ...     meetings_root="/tmp/test-idem",
            ...     meeting_id="meeting_20260611_idem00001",
            ... )
            >>> result1.success
            True
            >>> result1.idempotent
            False
            >>> result2 = initiate_meeting(
            ...     payload={"agenda": "test", "user_id": "u1", "channel_id": "c1"},
            ...     source=TriggerSource.DIRECT_API,
            ...     meetings_root="/tmp/test-idem",
            ...     meeting_id="meeting_20260611_idem00001",
            ... )
            >>> result2.success
            True
            >>> result2.idempotent
            True
            >>> result2.context.meeting_id
            'meeting_20260611_idem00001'
    """
    # ── Phase 1: Parse the trigger payload ─────────────────────────────
    parse_result: ParseResult = parse_meeting_request(
        payload,
        source=source,
        **overrides,
    )

    if not parse_result.success:
        return InitiationResult(
            success=False,
            error=parse_result.error or "Unknown parse error",
            error_code=InitiationErrorCode.PARSE_FAILED,
            phase="parse",
        )

    request = parse_result.request
    assert request is not None  # guaranteed by success=True invariant

    # ── Phase 1b: Resolve meeting root and config ──────────────────────
    root = _resolve_meetings_root(meetings_root, config)
    cfg = config if config is not None else MeetingConfig()

    # Resolve the effective meeting root to an absolute path and update
    # config so downstream consumers see the canonical root.
    cfg = MeetingConfig(
        meetings_root=str(root),
        max_rounds=cfg.max_rounds,
        max_concurrent_meetings=cfg.max_concurrent_meetings,
        max_concurrent_opencode_calls=cfg.max_concurrent_opencode_calls,
        max_agents_per_meeting=cfg.max_agents_per_meeting,
        token_limit_worker=cfg.token_limit_worker,
        token_limit_validator=cfg.token_limit_validator,
        token_limit_codex=cfg.token_limit_codex,
        primary_validator_model=cfg.primary_validator_model,
        conditional_validator_model=cfg.conditional_validator_model,
        manifest_schema_version=cfg.manifest_schema_version,
    )

    # ── Phase 2: Resolve or generate meeting_id ────────────────────────
    _id = meeting_id if meeting_id is not None else _generate_meeting_id()
    manifest_path = str(root / _id / "manifest.json")

    # ── Phase 3: Idempotency check ─────────────────────────────────────
    if _os.path.exists(manifest_path):
        return _load_existing_session(manifest_path, root, _id, cfg)

    # ── Phase 4: Convert MeetingRequest → MeetingCommandRequest ────────
    cmd_request = request.to_command_request()

    # ── Phase 5: Create session record (in-memory) ─────────────────────
    factory_result: SessionFactoryResult = create_meeting_session(
        cmd_request,
        config=cfg,
        meeting_id=_id,
    )

    if not factory_result.success:
        return InitiationResult(
            success=False,
            error=factory_result.error or "Session factory validation failed",
            error_code=InitiationErrorCode.FACTORY_FAILED,
            phase="factory",
        )

    session = factory_result.session
    assert session is not None

    # ── Phase 6: Route the trigger ─────────────────────────────────────
    routing_result: TriggerRoutingResult = route_trigger(
        ParsedTriggerInput(
            topic=request.agenda,
            meeting_type=request.meeting_type,
            priority=request.priority,
            participants=request.participants,
            teams=request.teams,
            suggested_roles=request.suggested_roles,
            is_meeting=True,
        ),
    )

    if not routing_result.success:
        return InitiationResult(
            success=False,
            error=routing_result.error or "Trigger routing rejected",
            error_code=InitiationErrorCode.ROUTE_FAILED,
            phase="route",
        )

    route = routing_result.route
    assert route is not None

    # ── Phase 6b: Verify action is INITIATE ────────────────────────────
    if route.action != TriggerAction.INITIATE:
        return InitiationResult(
            success=False,
            error=(
                f"Initiation orchestrator only handles INITIATE actions, "
                f"got {route.action.value}"
            ),
            error_code=InitiationErrorCode.NOT_INITIATE_ACTION,
            phase="route",
        )

    # ── Phase 7: Persist — directories + manifest ──────────────────────
    try:
        context = _persist_meeting(
            cmd_request=cmd_request,
            meeting_id=_id,
            manifest_path=manifest_path,
            root=root,
            config=cfg,
        )
    except OSError as exc:
        return InitiationResult(
            success=False,
            error=f"Filesystem error during persistence: {exc}",
            error_code=InitiationErrorCode.DISK_ERROR,
            phase="persist",
        )
    except Exception as exc:
        return InitiationResult(
            success=False,
            error=f"Persistence failed: {type(exc).__name__}: {exc}",
            error_code=InitiationErrorCode.PERSIST_FAILED,
            phase="persist",
        )

    # ── Phase 8: Return confirmed session ──────────────────────────────
    return InitiationResult(
        success=True,
        context=context,
        idempotent=False,
    )


# ── Idempotency helpers ────────────────────────────────────────────────────


def _load_existing_session(
    manifest_path: str,
    root: _Path,
    meeting_id: str,
    config: MeetingConfig,
) -> InitiationResult:
    """Load an existing manifest and return as an idempotent success.

    The sub-directories are re-created if missing (possible after partial
    cleanup) to ensure the returned ``MeetingContext`` is fully usable.
    """
    try:
        existing_manifest = load_manifest(manifest_path)
    except Exception as exc:
        return InitiationResult(
            success=False,
            error=(
                f"Existing manifest at {manifest_path} could not be "
                f"loaded: {type(exc).__name__}: {exc}"
            ),
            error_code=InitiationErrorCode.MANIFEST_CORRUPTED,
            phase="idempotent_check",
        )

    meeting_dir = root / meeting_id
    try:
        context = _build_context_from_manifest(
            manifest=existing_manifest,
            meeting_dir=str(meeting_dir),
            manifest_path=manifest_path,
            config=config,
        )
    except OSError as exc:
        return InitiationResult(
            success=False,
            error=f"Filesystem error rebuilding context: {exc}",
            error_code=InitiationErrorCode.DISK_ERROR,
            phase="idempotent_check",
        )

    return InitiationResult(
        success=True,
        context=context,
        idempotent=True,
    )


# ── Persistence ────────────────────────────────────────────────────────────


def _persist_meeting(
    *,
    cmd_request: MeetingCommandRequest,
    meeting_id: str,
    manifest_path: str,
    root: _Path,
    config: MeetingConfig,
) -> MeetingContext:
    """Create directory structure, build manifest, and persist to disk.

    This is the self-contained persistence layer for the initiation
    orchestrator.  It does NOT call ``create_meeting()`` because that
    function generates its own meeting_id internally, which would break
    our idempotency key.

    Steps:
    1. Create the isolated meeting directory + subdirectories.
    2. Build the initial ``MeetingManifest`` (state='created').
    3. Atomically write ``manifest.json``.
    4. Return a ``MeetingContext``.

    Raises:
        OSError: On directory creation or manifest write failures.
    """
    now_iso = _utc_now_iso()

    # ── Create directory structure ──
    meeting_dir = _ensure_directory(root / meeting_id, label="meeting directory")
    rounds_dir = _ensure_directory(meeting_dir / "rounds", label="rounds directory")
    raw_outputs_dir = _ensure_directory(
        meeting_dir / "raw_outputs", label="raw_outputs directory"
    )
    decisions_dir = _ensure_directory(
        meeting_dir / "decisions", label="decisions directory"
    )
    knowledge_dir = _ensure_directory(
        meeting_dir / "knowledge", label="knowledge directory"
    )

    # ── Build manifest ──
    manifest = MeetingManifest(
        meeting_id=meeting_id,
        state=str(LifecycleState.CREATED),
        priority=cmd_request.priority,
        agenda=cmd_request.agenda,
        agenda_type="",
        tags=(),
        risk_tags=(),
        required_roles=cmd_request.teams,
        optional_roles=cmd_request.suggested_roles,
        round_count=0,
        validation_score=0.0,
        validation_verdict="",
        validator_required=True,
        codex_required=False,
        consensus="",
        user_id=cmd_request.user_id,
        channel_id=cmd_request.channel_id,
        thread_id=cmd_request.thread_id,
        guild_id=cmd_request.guild_id,
        error_log=(),
        manifest_path=manifest_path,
        meetings_root=str(config.meetings_root),
        max_rounds=config.max_rounds,
        max_agents_per_meeting=config.max_agents_per_meeting,
        token_limit_worker=config.token_limit_worker,
        token_limit_validator=config.token_limit_validator,
        token_limit_codex=config.token_limit_codex,
        primary_validator_model=config.primary_validator_model,
        conditional_validator_model=config.conditional_validator_model,
        schema_version=config.manifest_schema_version,
        created_at=now_iso,
        updated_at=now_iso,
    )

    # ── Persist manifest atomically (before any external call) ──
    _write_manifest_atomic(manifest, manifest_path)

    # ── Return context ──
    return MeetingContext(
        meeting_id=meeting_id,
        manifest=manifest,
        meeting_dir=str(meeting_dir),
        manifest_path=manifest_path,
        rounds_dir=str(rounds_dir),
        raw_outputs_dir=str(raw_outputs_dir),
        decisions_dir=str(decisions_dir),
        knowledge_dir=str(knowledge_dir),
        config=config,
    )


# ── Filesystem helpers ─────────────────────────────────────────────────────


def _resolve_meetings_root(
    meetings_root: _Optional[str],
    config: _Optional[MeetingConfig],
) -> _Path:
    """Resolve the meetings root directory to an absolute Path.

    Priority:
    1. Explicit ``meetings_root`` argument.
    2. ``config.meetings_root`` (when config is provided).
    3. Default: ``<cwd>/meetings/``.
    """
    if meetings_root is not None:
        return _Path(meetings_root).resolve()
    if config is not None:
        return _Path(config.meetings_root).resolve()
    return _Path.cwd() / "meetings"


def _generate_meeting_id() -> str:
    """Generate a unique, sortable meeting ID.

    Format: ``meeting_YYYYMMDD_<short-uuid>``
    """
    date_part = _datetime.now(_UTC).strftime("%Y%m%d")
    short_uuid = _uuid.uuid4().hex[:12]
    return f"meeting_{date_part}_{short_uuid}"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return _datetime.now(_UTC).isoformat()


def _ensure_directory(path: _Path, *, label: str = "directory") -> _Path:
    """Create a directory if it does not exist.

    Args:
        path: Directory path to create.
        label: Human-readable label for error messages.

    Returns:
        The resolved Path.

    Raises:
        OSError: If the path exists but is not a directory, or if
                 creation fails for permission/disk reasons.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise NotADirectoryError(
            f"{label} path exists but is not a directory: {path}"
        ) from None
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot create {label}: permission denied: {path}"
        ) from exc
    except OSError as exc:
        raise OSError(
            f"Failed to create {label}: {path} — {exc}"
        ) from exc
    return path


def _write_manifest_atomic(manifest: MeetingManifest, path: str) -> None:
    """Write the manifest to disk atomically.

    Uses a temporary file + atomic rename to prevent partial writes
    from corrupting the manifest on crash.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(manifest.to_json())
        f.flush()
        _os.fsync(f.fileno())
    _os.replace(tmp_path, path)


def _build_context_from_manifest(
    manifest: MeetingManifest,
    meeting_dir: str,
    manifest_path: str,
    config: MeetingConfig,
) -> MeetingContext:
    """Build a ``MeetingContext`` from a loaded manifest for idempotent retry.

    The sub-directories are expected to already exist on disk —
    ``_persist_meeting()`` created them during the initial invocation.
    If they do not exist (possible after partial cleanup), they are
    re-created.
    """
    _meeting_dir = _Path(meeting_dir)

    def _ensure(path: _Path) -> str:
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    return MeetingContext(
        meeting_id=manifest.meeting_id,
        manifest=manifest,
        meeting_dir=meeting_dir,
        manifest_path=manifest_path,
        rounds_dir=_ensure(_meeting_dir / "rounds"),
        raw_outputs_dir=_ensure(_meeting_dir / "raw_outputs"),
        decisions_dir=_ensure(_meeting_dir / "decisions"),
        knowledge_dir=_ensure(_meeting_dir / "knowledge"),
        config=config,
    )


# ── Exports ────────────────────────────────────────────────────────────────

__all__ = [
    "InitiationResult",
    "InitiationErrorCode",
    "initiate_meeting",
]
