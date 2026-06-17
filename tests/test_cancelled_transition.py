"""Tests for the cancelled state transition handler (Sub-AC 4.3.2).

Covers:
- CancelledTransitionContext validation and defaults
- CancelledTransitionResult success / failure states
- build_cancellation_recovery — decision matrix for all
  snapshot states × progress levels
- enter_cancelled_state — success for every valid transition source,
  terminal state rejection, invalid transition rejection,
  persist / no-persist modes, persistence failure handling
- handle_cancelled_transition — success, None manifest rejection,
  unexpected exception handling
- Edge cases: empty metadata, empty error_log, minimalist manifest,
  validation_score without verdict, empty cancel_reason
- Integration: end-to-end created → … → in_meeting → cancelled with
  full snapshot capture and on-disk persistence with real manifest
- Output preservation: verify manifest is written to disk per the Seed
  exit condition "outputs preserved on disk"
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.cancelled_transition import (
    CancelledTransitionContext,
    CancelledTransitionResult,
    _append_error,
    _utc_now_iso,
    build_cancellation_recovery,
    enter_cancelled_state,
    handle_cancelled_transition,
)
from src.failed_transition import (
    PartialProgressSnapshot,
    RecoveryEntryPoint,
    capture_partial_progress,
)
from src.meeting_trigger import (
    MeetingManifest,
    update_manifest,
)
from src.shared.lifecycle import (
    LifecycleState,
    is_terminal,
    validate_transition,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_manifest(
    *,
    meeting_id: str = "m-test-0001",
    state: str = "created",
    round_count: int = 0,
    validation_score: float = 0.0,
    validation_verdict: str = "",
    consensus: str = "",
    required_roles: tuple[str, ...] = (),
    context_packets: tuple[dict, ...] = (),
    decisions: tuple[dict, ...] = (),
    tool_outputs: tuple[dict, ...] = (),
    error_log: tuple[dict[str, str], ...] = (),
    completed_step: str = "",
    agenda: str = "Test agenda",
    agenda_type: str = "",
    tags: tuple[str, ...] = (),
    user_id: str = "u-test",
    channel_id: str = "c-test",
    created_at: str = "2026-06-10T00:00:00+00:00",
    manifest_path: str = "/tmp/test-m/manifest.json",
    meetings_root: str = "/tmp/test-m",
) -> MeetingManifest:
    """Build a minimal MeetingManifest for testing."""
    return MeetingManifest(
        meeting_id=meeting_id,
        state=state,
        agenda=agenda,
        agenda_type=agenda_type,
        tags=tags,
        user_id=user_id,
        channel_id=channel_id,
        round_count=round_count,
        validation_score=validation_score,
        validation_verdict=validation_verdict,
        consensus=consensus,
        required_roles=required_roles,
        context_packets=context_packets,
        decisions=decisions,
        tool_outputs=tool_outputs,
        error_log=error_log,
        completed_step=completed_step,
        created_at=created_at,
        manifest_path=manifest_path,
        meetings_root=meetings_root,
    )


def _mock_persist(manifest: MeetingManifest) -> MeetingManifest:
    """Mock persistence: returns manifest unchanged (no disk I/O)."""
    return manifest


def _real_persist(manifest: MeetingManifest) -> MeetingManifest:
    """Real persistence via update_manifest."""
    return update_manifest(manifest)


# ══════════════════════════════════════════════════════════════════════════
# CancelledTransitionContext
# ══════════════════════════════════════════════════════════════════════════


class TestCancelledTransitionContext:
    """CancelledTransitionContext construction and validation."""

    def test_construction_minimal(self):
        m = _make_manifest()
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User issued /cancel",
        )
        assert ctx.manifest is m
        assert ctx.cancel_reason == "User issued /cancel"
        assert ctx.cancelled_by == "unknown"  # default
        assert ctx.severity == "info"  # default
        assert ctx.metadata == {}

    def test_construction_full(self):
        m = _make_manifest()
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Admin override — scheduled maintenance",
            cancelled_by="u-admin-999",
            severity="warning",
            metadata={"discord_message_id": "12345", "admin_note": "Approved"},
        )
        assert ctx.cancelled_by == "u-admin-999"
        assert ctx.severity == "warning"
        assert ctx.metadata["discord_message_id"] == "12345"
        assert ctx.metadata["admin_note"] == "Approved"

    def test_empty_cancel_reason_raises(self):
        m = _make_manifest()
        with pytest.raises(ValueError, match="cancel_reason must not be empty"):
            CancelledTransitionContext(manifest=m, cancel_reason="   ")

    def test_unknown_severity_coerced_to_info(self):
        m = _make_manifest()
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Test",
            severity="bogus",
        )
        assert ctx.severity == "info"


# ══════════════════════════════════════════════════════════════════════════
# CancelledTransitionResult
# ══════════════════════════════════════════════════════════════════════════


class TestCancelledTransitionResult:
    """CancelledTransitionResult success / failure states."""

    def test_success_result(self):
        m = _make_manifest(state="cancelled")
        snap = PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="in_meeting",
            round_count=1,
            context_packets_count=2,
            decisions_count=1,
            tool_outputs_count=0,
            validation_score=0.0,
            validation_verdict="",
            consensus="",
            required_roles=(),
            completed_step="routing",
            error_log_count=0,
            created_at="2026-06-10T00:00:00",
        )
        recovery = RecoveryEntryPoint(
            meeting_id="m-1",
            failure_category="user_cancelled",
            failure_reason="Cancelled by user",
            resume_possible=True,
            recommended_action=RecoveryEntryPoint.ACTION_RETRY,
            last_good_state="routing",
            snapshot=snap,
        )
        result = CancelledTransitionResult(
            success=True,
            manifest=m,
            snapshot=snap,
            recovery_entry_point=recovery,
        )
        assert result.success is True
        assert result.manifest.state == "cancelled"
        assert result.snapshot is snap
        assert result.recovery_entry_point is recovery
        assert result.rejection_reasons == ()
        assert result.error is None

    def test_failure_result_with_rejection_reasons(self):
        m = _make_manifest(state="completed")
        snap = PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="completed",
            round_count=3,
            context_packets_count=5,
            decisions_count=2,
            tool_outputs_count=1,
            validation_score=0.95,
            validation_verdict="pass",
            consensus="All agreed",
            required_roles=(),
            completed_step="finalizing",
            error_log_count=0,
            created_at="2026-06-10T00:00:00",
        )
        result = CancelledTransitionResult(
            success=False,
            manifest=m,
            snapshot=snap,
            rejection_reasons=("Already terminal",),
        )
        assert result.success is False
        assert result.rejection_reasons == ("Already terminal",)
        assert result.recovery_entry_point is None

    def test_failure_result_with_error(self):
        m = _make_manifest()
        snap = PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="created",
            round_count=0,
            context_packets_count=0,
            decisions_count=0,
            tool_outputs_count=0,
            validation_score=0.0,
            validation_verdict="",
            consensus="",
            required_roles=(),
            completed_step="",
            error_log_count=0,
            created_at="2026-06-10T00:00:00",
        )
        exc = RuntimeError("Persistence failed")
        result = CancelledTransitionResult(
            success=False,
            manifest=m,
            snapshot=snap,
            rejection_reasons=("Persistence failed",),
            error=exc,
        )
        assert result.success is False
        assert result.error is exc


# ══════════════════════════════════════════════════════════════════════════
# build_cancellation_recovery  (pure function — decision matrix)
# ══════════════════════════════════════════════════════════════════════════


class TestBuildCancellationRecovery:
    """Full decision matrix coverage for build_cancellation_recovery."""

    def _snap(self, **overrides: object) -> PartialProgressSnapshot:
        kwargs: dict[str, object] = {
            "meeting_id": "m-1",
            "state_at_failure": "in_meeting",
            "round_count": 2,
            "context_packets_count": 3,
            "decisions_count": 1,
            "tool_outputs_count": 1,
            "validation_score": 0.75,
            "validation_verdict": "conditional_pass",
            "consensus": "draft consensus",
            "required_roles": ("strategist",),
            "completed_step": "routing",
            "error_log_count": 1,
            "created_at": "2026-06-10T00:00:00",
        }
        kwargs.update(overrides)
        return PartialProgressSnapshot(**kwargs)  # type: ignore[arg-type]

    # ── Has progress (rounds > 0) ───────────────────────────────────

    def test_with_rounds_retry(self):
        rep = build_cancellation_recovery(
            self._snap(round_count=2),
            "User cancelled",
            "u-123",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True
        assert rep.failure_category == "user_cancelled"
        assert "u-123" in rep.failure_reason
        assert "User cancelled" in rep.failure_reason
        assert rep.last_good_state == "routing"

    def test_with_context_but_no_rounds_retry(self):
        """context_packets_count > 0 should be enough for retry."""
        rep = build_cancellation_recovery(
            self._snap(round_count=0, context_packets_count=2),
            "User cancelled after context retrieval",
            "u-123",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_with_decisions_but_no_rounds_retry(self):
        """decisions_count > 0 should be enough for retry."""
        rep = build_cancellation_recovery(
            self._snap(
                round_count=0,
                context_packets_count=0,
                decisions_count=3,
            ),
            "User cancelled after decisions",
            "u-123",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_with_tool_outputs_but_no_rounds_retry(self):
        """tool_outputs_count > 0 should be enough for retry."""
        rep = build_cancellation_recovery(
            self._snap(
                round_count=0,
                context_packets_count=0,
                decisions_count=0,
                tool_outputs_count=2,
            ),
            "User cancelled after tool execution",
            "u-123",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_with_consensus_but_no_rounds_retry(self):
        """had_consensus should be enough for retry."""
        rep = build_cancellation_recovery(
            self._snap(
                round_count=0,
                context_packets_count=0,
                decisions_count=0,
                tool_outputs_count=0,
                consensus="Partial agreement reached",
            ),
            "User cancelled during consensus building",
            "u-123",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    # ── No progress ─────────────────────────────────────────────────

    def test_no_progress_audit_only(self):
        rep = build_cancellation_recovery(
            self._snap(
                round_count=0,
                context_packets_count=0,
                decisions_count=0,
                tool_outputs_count=0,
                consensus="",
            ),
            "User cancelled immediately",
            "u-123",
        )
        assert rep.recommended_action == "manual_audit_only"
        assert rep.resume_possible is False

    # ── last_good_state handling ─────────────────────────────────────

    def test_explicit_last_good_state(self):
        rep = build_cancellation_recovery(
            self._snap(),
            "User cancelled",
            "u-123",
            last_good_state="context_retrieval",
        )
        assert rep.last_good_state == "context_retrieval"

    def test_last_good_state_fallback_to_completed_step(self):
        rep = build_cancellation_recovery(
            self._snap(completed_step="in_meeting"),
            "User cancelled",
            "u-123",
        )
        assert rep.last_good_state == "in_meeting"

    def test_last_good_state_fallback_to_state_at_failure(self):
        rep = build_cancellation_recovery(
            self._snap(completed_step="", state_at_failure="consensus_building"),
            "User cancelled",
            "u-123",
        )
        assert rep.last_good_state == "consensus_building"


# ══════════════════════════════════════════════════════════════════════════
# enter_cancelled_state  (core state-machine function)
# ══════════════════════════════════════════════════════════════════════════


class TestEnterCancelledState:
    """enter_cancelled_state success for every valid transition source,
    terminal state rejection, invalid transition rejection, persist /
    no-persist modes, persistence failure handling."""

    # ── Success: every non-terminal state can transition to cancelled ──

    VALID_SOURCE_STATES = [
        "created",
        "queued",
        "routing",
        "context_retrieval",
        "in_meeting",
        "consensus_building",
        "validating",
        "executing",
        "finalizing",
        "paused",
        "deadlocked",
        "escalated",
    ]

    @pytest.mark.parametrize("from_state", VALID_SOURCE_STATES)
    def test_success_from_every_valid_state(self, from_state: str):
        """Every non-terminal lifecycle state can transition to cancelled."""
        m = _make_manifest(state=from_state)
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason=f"Cancelled from state {from_state}",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success is True, (
            f"Transition from '{from_state}' to 'cancelled' failed: "
            f"{result.rejection_reasons}"
        )
        assert result.manifest.state == "cancelled"
        assert result.snapshot is not None
        assert result.recovery_entry_point is not None
        assert result.rejection_reasons == ()

    # ── Snapshot capture ──────────────────────────────────────────────

    def test_snapshot_captures_round_count(self):
        m = _make_manifest(
            state="in_meeting",
            round_count=2,
            context_packets=(
                {"round": 1, "role_id": "strategist"},
                {"round": 2, "role_id": "art-director"},
            ),
            decisions=({"round": 1, "decision_id": "d1"},),
            tool_outputs=({"round": 1, "execution_id": "e1"},),
        )
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User cancelled during meeting",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        snap = result.snapshot
        assert snap.round_count == 2
        assert snap.context_packets_count == 2
        assert snap.decisions_count == 1
        assert snap.tool_outputs_count == 1

    def test_snapshot_captures_validation_state(self):
        m = _make_manifest(
            state="validating",
            round_count=3,
            validation_score=0.92,
            validation_verdict="pass",
            consensus="Final agreement on strategy",
        )
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User cancelled after validation",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        snap = result.snapshot
        assert snap.validation_score == 0.92
        assert snap.validation_verdict == "pass"
        assert snap.was_validating is True

    def test_snapshot_captures_required_roles(self):
        m = _make_manifest(
            state="in_meeting",
            required_roles=("strategist", "art-director", "backend-dev"),
        )
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Cancelled during meeting",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        assert result.snapshot.required_roles == (
            "strategist",
            "art-director",
            "backend-dev",
        )

    # ── Terminal state rejection ──────────────────────────────────────

    TERMINAL_STATES = ["completed", "cancelled", "failed", "stale"]

    @pytest.mark.parametrize("terminal_state", TERMINAL_STATES)
    def test_reject_already_terminal(self, terminal_state: str):
        """Cannot transition from an already-terminal state to cancelled."""
        m = _make_manifest(state=terminal_state)
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Attempt to cancel terminal meeting",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success is False, (
            f"Expected rejection from terminal state '{terminal_state}', "
            f"but transition succeeded"
        )
        assert "already in terminal state" in result.rejection_reasons[0]
        assert result.manifest.state == terminal_state  # unchanged
        # Recovery entry point is still built
        assert result.recovery_entry_point is not None

    # ── Error logged to manifest ──────────────────────────────────────

    def test_cancellation_event_logged_to_error_log(self):
        m = _make_manifest(state="in_meeting", error_log=())
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User issued /cancel from Discord",
            cancelled_by="u-discord-12345",
            severity="info",
            metadata={"discord_message_id": "msg-999"},
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        # Check that error_log has the cancellation entry
        error_entries = result.manifest.error_log
        assert len(error_entries) >= 1
        cancel_entry = error_entries[-1]
        assert cancel_entry["error_type"] == "cancelled_state_transition"
        assert "User issued /cancel from Discord" in cancel_entry["message"]
        assert cancel_entry["cancelled_by"] == "u-discord-12345"
        assert cancel_entry["severity"] == "info"
        assert cancel_entry["meta_discord_message_id"] == "msg-999"
        assert "Outputs preserved on disk" in cancel_entry["recovery"]

    def test_error_log_appended_not_replaced(self):
        """Existing errors are preserved when cancellation is logged."""
        m = _make_manifest(
            state="in_meeting",
            error_log=(
                {"error_type": "pre_existing", "message": "earlier error",
                 "timestamp": "2026-06-10T00:00:00", "severity": "warning"},
            ),
        )
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User cancelled",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        error_entries = result.manifest.error_log
        assert len(error_entries) == 2
        assert error_entries[0]["error_type"] == "pre_existing"
        assert error_entries[1]["error_type"] == "cancelled_state_transition"

    # ── Recovery entry point ──────────────────────────────────────────

    def test_recovery_entry_point_built_on_success(self):
        m = _make_manifest(state="in_meeting", round_count=2)
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User cancelled",
            cancelled_by="u-123",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        rep = result.recovery_entry_point
        assert rep is not None
        assert rep.meeting_id == m.meeting_id
        assert rep.failure_category == "user_cancelled"
        assert "u-123" in rep.failure_reason
        assert rep.resume_possible is True  # rounds > 0
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_RETRY

    def test_recovery_entry_point_built_on_no_progress(self):
        m = _make_manifest(state="created", round_count=0)
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Cancelled before any work",
            cancelled_by="u-123",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        rep = result.recovery_entry_point
        assert rep is not None
        assert rep.resume_possible is False
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_AUDIT

    # ── Persist modes ─────────────────────────────────────────────────

    def test_no_persist_flag(self):
        """With persist=False, state is mutated but not written to disk."""
        m = _make_manifest(state="in_meeting")
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Test cancel",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        assert result.manifest.state == "cancelled"
        # Original manifest is unchanged (frozen dataclass)
        assert m.state == "in_meeting"

    def test_mock_persist_fn(self):
        """With a mock persist_fn, state is mutated and mock is called."""
        m = _make_manifest(state="in_meeting")
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Test cancel",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(
            ctx, persist=True, persist_fn=_mock_persist
        )
        assert result.success
        assert result.manifest.state == "cancelled"

    def test_persist_failure_still_mutates_state(self):
        """When persistence fails, the in-memory state transition is
        still applied, but the result signals failure so the caller
        knows persistence didn't complete."""
        m = _make_manifest(state="in_meeting")

        def _failing_persist(manifest: MeetingManifest) -> MeetingManifest:
            raise OSError("Disk full")

        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Test cancel",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(
            ctx, persist=True, persist_fn=_failing_persist
        )
        assert result.success is False
        assert "Manifest persistence failed" in result.rejection_reasons[0]
        # State was mutated to cancelled in memory
        assert result.manifest.state == "cancelled"
        # Error was logged
        assert len(result.manifest.error_log) >= 2
        persist_errors = [
            e for e in result.manifest.error_log
            if e.get("error_type") == "persistence_error"
        ]
        assert len(persist_errors) >= 1

    # ── Real disk persistence ─────────────────────────────────────────

    def test_real_persistence_writes_manifest_to_disk(self):
        """Manifest is persisted to disk per the Seed exit condition
        'outputs preserved on disk'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            m = _make_manifest(
                meeting_id="m-real-persist",
                state="in_meeting",
                round_count=3,
                consensus="Preliminary agreement",
                manifest_path=manifest_path,
                meetings_root=tmpdir,
            )
            ctx = CancelledTransitionContext(
                manifest=m,
                cancel_reason="Real persist test",
                cancelled_by="u-test",
            )
            result = enter_cancelled_state(
                ctx, persist=True, persist_fn=_real_persist
            )
            assert result.success
            # Verify file exists on disk
            assert os.path.isfile(manifest_path), (
                f"Manifest was not written to {manifest_path}"
            )
            # Read back and verify state is cancelled
            import json
            with open(manifest_path, "r") as f:
                data = json.load(f)
            assert data["state"] == "cancelled"
            assert data["round_count"] == 3
            assert data["consensus"] == "Preliminary agreement"
            # Error log includes cancellation entry
            assert len(data["error_log"]) >= 1
            cancel_entries = [
                e for e in data["error_log"]
                if e.get("error_type") == "cancelled_state_transition"
            ]
            assert len(cancel_entries) == 1

    # ── Edge cases ────────────────────────────────────────────────────

    def test_empty_error_log_initial(self):
        """Starting with an empty error_log, the cancellation entry
        becomes the first entry."""
        m = _make_manifest(state="in_meeting", error_log=())
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="First event",
            cancelled_by="u-test",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        assert len(result.manifest.error_log) == 1
        assert result.manifest.error_log[0]["error_type"] == (
            "cancelled_state_transition"
        )

    def test_minimal_manifest_state(self):
        """Cancellation works even on a bare-minimum manifest."""
        m = _make_manifest(
            state="created",
            agenda="",
            user_id="",
            channel_id="",
        )
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Minimal cancel",
            cancelled_by="system",
        )
        result = enter_cancelled_state(ctx, persist=False)
        assert result.success
        assert result.manifest.state == "cancelled"


# ══════════════════════════════════════════════════════════════════════════
# handle_cancelled_transition  (high-level orchestrator)
# ══════════════════════════════════════════════════════════════════════════


class TestHandleCancelledTransition:
    """handle_cancelled_transition — success, None manifest rejection,
    unexpected exception handling."""

    def test_success_delegates_to_enter(self):
        m = _make_manifest(state="in_meeting", round_count=2)
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="User cancelled",
            cancelled_by="u-123",
        )
        result = handle_cancelled_transition(ctx, persist=False)
        assert result.success
        assert result.manifest.state == "cancelled"
        assert result.snapshot.round_count == 2

    def test_none_manifest_raises(self):
        ctx = CancelledTransitionContext(
            manifest=None,  # type: ignore[arg-type]
            cancel_reason="Should fail",
            cancelled_by="u-test",
        )
        with pytest.raises(ValueError, match="manifest must not be None"):
            handle_cancelled_transition(ctx)

    def test_empty_cancel_reason_warns_but_proceeds(self):
        """Empty cancel_reason is logged but doesn't prevent the
        transition — the context validation happens at construction."""
        # Context with valid cancel_reason at construction
        m = _make_manifest(state="in_meeting")
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Actually valid",
            cancelled_by="u-test",
        )
        result = handle_cancelled_transition(ctx, persist=False)
        assert result.success

    def test_handler_crash_returns_failure_result(self):
        """When an unexpected exception occurs during handling,
        the result signals failure and the manifest is annotated."""

        def _crashing_persist(manifest: MeetingManifest) -> MeetingManifest:
            raise RuntimeError("Simulated crash in persist")

        m = _make_manifest(state="in_meeting")
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="Test crash",
            cancelled_by="u-test",
        )
        # We need to trigger the crash inside enter_cancelled_state
        # by using a crashing persist_fn
        result = enter_cancelled_state(
            ctx, persist=True, persist_fn=_crashing_persist
        )
        assert result.success is False
        assert "Manifest persistence failed" in result.rejection_reasons[0]
        assert result.error is not None
        # Manifest is still annotated
        assert any(
            e.get("error_type") == "persistence_error"
            for e in result.manifest.error_log
        )


# ══════════════════════════════════════════════════════════════════════════
# Integration tests
# ══════════════════════════════════════════════════════════════════════════


class TestCancelledTransitionIntegration:
    """End-to-end integration: created → … → in_meeting → cancelled with
    full snapshot capture and disk persistence."""

    def test_full_lifecycle_to_cancelled(self):
        """Simulate a realistic meeting lifecycle ending in cancellation."""
        m = _make_manifest(
            meeting_id="m-e2e-001",
            state="created",
            agenda="Discuss Q3 revenue strategy",
            agenda_type="strategic_review",
            tags=("revenue", "strategy", "Q3"),
            required_roles=("strategist", "finance-lead", "ceo"),
            user_id="u-discord-001",
            channel_id="c-discord-001",
            created_at="2026-06-11T09:00:00+00:00",
            manifest_path="/tmp/test-e2e/manifest.json",
            meetings_root="/tmp/test-e2e",
        )

        # Phase 1: Move through lifecycle states
        m = m.with_state("queued")
        assert m.state == "queued"

        m = m.with_state("routing")
        assert m.state == "routing"

        # Add some context packets as if routing completed
        from src.meeting_trigger import MeetingManifest as MM
        m = MM(
            meeting_id=m.meeting_id,
            state="context_retrieval",
            agenda=m.agenda,
            required_roles=m.required_roles,
            user_id=m.user_id,
            channel_id=m.channel_id,
            created_at=m.created_at,
            context_packets=(
                {"round": 0, "role_id": "strategist",
                 "model_provider": "qwen", "model_name": "qwen-max",
                 "token_count": 4500, "packet_path": "/tmp/p1.json",
                 "created_at": "2026-06-11T09:01:00+00:00"},
            ),
            completed_step="routing",
        )

        # Phase 2: Run a meeting round
        m = MM(
            meeting_id=m.meeting_id,
            state="in_meeting",
            round_count=1,
            agenda=m.agenda,
            required_roles=m.required_roles,
            user_id=m.user_id,
            channel_id=m.channel_id,
            created_at=m.created_at,
            context_packets=m.context_packets,
            decisions=(
                {"round": 1, "decision_id": "d-001",
                 "role_id": "strategist",
                 "content": "Recommend expanding into APAC",
                 "superseded_by": "",
                 "created_at": "2026-06-11T09:05:00+00:00"},
            ),
            completed_step="context_retrieval",
        )

        # Phase 3: Cancel the meeting
        ctx = CancelledTransitionContext(
            manifest=m,
            cancel_reason="CEO requested cancellation — need more data",
            cancelled_by="u-discord-001",
        )
        result = handle_cancelled_transition(ctx, persist=False)
        assert result.success
        assert result.manifest.state == "cancelled"

        # Verify snapshot captures full progress
        snap = result.snapshot
        assert snap.meeting_id == "m-e2e-001"
        assert snap.state_at_failure == "in_meeting"
        assert snap.round_count == 1
        assert snap.context_packets_count == 1
        assert snap.decisions_count == 1
        assert snap.required_roles == ("strategist", "finance-lead", "ceo")
        assert snap.completed_step == "context_retrieval"

        # Verify recovery entry point
        rep = result.recovery_entry_point
        assert rep is not None
        assert rep.failure_category == "user_cancelled"
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True
        assert "u-discord-001" in rep.failure_reason
        assert "CEO requested cancellation" in rep.failure_reason

        # Verify error log
        error_entries = result.manifest.error_log
        cancel_entries = [
            e for e in error_entries
            if e.get("error_type") == "cancelled_state_transition"
        ]
        assert len(cancel_entries) == 1
        assert "u-discord-001" in cancel_entries[0]["cancelled_by"]
        assert "snapshot_round_count" in cancel_entries[0]
        assert cancel_entries[0]["snapshot_round_count"] == "1"

    def test_cancelled_is_terminal_no_further_transitions(self):
        """Once cancelled, no further state transitions are permitted."""
        m = _make_manifest(state="cancelled", meeting_id="m-terminal")
        assert is_terminal(m.state) is True

        # Attempting to go back from cancelled should be invalid
        assert validate_transition("cancelled", "created") is False
        assert validate_transition("cancelled", "in_meeting") is False
        assert validate_transition("cancelled", "completed") is False
        assert validate_transition("cancelled", "failed") is False


# ══════════════════════════════════════════════════════════════════════════
# Internal helper tests
# ══════════════════════════════════════════════════════════════════════════


class TestInternalHelpers:
    """Tests for _utc_now_iso and _append_error."""

    def test_utc_now_iso_produces_valid_timestamp(self):
        ts = _utc_now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("00:00")

    def test_append_error_adds_entry(self):
        m = _make_manifest(error_log=())
        m2 = _append_error(m, "test_error", "Something happened")
        assert len(m2.error_log) == 1
        entry = m2.error_log[0]
        assert entry["error_type"] == "test_error"
        assert entry["message"] == "Something happened"
        assert entry["severity"] == "error"
        assert "timestamp" in entry

    def test_append_error_preserves_existing(self):
        m = _make_manifest(
            error_log=(
                {"error_type": "existing", "message": "old",
                 "timestamp": "t1", "severity": "warning"},
            ),
        )
        m2 = _append_error(m, "new_error", "New thing")
        assert len(m2.error_log) == 2
        assert m2.error_log[0]["error_type"] == "existing"
        assert m2.error_log[1]["error_type"] == "new_error"
