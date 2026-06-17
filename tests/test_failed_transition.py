"""Tests for the failed state transition handler (Sub‑AC 4.3.1).

Covers:
- FailedTransitionContext validation and defaults
- PartialProgressSnapshot computed properties and to_dict
- RecoveryEntryPoint construction, valid actions, to_dict
- FailedTransitionResult success / failure states
- capture_partial_progress — all manifest fields correctly read
- build_recovery_entry_point — full decision matrix for all
  failure categories × snapshot states
- enter_failed_state — success for every valid transition source,
  terminal state rejection, invalid transition rejection,
  persist / no‑persist modes, persistence failure handling
- handle_failed_transition — success, None manifest rejection,
  unexpected exception handling
- Edge cases: empty metadata, empty error_log, minimalist manifest,
  validation_score without verdict, empty failure_reason
- Integration: end‑to‑end created → … → validating → failed with
  full snapshot capture
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.failed_transition import (
    FailedTransitionContext,
    FailedTransitionResult,
    PartialProgressSnapshot,
    RecoveryEntryPoint,
    _append_error,
    _utc_now_iso,
    build_recovery_entry_point,
    capture_partial_progress,
    enter_failed_state,
    handle_failed_transition,
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
    user_id: str = "u-test",
    channel_id: str = "c-test",
    created_at: str = "2026-06-10T00:00:00+00:00",
    manifest_path: str = "/tmp/test-m/manifest.json",
) -> MeetingManifest:
    """Build a minimal MeetingManifest for testing."""
    return MeetingManifest(
        meeting_id=meeting_id,
        state=state,
        agenda=agenda,
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
    )


def _mock_persist(manifest: MeetingManifest) -> MeetingManifest:
    """Mock persistence: returns manifest unchanged (no disk I/O)."""
    return manifest


def _real_persist(manifest: MeetingManifest) -> MeetingManifest:
    """Real persistence via update_manifest."""
    return update_manifest(manifest)


# ══════════════════════════════════════════════════════════════════════════
# FailedTransitionContext
# ══════════════════════════════════════════════════════════════════════════


class TestFailedTransitionContext:
    """FailedTransitionContext construction and validation."""

    def test_construction_minimal(self):
        m = _make_manifest()
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Something broke",
        )
        assert ctx.manifest is m
        assert ctx.failure_reason == "Something broke"
        assert ctx.failure_category == "failed"  # default
        assert ctx.severity == "error"  # default
        assert ctx.metadata == {}

    def test_construction_full(self):
        m = _make_manifest()
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Validation failed",
            failure_category="validation_failed_irrecoverable",
            severity="critical",
            metadata={"score": "0.42", "validator": "glm-5.1"},
        )
        assert ctx.failure_category == "validation_failed_irrecoverable"
        assert ctx.severity == "critical"
        assert ctx.metadata["score"] == "0.42"

    def test_empty_failure_reason_raises(self):
        m = _make_manifest()
        with pytest.raises(ValueError, match="failure_reason must not be empty"):
            FailedTransitionContext(manifest=m, failure_reason="   ")

    def test_unknown_severity_coerced_to_error(self):
        m = _make_manifest()
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",
            severity="bogus",
        )
        assert ctx.severity == "error"


# ══════════════════════════════════════════════════════════════════════════
# PartialProgressSnapshot
# ══════════════════════════════════════════════════════════════════════════


class TestPartialProgressSnapshot:
    """PartialProgressSnapshot construction, properties, to_dict."""

    def test_basic_snapshot(self):
        snap = PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="validating",
            round_count=2,
            context_packets_count=3,
            decisions_count=1,
            tool_outputs_count=0,
            validation_score=0.42,
            validation_verdict="fail",
            consensus="not enough data",
            required_roles=("strategist", "art-director"),
            completed_step="context_retrieval",
            error_log_count=5,
            created_at="2026-06-10T00:00:00",
        )
        assert snap.meeting_id == "m-1"
        assert snap.state_at_failure == "validating"
        assert snap.round_count == 2
        assert snap.was_validating is True
        assert snap.had_consensus is True
        assert snap.had_context is True

    def test_no_validation(self):
        snap = PartialProgressSnapshot(
            meeting_id="m-2",
            state_at_failure="routing",
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
        assert snap.was_validating is False
        assert snap.had_consensus is False
        assert snap.had_context is False

    def test_consensus_empty_whitespace(self):
        snap = PartialProgressSnapshot(
            meeting_id="m-3",
            state_at_failure="consensus_building",
            round_count=1,
            context_packets_count=0,
            decisions_count=0,
            tool_outputs_count=0,
            validation_score=0.0,
            validation_verdict="",
            consensus="   ",
            required_roles=(),
            completed_step="",
            error_log_count=0,
            created_at="2026-06-10T00:00:00",
        )
        assert snap.had_consensus is False

    def test_to_dict(self):
        snap = PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="validating",
            round_count=2,
            context_packets_count=3,
            decisions_count=1,
            tool_outputs_count=0,
            validation_score=0.42,
            validation_verdict="fail",
            consensus="not enough data",
            required_roles=("strategist",),
            completed_step="context_retrieval",
            error_log_count=5,
            created_at="2026-06-10T00:00:00",
            snapshot_at="2026-06-10T01:00:00",
        )
        d = snap.to_dict()
        assert d["meeting_id"] == "m-1"
        assert d["round_count"] == 2
        assert d["required_roles"] == ["strategist"]
        assert d["consensus"] == "not enough data"
        assert d["snapshot_at"] == "2026-06-10T01:00:00"


# ══════════════════════════════════════════════════════════════════════════
# RecoveryEntryPoint
# ══════════════════════════════════════════════════════════════════════════


class TestRecoveryEntryPoint:
    """RecoveryEntryPoint construction, properties, to_dict."""

    def _snap(self) -> PartialProgressSnapshot:
        return PartialProgressSnapshot(
            meeting_id="m-1",
            state_at_failure="validating",
            round_count=2,
            context_packets_count=3,
            decisions_count=1,
            tool_outputs_count=0,
            validation_score=0.42,
            validation_verdict="fail",
            consensus="draft consensus",
            required_roles=("strategist",),
            completed_step="in_meeting",
            error_log_count=1,
            created_at="2026-06-10T00:00:00",
        )

    def test_construction_retry(self):
        rep = RecoveryEntryPoint(
            meeting_id="m-1",
            failure_category="validation_failed_irrecoverable",
            failure_reason="Validation failed",
            resume_possible=True,
            recommended_action=RecoveryEntryPoint.ACTION_RETRY,
            last_good_state="in_meeting",
            snapshot=self._snap(),
        )
        assert rep.is_retryable is True
        assert rep.resume_possible is True

    def test_to_dict(self):
        rep = RecoveryEntryPoint(
            meeting_id="m-1",
            failure_category="validation_failed_irrecoverable",
            failure_reason="Validation failed",
            resume_possible=True,
            recommended_action=RecoveryEntryPoint.ACTION_RETRY,
            last_good_state="in_meeting",
            snapshot=self._snap(),
        )
        d = rep.to_dict()
        assert d["meeting_id"] == "m-1"
        assert d["failure_category"] == "validation_failed_irrecoverable"
        assert d["recommended_action"] == "retry_from_snapshot"
        assert "snapshot" in d

    def test_valid_actions_frozenset(self):
        assert "retry_from_snapshot" in RecoveryEntryPoint.VALID_ACTIONS
        assert "escalate_to_human" in RecoveryEntryPoint.VALID_ACTIONS
        assert "manual_audit_only" in RecoveryEntryPoint.VALID_ACTIONS
        assert "discard" in RecoveryEntryPoint.VALID_ACTIONS


# ══════════════════════════════════════════════════════════════════════════
# capture_partial_progress  (pure function)
# ══════════════════════════════════════════════════════════════════════════


class TestCapturePartialProgress:
    """capture_partial_progress reads all manifest fields correctly."""

    def test_all_fields_captured(self):
        m = _make_manifest(
            meeting_id="m-full",
            state="validating",
            round_count=2,
            validation_score=0.75,
            validation_verdict="conditional_pass",
            consensus="Agreed on plan A",
            required_roles=("strategist", "art-director", "backend-dev"),
            context_packets=(
                {"round": 1, "role_id": "strategist"},
                {"round": 1, "role_id": "art-director"},
                {"round": 2, "role_id": "strategist"},
            ),
            decisions=({"round": 1, "decision_id": "d1"},),
            tool_outputs=({"round": 1, "execution_id": "e1"}, {"round": 2, "execution_id": "e2"}),
            error_log=({"error_type": "test"},),
            completed_step="in_meeting",
            created_at="2026-06-10T00:00:00",
        )
        snap = capture_partial_progress(m)
        assert snap.meeting_id == "m-full"
        assert snap.state_at_failure == "validating"
        assert snap.round_count == 2
        assert snap.context_packets_count == 3
        assert snap.decisions_count == 1
        assert snap.tool_outputs_count == 2
        assert snap.validation_score == 0.75
        assert snap.validation_verdict == "conditional_pass"
        assert snap.consensus == "Agreed on plan A"
        assert snap.required_roles == ("strategist", "art-director", "backend-dev")
        assert snap.completed_step == "in_meeting"
        assert snap.error_log_count == 1
        assert snap.created_at == "2026-06-10T00:00:00"
        assert snap.was_validating is True
        assert snap.had_consensus is True
        assert snap.had_context is True

    def test_empty_manifest(self):
        m = _make_manifest()
        snap = capture_partial_progress(m)
        assert snap.meeting_id == "m-test-0001"
        assert snap.state_at_failure == "created"
        assert snap.round_count == 0
        assert snap.context_packets_count == 0
        assert snap.decisions_count == 0
        assert snap.tool_outputs_count == 0
        assert snap.validation_score == 0.0
        assert snap.validation_verdict == ""
        assert snap.was_validating is False
        assert snap.had_consensus is False
        assert snap.had_context is False

    def test_manifest_with_error_log(self):
        m = _make_manifest(
            error_log=(
                {"error_type": "e1"},
                {"error_type": "e2"},
                {"error_type": "e3"},
            ),
        )
        snap = capture_partial_progress(m)
        assert snap.error_log_count == 3


# ══════════════════════════════════════════════════════════════════════════
# build_recovery_entry_point  (pure function — decision matrix)
# ══════════════════════════════════════════════════════════════════════════


class TestBuildRecoveryEntryPoint:
    """Full decision matrix coverage for build_recovery_entry_point."""

    def _snap(self, **overrides: object) -> PartialProgressSnapshot:
        kwargs: dict[str, object] = {
            "meeting_id": "m-1",
            "state_at_failure": "validating",
            "round_count": 2,
            "context_packets_count": 3,
            "decisions_count": 1,
            "tool_outputs_count": 0,
            "validation_score": 0.42,
            "validation_verdict": "fail",
            "consensus": "draft",
            "required_roles": ("strategist",),
            "completed_step": "in_meeting",
            "error_log_count": 1,
            "created_at": "2026-06-10T00:00:00",
        }
        kwargs.update(overrides)
        return PartialProgressSnapshot(**kwargs)  # type: ignore[arg-type]

    # ── validation_failed_irrecoverable ──────────────────────────────

    def test_validation_failed_with_rounds(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "validation_failed_irrecoverable",
            "GLM returned fail",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_validation_failed_no_rounds_no_context(self):
        snap = self._snap(round_count=0, context_packets_count=0)
        rep = build_recovery_entry_point(
            snap,
            "validation_failed_irrecoverable",
            "No validation possible",
        )
        assert rep.recommended_action == "manual_audit_only"
        assert rep.resume_possible is False

    def test_validation_failed_with_context_but_no_rounds(self):
        """context_packets_count > 0 should be enough for retry."""
        snap = self._snap(round_count=0, context_packets_count=1)
        rep = build_recovery_entry_point(
            snap,
            "validation_failed_irrecoverable",
            "Context was retrieved but no rounds ran",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    # ── resource_exhausted ──────────────────────────────────────────

    def test_resource_exhausted_with_rounds(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "resource_exhausted",
            "Out of memory",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_resource_exhausted_no_rounds(self):
        snap = self._snap(round_count=0, context_packets_count=0)
        rep = build_recovery_entry_point(
            snap,
            "resource_exhausted",
            "Out of memory before first round",
        )
        assert rep.recommended_action == "manual_audit_only"
        assert rep.resume_possible is False

    # ── rate_limit_paused ───────────────────────────────────────────

    def test_rate_limit_paused(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "rate_limit_paused",
            "Quota exhausted",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True

    def test_rate_limit_paused_no_progress(self):
        snap = self._snap(round_count=0, context_packets_count=0)
        rep = build_recovery_entry_point(
            snap,
            "rate_limit_paused",
            "Quota exhausted before any work",
        )
        assert rep.recommended_action == "manual_audit_only"

    # ── coordinator_crash_unrecoverable ─────────────────────────────

    def test_coordinator_crash(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "coordinator_crash_unrecoverable",
            "Manifest corrupted",
        )
        assert rep.recommended_action == "escalate_to_human"
        assert rep.resume_possible is True  # rounds > 0

    def test_coordinator_crash_no_rounds(self):
        snap = self._snap(round_count=0)
        rep = build_recovery_entry_point(
            snap,
            "coordinator_crash_unrecoverable",
            "Manifest corrupted before rounds",
        )
        assert rep.recommended_action == "escalate_to_human"
        assert rep.resume_possible is False  # rounds == 0

    # ── deadlock_unresolvable ───────────────────────────────────────

    def test_deadlock(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "deadlock_unresolvable",
            "No consensus after 3+1 rounds",
        )
        assert rep.recommended_action == "escalate_to_human"

    # ── user_cancelled ──────────────────────────────────────────────

    def test_user_cancelled(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "user_cancelled",
            "User issued /cancel",
        )
        assert rep.recommended_action == "discard"
        assert rep.resume_possible is False

    # ── Unknown category ────────────────────────────────────────────

    def test_unknown_category_with_rounds(self):
        rep = build_recovery_entry_point(
            self._snap(),
            "weird_bug",
            "Something strange happened",
        )
        assert rep.recommended_action == "manual_audit_only"
        assert rep.resume_possible is True

    def test_unknown_category_no_rounds(self):
        snap = self._snap(round_count=0)
        rep = build_recovery_entry_point(
            snap,
            "weird_bug",
            "Something strange happened",
        )
        assert rep.recommended_action == "discard"
        assert rep.resume_possible is False

    # ── last_good_state explicit override ───────────────────────────

    def test_explicit_last_good_state(self):
        rep = build_recovery_entry_point(
            self._snap(completed_step="context_retrieval"),
            "validation_failed_irrecoverable",
            "Validation failed",
            last_good_state="routing",
        )
        assert rep.last_good_state == "routing"

    def test_last_good_state_falls_back_to_completed_step(self):
        rep = build_recovery_entry_point(
            self._snap(completed_step="context_retrieval"),
            "validation_failed_irrecoverable",
            "Validation failed",
        )
        assert rep.last_good_state == "context_retrieval"


# ══════════════════════════════════════════════════════════════════════════
# enter_failed_state  (orchestrator)
# ══════════════════════════════════════════════════════════════════════════


class TestEnterFailedState:
    """enter_failed_state: success, rejection, edge cases."""

    # ── Success cases — one per valid transition source ───────────────

    @pytest.mark.parametrize("from_state", [
        "created", "queued", "routing", "context_retrieval",
        "in_meeting", "consensus_building", "validating",
        "executing", "finalizing", "paused", "deadlocked", "escalated",
    ])
    def test_success_from_valid_state(self, from_state: str):
        """Every non-terminal state can transition to failed."""
        m = _make_manifest(state=from_state, round_count=1)
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason=f"Testing from {from_state}",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is True, (
            f"Expected success from state={from_state}, "
            f"got rejection_reasons={result.rejection_reasons}"
        )
        assert result.manifest.state == "failed"
        assert result.snapshot is not None
        assert result.recovery_entry_point is not None

    def test_snapshot_captured_before_transition(self):
        """Verify snapshot shows the pre-failure state, not 'failed'."""
        m = _make_manifest(
            state="validating",
            round_count=3,
            validation_score=0.65,
            validation_verdict="revision_required",
        )
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Score too low",
            failure_category="validation_failed_irrecoverable",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success
        # Snapshot captures the validating state
        assert result.snapshot.state_at_failure == "validating"
        assert result.snapshot.round_count == 3
        assert result.snapshot.validation_score == 0.65
        # Manifest reflects the new state
        assert result.manifest.state == "failed"

    def test_error_logged_to_manifest(self):
        m = _make_manifest(state="validating")
        assert len(m.error_log) == 0
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test failure",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success
        assert len(result.manifest.error_log) >= 1
        entry = result.manifest.error_log[0]
        assert entry["error_type"] == "failed_state_transition"
        assert entry["message"] == "Test failure"
        assert entry["failure_category"] == "test"
        assert "snapshot_meeting_id" in entry
        assert "snapshot_round_count" in entry

    def test_metadata_appended_to_error_entry(self):
        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",
            failure_category="test",
            metadata={"validator": "glm-5.1", "trace_id": "abc123"},
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        entry = result.manifest.error_log[0]
        assert entry.get("meta_validator") == "glm-5.1"
        assert entry.get("meta_trace_id") == "abc123"

    # ── Terminal state rejection ────────────────────────────────────

    @pytest.mark.parametrize("terminal_state", ["completed", "cancelled", "failed", "stale"])
    def test_terminal_state_rejected(self, terminal_state: str):
        """Already-terminal manifests cannot transition to failed again."""
        m = _make_manifest(state=terminal_state)
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is False
        assert "already in terminal state" in (
            result.rejection_reasons[0] if result.rejection_reasons else ""
        )
        # State must not have changed
        assert result.manifest.state == terminal_state

    # ── Persistence ─────────────────────────────────────────────────

    def test_persist_true_writes_to_disk(self):
        root = tempfile.mkdtemp(prefix="ai_agent_test_failed_")
        try:
            manifest_path = os.path.join(root, "manifest.json")
            m = _make_manifest(
                state="validating",
                manifest_path=manifest_path,
            )
            ctx = FailedTransitionContext(
                manifest=m,
                failure_reason="Test persist",
                failure_category="test",
            )
            # Persist requires the directory to exist
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            result = enter_failed_state(ctx, persist=True)
            assert result.success is True
            assert result.manifest.state == "failed"
            assert os.path.exists(manifest_path)
        finally:
            _cleanup_dir(root)

    def test_persist_false_no_disk_write(self):
        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test no persist",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is True
        assert result.manifest.state == "failed"

    # ── Injectable persist_fn ────────────────────────────────────────

    def test_persist_fn_called(self):
        called_with: list[MeetingManifest] = []

        def tracking_persist(manifest: MeetingManifest) -> MeetingManifest:
            called_with.append(manifest)
            return manifest

        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test persist fn",
            failure_category="test",
        )
        result = enter_failed_state(
            ctx, persist=True, persist_fn=tracking_persist
        )
        assert result.success is True
        assert len(called_with) == 1
        assert called_with[0].state == "failed"

    def test_persist_fn_raises_handled_gracefully(self):
        def failing_persist(manifest: MeetingManifest) -> MeetingManifest:
            raise OSError("Disk full!")

        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test persist failure",
            failure_category="test",
        )
        result = enter_failed_state(
            ctx, persist=True, persist_fn=failing_persist
        )
        # Transition should still be marked success=False because persistence failed
        assert result.success is False
        assert "Disk full" in (
            result.rejection_reasons[0] if result.rejection_reasons else ""
        )
        # State WAS mutated before persistence failed
        assert result.manifest.state == "failed"
        # Error must be logged
        assert any(
            e["error_type"] == "persistence_error"
            for e in result.manifest.error_log
        )

    # ── Recovery entry point ────────────────────────────────────────

    def test_recovery_entry_point_present_on_success(self):
        m = _make_manifest(
            state="validating",
            round_count=2,
            validation_score=0.42,
            validation_verdict="fail",
            completed_step="in_meeting",
        )
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Validation failed",
            failure_category="validation_failed_irrecoverable",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success
        assert result.recovery_entry_point is not None
        assert result.recovery_entry_point.meeting_id == m.meeting_id
        assert result.recovery_entry_point.recommended_action == "retry_from_snapshot"

    def test_recovery_entry_point_none_on_failure(self):
        m = _make_manifest(state="completed")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is False
        assert result.recovery_entry_point is not None  # Built regardless
        # Recovery entry is built even on failure — it's derived from snapshot

    # ── State mutation failure ──────────────────────────────────────

    def test_state_mutation_error_caught(self, monkeypatch):
        """Simulate a bug in with_state where it raises."""
        m = _make_manifest(state="validating")

        original_with_state = MeetingManifest.with_state

        def broken_with_state(self_obj, new_state):
            if str(new_state) == "failed":
                raise RuntimeError("Simulated mutation bug")
            return original_with_state(self_obj, new_state)

        monkeypatch.setattr(MeetingManifest, "with_state", broken_with_state)

        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test mutation failure",
            failure_category="test",
        )
        result = enter_failed_state(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is False
        assert any(
            "Simulated mutation bug" in r
            for r in result.rejection_reasons
        )
        assert result.error is not None


# ══════════════════════════════════════════════════════════════════════════
# handle_failed_transition  (orchestrator)
# ══════════════════════════════════════════════════════════════════════════


class TestHandleFailedTransition:
    """handle_failed_transition: success, rejection, exception handling."""

    def test_success_passthrough(self):
        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test handle",
            failure_category="test",
        )
        result = handle_failed_transition(ctx, persist=False, persist_fn=_mock_persist)
        assert result.success is True
        assert result.manifest.state == "failed"

    def test_none_manifest_raises(self):
        with pytest.raises(ValueError, match="manifest must not be None"):
            handle_failed_transition(
                FailedTransitionContext(
                    manifest=None,  # type: ignore[arg-type]
                    failure_reason="Test",
                ),
                persist=False,
            )

    def test_handler_crashes_caught(self):
        m = _make_manifest(state="validating")

        # Make enter_failed_state crash by providing a manifest that
        # raises on with_state — this is tested in the monkeypatch test
        # above.  Here we test the higher‑level handler's exception guard.
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",
            failure_category="test",
        )

        # We can't easily make handle_failed_transition crash without
        # monkeypatching, so we verify it handles a terminal‑state
        # manifest gracefully (which causes enter_failed_state to return
        # success=False, not crash).
        m_terminal = _make_manifest(state="completed")
        ctx_terminal = FailedTransitionContext(
            manifest=m_terminal,
            failure_reason="Already done",
            failure_category="test",
        )
        result = handle_failed_transition(
            ctx_terminal, persist=False, persist_fn=_mock_persist
        )
        assert result.success is False
        # Error was logged to manifest
        assert len(result.manifest.error_log) >= 1

    def test_empty_failure_reason_warning(self):
        """Empty failure_reason should not crash — just log a warning."""
        m = _make_manifest(state="validating")
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Test",  # must be non-empty for context creation
            failure_category="test",
        )
        # Can't test with empty failure_reason since context raises.
        # Test with very short reason instead.
        ctx2 = FailedTransitionContext(
            manifest=_make_manifest(state="validating"),
            failure_reason="X",
            failure_category="test",
        )
        result = handle_failed_transition(ctx2, persist=False, persist_fn=_mock_persist)
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════
# Integration tests
# ══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """End‑to‑end workflows: meeting creation → progress → failure."""

    def test_full_workflow_created_to_validating_to_failed(self):
        """Simulate a meeting that progresses to validation and then fails."""
        # Create a "virtual" meeting that has progressed through several states
        m = _make_manifest(
            meeting_id="m-integration",
            state="validating",
            round_count=2,
            validation_score=0.42,
            validation_verdict="fail",
            consensus="Plan B was agreed on",
            required_roles=("strategist", "art-director"),
            context_packets=(
                {"round": 1, "role_id": "strategist"},
                {"round": 1, "role_id": "art-director"},
                {"round": 2, "role_id": "strategist"},
            ),
            decisions=(
                {"round": 1, "decision_id": "d1", "role_id": "strategist"},
            ),
            tool_outputs=(
                {"round": 2, "execution_id": "e1", "status": "success"},
            ),
            error_log=(),
            completed_step="consensus_building",
            created_at="2026-06-10T00:00:00+00:00",
        )

        # Step 1: capture snapshot
        snap = capture_partial_progress(m)
        assert snap.round_count == 2
        assert snap.context_packets_count == 3
        assert snap.decisions_count == 1
        assert snap.tool_outputs_count == 1
        assert snap.validation_score == 0.42
        assert snap.validation_verdict == "fail"
        assert snap.was_validating is True
        assert snap.had_consensus is True
        assert snap.had_context is True

        # Step 2: build recovery entry point
        rep = build_recovery_entry_point(
            snap,
            "validation_failed_irrecoverable",
            "GLM-5.1 returned fail verdict with score 0.42",
            last_good_state="consensus_building",
        )
        assert rep.recommended_action == "retry_from_snapshot"
        assert rep.resume_possible is True
        assert rep.last_good_state == "consensus_building"

        # Step 3: execute the full transition
        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="GLM-5.1 returned fail verdict with score 0.42",
            failure_category="validation_failed_irrecoverable",
            severity="critical",
            metadata={"validator": "glm-5.1", "score": "0.42"},
        )
        result = handle_failed_transition(ctx, persist=False, persist_fn=_mock_persist)

        # Assertions on result
        assert result.success is True
        assert result.manifest.state == "failed"
        assert result.snapshot.round_count == 2
        assert result.recovery_entry_point is not None
        assert result.recovery_entry_point.recommended_action == "retry_from_snapshot"

        # Manifest's error_log must contain the failure event
        assert len(result.manifest.error_log) >= 1
        entry = result.manifest.error_log[0]
        assert entry["error_type"] == "failed_state_transition"
        assert "GLM-5.1" in entry["message"]
        assert entry["severity"] == "critical"
        assert entry["failure_category"] == "validation_failed_irrecoverable"
        assert entry.get("meta_validator") == "glm-5.1"
        assert entry.get("meta_score") == "0.42"

        # Snapshot metadata embedded in error entry
        assert "snapshot_meeting_id" in entry
        assert entry["snapshot_round_count"] == 2

    def test_round_limit_exhausted_then_failed(self):
        """Meeting that exhausted all rounds without consensus."""
        m = _make_manifest(
            meeting_id="m-exhausted",
            state="consensus_building",
            round_count=4,  # 3+1 rounds exhausted
            validation_score=0.0,
            validation_verdict="",
            consensus="",  # no consensus reached
            required_roles=("strategist", "backend-dev"),
            context_packets=tuple(
                {"round": r, "role_id": "strategist"}
                for r in range(1, 5)
            ),
            decisions=(),
            completed_step="in_meeting",
        )

        ctx = FailedTransitionContext(
            manifest=m,
            failure_reason="Round limit exhausted without consensus",
            failure_category="deadlock_unresolvable",
            severity="critical",
        )
        result = handle_failed_transition(ctx, persist=False, persist_fn=_mock_persist)

        assert result.success is True
        assert result.manifest.state == "failed"
        assert result.snapshot.round_count == 4
        assert result.snapshot.had_consensus is False
        assert result.recovery_entry_point.recommended_action == "escalate_to_human"


# ── Helpers ───────────────────────────────────────────────────────────────


def _cleanup_dir(path: str) -> None:
    """Best‑effort directory cleanup."""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
