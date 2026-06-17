"""Tests for Partial Progress Snapshot (Sub-AC 4.3.4).

Verifies:
- SnapshotWorkerState construction and serialization
- SnapshotPendingAction construction and serialization
- PartialProgressSnapshot construction from mock states
- Snapshot captures manifest-derived fields correctly
- Active worker state tracking (pending/running/completed/failed)
- Pending action dependency ordering
- File-based persistence: write + load round-trip
- find_latest_snapshot with multiple snapshots
- Snapshot statistics (counts, quorum, summaries)
- Varying complexity: 0 workers, many workers, many pending actions
- Edge cases: empty state, missing optional fields, max rounds
- Transition type recording
- JSON round-trip fidelity
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingManifest,
    create_meeting,
)
from src.partial_progress_snapshot import (
    DEFAULT_SNAPSHOT_SCHEMA_VERSION,
    PartialProgressSnapshot,
    SnapshotPendingAction,
    SnapshotWorkerState,
    capture_snapshot,
    find_latest_snapshot,
    list_snapshots,
    load_snapshot,
    snapshot_count,
)
from src.shared.lifecycle import LifecycleState
from src.transition_engine import execute_transition

# ── Test constants ─────────────────────────────────────────────────────────

_VALID_AGENDA = "신규 캐릭터 '루나'의 비주얼 디자인 회의"
_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_request(**overrides: str) -> MeetingCommandRequest:
    defaults = {
        "agenda": _VALID_AGENDA,
        "user_id": _VALID_USER_ID,
        "channel_id": _VALID_CHANNEL_ID,
        "priority": "p1",
    }
    defaults.update(overrides)
    return MeetingCommandRequest(**defaults)


def _tmp_meetings_root() -> str:
    return tempfile.mkdtemp(prefix="ai_agent_test_snapshot_")


def _create_test_manifest(root: str) -> MeetingManifest:
    ctx = create_meeting(_make_request(), meetings_root=root)
    return ctx.manifest


def _cleanup_dir(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _advance_state(
    manifest: MeetingManifest, target: LifecycleState | str
) -> MeetingManifest:
    target_str = str(target)
    current = manifest
    path = [
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.FINALIZING,
        LifecycleState.COMPLETED,
    ]
    for state in path:
        if current.state == target_str:
            break
        result = execute_transition(current, state)
        if not result.success:
            raise RuntimeError(
                f"Failed to advance to {state}: {result.rejection_reasons}"
            )
        current = result.manifest
        if current.state == target_str:
            break
    if current.state != target_str:
        raise RuntimeError(
            f"Could not reach {target_str}; ended at {current.state}"
        )
    return current


# ═══════════════════════════════════════════════════════════════════════════
# 1. SnapshotWorkerState
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotWorkerState:
    """Verify SnapshotWorkerState construction and serialization."""

    def test_construction_minimal(self):
        w = SnapshotWorkerState(role_id="producer-kim")
        assert w.role_id == "producer-kim"
        assert w.model_provider == ""
        assert w.model_name == ""
        assert w.status == "pending"
        assert w.packet_path == ""
        assert w.opinion_summary == ""
        assert w.started_at == ""
        assert w.error_message == ""

    def test_construction_full(self):
        w = SnapshotWorkerState(
            role_id="director-lee",
            model_provider="qwen",
            model_name="qwen3-max",
            status="running",
            packet_path="rounds/round_1/director-lee.json",
            opinion_summary="Proposing 3 concepts",
            started_at="2026-06-11T10:00:00Z",
            error_message="",
        )
        assert w.role_id == "director-lee"
        assert w.model_provider == "qwen"
        assert w.model_name == "qwen3-max"
        assert w.status == "running"
        assert w.packet_path == "rounds/round_1/director-lee.json"
        assert w.opinion_summary == "Proposing 3 concepts"
        assert w.started_at == "2026-06-11T10:00:00Z"

    def test_failed_worker(self):
        w = SnapshotWorkerState(
            role_id="finance-park",
            status="failed",
            error_message="Rate limit exceeded after retry",
        )
        assert w.status == "failed"
        assert w.error_message == "Rate limit exceeded after retry"

    def test_timed_out_worker(self):
        w = SnapshotWorkerState(
            role_id="art-director-choi",
            status="timed_out",
            error_message="opencode-go CLI timeout after 120s",
        )
        assert w.status == "timed_out"

    def test_frozen_immutable(self):
        w = SnapshotWorkerState(role_id="producer-kim")
        with pytest.raises(Exception):
            w.role_id = "other"  # type: ignore[misc]

    def test_to_dict(self):
        w = SnapshotWorkerState(
            role_id="producer-kim",
            model_provider="qwen",
            model_name="qwen3-max",
            status="running",
        )
        d = w.to_dict()
        assert d["role_id"] == "producer-kim"
        assert d["model_provider"] == "qwen"
        assert d["model_name"] == "qwen3-max"
        assert d["status"] == "running"

    def test_from_dict(self):
        data = {
            "role_id": "producer-kim",
            "model_provider": "qwen",
            "model_name": "qwen3-max",
            "status": "completed",
            "packet_path": "rounds/round_1/producer-kim.json",
            "opinion_summary": "Budget approved",
            "started_at": "2026-06-11T10:00:00Z",
            "error_message": "",
        }
        w = SnapshotWorkerState.from_dict(data)
        assert w.role_id == "producer-kim"
        assert w.status == "completed"
        assert w.opinion_summary == "Budget approved"

    def test_from_dict_defaults(self):
        """Missing optional fields get sensible defaults."""
        w = SnapshotWorkerState.from_dict({"role_id": "role-x"})
        assert w.role_id == "role-x"
        assert w.status == "pending"
        assert w.model_provider == ""

    def test_round_trip(self):
        original = SnapshotWorkerState(
            role_id="director-lee",
            model_provider="deepseek",
            model_name="deepseek-v4",
            status="completed",
            packet_path="rounds/round_2/director-lee.json",
            opinion_summary="3 locations proposed",
            started_at="2026-06-11T10:05:00Z",
            error_message="",
        )
        restored = SnapshotWorkerState.from_dict(original.to_dict())
        assert restored == original


# ═══════════════════════════════════════════════════════════════════════════
# 2. SnapshotPendingAction
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotPendingAction:
    """Verify SnapshotPendingAction construction and serialization."""

    def test_construction_minimal(self):
        a = SnapshotPendingAction(action_id="a1", action_type="next_speaker")
        assert a.action_id == "a1"
        assert a.action_type == "next_speaker"
        assert a.target_role == ""
        assert a.priority == 0
        assert a.dependencies == ()
        assert a.payload is None
        assert a.created_at != ""  # auto-set by __post_init__
        assert "T" in a.created_at

    def test_construction_full(self):
        a = SnapshotPendingAction(
            action_id="a2",
            action_type="validate",
            target_role="glm-validator",
            priority=2,
            dependencies=("a1",),
            payload={"consensus_text": "Budget: ₩45M"},
            created_at="2026-06-11T10:30:00Z",
        )
        assert a.action_id == "a2"
        assert a.action_type == "validate"
        assert a.target_role == "glm-validator"
        assert a.priority == 2
        assert a.dependencies == ("a1",)
        assert a.payload == {"consensus_text": "Budget: ₩45M"}

    def test_multiple_dependencies(self):
        a = SnapshotPendingAction(
            action_id="a5",
            action_type="execute",
            dependencies=("a3", "a4"),
            priority=3,
        )
        assert a.dependencies == ("a3", "a4")

    def test_frozen_immutable(self):
        a = SnapshotPendingAction(action_id="a1", action_type="next_speaker")
        with pytest.raises(Exception):
            a.action_id = "a2"  # type: ignore[misc]

    def test_to_dict(self):
        a = SnapshotPendingAction(
            action_id="a1",
            action_type="next_speaker",
            target_role="director-lee",
            priority=1,
        )
        d = a.to_dict()
        assert d["action_id"] == "a1"
        assert d["action_type"] == "next_speaker"
        assert d["target_role"] == "director-lee"
        assert d["priority"] == 1
        assert d["dependencies"] == []

    def test_from_dict(self):
        data = {
            "action_id": "a2",
            "action_type": "validate",
            "target_role": "glm-validator",
            "priority": 2,
            "dependencies": ["a1"],
            "payload": {"key": "value"},
            "created_at": "2026-06-11T10:30:00Z",
        }
        a = SnapshotPendingAction.from_dict(data)
        assert a.action_id == "a2"
        assert a.dependencies == ("a1",)
        assert a.payload == {"key": "value"}

    def test_from_dict_defaults(self):
        a = SnapshotPendingAction.from_dict(
            {"action_id": "x", "action_type": "route"}
        )
        assert a.priority == 0
        assert a.dependencies == ()
        assert a.payload is None

    def test_round_trip(self):
        original = SnapshotPendingAction(
            action_id="a3",
            action_type="finalize",
            target_role="coordinator",
            priority=5,
            dependencies=("a1", "a2"),
            payload={"output_format": "markdown"},
            created_at="2026-06-11T11:00:00Z",
        )
        restored = SnapshotPendingAction.from_dict(original.to_dict())
        assert restored == original


# ═══════════════════════════════════════════════════════════════════════════
# 3. PartialProgressSnapshot — construction and properties
# ═══════════════════════════════════════════════════════════════════════════


class TestPartialProgressSnapshotConstruction:
    """Verify PartialProgressSnapshot construction from manifest + runtime state."""

    def test_empty_state(self):
        """Snapshot with no active workers and no pending actions."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest)

            assert snap.meeting_id == manifest.meeting_id
            assert snap.state == "created"
            assert snap.round == 0
            assert snap.max_rounds == 3
            assert snap.active_workers == ()
            assert snap.pending_actions == ()
            assert snap.context_packets == ()
            assert snap.decisions == ()
            assert snap.tool_outputs == ()
            assert snap.transition_type == "generic"
            assert snap.schema_version == DEFAULT_SNAPSHOT_SCHEMA_VERSION
            assert snap.snapshot_id.startswith("snap_")
            assert "T" in snap.created_at  # ISO 8601
        finally:
            _cleanup_dir(root)

    def test_with_active_workers(self):
        """Snapshot captures multiple active workers."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            workers = [
                SnapshotWorkerState(
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    status="running",
                    started_at="2026-06-11T10:00:00Z",
                ),
                SnapshotWorkerState(
                    role_id="director-lee",
                    model_provider="deepseek",
                    model_name="deepseek-v4",
                    status="completed",
                    opinion_summary="3 MV locations proposed",
                    started_at="2026-06-11T10:01:00Z",
                ),
                SnapshotWorkerState(
                    role_id="finance-park",
                    model_provider="glm",
                    model_name="glm-5.1",
                    status="pending",
                ),
            ]
            snap = capture_snapshot(
                manifest,
                active_workers=workers,
                transition_type="round_advance",
            )

            assert len(snap.active_workers) == 3
            assert snap.active_workers[0].role_id == "producer-kim"
            assert snap.active_workers[1].role_id == "director-lee"
            assert snap.active_workers[2].role_id == "finance-park"
            assert snap.transition_type == "round_advance"
        finally:
            _cleanup_dir(root)

    def test_with_pending_actions(self):
        """Snapshot captures pending actions with dependencies."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            actions = [
                SnapshotPendingAction(
                    action_id="a1",
                    action_type="next_speaker",
                    target_role="producer-kim",
                    priority=1,
                ),
                SnapshotPendingAction(
                    action_id="a2",
                    action_type="validate",
                    target_role="glm-validator",
                    priority=2,
                    dependencies=("a1",),
                ),
            ]
            snap = capture_snapshot(
                manifest, pending_actions=actions, transition_type="context_packet"
            )

            assert len(snap.pending_actions) == 2
            assert snap.pending_actions[0].action_id == "a1"
            assert snap.pending_actions[1].dependencies == ("a1",)
            assert snap.transition_type == "context_packet"
        finally:
            _cleanup_dir(root)

    def test_with_both_workers_and_actions(self):
        """Snapshot captures both active workers and pending actions."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            workers = [
                SnapshotWorkerState(role_id="producer-kim", status="running"),
            ]
            actions = [
                SnapshotPendingAction(
                    action_id="a1", action_type="next_speaker", target_role="director-lee"
                ),
                SnapshotPendingAction(
                    action_id="a2", action_type="validate", target_role="glm-validator", priority=3
                ),
            ]
            snap = capture_snapshot(
                manifest,
                active_workers=workers,
                pending_actions=actions,
                transition_type="state_change",
            )

            assert len(snap.active_workers) == 1
            assert len(snap.pending_actions) == 2
        finally:
            _cleanup_dir(root)

    def test_manifest_fields_reflected(self):
        """Snapshot correctly copies all manifest-derived fields."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Advance to in_meeting state
            manifest = _advance_state(manifest, LifecycleState.IN_MEETING)

            snap = capture_snapshot(manifest)
            assert snap.state == "in_meeting"
            assert snap.round == 0  # no rounds completed yet
            assert snap.agenda == _VALID_AGENDA
            assert snap.priority == "p1"
            assert snap.agenda_type == ""  # not yet classified
            assert snap.validation_score == 0.0
            assert snap.validation_verdict == ""
            assert snap.current_speaker == ""
            assert snap.speaker_queue == ()
        finally:
            _cleanup_dir(root)

    def test_custom_snapshot_id(self):
        """Snapshot accepts a custom snapshot_id."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest, snapshot_id="my-custom-snap-001")
            assert snap.snapshot_id == "my-custom-snap-001"
        finally:
            _cleanup_dir(root)

    def test_transition_type_preserved(self):
        """All standard transition types are preserved in the snapshot."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            for ttype in [
                "state_change",
                "speaker_change",
                "decision_commit",
                "context_packet",
                "tool_output",
                "round_advance",
            ]:
                snap = capture_snapshot(manifest, transition_type=ttype)
                assert snap.transition_type == ttype, f"Failed for {ttype}"
        finally:
            _cleanup_dir(root)

    def test_frozen_immutable(self):
        """PartialProgressSnapshot is immutable."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest)
            with pytest.raises(Exception):
                snap.state = "queued"  # type: ignore[misc]
            with pytest.raises(Exception):
                snap.round = 5  # type: ignore[misc]
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Snapshot statistics (derived properties)
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotStatistics:
    """Verify derived statistics properties on PartialProgressSnapshot."""

    def _make_snap_with_workers(
        self, workers: list[SnapshotWorkerState]
    ) -> PartialProgressSnapshot:
        root = _tmp_meetings_root()
        manifest = _create_test_manifest(root)
        snap = capture_snapshot(manifest, active_workers=workers)
        _cleanup_dir(root)
        return snap

    def test_active_worker_count_zero(self):
        snap = self._make_snap_with_workers([])
        assert snap.active_worker_count == 0

    def test_active_worker_count_mixed_statuses(self):
        workers = [
            SnapshotWorkerState(role_id="w1", status="pending"),
            SnapshotWorkerState(role_id="w2", status="running"),
            SnapshotWorkerState(role_id="w3", status="running"),
            SnapshotWorkerState(role_id="w4", status="completed"),
            SnapshotWorkerState(role_id="w5", status="failed"),
            SnapshotWorkerState(role_id="w6", status="timed_out"),
        ]
        snap = self._make_snap_with_workers(workers)
        assert snap.active_worker_count == 2  # only 'running'
        assert snap.completed_worker_count == 1
        assert snap.failed_worker_count == 2  # failed + timed_out

    def test_all_completed(self):
        workers = [
            SnapshotWorkerState(role_id="w1", status="completed"),
            SnapshotWorkerState(role_id="w2", status="completed"),
        ]
        snap = self._make_snap_with_workers(workers)
        assert snap.active_worker_count == 0
        assert snap.completed_worker_count == 2
        assert snap.failed_worker_count == 0

    def test_pending_action_count(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            actions = [
                SnapshotPendingAction(action_id="a1", action_type="route"),
                SnapshotPendingAction(action_id="a2", action_type="validate"),
                SnapshotPendingAction(action_id="a3", action_type="execute"),
            ]
            snap = capture_snapshot(manifest, pending_actions=actions)
            assert snap.pending_action_count == 3
        finally:
            _cleanup_dir(root)

    def test_pending_actions_ordered_by_priority(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            actions = [
                SnapshotPendingAction(
                    action_id="medium", action_type="validate", priority=3
                ),
                SnapshotPendingAction(
                    action_id="high", action_type="next_speaker", priority=1
                ),
                SnapshotPendingAction(
                    action_id="low", action_type="finalize", priority=5
                ),
                SnapshotPendingAction(
                    action_id="critical", action_type="route", priority=0
                ),
            ]
            snap = capture_snapshot(manifest, pending_actions=actions)
            ordered = snap.pending_actions_ordered
            assert ordered[0].action_id == "critical"  # priority 0
            assert ordered[1].action_id == "high"       # priority 1
            assert ordered[2].action_id == "medium"      # priority 3
            assert ordered[3].action_id == "low"         # priority 5
        finally:
            _cleanup_dir(root)

    def test_context_packet_count(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Since the manifest starts with 0 packets, we verify count=0
            snap = capture_snapshot(manifest)
            assert snap.context_packet_count == 0
            assert snap.decision_count == 0
            assert snap.tool_output_count == 0
            assert snap.error_count == 0
        finally:
            _cleanup_dir(root)

    def test_is_quorum_met(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Set required_roles on the manifest needs a different approach
            # We test the property directly via snapshot construction
            workers = [
                SnapshotWorkerState(role_id="producer-kim", status="running"),
                SnapshotWorkerState(role_id="director-lee", status="completed"),
            ]
            # required_roles from manifest is empty by default, so quorum is met
            snap = capture_snapshot(
                manifest, active_workers=workers, transition_type="round_advance"
            )
            # No required roles → quorum is always met (0 active+completed >= 0)
            assert snap.is_quorum_met is True
        finally:
            _cleanup_dir(root)

    def test_worker_summary(self):
        workers = [
            SnapshotWorkerState(role_id="w1", status="pending"),
            SnapshotWorkerState(role_id="w2", status="pending"),
            SnapshotWorkerState(role_id="w3", status="running"),
            SnapshotWorkerState(role_id="w4", status="completed"),
            SnapshotWorkerState(role_id="w5", status="completed"),
            SnapshotWorkerState(role_id="w6", status="completed"),
            SnapshotWorkerState(role_id="w7", status="failed"),
            SnapshotWorkerState(role_id="w8", status="timed_out"),
        ]
        snap = self._make_snap_with_workers(workers)
        summary = snap.worker_summary
        assert summary["pending"] == 2
        assert summary["running"] == 1
        assert summary["completed"] == 3
        assert summary["failed"] == 1
        assert summary["timed_out"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. File-based persistence: write + load round-trip
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotPersistence:
    """Verify snapshot file write/load round-trip."""

    def test_write_and_load(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            workers = [
                SnapshotWorkerState(
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    status="running",
                    started_at="2026-06-11T10:00:00Z",
                ),
                SnapshotWorkerState(
                    role_id="director-lee",
                    model_provider="deepseek",
                    model_name="deepseek-v4",
                    status="completed",
                    opinion_summary="Budget: ₩50M",
                    started_at="2026-06-11T10:01:00Z",
                ),
            ]
            actions = [
                SnapshotPendingAction(
                    action_id="a1",
                    action_type="next_speaker",
                    target_role="finance-park",
                    priority=1,
                ),
                SnapshotPendingAction(
                    action_id="a2",
                    action_type="validate",
                    target_role="glm-validator",
                    priority=2,
                    dependencies=("a1",),
                ),
            ]
            snap = capture_snapshot(
                manifest,
                active_workers=workers,
                pending_actions=actions,
                transition_type="round_advance",
                snapshot_id="test-snap-001",
            )

            # Write
            meeting_dir = str(Path(manifest.manifest_path).parent)
            path = snap.write(meeting_dir)
            assert os.path.exists(path)
            assert path.endswith("snapshot_0001.json")

            # Load
            loaded = load_snapshot(path)
            assert loaded.snapshot_id == "test-snap-001"
            assert loaded.meeting_id == manifest.meeting_id
            assert loaded.state == "created"
            assert loaded.round == 0
            assert len(loaded.active_workers) == 2
            assert len(loaded.pending_actions) == 2
            assert loaded.transition_type == "round_advance"

            # Verify worker fidelity
            assert loaded.active_workers[0].role_id == "producer-kim"
            assert loaded.active_workers[0].status == "running"
            assert loaded.active_workers[1].role_id == "director-lee"
            assert loaded.active_workers[1].status == "completed"
            assert loaded.active_workers[1].opinion_summary == "Budget: ₩50M"

            # Verify action fidelity
            assert loaded.pending_actions[0].action_id == "a1"
            assert loaded.pending_actions[1].dependencies == ("a1",)
        finally:
            _cleanup_dir(root)

    def test_write_multiple_snapshots(self):
        """Multiple snapshots in same meeting dir get incremented filenames."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            meeting_dir = str(Path(manifest.manifest_path).parent)

            workers_full = [
                SnapshotWorkerState(role_id="producer-kim", status="running"),
                SnapshotWorkerState(role_id="director-lee", status="running"),
            ]
            workers_partial = [
                SnapshotWorkerState(role_id="producer-kim", status="running"),
            ]
            workers_none: list[SnapshotWorkerState] = []

            snap1 = capture_snapshot(
                manifest,
                active_workers=workers_full,
                snapshot_id="snap-1",
            )
            snap2 = capture_snapshot(
                manifest,
                active_workers=workers_partial,
                snapshot_id="snap-2",
            )
            snap3 = capture_snapshot(
                manifest,
                active_workers=workers_none,
                snapshot_id="snap-3",
            )

            p1 = snap1.write(meeting_dir)
            p2 = snap2.write(meeting_dir)
            p3 = snap3.write(meeting_dir)

            assert p1.endswith("snapshot_0001.json")
            assert p2.endswith("snapshot_0002.json")
            assert p3.endswith("snapshot_0003.json")

            assert os.path.exists(p1)
            assert os.path.exists(p2)
            assert os.path.exists(p3)

            # All loadable
            for p in (p1, p2, p3):
                loaded = load_snapshot(p)
                assert loaded.meeting_id == manifest.meeting_id
        finally:
            _cleanup_dir(root)

    def test_find_latest_snapshot(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            meeting_dir = str(Path(manifest.manifest_path).parent)

            # No snapshots yet
            assert find_latest_snapshot(meeting_dir) is None
            assert snapshot_count(meeting_dir) == 0
            assert list_snapshots(meeting_dir) == []

            # Write 3 snapshots
            for i in range(3):
                snap = capture_snapshot(
                    manifest, snapshot_id=f"snap-{i+1}"
                )
                snap.write(meeting_dir)

            latest = find_latest_snapshot(meeting_dir)
            assert latest is not None
            assert latest.endswith("snapshot_0003.json")

            loaded = load_snapshot(latest)
            assert loaded.snapshot_id == "snap-3"

            assert snapshot_count(meeting_dir) == 3
            paths = list_snapshots(meeting_dir)
            assert len(paths) == 3
            assert paths[-1].endswith("snapshot_0003.json")
        finally:
            _cleanup_dir(root)

    def test_snapshot_count_zero_when_no_dir(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            meeting_dir = str(Path(manifest.manifest_path).parent)
            # snapshots/ subdir doesn't exist yet
            assert snapshot_count(meeting_dir) == 0
            assert find_latest_snapshot(meeting_dir) is None
        finally:
            _cleanup_dir(root)

    def test_load_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_snapshot("/nonexistent/path/snapshot.json")

    def test_load_corrupted_json(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            meeting_dir = str(Path(manifest.manifest_path).parent)
            bad_path = os.path.join(meeting_dir, "bad.json")
            with open(bad_path, "w") as f:
                f.write("this is not json {{{")
            with pytest.raises(json.JSONDecodeError):
                load_snapshot(bad_path)
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Varying complexity
# ═══════════════════════════════════════════════════════════════════════════


class TestVaryingComplexity:
    """Verify snapshots handle varying state complexity correctly."""

    def test_zero_workers_zero_actions(self):
        """Simplest possible snapshot: no workers, no actions."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest)
            assert snap.active_worker_count == 0
            assert snap.pending_action_count == 0
            assert snap.completed_worker_count == 0
            assert snap.failed_worker_count == 0
            assert snap.worker_summary == {
                "pending": 0, "running": 0, "completed": 0,
                "failed": 0, "timed_out": 0,
            }
        finally:
            _cleanup_dir(root)

    def test_many_workers_7_roles(self):
        """Simulate a full meeting with 7 active workers."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            role_ids = [
                "producer-kim", "director-lee", "finance-park",
                "art-director-choi", "concept-artist-yoon",
                "backend-dev-kang", "marketing-lee",
            ]
            statuses = [
                "completed", "completed", "running",
                "running", "pending", "pending", "failed",
            ]
            workers = [
                SnapshotWorkerState(
                    role_id=rid,
                    model_provider="qwen",
                    model_name="qwen3-max",
                    status=st,
                )
                for rid, st in zip(role_ids, statuses)
            ]
            snap = capture_snapshot(
                manifest, active_workers=workers, transition_type="round_advance"
            )

            assert len(snap.active_workers) == 7
            assert snap.active_worker_count == 2  # running
            assert snap.completed_worker_count == 2
            assert snap.failed_worker_count == 1  # failed only, no timed_out

            summary = snap.worker_summary
            assert summary["pending"] == 2
            assert summary["running"] == 2
            assert summary["completed"] == 2
            assert summary["failed"] == 1
            assert summary["timed_out"] == 0
        finally:
            _cleanup_dir(root)

    def test_many_pending_actions_with_complex_dependencies(self):
        """Simulate a Coordinator queue with chained dependencies."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            actions = [
                SnapshotPendingAction(
                    action_id="a1", action_type="next_speaker",
                    target_role="producer-kim", priority=1,
                ),
                SnapshotPendingAction(
                    action_id="a2", action_type="next_speaker",
                    target_role="director-lee", priority=1,
                    dependencies=("a1",),
                ),
                SnapshotPendingAction(
                    action_id="a3", action_type="next_speaker",
                    target_role="finance-park", priority=1,
                    dependencies=("a2",),
                ),
                SnapshotPendingAction(
                    action_id="a4", action_type="validate",
                    target_role="glm-validator", priority=2,
                    dependencies=("a3",),
                ),
                SnapshotPendingAction(
                    action_id="a5", action_type="execute",
                    target_role="openclaw-executor", priority=3,
                    dependencies=("a4",),
                ),
                SnapshotPendingAction(
                    action_id="a6", action_type="finalize",
                    target_role="coordinator", priority=5,
                    dependencies=("a4", "a5"),
                ),
            ]
            snap = capture_snapshot(manifest, pending_actions=actions)

            assert snap.pending_action_count == 6

            # Verify dependency chain preserved
            by_id = {a.action_id: a for a in snap.pending_actions}
            assert by_id["a2"].dependencies == ("a1",)
            assert by_id["a3"].dependencies == ("a2",)
            assert by_id["a6"].dependencies == ("a4", "a5")
        finally:
            _cleanup_dir(root)

    def test_high_priority_preemption(self):
        """Verify pending_actions_ordered respects priority."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            actions = [
                SnapshotPendingAction(
                    action_id="paused-meeting", action_type="resume",
                    priority=0,  # highest
                ),
                SnapshotPendingAction(
                    action_id="normal-validate", action_type="validate",
                    priority=2,
                ),
                SnapshotPendingAction(
                    action_id="urgent-escalate", action_type="escalate",
                    priority=0,  # also highest, ties broken by order
                ),
            ]
            snap = capture_snapshot(manifest, pending_actions=actions)
            ordered = snap.pending_actions_ordered
            # Both priority 0 actions come first
            assert ordered[0].priority <= ordered[1].priority <= ordered[2].priority
            assert ordered[0].priority == 0
            assert ordered[1].priority == 0
            assert ordered[2].priority == 2
        finally:
            _cleanup_dir(root)

    def test_across_lifecycle_states(self):
        """Snapshot captures correct state at different lifecycle phases."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            states_and_workers = [
                ("created", []),
                ("queued", [
                    SnapshotWorkerState(role_id="router", status="pending"),
                ]),
                ("routing", [
                    SnapshotWorkerState(role_id="qwen-router", status="running"),
                ]),
                ("context_retrieval", [
                    SnapshotWorkerState(role_id="knowledge-retriever", status="running"),
                ]),
                ("in_meeting", [
                    SnapshotWorkerState(role_id="producer-kim", status="running"),
                    SnapshotWorkerState(role_id="director-lee", status="pending"),
                    SnapshotWorkerState(role_id="finance-park", status="pending"),
                ]),
                ("consensus_building", [
                    SnapshotWorkerState(role_id="consensus-builder", status="running"),
                ]),
                ("validating", [
                    SnapshotWorkerState(role_id="glm-validator", status="running"),
                ]),
            ]

            for expected_state, workers in states_and_workers:
                # Advance manifest to expected state
                m = _advance_state(manifest, expected_state)
                snap = capture_snapshot(
                    m,
                    active_workers=workers,
                    transition_type="state_change",
                )
                assert snap.state == expected_state, (
                    f"Expected state={expected_state}, got {snap.state}"
                )
                assert len(snap.active_workers) == len(workers)
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Verify edge-case handling."""

    def test_none_workers_defaults_to_empty(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest, active_workers=None)
            assert snap.active_workers == ()
            assert snap.active_worker_count == 0
        finally:
            _cleanup_dir(root)

    def test_none_actions_defaults_to_empty(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest, pending_actions=None)
            assert snap.pending_actions == ()
            assert snap.pending_action_count == 0
        finally:
            _cleanup_dir(root)

    def test_max_rounds_exceeded(self):
        """Snapshot reflects max_rounds even when close to limit."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Simulate a manifest at round 3 (max)
            # We can't easily set round_count on a frozen manifest, but
            # the snapshot should reflect max_rounds from config
            snap = capture_snapshot(manifest)
            assert snap.max_rounds == 3
            assert snap.round == 0  # initial
        finally:
            _cleanup_dir(root)

    def test_empty_manifest_fields(self):
        """Snapshot handles empty/zero-value manifest fields."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            snap = capture_snapshot(manifest)
            assert snap.agenda_type == ""
            assert snap.validation_verdict == ""
            assert snap.completed_step == ""
        finally:
            _cleanup_dir(root)

    def test_from_json_string(self):
        """PartialProgressSnapshot can be deserialized from a JSON string."""
        snap_dict = {
            "snapshot_id": "test-json",
            "meeting_id": "meeting_20260611_abc123",
            "created_at": "2026-06-11T10:00:00Z",
            "state": "in_meeting",
            "round": 2,
            "max_rounds": 3,
            "active_workers": [
                {
                    "role_id": "producer-kim",
                    "model_provider": "qwen",
                    "model_name": "qwen3-max",
                    "status": "running",
                    "packet_path": "",
                    "opinion_summary": "",
                    "started_at": "2026-06-11T10:00:00Z",
                    "error_message": "",
                }
            ],
            "pending_actions": [
                {
                    "action_id": "a1",
                    "action_type": "validate",
                    "target_role": "",
                    "priority": 0,
                    "dependencies": [],
                    "payload": None,
                    "created_at": "2026-06-11T10:00:00Z",
                }
            ],
            "context_packets": [],
            "decisions": [],
            "tool_outputs": [],
            "error_log": [],
            "current_speaker": "producer-kim",
            "speaker_queue": ["producer-kim", "director-lee"],
            "validation_score": 0.5,
            "validation_verdict": "pending",
            "agenda": "Test agenda",
            "agenda_type": "creative_review",
            "required_roles": ["producer-kim"],
            "optional_roles": [],
            "priority": "p1",
            "transition_type": "round_advance",
            "completed_step": "routing",
            "schema_version": "snapshot.v1",
        }
        json_str = json.dumps(snap_dict)
        snap = PartialProgressSnapshot.from_json(json_str)

        assert snap.snapshot_id == "test-json"
        assert snap.meeting_id == "meeting_20260611_abc123"
        assert snap.state == "in_meeting"
        assert snap.round == 2
        assert len(snap.active_workers) == 1
        assert snap.active_workers[0].role_id == "producer-kim"
        assert snap.active_workers[0].status == "running"
        assert len(snap.pending_actions) == 1
        assert snap.current_speaker == "producer-kim"
        assert snap.speaker_queue == ("producer-kim", "director-lee")
        assert snap.transition_type == "round_advance"
        assert snap.completed_step == "routing"
        assert snap.agenda_type == "creative_review"
        assert snap.required_roles == ("producer-kim",)

    def test_json_round_trip_full(self):
        """Full round-trip: snapshot → JSON → snapshot, verify equality."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            workers = [
                SnapshotWorkerState(
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    status="running",
                    started_at="2026-06-11T10:00:00Z",
                ),
                SnapshotWorkerState(
                    role_id="director-lee",
                    model_provider="deepseek",
                    model_name="deepseek-v4",
                    status="completed",
                    opinion_summary="Proposed 3 MV locations",
                ),
            ]
            actions = [
                SnapshotPendingAction(
                    action_id="a1",
                    action_type="next_speaker",
                    target_role="finance-park",
                    priority=1,
                ),
                SnapshotPendingAction(
                    action_id="a2",
                    action_type="validate",
                    target_role="glm-validator",
                    priority=2,
                    dependencies=("a1",),
                    payload={"consensus": "Budget ₩45M"},
                ),
            ]
            original = capture_snapshot(
                manifest,
                active_workers=workers,
                pending_actions=actions,
                transition_type="round_advance",
                snapshot_id="round-trip-test",
            )

            # Serialize
            json_str = original.to_json()
            # Deserialize
            restored = PartialProgressSnapshot.from_json(json_str)

            # Verify equality (comparing JSON representation since
            # dataclass eq compares tuples of dicts which may differ
            # in ordering)
            orig_dict = json.loads(original.to_json())
            rest_dict = json.loads(restored.to_json())
            assert orig_dict == rest_dict
        finally:
            _cleanup_dir(root)

    def test_write_increments_across_directories(self):
        """Each meeting dir gets its own snapshot sequence."""
        root = _tmp_meetings_root()
        try:
            manifest1 = _create_test_manifest(root)
            # Create second meeting
            req = _make_request(agenda="Second meeting")
            ctx2 = create_meeting(req, meetings_root=root)
            manifest2 = ctx2.manifest

            meeting_dir1 = str(Path(manifest1.manifest_path).parent)
            meeting_dir2 = str(Path(manifest2.manifest_path).parent)

            snap1 = capture_snapshot(manifest1, snapshot_id="snap-1-1")
            snap2 = capture_snapshot(manifest2, snapshot_id="snap-2-1")
            snap3 = capture_snapshot(manifest1, snapshot_id="snap-1-2")

            p1 = snap1.write(meeting_dir1)  # snapshot_0001.json
            p2 = snap2.write(meeting_dir2)  # snapshot_0001.json (separate dir)
            p3 = snap3.write(meeting_dir1)  # snapshot_0002.json

            assert p1.endswith("snapshot_0001.json")
            assert p2.endswith("snapshot_0001.json")  # separate meeting
            assert p3.endswith("snapshot_0002.json")

            assert snapshot_count(meeting_dir1) == 2
            assert snapshot_count(meeting_dir2) == 1
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Integration: snapshot + transition hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestSnapshotIntegration:
    """Verify snapshot capture integrates with the transition system."""

    def test_snapshot_during_state_transition(self):
        """Capture a snapshot immediately after a state transition."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            # Transition to queued
            result = execute_transition(manifest, LifecycleState.QUEUED)
            assert result.success

            snap = capture_snapshot(
                result.manifest,
                transition_type="state_change",
            )
            assert snap.state == "queued"
            assert snap.transition_type == "state_change"
        finally:
            _cleanup_dir(root)

    def test_snapshot_with_round_advance(self):
        """Capture a snapshot simulating a round-advance transition."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            manifest = _advance_state(manifest, LifecycleState.IN_MEETING)

            workers = [
                SnapshotWorkerState(role_id="producer-kim", status="completed"),
                SnapshotWorkerState(role_id="director-lee", status="completed"),
            ]
            actions = [
                SnapshotPendingAction(
                    action_id="r2-start",
                    action_type="next_speaker",
                    target_role="producer-kim",
                    priority=1,
                ),
            ]

            snap = capture_snapshot(
                manifest,
                active_workers=workers,
                pending_actions=actions,
                transition_type="round_advance",
            )
            assert snap.state == "in_meeting"
            assert snap.transition_type == "round_advance"
            assert snap.completed_worker_count == 2
            assert snap.pending_action_count == 1

            # Write and load
            meeting_dir = str(Path(manifest.manifest_path).parent)
            path = snap.write(meeting_dir)
            loaded = load_snapshot(path)
            assert loaded.transition_type == "round_advance"
            assert loaded.completed_worker_count == 2
        finally:
            _cleanup_dir(root)

    def test_snapshot_persistence_independent_of_manifest(self):
        """Snapshot files don't interfere with manifest.json."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            meeting_dir = str(Path(manifest.manifest_path).parent)

            # Write a snapshot
            snap = capture_snapshot(manifest, transition_type="state_change")
            snap.write(meeting_dir)

            # Manifest should still be intact
            from src.meeting_trigger import load_manifest
            reloaded = load_manifest(manifest.manifest_path)
            assert reloaded.meeting_id == manifest.meeting_id
            assert reloaded.state == "created"

            # Snapshots/ directory exists alongside manifest.json
            assert os.path.isdir(os.path.join(meeting_dir, "snapshots"))
            assert os.path.exists(manifest.manifest_path)
        finally:
            _cleanup_dir(root)
