"""Tests for crash detection and recovery entry (Sub-AC 4.4.4).

Covers:
- scan_for_incomplete_manifests: directory scan, terminal filtering,
  corruption handling, empty/missing directories
- classify_recoverability: all verdict classifications, staleness,
  idempotency, corrupted states
- build_recovery_plan: resume state resolution, round/speaker context
- recover_meeting: recovery event logging, state update, persistence,
  idempotency, error handling
- auto_recover_all: end-to-end sweep with mixed states
- Integration: write mid-meeting manifest, simulate restart, verify
  correct lifecycle position on resume
- Edge cases: empty error_log, missing updated_at, concurrent recovery
  race (idempotency via recovery marker)
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.crash_recovery import (
    DEFAULT_STALE_TIMEOUT_SECONDS,
    RECOVERABLE_STATES,
    Recoverability,
    RecoverabilityVerdict,
    RecoveryEntryResult,
    RecoveryPlan,
    _has_recovery_marker,
    _manifest_age_seconds,
    _resolve_resume_state,
    auto_recover_all,
    build_recovery_plan,
    classify_recoverability,
    recover_meeting,
    scan_for_incomplete_manifests,
)
from src.meeting_trigger import (
    MAX_AGENTS_PER_MEETING,
    MAX_ROUNDS,
    DEFAULT_MEETINGS_ROOT,
    MeetingCommandRequest,
    MeetingConfig,
    MeetingManifest,
    create_meeting,
    load_manifest,
    update_manifest,
)
from src.shared.lifecycle import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    LifecycleState,
    is_terminal,
)
from src.transition_engine import execute_transition


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_manifest(
    *,
    meeting_id: str = "meeting_20260610_test00000001",
    state: str = "created",
    updated_at: str | None = None,
    error_log: tuple[dict[str, str], ...] = (),
    completed_step: str = "",
    round_count: int = 0,
    current_speaker: str = "",
    speaker_queue: tuple[str, ...] = (),
    context_packets: tuple[dict, ...] = (),
    decisions: tuple[dict, ...] = (),
    meetings_root: str = "/tmp/test-meetings",
    agenda: str = "Test agenda",
    user_id: str = "u-test",
    channel_id: str = "c-test",
) -> MeetingManifest:
    """Build a minimal MeetingManifest for crash recovery testing."""
    if updated_at is None:
        updated_at = datetime.now(timezone.utc).isoformat()
    return MeetingManifest(
        meeting_id=meeting_id,
        state=state,
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        error_log=error_log,
        completed_step=completed_step,
        round_count=round_count,
        current_speaker=current_speaker,
        speaker_queue=speaker_queue,
        context_packets=context_packets,
        decisions=decisions,
        meetings_root=meetings_root,
        manifest_path=f"{meetings_root}/{meeting_id}/manifest.json",
        updated_at=updated_at,
        created_at=updated_at,
    )


def _make_manifest_json(
    meeting_dir: Path,
    *,
    meeting_id: str = "meeting_20260610_test00000001",
    state: str = "created",
    updated_at: str | None = None,
    round_count: int = 0,
    error_log: list[dict] | None = None,
    completed_step: str = "",
    current_speaker: str = "",
    speaker_queue: list[str] | None = None,
    context_packets: list[dict] | None = None,
    decisions: list[dict] | None = None,
    agenda: str = "Test agenda",
) -> tuple[Path, dict]:
    """Write a manifest.json to a temp meeting directory and return the path + data."""
    meeting_dir.mkdir(parents=True, exist_ok=True)
    if updated_at is None:
        updated_at = datetime.now(timezone.utc).isoformat()
    data = {
        "meeting_id": meeting_id,
        "state": state,
        "priority": "p2",
        "agenda": agenda,
        "agenda_type": "",
        "tags": [],
        "risk_tags": [],
        "required_roles": [],
        "optional_roles": [],
        "round_count": round_count,
        "validation_score": 0.0,
        "validation_verdict": "",
        "validator_required": True,
        "codex_required": False,
        "consensus": "",
        "user_id": "u-test",
        "channel_id": "c-test",
        "thread_id": "",
        "guild_id": "",
        "error_log": error_log or [],
        "manifest_path": str(meeting_dir / "manifest.json"),
        "meetings_root": str(meeting_dir.parent),
        "max_rounds": 3,
        "max_agents_per_meeting": 7,
        "token_limit_worker": 12000,
        "token_limit_validator": 20000,
        "token_limit_codex": 30000,
        "primary_validator_model": "glm-5.1",
        "conditional_validator_model": "gpt-5.5",
        "schema_version": "meeting-manifest.v1",
        "current_speaker": current_speaker,
        "speaker_queue": speaker_queue or [],
        "completed_step": completed_step,
        "context_packets": context_packets or [],
        "decisions": decisions or [],
        "tool_outputs": [],
        "created_at": updated_at,
        "updated_at": updated_at,
    }
    manifest_path = meeting_dir / "manifest.json"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return manifest_path, data


# ── scan_for_incomplete_manifests tests ───────────────────────────────────


class TestScanForIncompleteManifests:
    """Verify directory scanning for incomplete manifests."""

    def test_empty_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = scan_for_incomplete_manifests(tmpdir)
            assert result == []

    def test_nonexistent_directory_returns_empty(self):
        result = scan_for_incomplete_manifests("/tmp/does_not_exist_xyz")
        assert result == []

    def test_single_created_manifest_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meeting_dir = Path(tmpdir) / "meeting_test_001"
            manifest_path, _ = _make_manifest_json(
                meeting_dir, state="created"
            )
            result = scan_for_incomplete_manifests(tmpdir)
            assert len(result) == 1
            assert result[0].meeting_id == "meeting_20260610_test00000001"
            assert result[0].state == "created"

    def test_terminal_states_filtered_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, state in enumerate(TERMINAL_STATES):
                meeting_dir = Path(tmpdir) / f"meeting_term_{i}"
                _make_manifest_json(
                    meeting_dir,
                    meeting_id=f"meeting_term_{i}",
                    state=str(state),
                )
            result = scan_for_incomplete_manifests(tmpdir)
            assert result == []

    def test_mixed_states_only_active_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Terminal — should be skipped
            _make_manifest_json(
                Path(tmpdir) / "completed_1",
                meeting_id="completed_1",
                state="completed",
            )
            _make_manifest_json(
                Path(tmpdir) / "cancelled_1",
                meeting_id="cancelled_1",
                state="cancelled",
            )
            # Active — should be returned
            _make_manifest_json(
                Path(tmpdir) / "in_meeting_1",
                meeting_id="in_meeting_1",
                state="in_meeting",
            )
            _make_manifest_json(
                Path(tmpdir) / "validating_1",
                meeting_id="validating_1",
                state="validating",
            )
            result = scan_for_incomplete_manifests(tmpdir)
            assert len(result) == 2
            ids = {m.meeting_id for m in result}
            assert ids == {"in_meeting_1", "validating_1"}

    def test_corrupted_manifest_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meeting_dir = Path(tmpdir) / "corrupt_1"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "manifest.json").write_text("not valid json {{{")
            # Also put a valid manifest so we know scan didn't crash
            _make_manifest_json(
                Path(tmpdir) / "valid_1",
                meeting_id="valid_1",
                state="created",
            )
            result = scan_for_incomplete_manifests(tmpdir)
            assert len(result) == 1
            assert result[0].meeting_id == "valid_1"

    def test_results_sorted_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc)
            old = (now - timedelta(hours=2)).isoformat()
            new = (now - timedelta(minutes=5)).isoformat()

            _make_manifest_json(
                Path(tmpdir) / "old_meeting",
                meeting_id="old_meeting",
                state="queued",
                updated_at=old,
            )
            _make_manifest_json(
                Path(tmpdir) / "new_meeting",
                meeting_id="new_meeting",
                state="queued",
                updated_at=new,
            )

            result = scan_for_incomplete_manifests(tmpdir)
            assert len(result) == 2
            assert result[0].meeting_id == "old_meeting"
            assert result[1].meeting_id == "new_meeting"

    def test_deeply_nested_manifest_found(self):
        """Manifests in nested subdirectories should be found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = Path(tmpdir) / "a" / "b" / "c" / "meeting_deep_1"
            _make_manifest_json(
                deep, meeting_id="deep_1", state="created"
            )
            result = scan_for_incomplete_manifests(tmpdir)
            assert len(result) == 1
            assert result[0].meeting_id == "deep_1"


