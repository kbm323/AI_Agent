"""Partial-progress snapshot capture (Sub-AC 4.3.4).

Serializes the current meeting state — round, active workers, pending
actions, and context packets — into a structured snapshot at any
transition boundary.  Designed to be independently testable with mock
meeting states of varying complexity.

Architecture
------------

A **snapshot** is a point-in-time capture of the meeting's runtime
state.  Unlike the manifest (which accumulates append-only data), a
snapshot captures *transient* runtime information: which workers are
currently active (mid-LLM-call), which actions are pending in the
Coordinator's queue, and what the active context-packet map looks like.

Snapshots are written as ``snapshot_<N>.json`` in the meeting directory,
providing a chronological history of meeting progress that can be used
for:

- **Crash recovery**: the latest snapshot tells the Coordinator exactly
  where to resume (which workers to wait for, which actions to re-queue).
- **Progress monitoring**: external observers can read snapshot files
  to understand meeting progress without loading the full manifest.
- **Debugging**: each snapshot is a self-contained trace of the meeting
  state at that moment.

The snapshot is *in addition to* the manifest, not a replacement.  The
manifest remains the source of truth for persistent meeting data; the
snapshot captures the ephemeral runtime state.

Usage::

    from src.partial_progress_snapshot import (
        PartialProgressSnapshot,
        capture_snapshot,
        load_snapshot,
        find_latest_snapshot,
        SnapshotWorkerState,
        SnapshotPendingAction,
    )

    # Build a snapshot from current runtime state
    snapshot = capture_snapshot(
        manifest=manifest,
        active_workers=[
            SnapshotWorkerState(
                role_id="producer-kim",
                model_provider="qwen",
                model_name="qwen3-max",
                status="running",
                packet_path="rounds/round_1/producer-kim.json",
            ),
        ],
        pending_actions=[
            SnapshotPendingAction(
                action_type="next_speaker",
                target_role="director-lee",
                priority=1,
            ),
        ],
        transition_type="round_advance",
    )

    # Write to disk
    snapshot_path = snapshot.write(meeting_dir)

    # Later: find and load the latest snapshot
    path = find_latest_snapshot(meeting_dir)
    loaded = load_snapshot(path)

Modules:
    SnapshotWorkerState: Immutable record of an active worker.
    SnapshotPendingAction: Immutable record of a pending action.
    PartialProgressSnapshot: The complete snapshot dataclass.
    capture_snapshot: Build a snapshot from current state.
    load_snapshot: Load a snapshot from a JSON file.
    find_latest_snapshot: Find the most recent snapshot in a meeting dir.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.meeting_trigger import MeetingManifest

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

DEFAULT_SNAPSHOT_SCHEMA_VERSION = "snapshot.v1"
"""Schema version for snapshot files."""

# ── Timestamp helper ──────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _generate_snapshot_id() -> str:
    """Generate a unique, sortable snapshot ID."""
    import uuid

    date_part = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"snap_{date_part}_{short_uuid}"


# ── Worker state record ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotWorkerState:
    """Immutable record of an active worker in a meeting snapshot.

    Captures the transient state of a worker that is currently
    processing a context packet.  Once the worker completes, its
    output is recorded as a decision or tool output in the manifest;
    the worker itself is removed from future snapshot ``active_workers``.

    Attributes:
        role_id: Unique role identifier (kebab-case).
        model_provider: LLM provider name.
        model_name: Primary model identifier.
        status: Current status — 'pending' (queued but not yet dispatched),
                'running' (dispatched to opencode-go CLI),
                'completed' (response received),
                'failed' (error or timeout), or
                'timed_out' (rate-limit or timeout).
        packet_path: Relative path to the context-packet JSON file.
        opinion_summary: Short summary of the worker's opinion
                         (empty until status='completed').
        started_at: ISO 8601 timestamp when the worker was dispatched.
        error_message: Error message if status is 'failed' or 'timed_out'.
    """

    role_id: str
    model_provider: str = ""
    model_name: str = ""
    status: str = "pending"
    packet_path: str = ""
    opinion_summary: str = ""
    started_at: str = ""
    error_message: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "role_id": self.role_id,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "status": self.status,
            "packet_path": self.packet_path,
            "opinion_summary": self.opinion_summary,
            "started_at": self.started_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SnapshotWorkerState:
        """Deserialize from a dict."""
        return cls(
            role_id=data.get("role_id", ""),
            model_provider=data.get("model_provider", ""),
            model_name=data.get("model_name", ""),
            status=data.get("status", "pending"),
            packet_path=data.get("packet_path", ""),
            opinion_summary=data.get("opinion_summary", ""),
            started_at=data.get("started_at", ""),
            error_message=data.get("error_message", ""),
        )


# ── Pending action record ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotPendingAction:
    """Immutable record of a pending action in the Coordinator's queue.

    Captures what the Coordinator needs to do next.  Actions are
    ordered by ``priority`` (lower = more urgent).  An action may
    have ``dependencies`` — prerequisite action IDs that must
    complete before this action can be dispatched.

    Attributes:
        action_id: Unique identifier for this action within the snapshot.
        action_type: Type of action (next_speaker, validate, execute,
                     finalize, route, classify, retrieve_context).
        target_role: Role ID if the action targets a specific role.
        priority: Lower value = higher urgency (0 is highest).
        dependencies: List of ``action_id`` values that must complete
                      before this action can run.
        payload: Optional extra data for the action (e.g. routing rules,
                 validation parameters).
        created_at: ISO 8601 timestamp when the action was queued.
    """

    action_id: str
    action_type: str
    target_role: str = ""
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    payload: dict | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        """Auto-set created_at if not provided (frozen-safe via object.__setattr__)."""
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc_now_iso())

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target_role": self.target_role,
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SnapshotPendingAction:
        """Deserialize from a dict."""
        return cls(
            action_id=data.get("action_id", ""),
            action_type=data.get("action_type", ""),
            target_role=data.get("target_role", ""),
            priority=data.get("priority", 0),
            dependencies=tuple(data.get("dependencies", ())),
            payload=data.get("payload"),
            created_at=data.get("created_at", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Partial Progress Snapshot
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PartialProgressSnapshot:
    """Complete partial-progress snapshot for a meeting at a moment in time.

    This is the main data structure for Sub-AC 4.3.4.  It captures the
    full meeting state at a transition boundary — both persistent data
    (from the manifest) and transient runtime data (active workers,
    pending actions).

    Required AC fields:
        snapshot_id, meeting_id, created_at, state, round, active_workers,
        pending_actions, context_packets, decisions, tool_outputs,
        transition_type.

    Attributes:
        snapshot_id: Unique identifier for this snapshot.
        meeting_id: The meeting this snapshot belongs to.
        created_at: ISO 8601 timestamp when the snapshot was captured.
        state: Current lifecycle state of the meeting.
        round: Current round number (matches manifest.round_count).
        max_rounds: Maximum allowed rounds for this meeting.
        active_workers: Workers currently processing context packets.
        pending_actions: Actions queued for the Coordinator.
        context_packets: All context-packet entries accumulated so far.
        decisions: All decisions committed so far.
        tool_outputs: All tool-use outputs recorded so far.
        error_log: Error entries from the manifest.
        current_speaker: Currently active speaker role_id.
        speaker_queue: Ordered speaker queue for the current round.
        validation_score: Current validation score (0.0 to 1.0).
        validation_verdict: Current validation verdict.
        agenda: Meeting agenda text.
        agenda_type: Classified meeting type.
        required_roles: Role IDs required for quorum.
        optional_roles: Optional role IDs.
        priority: Meeting priority level (p0-p3).
        transition_type: What kind of transition triggered this snapshot
                         (matching transition_persistence_hook types).
        completed_step: Last completed lifecycle step.
        schema_version: Snapshot schema version for forward compatibility.
    """

    # Identity
    snapshot_id: str
    meeting_id: str

    # Timing
    created_at: str = ""

    # Core lifecycle
    state: str = ""
    round: int = 0
    max_rounds: int = 3

    # Active workers (transient runtime state)
    active_workers: tuple[SnapshotWorkerState, ...] = ()

    # Pending actions (transient runtime state)
    pending_actions: tuple[SnapshotPendingAction, ...] = ()

    # Accumulated data (from manifest)
    context_packets: tuple[dict, ...] = ()
    decisions: tuple[dict, ...] = ()
    tool_outputs: tuple[dict, ...] = ()
    error_log: tuple[dict, ...] = ()

    # Speaker state
    current_speaker: str = ""
    speaker_queue: tuple[str, ...] = ()

    # Validation
    validation_score: float = 0.0
    validation_verdict: str = ""

    # Meeting metadata
    agenda: str = ""
    agenda_type: str = ""
    required_roles: tuple[str, ...] = ()
    optional_roles: tuple[str, ...] = ()
    priority: str = "p2"

    # Transition metadata
    transition_type: str = ""
    completed_step: str = ""

    # Schema
    schema_version: str = DEFAULT_SNAPSHOT_SCHEMA_VERSION

    # ── Statistics ───────────────────────────────────────────────────────

    @property
    def active_worker_count(self) -> int:
        """Number of workers currently active (status='running')."""
        return sum(1 for w in self.active_workers if w.status == "running")

    @property
    def completed_worker_count(self) -> int:
        """Number of workers that have completed in this round."""
        return sum(1 for w in self.active_workers if w.status == "completed")

    @property
    def failed_worker_count(self) -> int:
        """Number of workers that failed or timed out."""
        return sum(
            1
            for w in self.active_workers
            if w.status in ("failed", "timed_out")
        )

    @property
    def pending_action_count(self) -> int:
        """Number of pending actions in the Coordinator queue."""
        return len(self.pending_actions)

    @property
    def pending_actions_ordered(self) -> tuple[SnapshotPendingAction, ...]:
        """Pending actions sorted by priority (lowest first)."""
        return tuple(sorted(self.pending_actions, key=lambda a: a.priority))

    @property
    def context_packet_count(self) -> int:
        """Total number of context-packet entries accumulated."""
        return len(self.context_packets)

    @property
    def decision_count(self) -> int:
        """Total number of decisions committed."""
        return len(self.decisions)

    @property
    def tool_output_count(self) -> int:
        """Total number of tool outputs recorded."""
        return len(self.tool_outputs)

    @property
    def error_count(self) -> int:
        """Total number of errors logged."""
        return len(self.error_log)

    @property
    def is_quorum_met(self) -> bool:
        """True if at least as many workers are running/completed as required roles."""
        return self.active_worker_count + self.completed_worker_count >= len(
            self.required_roles
        )

    @property
    def worker_summary(self) -> dict[str, int]:
        """Breakdown of worker statuses."""
        summary: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
        }
        for w in self.active_workers:
            status = w.status
            if status in summary:
                summary[status] += 1
        return summary

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "snapshot_id": self.snapshot_id,
            "meeting_id": self.meeting_id,
            "created_at": self.created_at or _utc_now_iso(),
            "state": self.state,
            "round": self.round,
            "max_rounds": self.max_rounds,
            "active_workers": [w.to_dict() for w in self.active_workers],
            "pending_actions": [a.to_dict() for a in self.pending_actions],
            "context_packets": list(self.context_packets),
            "decisions": list(self.decisions),
            "tool_outputs": list(self.tool_outputs),
            "error_log": list(self.error_log),
            "current_speaker": self.current_speaker,
            "speaker_queue": list(self.speaker_queue),
            "validation_score": self.validation_score,
            "validation_verdict": self.validation_verdict,
            "agenda": self.agenda,
            "agenda_type": self.agenda_type,
            "required_roles": list(self.required_roles),
            "optional_roles": list(self.optional_roles),
            "priority": self.priority,
            "transition_type": self.transition_type,
            "completed_step": self.completed_step,
            "schema_version": self.schema_version,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a pretty-printed JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def write(self, meeting_dir: str) -> str:
        """Write the snapshot to disk in the meeting directory.

        The file is named ``snapshot_<N>.json`` where N is
        auto-incremented based on existing snapshots in the directory.

        Args:
            meeting_dir: Absolute path to the meeting directory.

        Returns:
            Absolute path to the written snapshot file.

        Raises:
            OSError: If the directory cannot be created or written to.
            json.JSONEncodeError: If the snapshot cannot be serialized.
        """
        snapshots_dir = Path(meeting_dir) / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        # Determine next snapshot index
        existing = sorted(snapshots_dir.glob("snapshot_*.json"))
        next_index = len(existing) + 1
        filename = f"snapshot_{next_index:04d}.json"
        filepath = snapshots_dir / filename

        self._atomic_write(str(filepath))
        logger.info(
            "Snapshot %s written to %s (meeting_id=%s, state=%s, round=%d)",
            self.snapshot_id,
            filepath,
            self.meeting_id,
            self.state,
            self.round,
        )
        return str(filepath)

    def _atomic_write(self, path: str) -> None:
        """Write snapshot to disk atomically (tmp + rename)."""
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    @classmethod
    def from_dict(cls, data: dict) -> PartialProgressSnapshot:
        """Deserialize from a dict (e.g. loaded from JSON)."""
        return cls(
            snapshot_id=data.get("snapshot_id", ""),
            meeting_id=data.get("meeting_id", ""),
            created_at=data.get("created_at", ""),
            state=data.get("state", ""),
            round=data.get("round", 0),
            max_rounds=data.get("max_rounds", 3),
            active_workers=tuple(
                SnapshotWorkerState.from_dict(w)
                for w in data.get("active_workers", [])
            ),
            pending_actions=tuple(
                SnapshotPendingAction.from_dict(a)
                for a in data.get("pending_actions", [])
            ),
            context_packets=tuple(data.get("context_packets", ())),
            decisions=tuple(data.get("decisions", ())),
            tool_outputs=tuple(data.get("tool_outputs", ())),
            error_log=tuple(data.get("error_log", ())),
            current_speaker=data.get("current_speaker", ""),
            speaker_queue=tuple(data.get("speaker_queue", ())),
            validation_score=data.get("validation_score", 0.0),
            validation_verdict=data.get("validation_verdict", ""),
            agenda=data.get("agenda", ""),
            agenda_type=data.get("agenda_type", ""),
            required_roles=tuple(data.get("required_roles", ())),
            optional_roles=tuple(data.get("optional_roles", ())),
            priority=data.get("priority", "p2"),
            transition_type=data.get("transition_type", ""),
            completed_step=data.get("completed_step", ""),
            schema_version=data.get(
                "schema_version", DEFAULT_SNAPSHOT_SCHEMA_VERSION
            ),
        )

    @classmethod
    def from_json(cls, json_str: str) -> PartialProgressSnapshot:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(json_str))


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def capture_snapshot(
    manifest: MeetingManifest,
    *,
    active_workers: list[SnapshotWorkerState] | None = None,
    pending_actions: list[SnapshotPendingAction] | None = None,
    transition_type: str = "generic",
    snapshot_id: str | None = None,
) -> PartialProgressSnapshot:
    """Capture a partial-progress snapshot from the current meeting state.

    Builds a ``PartialProgressSnapshot`` by merging persistent manifest
    data with transient runtime state (active workers, pending actions).

    This is the primary entry point — call this at any transition boundary
    to capture the meeting's complete state at that moment.

    Args:
        manifest: Current meeting manifest (persistent state).
        active_workers: Currently active worker states.  Defaults to
                        an empty list.
        pending_actions: Pending Coordinator actions.  Defaults to
                         an empty list.
        transition_type: What triggered this snapshot (e.g.
                         'state_change', 'round_advance', 'context_packet',
                         'speaker_change', 'decision_commit', 'tool_output').
        snapshot_id: Explicit snapshot ID (auto-generated if omitted).

    Returns:
        A complete ``PartialProgressSnapshot`` ready for inspection or
        persistence via ``write()``.

    Example:
        >>> from src.meeting_trigger import create_meeting, MeetingCommandRequest
        >>> from src.partial_progress_snapshot import (
        ...     capture_snapshot, SnapshotWorkerState, SnapshotPendingAction,
        ... )
        >>> req = MeetingCommandRequest(
        ...     agenda="Test", user_id="u1", channel_id="c1",
        ... )
        >>> ctx = create_meeting(req, meetings_root="/tmp/test-snap")
        >>> snapshot = capture_snapshot(
        ...     manifest=ctx.manifest,
        ...     active_workers=[
        ...         SnapshotWorkerState(
        ...             role_id="producer-kim",
        ...             status="running",
        ...         ),
        ...     ],
        ...     pending_actions=[
        ...         SnapshotPendingAction(
        ...             action_id="a1",
        ...             action_type="next_speaker",
        ...             target_role="director-lee",
        ...             priority=1,
        ...         ),
        ...     ],
        ...     transition_type="round_advance",
        ... )
        >>> snapshot.meeting_id == ctx.meeting_id
        True
        >>> snapshot.state
        'created'
        >>> len(snapshot.active_workers)
        1
    """
    workers_tuple = tuple(active_workers or ())
    actions_tuple = tuple(pending_actions or ())

    return PartialProgressSnapshot(
        snapshot_id=snapshot_id or _generate_snapshot_id(),
        meeting_id=manifest.meeting_id,
        created_at=_utc_now_iso(),
        state=manifest.state,
        round=manifest.round_count,
        max_rounds=manifest.max_rounds,
        active_workers=workers_tuple,
        pending_actions=actions_tuple,
        context_packets=manifest.context_packets,
        decisions=manifest.decisions,
        tool_outputs=manifest.tool_outputs,
        error_log=manifest.error_log,
        current_speaker=manifest.current_speaker,
        speaker_queue=manifest.speaker_queue,
        validation_score=manifest.validation_score,
        validation_verdict=manifest.validation_verdict,
        agenda=manifest.agenda,
        agenda_type=manifest.agenda_type,
        required_roles=manifest.required_roles,
        optional_roles=manifest.optional_roles,
        priority=manifest.priority,
        transition_type=transition_type,
        completed_step=manifest.completed_step,
    )


def load_snapshot(snapshot_path: str) -> PartialProgressSnapshot:
    """Load a partial-progress snapshot from a JSON file.

    Args:
        snapshot_path: Absolute path to a snapshot JSON file.

    Returns:
        A fully populated ``PartialProgressSnapshot``.

    Raises:
        FileNotFoundError: If the snapshot file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(snapshot_path, encoding="utf-8") as f:
        data = json.load(f)
    return PartialProgressSnapshot.from_dict(data)


