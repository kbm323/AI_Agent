"""Meeting creation trigger for the multi-agent meeting system.

Sub-AC 1c: Receives a validated command request (typically from the
Discord command pipeline) and initiates the meeting creation process.
Generates a unique meeting_id, creates an isolated meeting directory,
writes the initial manifest.json with all configuration parameters,
and returns a MeetingContext ready for the Coordinator to consume.

Every state transition is persisted to manifest.json *before* any
external call per the Seed constraint: "All state transitions persist
to manifest.json before external calls."

Modules:
    MeetingCommandRequest: Validated input from the command pipeline.
    MeetingConfig: System-wide configuration defaults.
    MeetingManifest: Full manifest JSON data structure (immutable).
    MeetingContext: Creation result with paths, state, and manifest.
    create_meeting: Main entry point for meeting creation.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.shared.lifecycle import LifecycleState

# ── Configuration constants ─────────────────────────────────────────────

DEFAULT_MEETINGS_ROOT = "meetings"
"""Default root directory for meeting storage."""

MAX_ROUNDS = 3
"""Maximum number of meeting rounds (plus 1 tie-break)."""

MAX_CONCURRENT_MEETINGS = 2
"""Maximum concurrent meetings system-wide."""

MAX_CONCURRENT_OPENCODE_CALLS = 4
"""Maximum concurrent opencode-go CLI calls."""

MAX_AGENTS_PER_MEETING = 7
"""Maximum agents (roles) per meeting."""

TOKEN_LIMIT_WORKER = 12_000
"""Token limit for normal worker context packets."""

TOKEN_LIMIT_VALIDATOR = 20_000
"""Token limit for validator context packets."""

TOKEN_LIMIT_CODEX = 30_000
"""Token limit for Codex GPT-5.5 context packets."""

PRIMARY_VALIDATOR_MODEL = "glm-5.1"
"""Primary validator LLM model."""

CONDITIONAL_VALIDATOR_MODEL = "gpt-5.5"
"""Conditional secondary validator (Codex GPT-5.5)."""

MANIFEST_SCHEMA_VERSION = "meeting-manifest.v1"
"""Schema version for the meeting manifest."""


# ── Input: validated command request ────────────────────────────────────

@dataclass(frozen=True)
class MeetingCommandRequest:
    """A validated command request from the Discord command pipeline.

    This is the input to the meeting creation trigger.  It has already
    passed command schema validation before reaching this module.

    Required fields match the Seed ontology concepts: user_id,
    channel_id, thread_id, agenda (the original user meeting topic).

    The priority field defaults to P2 but may be set higher by the
    command parser based on urgency keywords.

    .. versionchanged:: 2.2
       Added ``teams`` and ``suggested_roles`` fields to carry
       structured participant constraints from the intent parser
       to the meeting manifest.
    """

    agenda: str
    """Original user meeting topic and objectives."""

    user_id: str
    """Discord user ID who initiated the meeting."""

    channel_id: str
    """Discord channel ID where the command was issued."""

    priority: str = "p2"
    """Priority level: p0 (blocking), p1 (high), p2 (normal), p3 (low)."""

    thread_id: str = ""
    """Discord thread ID for the meeting space (created by Coordinator)."""

    guild_id: str = ""
    """Discord guild/server ID (optional, for multi-server deployments)."""

    teams: tuple[str, ...] = ()
    """Team-level selection: which teams the user wants to convene.
    Values are team-leader role-IDs (e.g. ``'art-director'``)."""

    suggested_roles: tuple[str, ...] = ()
    """Specific role-level participant constraints mentioned by the user.
    Values are role-IDs (e.g. ``'concept-artist'``, ``'backend-dev'``)."""


# ── System configuration ────────────────────────────────────────────────

@dataclass(frozen=True)
class MeetingConfig:
    """System-wide meeting configuration defaults.

    These are the Seed-level parameters that govern all meetings.
    Individual meetings may override some values via the manifest.
    """

    meetings_root: str = DEFAULT_MEETINGS_ROOT
    max_rounds: int = MAX_ROUNDS
    max_concurrent_meetings: int = MAX_CONCURRENT_MEETINGS
    max_concurrent_opencode_calls: int = MAX_CONCURRENT_OPENCODE_CALLS
    max_agents_per_meeting: int = MAX_AGENTS_PER_MEETING
    token_limit_worker: int = TOKEN_LIMIT_WORKER
    token_limit_validator: int = TOKEN_LIMIT_VALIDATOR
    token_limit_codex: int = TOKEN_LIMIT_CODEX
    primary_validator_model: str = PRIMARY_VALIDATOR_MODEL
    conditional_validator_model: str = CONDITIONAL_VALIDATOR_MODEL
    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION


# ── Meeting manifest ────────────────────────────────────────────────────

@dataclass(frozen=True)
class MeetingManifest:
    """Complete meeting manifest data structure.

    Represents the full manifest.json file.  Every field maps to a
    Seed ontology concept.  This dataclass is frozen — mutations
    create new instances for safe state transitions.

    Required ontology concepts (must be present):
        meeting_id, state, priority, agenda, agenda_type, tags,
        risk_tags, required_roles, optional_roles, round_count,
        validation_score, validation_verdict, consensus, error_log,
        manifest_path, user_id, channel_id, thread_id.
    """

    # ── Identity ──
    meeting_id: str
    """Unique meeting identifier."""

    # ── Lifecycle ──
    state: str = "created"
    """Current lifecycle state (one of LifecycleState values)."""

    # ── Content ──
    priority: str = "p2"
    """Priority level: p0 | p1 | p2 | p3."""

    agenda: str = ""
    """Original user meeting topic and objectives."""

    agenda_type: str = ""
    """Classified meeting type from Qwen router (populated later)."""

    # ── Classification (populated by Qwen router in ready phase) ──
    tags: tuple[str, ...] = ()
    """Topic tags for routing and knowledge retrieval."""

    risk_tags: tuple[str, ...] = ()
    """Risk-indicating tags triggering validation and escalation rules."""

    required_roles: tuple[str, ...] = ()
    """Role IDs that must participate for valid quorum."""

    optional_roles: tuple[str, ...] = ()
    """Role IDs that may participate if capacity allows."""

    # ── Progress ──
    round_count: int = 0
    """Number of completed rounds (max 3 plus 1 tie-break)."""

    # ── Validation ──
    validation_score: float = 0.0
    """Overall validation score 0.0 to 1.0."""

    validation_verdict: str = ""
    """Final verdict: pass | conditional_pass | revision_required |
    escalate | fail."""

    validator_required: bool = True
    """Whether GLM-5.1 validation is needed for this meeting."""

    codex_required: bool = False
    """Whether Codex GPT-5.5 dual-validation is needed."""

    # ── Outcome ──
    consensus: str = ""
    """Final consensus text or deadlock description."""

    # ── Source ──
    user_id: str = ""
    """Discord user ID who initiated meeting."""

    channel_id: str = ""
    """Discord channel ID."""

    thread_id: str = ""
    """Discord thread ID for meeting space."""

    guild_id: str = ""
    """Discord guild/server ID."""

    # ── Errors ──
    error_log: tuple[dict[str, str], ...] = ()
    """All errors, fallbacks, and degradation events with timestamps."""

    # ── File system ──
    manifest_path: str = ""
    """Filesystem path to this manifest.json (self-referential)."""

    meetings_root: str = DEFAULT_MEETINGS_ROOT
    """Root directory for all meetings."""

    # ── Configuration (inherited from MeetingConfig) ──
    max_rounds: int = MAX_ROUNDS
    max_agents_per_meeting: int = MAX_AGENTS_PER_MEETING
    token_limit_worker: int = TOKEN_LIMIT_WORKER
    token_limit_validator: int = TOKEN_LIMIT_VALIDATOR
    token_limit_codex: int = TOKEN_LIMIT_CODEX
    primary_validator_model: str = PRIMARY_VALIDATOR_MODEL
    conditional_validator_model: str = CONDITIONAL_VALIDATOR_MODEL
    schema_version: str = MANIFEST_SCHEMA_VERSION

    # ── Round / speaker state (Sub-AC 4.4.1: manifest serialization) ──
    current_speaker: str = ""
    """Currently speaking role_id (empty when no speaker is active)."""

    speaker_queue: tuple[str, ...] = ()
    """Ordered speaker queue for the current round."""

    completed_step: str = ""
    """Last completed lifecycle step — used for crash recovery resume."""

    context_packets: tuple[dict, ...] = ()
    """All context-packet entries, accumulated across rounds. Each entry is
    a dict with keys: round, role_id, model_provider, model_name, token_count,
    packet_path, created_at."""

    decisions: tuple[dict, ...] = ()
    """All meeting decisions, append-only. Each entry is a dict with keys:
    round, decision_id, role_id, content, superseded_by, created_at."""

    tool_outputs: tuple[dict, ...] = ()
    """All tool-use execution outputs, append-only. Each entry is a dict with
    keys: round, execution_id, action_type, role_id, status, output,
    risk_level, human_approved, created_at."""

    # ── Timestamps ──
    created_at: str = ""
    """ISO 8601 creation timestamp."""

    updated_at: str = ""
    """ISO 8601 last-update timestamp."""

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary.

        Converts tuples to lists for JSON serialization.  Values are
        written deterministically for byte-identical manifests given
        identical inputs.
        """
        return {
            # Identity
            "meeting_id": self.meeting_id,
            # Lifecycle
            "state": self.state,
            # Content
            "priority": self.priority,
            "agenda": self.agenda,
            "agenda_type": self.agenda_type,
            # Classification
            "tags": list(self.tags),
            "risk_tags": list(self.risk_tags),
            "required_roles": list(self.required_roles),
            "optional_roles": list(self.optional_roles),
            # Progress
            "round_count": self.round_count,
            # Validation
            "validation_score": self.validation_score,
            "validation_verdict": self.validation_verdict,
            "validator_required": self.validator_required,
            "codex_required": self.codex_required,
            # Outcome
            "consensus": self.consensus,
            # Source
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "guild_id": self.guild_id,
            # Errors
            "error_log": list(self.error_log),
            # File system
            "manifest_path": self.manifest_path,
            "meetings_root": self.meetings_root,
            # Configuration
            "max_rounds": self.max_rounds,
            "max_agents_per_meeting": self.max_agents_per_meeting,
            "token_limit_worker": self.token_limit_worker,
            "token_limit_validator": self.token_limit_validator,
            "token_limit_codex": self.token_limit_codex,
            "primary_validator_model": self.primary_validator_model,
            "conditional_validator_model": self.conditional_validator_model,
            "schema_version": self.schema_version,
            # Round / speaker state (Sub-AC 4.4.1)
            "current_speaker": self.current_speaker,
            "speaker_queue": list(self.speaker_queue),
            "completed_step": self.completed_step,
            "context_packets": list(self.context_packets),
            "decisions": list(self.decisions),
            "tool_outputs": list(self.tool_outputs),
            # Timestamps
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a pretty-printed JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def with_state(self, new_state: LifecycleState | str) -> MeetingManifest:
        """Return a new manifest with updated state and timestamp.

        The canonical way to transition lifecycle states.  Produces a
        new frozen instance — the original is unchanged.
        """
        state_str = str(new_state)
        return MeetingManifest(
            meeting_id=self.meeting_id,
            state=state_str,
            priority=self.priority,
            agenda=self.agenda,
            agenda_type=self.agenda_type,
            tags=self.tags,
            risk_tags=self.risk_tags,
            required_roles=self.required_roles,
            optional_roles=self.optional_roles,
            round_count=self.round_count,
            validation_score=self.validation_score,
            validation_verdict=self.validation_verdict,
            validator_required=self.validator_required,
            codex_required=self.codex_required,
            consensus=self.consensus,
            user_id=self.user_id,
            channel_id=self.channel_id,
            thread_id=self.thread_id,
            guild_id=self.guild_id,
            error_log=self.error_log,
            manifest_path=self.manifest_path,
            meetings_root=self.meetings_root,
            max_rounds=self.max_rounds,
            max_agents_per_meeting=self.max_agents_per_meeting,
            token_limit_worker=self.token_limit_worker,
            token_limit_validator=self.token_limit_validator,
            token_limit_codex=self.token_limit_codex,
            primary_validator_model=self.primary_validator_model,
            conditional_validator_model=self.conditional_validator_model,
            schema_version=self.schema_version,
            # Round / speaker state (Sub-AC 4.4.1)
            current_speaker=self.current_speaker,
            speaker_queue=self.speaker_queue,
            completed_step=str(self.state),  # mark previous step as completed
            context_packets=self.context_packets,
            decisions=self.decisions,
            tool_outputs=self.tool_outputs,
            created_at=self.created_at,
            updated_at=_utc_now_iso(),
        )

    def with_error(self, error: dict[str, str]) -> MeetingManifest:
        """Return a new manifest with an error appended to error_log."""
        new_errors = (*self.error_log, error)
        return MeetingManifest(
            meeting_id=self.meeting_id,
            state=self.state,
            priority=self.priority,
            agenda=self.agenda,
            agenda_type=self.agenda_type,
            tags=self.tags,
            risk_tags=self.risk_tags,
            required_roles=self.required_roles,
            optional_roles=self.optional_roles,
            round_count=self.round_count,
            validation_score=self.validation_score,
            validation_verdict=self.validation_verdict,
            validator_required=self.validator_required,
            codex_required=self.codex_required,
            consensus=self.consensus,
            user_id=self.user_id,
            channel_id=self.channel_id,
            thread_id=self.thread_id,
            guild_id=self.guild_id,
            error_log=new_errors,
            manifest_path=self.manifest_path,
            meetings_root=self.meetings_root,
            max_rounds=self.max_rounds,
            max_agents_per_meeting=self.max_agents_per_meeting,
            token_limit_worker=self.token_limit_worker,
            token_limit_validator=self.token_limit_validator,
            token_limit_codex=self.token_limit_codex,
            primary_validator_model=self.primary_validator_model,
            conditional_validator_model=self.conditional_validator_model,
            schema_version=self.schema_version,
            # Round / speaker state (Sub-AC 4.4.1)
            current_speaker=self.current_speaker,
            speaker_queue=self.speaker_queue,
            completed_step=self.completed_step,
            context_packets=self.context_packets,
            decisions=self.decisions,
            tool_outputs=self.tool_outputs,
            created_at=self.created_at,
            updated_at=_utc_now_iso(),
        )


# ── Meeting context (creation result) ───────────────────────────────────

@dataclass(frozen=True)
class MeetingContext:
    """The result of a successful meeting creation.

    Holds everything the Coordinator needs to begin the meeting
    pipeline: the meeting_id, filesystem paths, initial manifest,
    and a snapshot of the active configuration.
    """

    meeting_id: str
    """Unique meeting identifier."""

    manifest: MeetingManifest
    """Initial meeting manifest (state=created)."""

    meeting_dir: str
    """Absolute path to the isolated meeting directory."""

    manifest_path: str
    """Absolute path to the manifest.json file."""

    rounds_dir: str
    """Absolute path to the rounds/ subdirectory."""

    raw_outputs_dir: str
    """Absolute path to the raw_outputs/ subdirectory."""

    decisions_dir: str
    """Absolute path to the decisions/ subdirectory."""

    knowledge_dir: str
    """Absolute path to the knowledge/ subdirectory."""

    config: MeetingConfig
    """System configuration snapshot at creation time."""


# ── Internal helpers ────────────────────────────────────────────────────

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


def _resolve_meetings_root(
    meetings_root: str | Path | None,
) -> Path:
    """Resolve the meetings root directory to an absolute Path.

    Args:
        meetings_root: Explicit path, or None to use the default
                       relative to the current working directory.

    Returns:
        Absolute Path to the meetings root directory.
    """
    if meetings_root is not None:
        return Path(meetings_root).resolve()
    return Path.cwd() / DEFAULT_MEETINGS_ROOT


def _ensure_directory(path: Path, *, label: str = "directory") -> Path:
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


def _build_manifest(
    request: MeetingCommandRequest,
    meeting_id: str,
    manifest_path: str,
    config: MeetingConfig,
    now_iso: str,
) -> MeetingManifest:
    """Build the initial MeetingManifest from a validated request.

    All classification fields (tags, risk_tags, etc.) start empty —
    they are populated by the Qwen router during the ready phase.

    The ``required_roles`` are seeded from ``request.teams`` (team-level
    selection) and ``optional_roles`` from ``request.suggested_roles``
    (role-level participant constraints).  These are refined by the
    routing system during the ready phase.

    Args:
        request: The validated command request.
        meeting_id: Generated meeting ID.
        manifest_path: Absolute path to the manifest.json file.
        config: System configuration.
        now_iso: Current UTC timestamp in ISO 8601.

    Returns:
        A frozen MeetingManifest with state='created'.
    """
    return MeetingManifest(
        meeting_id=meeting_id,
        state=str(LifecycleState.CREATED),
        priority=request.priority,
        agenda=request.agenda,
        agenda_type="",
        tags=(),
        risk_tags=(),
        required_roles=request.teams,  # seeded from user team selection
        optional_roles=request.suggested_roles,  # seeded from user role mentions
        round_count=0,
        validation_score=0.0,
        validation_verdict="",
        validator_required=True,
        codex_required=False,
        consensus="",
        user_id=request.user_id,
        channel_id=request.channel_id,
        thread_id=request.thread_id,
        guild_id=request.guild_id,
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


# ── Public API ──────────────────────────────────────────────────────────

def create_meeting(
    request: MeetingCommandRequest,
    *,
    meetings_root: str | Path | None = None,
    config: MeetingConfig | None = None,
) -> MeetingContext:
    """Create a new meeting with an isolated directory and manifest.

    This is the primary entry point for Sub-AC 1c.  It receives a
    validated ``MeetingCommandRequest`` and produces a complete
    ``MeetingContext`` ready for the Coordinator to consume.

    **Process:**

    1. Generate a unique ``meeting_id``.
    2. Create the isolated meeting directory under ``meetings_root``.
    3. Create subdirectories: ``rounds/``, ``raw_outputs/``,
       ``decisions/``, ``knowledge/``.
    4. Build the initial ``MeetingManifest`` with ``state='created'``.
    5. Write ``manifest.json`` to disk (persist before any external
       call per Seed constraint).
    6. Return a ``MeetingContext`` with all paths and the manifest.

    **Directory structure created:**
    ::

        {meetings_root}/
          {meeting_id}/
            manifest.json
            rounds/
            raw_outputs/
            decisions/
            knowledge/

    Args:
        request: A validated ``MeetingCommandRequest`` with at minimum
                 ``agenda``, ``user_id``, and ``channel_id``.
        meetings_root: Root directory for meeting storage.  Defaults to
                       ``./meetings/`` relative to the current working
                       directory.
        config: System-wide meeting configuration.  Uses defaults when
                ``None``.

    Returns:
        A ``MeetingContext`` with the meeting ID, manifest, and all
        filesystem paths.

    Raises:
        ValueError: If ``request.agenda`` is empty or whitespace-only.
        ValueError: If ``request.user_id`` or ``request.channel_id``
                    is empty.
        OSError: If the meeting directory cannot be created.
        json.JSONEncodeError: If the manifest cannot be serialized
                              (should not happen in practice).

    Example:
        >>> from src.meeting_trigger import MeetingCommandRequest, create_meeting
        >>> req = MeetingCommandRequest(
        ...     agenda="신규 캐릭터 '루나'의 비주얼 디자인 회의",
        ...     user_id="discord_user_12345",
        ...     channel_id="discord_channel_67890",
        ...     priority="p1",
        ... )
        >>> ctx = create_meeting(req, meetings_root="/tmp/meetings")
        >>> ctx.meeting_id
        'meeting_20260610_a1b2c3d4e5f6'
        >>> ctx.manifest.state
        'created'
    """
    # ── Validate input ──
    if not request.agenda or not request.agenda.strip():
        raise ValueError("Meeting agenda must not be empty")
    if not request.user_id or not request.user_id.strip():
        raise ValueError("user_id must not be empty")
    if not request.channel_id or not request.channel_id.strip():
        raise ValueError("channel_id must not be empty")

    cfg = config if config is not None else MeetingConfig()

    # ── Generate meeting ID ──
    meeting_id = _generate_meeting_id()
    now_iso = _utc_now_iso()

    # ── Create directory structure ──
    root = _resolve_meetings_root(meetings_root)
    meeting_dir = _ensure_directory(root / meeting_id, label="meeting directory")

    rounds_dir = _ensure_directory(
        meeting_dir / "rounds", label="rounds directory"
    )
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
    manifest_path = str(meeting_dir / "manifest.json")
    manifest = _build_manifest(
        request=request,
        meeting_id=meeting_id,
        manifest_path=manifest_path,
        config=cfg,
        now_iso=now_iso,
    )

    # ── Persist manifest (before any external call) ──
    _write_manifest(manifest, manifest_path)

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
        config=cfg,
    )


def _write_manifest(manifest: MeetingManifest, path: str) -> None:
    """Write the manifest to disk atomically.

    Uses a temporary file + atomic rename to prevent partial writes
    from corrupting the manifest on crash.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(manifest.to_json())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def load_manifest(manifest_path: str) -> MeetingManifest:
    """Load and parse an existing manifest.json file.

    Args:
        manifest_path: Absolute path to the manifest.json file.

    Returns:
        A fully populated ``MeetingManifest``.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        KeyError: If required fields are missing (corrupted manifest).
    """
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    return MeetingManifest(
        meeting_id=data["meeting_id"],
        state=data.get("state", "created"),
        priority=data.get("priority", "p2"),
        agenda=data.get("agenda", ""),
        agenda_type=data.get("agenda_type", ""),
        tags=tuple(data.get("tags", ())),
        risk_tags=tuple(data.get("risk_tags", ())),
        required_roles=tuple(data.get("required_roles", ())),
        optional_roles=tuple(data.get("optional_roles", ())),
        round_count=data.get("round_count", 0),
        validation_score=data.get("validation_score", 0.0),
        validation_verdict=data.get("validation_verdict", ""),
        validator_required=data.get("validator_required", True),
        codex_required=data.get("codex_required", False),
        consensus=data.get("consensus", ""),
        user_id=data.get("user_id", ""),
        channel_id=data.get("channel_id", ""),
        thread_id=data.get("thread_id", ""),
        guild_id=data.get("guild_id", ""),
        error_log=tuple(data.get("error_log", ())),
        manifest_path=manifest_path,
        meetings_root=data.get("meetings_root", DEFAULT_MEETINGS_ROOT),
        max_rounds=data.get("max_rounds", MAX_ROUNDS),
        max_agents_per_meeting=data.get(
            "max_agents_per_meeting", MAX_AGENTS_PER_MEETING
        ),
        token_limit_worker=data.get(
            "token_limit_worker", TOKEN_LIMIT_WORKER
        ),
        token_limit_validator=data.get(
            "token_limit_validator", TOKEN_LIMIT_VALIDATOR
        ),
        token_limit_codex=data.get(
            "token_limit_codex", TOKEN_LIMIT_CODEX
        ),
        primary_validator_model=data.get(
            "primary_validator_model", PRIMARY_VALIDATOR_MODEL
        ),
        conditional_validator_model=data.get(
            "conditional_validator_model", CONDITIONAL_VALIDATOR_MODEL
        ),
        schema_version=data.get(
            "schema_version", MANIFEST_SCHEMA_VERSION
        ),
        # Round / speaker state (Sub-AC 4.4.1)
        current_speaker=data.get("current_speaker", ""),
        speaker_queue=tuple(data.get("speaker_queue", ())),
        completed_step=data.get("completed_step", ""),
        context_packets=tuple(data.get("context_packets", ())),
        decisions=tuple(data.get("decisions", ())),
        tool_outputs=tuple(data.get("tool_outputs", ())),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


def update_manifest(manifest: MeetingManifest) -> MeetingManifest:
    """Persist an updated manifest to disk and return the new instance.

    Writes the manifest to its ``manifest_path`` using atomic write,
    then returns a new frozen instance with ``updated_at`` refreshed.

    Args:
        manifest: Current manifest (will not be mutated).

    Returns:
        A new ``MeetingManifest`` with ``updated_at`` set to now.
    """
    now_iso = _utc_now_iso()
    updated = MeetingManifest(
        meeting_id=manifest.meeting_id,
        state=manifest.state,
        priority=manifest.priority,
        agenda=manifest.agenda,
        agenda_type=manifest.agenda_type,
        tags=manifest.tags,
        risk_tags=manifest.risk_tags,
        required_roles=manifest.required_roles,
        optional_roles=manifest.optional_roles,
        round_count=manifest.round_count,
        validation_score=manifest.validation_score,
        validation_verdict=manifest.validation_verdict,
        validator_required=manifest.validator_required,
        codex_required=manifest.codex_required,
        consensus=manifest.consensus,
        user_id=manifest.user_id,
        channel_id=manifest.channel_id,
        thread_id=manifest.thread_id,
        guild_id=manifest.guild_id,
        error_log=manifest.error_log,
        manifest_path=manifest.manifest_path,
        meetings_root=manifest.meetings_root,
        max_rounds=manifest.max_rounds,
        max_agents_per_meeting=manifest.max_agents_per_meeting,
        token_limit_worker=manifest.token_limit_worker,
        token_limit_validator=manifest.token_limit_validator,
        token_limit_codex=manifest.token_limit_codex,
        primary_validator_model=manifest.primary_validator_model,
        conditional_validator_model=manifest.conditional_validator_model,
        schema_version=manifest.schema_version,
        # Round / speaker state (Sub-AC 4.4.1)
        current_speaker=manifest.current_speaker,
        speaker_queue=manifest.speaker_queue,
        completed_step=manifest.completed_step,
        context_packets=manifest.context_packets,
        decisions=manifest.decisions,
        tool_outputs=manifest.tool_outputs,
        created_at=manifest.created_at,
        updated_at=now_iso,
    )
    _write_manifest(updated, updated.manifest_path)
    return updated