# ── classify_recoverability tests ─────────────────────────────────────────


class TestClassifyRecoverability:
    """Verify recoverability classification logic."""

    def test_terminal_state_returns_terminal(self):
        for state in TERMINAL_STATES:
            manifest = _make_manifest(state=str(state))
            verdict = classify_recoverability(manifest)
            assert verdict.verdict == Recoverability.TERMINAL
            assert not verdict.is_recoverable

    def test_active_state_returns_recoverable(self):
        manifest = _make_manifest(state="in_meeting")
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.RECOVERABLE
        assert verdict.is_recoverable
        assert verdict.is_auto_recoverable

    def test_paused_state_is_recoverable(self):
        manifest = _make_manifest(state="paused")
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.RECOVERABLE

    def test_deadlocked_state_is_recoverable(self):
        manifest = _make_manifest(state="deadlocked")
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.RECOVERABLE

    def test_already_recovered_detected(self):
        manifest = _make_manifest(
            state="in_meeting",
            error_log=(
                {
                    "timestamp": "2026-06-10T00:00:00+00:00",
                    "error_type": "crash_recovery",
                    "message": "previous recovery",
                    "severity": "info",
                    "recovery": "resumed",
                },
            ),
        )
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.ALREADY_RECOVERED

    def test_stale_manifest_returns_stale_recoverable(self):
        old_time = (
            datetime.now(timezone.utc)
            - timedelta(seconds=DEFAULT_STALE_TIMEOUT_SECONDS + 1)
        ).isoformat()
        manifest = _make_manifest(
            state="in_meeting", updated_at=old_time
        )
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.STALE_RECOVERABLE
        assert verdict.is_recoverable
        assert not verdict.is_auto_recoverable

    def test_fresh_manifest_not_stale(self):
        """A manifest updated just now should not be stale."""
        manifest = _make_manifest(
            state="routing",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.RECOVERABLE

    def test_custom_stale_timeout(self):
        """With a very short timeout, even a fresh manifest becomes stale."""
        old_time = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        manifest = _make_manifest(
            state="in_meeting", updated_at=old_time
        )
        verdict = classify_recoverability(
            manifest, stale_timeout_seconds=5
        )
        assert verdict.verdict == Recoverability.STALE_RECOVERABLE

    def test_unknown_state_is_corrupted(self):
        manifest = _make_manifest(state="invalid_state_xyz")
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.CORRUPTED

    def test_all_recoverable_states_pass(self):
        """Every state in RECOVERABLE_STATES should be classified recoverable."""
        for state in RECOVERABLE_STATES:
            manifest = _make_manifest(state=str(state))
            verdict = classify_recoverability(manifest)
            assert verdict.is_recoverable, f"State {state} should be recoverable"

    def test_reason_includes_meeting_id(self):
        manifest = _make_manifest(
            meeting_id="m-specific-123", state="queued"
        )
        verdict = classify_recoverability(manifest)
        assert "m-specific-123" in verdict.reason


# ── build_recovery_plan tests ─────────────────────────────────────────────


class TestBuildRecoveryPlan:
    """Verify recovery plan construction."""

    def test_basic_plan_from_created_state(self):
        manifest = _make_manifest(state="created")
        plan = build_recovery_plan(manifest)
        assert plan.meeting_id == manifest.meeting_id
        assert plan.last_state == "created"
        assert plan.resume_state == "created"
        assert plan.round_count == 0
        assert plan.current_speaker == ""
        assert not plan.stale

    def test_plan_uses_completed_step_when_set(self):
        """If completed_step is set, resume from there, not current state."""
        manifest = _make_manifest(
            state="routing",
            completed_step="queued",
        )
        plan = build_recovery_plan(manifest)
        assert plan.last_state == "routing"
        assert plan.resume_state == "queued"

    def test_plan_falls_back_to_current_state(self):
        """When completed_step is empty, resume from current state."""
        manifest = _make_manifest(
            state="validating",
            completed_step="",
        )
        plan = build_recovery_plan(manifest)
        assert plan.resume_state == "validating"

    def test_plan_preserves_round_count(self):
        manifest = _make_manifest(state="in_meeting", round_count=2)
        plan = build_recovery_plan(manifest)
        assert plan.round_count == 2

    def test_plan_preserves_speaker_state(self):
        manifest = _make_manifest(
            state="in_meeting",
            round_count=1,
            current_speaker="producer-kim",
            speaker_queue=("producer-kim", "director-lee", "finance-park"),
        )
        plan = build_recovery_plan(manifest)
        assert plan.current_speaker == "producer-kim"
        assert plan.speaker_queue == (
            "producer-kim",
            "director-lee",
            "finance-park",
        )

    def test_plan_preserves_context_packets_count(self):
        packets = (
            {"round": 1, "role_id": "role-a", "created_at": "..."},
            {"round": 1, "role_id": "role-b", "created_at": "..."},
        )
        manifest = _make_manifest(
            state="in_meeting", context_packets=packets
        )
        plan = build_recovery_plan(manifest)
        assert plan.context_packets_count == 2

    def test_plan_preserves_decisions_count(self):
        decisions = (
            {"round": 1, "decision_id": "d1", "role_id": "r1", "content": "x"},
        )
        manifest = _make_manifest(state="consensus_building", decisions=decisions)
        plan = build_recovery_plan(manifest)
        assert plan.decisions_count == 1

    def test_stale_plan_flagged(self):
        old_time = (
            datetime.now(timezone.utc)
            - timedelta(seconds=DEFAULT_STALE_TIMEOUT_SECONDS + 1)
        ).isoformat()
        manifest = _make_manifest(
            state="in_meeting", updated_at=old_time
        )
        plan = build_recovery_plan(manifest)
        assert plan.stale is True
        assert not plan.can_auto_recover
        assert "[STALE]" in plan.recovery_note

    def test_recovery_note_is_descriptive(self):
        manifest = _make_manifest(
            meeting_id="m-test-123",
            state="validating",
            round_count=2,
            current_speaker="role-x",
            context_packets=(
                {"round": 1, "role_id": "a"},
                {"round": 1, "role_id": "b"},
                {"round": 2, "role_id": "c"},
            ),
        )
        plan = build_recovery_plan(manifest)
        note = plan.recovery_note
        assert "m-test-123" in note
        assert "validating" in note
        assert "round 2" in note
        assert "speaker=role-x" in note
        assert "context_packets=3" in note


# ── recover_meeting tests ─────────────────────────────────────────────────


class TestRecoverMeeting:
    """Verify the recover_meeting execution."""

    def test_recover_appends_crash_recovery_event(self):
        manifest = _make_manifest(state="in_meeting")
        plan = build_recovery_plan(manifest)
        result = recover_meeting(manifest, plan, persist=False)

        assert result.success
        assert result.manifest is not None
        # Check that error_log has the recovery event
        assert len(result.manifest.error_log) == 1
        event = result.manifest.error_log[0]
        assert event["error_type"] == "crash_recovery"
        assert event["severity"] == "info"

    def test_recover_sets_resume_state(self):
        manifest = _make_manifest(
            state="routing", completed_step="queued"
        )
        plan = build_recovery_plan(manifest)
        result = recover_meeting(manifest, plan, persist=False)

        assert result.manifest is not None
        assert result.manifest.state == "queued"

    def test_recover_same_state_when_no_completed_step(self):
        """Resume state equals current state when completed_step is empty."""
        manifest = _make_manifest(state="validating", completed_step="")
        plan = build_recovery_plan(manifest)
        result = recover_meeting(manifest, plan, persist=False)

        assert result.manifest is not None
        assert result.manifest.state == "validating"

    def test_recover_is_idempotent(self):
        """Recovering an already-recovered manifest should still succeed."""
        manifest = _make_manifest(state="in_meeting")
        plan = build_recovery_plan(manifest)

        # First recovery
        result1 = recover_meeting(manifest, plan, persist=False)
        assert result1.success

        # Second recovery on same manifest
        assert result1.manifest is not None
        result2 = recover_meeting(result1.manifest, plan, persist=False)
        assert result2.success
        # Recovery event is appended each time (the recovery marker check
        # is in classify_recoverability, not recover_meeting itself)
        assert result2.manifest is not None
        assert len(result2.manifest.error_log) == 2

    def test_recover_preserves_manifest_data(self):
        """Recovery should not lose any existing manifest data."""
        packets = (
            {"round": 1, "role_id": "r1", "created_at": "t1"},
            {"round": 1, "role_id": "r2", "created_at": "t2"},
        )
        decisions = (
            {"round": 1, "decision_id": "d1", "role_id": "r1", "content": "ok"},
        )
        manifest = _make_manifest(
            state="consensus_building",
            round_count=2,
            agenda="Test agenda content",
            context_packets=packets,
            decisions=decisions,
        )
        plan = build_recovery_plan(manifest)
        result = recover_meeting(manifest, plan, persist=False)

        assert result.manifest is not None
        recovered = result.manifest
        assert recovered.agenda == "Test agenda content"
        assert recovered.round_count == 2
        assert len(recovered.context_packets) == 2
        assert len(recovered.decisions) == 1

    def test_recover_with_mock_persist(self):
        """Verify the on_persist callback is used when provided."""
        manifest = _make_manifest(state="in_meeting")
        plan = build_recovery_plan(manifest)

        persist_calls: list[MeetingManifest] = []

        def mock_persist(m: MeetingManifest) -> MeetingManifest:
            persist_calls.append(m)
            return m

        result = recover_meeting(
            manifest, plan, persist=True, on_persist=mock_persist
        )
        assert result.success
        assert len(persist_calls) == 1
        assert persist_calls[0].state == plan.resume_state

    def test_recover_persist_failure_reported(self):
        """When persistence fails, the result should report the error."""
        manifest = _make_manifest(state="in_meeting")
        plan = build_recovery_plan(manifest)

        def failing_persist(m: MeetingManifest) -> MeetingManifest:
            raise OSError("Disk full")

        result = recover_meeting(
            manifest, plan, persist=True, on_persist=failing_persist
        )
        assert not result.success
        assert result.error is not None
        assert isinstance(result.error, OSError)
        assert "Disk full" in result.message
        # In-memory state is still available
        assert result.manifest is not None
        assert result.manifest.state == plan.resume_state

    def test_recover_result_message_is_descriptive(self):
        manifest = _make_manifest(state="validating", round_count=2)
        plan = build_recovery_plan(manifest)
        result = recover_meeting(manifest, plan, persist=False)
        assert "validating" in result.message
        assert "round 2" in result.message


# ── auto_recover_all tests ────────────────────────────────────────────────


class TestAutoRecoverAll:
    """Verify the end-to-end auto_recover_all sweep."""

    def test_empty_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results = auto_recover_all(tmpdir)
            assert results == []

    def test_only_active_meetings_recovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "m1",
                meeting_id="m1",
                state="in_meeting",
            )
            _make_manifest_json(
                Path(tmpdir) / "m2",
                meeting_id="m2",
                state="completed",
            )
            _make_manifest_json(
                Path(tmpdir) / "m3",
                meeting_id="m3",
                state="validating",
            )

            results = auto_recover_all(tmpdir)
            # m2 (completed) = skipped with success=True, message set
            # m1, m3 = recovered
            recovered = [r for r in results if r.manifest is not None]
            assert len(recovered) == 2
            recovered_ids = {r.meeting_id for r in recovered}
            assert recovered_ids == {"m1", "m3"}

    def test_stale_meetings_not_auto_recovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_time = (
                datetime.now(timezone.utc) - timedelta(hours=25)
            ).isoformat()
            _make_manifest_json(
                Path(tmpdir) / "stale_m",
                meeting_id="stale_m",
                state="in_meeting",
                updated_at=old_time,
            )
            results = auto_recover_all(tmpdir)
            assert len(results) == 1
            assert not results[0].success
            assert "operator review" in results[0].message.lower()

    def test_stale_meetings_auto_recovered_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_time = (
                datetime.now(timezone.utc) - timedelta(hours=25)
            ).isoformat()
            _make_manifest_json(
                Path(tmpdir) / "stale_m",
                meeting_id="stale_m",
                state="in_meeting",
                updated_at=old_time,
            )
            results = auto_recover_all(tmpdir, auto_recover_stale=True)
            assert len(results) == 1
            assert results[0].success
            assert results[0].manifest is not None

    def test_already_recovered_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recovery_event = {
                "timestamp": "2026-06-10T00:00:00+00:00",
                "error_type": "crash_recovery",
                "message": "recovered",
                "severity": "info",
                "recovery": "resumed",
            }
            _make_manifest_json(
                Path(tmpdir) / "already",
                meeting_id="already",
                state="in_meeting",
                error_log=[recovery_event],
            )
            results = auto_recover_all(tmpdir)
            assert len(results) == 1
            assert results[0].success
            assert results[0].manifest is None  # skipped, not recovered
            assert "already" in results[0].message

    def test_corrupted_manifest_logged_not_recovered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meeting_dir = Path(tmpdir) / "broken"
            meeting_dir.mkdir(parents=True)
            (meeting_dir / "manifest.json").write_text("{{{broken")
            results = auto_recover_all(tmpdir)
            assert len(results) == 0  # corrupted, scan skips it

    def test_with_mock_persist_for_testing(self):
        """Use on_persist mock to avoid real disk writes during recovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "m1",
                meeting_id="m1",
                state="in_meeting",
            )

            persist_log: list[str] = []

            def mock_persist(m: MeetingManifest) -> MeetingManifest:
                persist_log.append(m.meeting_id)
                return m

            results = auto_recover_all(tmpdir, on_persist=mock_persist)
            assert len(results) == 1
            assert results[0].success
            assert persist_log == ["m1"]


# ── Integration tests ─────────────────────────────────────────────────────


class TestIntegrationCrashRecovery:
    """End-to-end tests: write mid-meeting manifest, simulate restart,
    verify correct lifecycle position on resume."""

    def test_mid_meeting_crash_resume_at_correct_state(self):
        """Simulate a crash in 'in_meeting' state, verify resume at that state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a mid-meeting manifest with speaker, packets, decisions
            packets = [
                {"round": 1, "role_id": "r1", "created_at": "t1"},
                {"round": 1, "role_id": "r2", "created_at": "t2"},
            ]
            decisions = [
                {"round": 1, "decision_id": "d1", "role_id": "r1", "content": "ok"},
            ]
            manifest_path, data = _make_manifest_json(
                Path(tmpdir) / "mid_crash",
                meeting_id="mid_crash",
                state="in_meeting",
                round_count=1,
                current_speaker="producer-kim",
                speaker_queue=["producer-kim", "director-lee"],
                context_packets=packets,
                decisions=decisions,
                completed_step="context_retrieval",
            )

            # Simulate startup: scan → classify → build plan → recover
            incomplete = scan_for_incomplete_manifests(tmpdir)
            assert len(incomplete) == 1
            manifest = incomplete[0]

            verdict = classify_recoverability(manifest)
            assert verdict.is_recoverable
            assert verdict.is_auto_recoverable

            plan = build_recovery_plan(manifest)
            # Should resume from completed_step=context_retrieval
            assert plan.resume_state == "context_retrieval"
            assert plan.last_state == "in_meeting"
            assert plan.round_count == 1
            assert plan.current_speaker == "producer-kim"
            assert plan.context_packets_count == 2
            assert plan.decisions_count == 1

            # Execute recovery
            result = recover_meeting(manifest, plan, persist=False)
            assert result.success
            assert result.manifest is not None

            recovered = result.manifest
            assert recovered.state == "context_retrieval"
            assert recovered.round_count == 1
            assert recovered.current_speaker == "producer-kim"
            assert len(recovered.context_packets) == 2
            assert len(recovered.decisions) == 1
            # Verify recovery event was logged
            assert any(
                e["error_type"] == "crash_recovery"
                for e in recovered.error_log
            )

    def test_created_state_resume_at_created(self):
        """A meeting that was created but never queued should resume at created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "fresh",
                meeting_id="fresh",
                state="created",
                round_count=0,
            )
            incomplete = scan_for_incomplete_manifests(tmpdir)
            assert len(incomplete) == 1

            plan = build_recovery_plan(incomplete[0])
            assert plan.resume_state == "created"
            assert plan.round_count == 0

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.manifest is not None
            assert result.manifest.state == "created"

    def test_validating_state_resume_at_validating(self):
        """A meeting mid-validation should resume at validating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "validating_m",
                meeting_id="validating_m",
                state="validating",
                round_count=2,
                completed_step="consensus_building",
            )
            incomplete = scan_for_incomplete_manifests(tmpdir)
            plan = build_recovery_plan(incomplete[0])
            # completed_step is consensus_building, so resume there
            assert plan.resume_state == "consensus_building"
            assert plan.last_state == "validating"

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.manifest is not None
            assert result.manifest.state == "consensus_building"

    def test_paused_state_resume(self):
        """A paused meeting should be recoverable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "paused_m",
                meeting_id="paused_m",
                state="paused",
                round_count=1,
            )
            incomplete = scan_for_incomplete_manifests(tmpdir)
            plan = build_recovery_plan(incomplete[0])
            assert plan.resume_state == "paused"

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.success
            assert result.manifest is not None
            assert result.manifest.state == "paused"

    def test_deadlocked_state_resume(self):
        """A deadlocked meeting should be recoverable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "deadlocked_m",
                meeting_id="deadlocked_m",
                state="deadlocked",
                round_count=3,
            )
            incomplete = scan_for_incomplete_manifests(tmpdir)
            plan = build_recovery_plan(incomplete[0])
            assert plan.resume_state == "deadlocked"

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.success

    def test_full_scan_to_recover_pipeline(self):
        """End-to-end: multiple meetings, mixed states, verify only
        recoverable ones get recovered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Terminal — skipped
            _make_manifest_json(
                Path(tmpdir) / "completed",
                meeting_id="completed",
                state="completed",
            )
            # Active — recovered
            _make_manifest_json(
                Path(tmpdir) / "active",
                meeting_id="active",
                state="consensus_building",
                round_count=2,
            )
            # Active with completed_step — recovered
            _make_manifest_json(
                Path(tmpdir) / "active2",
                meeting_id="active2",
                state="validating",
                completed_step="consensus_building",
                round_count=2,
            )
            # Already recovered — skipped
            recovery_event = {
                "timestamp": "t1",
                "error_type": "crash_recovery",
                "message": "prev",
                "severity": "info",
                "recovery": "done",
            }
            _make_manifest_json(
                Path(tmpdir) / "already",
                meeting_id="already",
                state="in_meeting",
                error_log=[recovery_event],
            )

            # Run the pipeline
            incomplete = scan_for_incomplete_manifests(tmpdir)
            incomplete_ids = {m.meeting_id for m in incomplete}
            # 'completed' is terminal → excluded from scan
            assert "completed" not in incomplete_ids
            assert "active" in incomplete_ids
            assert "active2" in incomplete_ids
            assert "already" in incomplete_ids  # scan returns it, classify skips

            recovery_results = []
            for manifest in incomplete:
                verdict = classify_recoverability(manifest)
                if verdict.is_auto_recoverable:
                    plan = build_recovery_plan(manifest)
                    result = recover_meeting(manifest, plan, persist=False)
                    recovery_results.append(result)

            assert len(recovery_results) == 2
            recovered_ids = {r.meeting_id for r in recovery_results}
            assert recovered_ids == {"active", "active2"}

            # Verify recovered state for active2 (completed_step → resume)
            active2_result = next(
                r for r in recovery_results if r.meeting_id == "active2"
            )
            assert active2_result.manifest.state == "consensus_building"


# ── Edge case tests ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions for crash recovery."""

    def test_empty_error_log_ok(self):
        manifest = _make_manifest(state="in_meeting", error_log=())
        verdict = classify_recoverability(manifest)
        assert verdict.is_recoverable

    def test_non_recovery_error_log_entries_ignored(self):
        """Only crash_recovery error_type triggers already_recovered."""
        manifest = _make_manifest(
            state="in_meeting",
            error_log=(
                {
                    "timestamp": "t1",
                    "error_type": "network_timeout",
                    "message": "timeout",
                    "severity": "error",
                    "recovery": "retry",
                },
            ),
        )
        verdict = classify_recoverability(manifest)
        assert verdict.is_recoverable
        assert verdict.verdict == Recoverability.RECOVERABLE

    def test_missing_updated_at_treated_as_stale(self):
        manifest = _make_manifest(
            state="in_meeting", updated_at=""
        )
        age = _manifest_age_seconds(manifest)
        assert age == float("inf")
        verdict = classify_recoverability(manifest)
        assert verdict.verdict == Recoverability.STALE_RECOVERABLE

    def test_unparseable_updated_at_treated_as_stale(self):
        manifest = _make_manifest(
            state="in_meeting", updated_at="not-a-date"
        )
        age = _manifest_age_seconds(manifest)
        assert age == float("inf")

    def test_recoverability_verdict_properties(self):
        """Test the convenience properties on RecoverabilityVerdict."""
        v1 = RecoverabilityVerdict(
            verdict=Recoverability.RECOVERABLE,
            reason="test",
            meeting_id="m1",
        )
        assert v1.is_recoverable
        assert v1.is_auto_recoverable

        v2 = RecoverabilityVerdict(
            verdict=Recoverability.STALE_RECOVERABLE,
            reason="test",
            meeting_id="m2",
        )
        assert v2.is_recoverable
        assert not v2.is_auto_recoverable

        v3 = RecoverabilityVerdict(
            verdict=Recoverability.TERMINAL,
            reason="test",
            meeting_id="m3",
        )
        assert not v3.is_recoverable
        assert not v3.is_auto_recoverable

    def test_recovery_plan_can_auto_recover(self):
        plan_fresh = RecoveryPlan(
            meeting_id="m1",
            last_state="in_meeting",
            resume_state="in_meeting",
            round_count=0,
            stale=False,
        )
        assert plan_fresh.can_auto_recover

        plan_stale = RecoveryPlan(
            meeting_id="m2",
            last_state="in_meeting",
            resume_state="in_meeting",
            round_count=0,
            stale=True,
        )
        assert not plan_stale.can_auto_recover

    def test_recovery_entry_result_with_none_plan(self):
        result = RecoveryEntryResult(
            meeting_id="m1",
            success=False,
            plan=None,
            manifest=None,
            message="skipped",
        )
        assert result.plan is None
        assert result.manifest is None
        assert not result.success

    def test_scan_with_meetings_root_as_path_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_manifest_json(
                Path(tmpdir) / "m1",
                meeting_id="m1",
                state="created",
            )
            result = scan_for_incomplete_manifests(Path(tmpdir))
            assert len(result) == 1

    def test_resolve_resume_state_invalid_completed_step(self):
        """If completed_step is not a recoverable state, fall back to current."""
        manifest = _make_manifest(
            state="validating",
            completed_step="completed",  # terminal, not recoverable
        )
        resolved = _resolve_resume_state(manifest)
        assert resolved == "validating"

    def test_has_recovery_marker_false_for_empty_log(self):
        manifest = _make_manifest(state="in_meeting", error_log=())
        assert not _has_recovery_marker(manifest)

    def test_has_recovery_marker_true(self):
        manifest = _make_manifest(
            state="in_meeting",
            error_log=(
                {
                    "timestamp": "t",
                    "error_type": "crash_recovery",
                    "message": "x",
                    "severity": "info",
                    "recovery": "y",
                },
            ),
        )
        assert _has_recovery_marker(manifest)