def find_latest_snapshot(
    meeting_dir: str,
) -> Optional[str]:
    """Find the most recent snapshot file in a meeting directory.

    Returns the path to the snapshot with the highest sequence number,
    or ``None`` if no snapshots exist.

    Args:
        meeting_dir: Absolute path to the meeting directory.

    Returns:
        Absolute path to the latest snapshot file, or None.
    """
    snapshots_dir = Path(meeting_dir) / "snapshots"
    if not snapshots_dir.is_dir():
        return None

    existing = sorted(snapshots_dir.glob("snapshot_*.json"))
    if not existing:
        return None

    return str(existing[-1])


def list_snapshots(meeting_dir: str) -> list[str]:
    """Return all snapshot paths in a meeting directory, chronologically.

    Args:
        meeting_dir: Absolute path to the meeting directory.

    Returns:
        List of absolute paths, sorted by snapshot sequence number.
    """
    snapshots_dir = Path(meeting_dir) / "snapshots"
    if not snapshots_dir.is_dir():
        return []

    return sorted(str(p) for p in snapshots_dir.glob("snapshot_*.json"))


def snapshot_count(meeting_dir: str) -> int:
    """Return the number of snapshots in a meeting directory.

    Args:
        meeting_dir: Absolute path to the meeting directory.

    Returns:
        Count of snapshot files.
    """
    snapshots_dir = Path(meeting_dir) / "snapshots"
    if not snapshots_dir.is_dir():
        return 0

    return len(list(snapshots_dir.glob("snapshot_*.json")))