# ── Integration with real create_meeting ───────────────────────────────────


class TestIntegrationWithCreateMeeting:
    """Verify crash recovery works with real MeetingManifest from create_meeting."""

    def test_recover_freshly_created_meeting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            req = MeetingCommandRequest(
                agenda="Test crash recovery meeting",
                user_id="u-test",
                channel_id="c-test",
                priority="p1",
            )
            ctx = create_meeting(req, meetings_root=tmpdir)

            # The meeting was created but Coordinator "crashed" before
            # transitioning to queued.  Simulate restart:
            incomplete = scan_for_incomplete_manifests(tmpdir)
            assert len(incomplete) == 1
            assert incomplete[0].meeting_id == ctx.meeting_id
            assert incomplete[0].state == "created"

            verdict = classify_recoverability(incomplete[0])
            assert verdict.is_recoverable

            plan = build_recovery_plan(incomplete[0])
            assert plan.resume_state == "created"
            assert plan.round_count == 0

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.success
            assert result.manifest is not None
            assert result.manifest.state == "created"

    def test_recover_meeting_after_queued_transition(self):
        """Simulate: meeting created → queued, then crash. Resume at queued."""
        with tempfile.TemporaryDirectory() as tmpdir:
            req = MeetingCommandRequest(
                agenda="Test meeting",
                user_id="u-test",
                channel_id="c-test",
            )
            ctx = create_meeting(req, meetings_root=tmpdir)

            # Coordinator transitions to queued
            transition_result = execute_transition(
                ctx.manifest,
                LifecycleState.QUEUED,
                label="test-enqueue",
            )
            assert transition_result.success

            # Now "crash" — simulate restart scan
            incomplete = scan_for_incomplete_manifests(tmpdir)
            assert len(incomplete) == 1
            assert incomplete[0].state == "queued"

            plan = build_recovery_plan(incomplete[0])
            # completed_step should be "created" (set by with_state)
            # but we're currently at "queued"
            assert plan.last_state == "queued"

            result = recover_meeting(incomplete[0], plan, persist=False)
            assert result.success

    def test_recover_with_manifest_persistence(self):
        """Recover a meeting and actually persist the change."""
        with tempfile.TemporaryDirectory() as tmpdir:
            req = MeetingCommandRequest(
                agenda="Persist recovery test",
                user_id="u-test",
                channel_id="c-test",
            )
            ctx = create_meeting(req, meetings_root=tmpdir)

            # Transition to in_meeting to create mid-meeting state
            for state in [
                LifecycleState.QUEUED,
                LifecycleState.ROUTING,
                LifecycleState.CONTEXT_RETRIEVAL,
                LifecycleState.IN_MEETING,
            ]:
                result = execute_transition(
                    ctx.manifest, state, label=f"test-{state}"
                )
                assert result.success
                ctx = type(ctx)(
                    meeting_id=ctx.meeting_id,
                    manifest=result.manifest,
                    meeting_dir=ctx.meeting_dir,
                    manifest_path=ctx.manifest_path,
                    rounds_dir=ctx.rounds_dir,
                    raw_outputs_dir=ctx.raw_outputs_dir,
                    decisions_dir=ctx.decisions_dir,
                    knowledge_dir=ctx.knowledge_dir,
                    config=ctx.config,
                )

            # Now simulate crash and recovery with real persistence
            incomplete = scan_for_incomplete_manifests(tmpdir)
            assert len(incomplete) == 1

            plan = build_recovery_plan(incomplete[0])
            result = recover_meeting(incomplete[0], plan, persist=True)

            assert result.success
            assert result.manifest is not None

            # Verify the persisted manifest has the recovery event
            reloaded = load_manifest(result.manifest.manifest_path)
            assert any(
                e["error_type"] == "crash_recovery"
                for e in reloaded.error_log
            )
            # The reloaded manifest should be in the resume state
            assert reloaded.state == plan.resume_state
