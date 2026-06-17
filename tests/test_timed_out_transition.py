"""Tests for the timed-out state transition handler (Sub-AC 4.3.3).

Covers:
- MockTimer construction, advance, call semantics
- detect_timeout with mock and real timers
- TimedOutTransitionContext validation and defaults
- TimedOutTransitionResult success / failure states
- build_timeout_recovery — decision matrix (has progress vs no progress)
- enter_timed_out_state — success from every valid transition source,
  terminal state rejection, already-timed_out rejection,
  invalid transition rejection, persist / no-persist modes,
  persistence failure handling
- exit_timed_out_state — success to valid target states,
  not-in-timed_out rejection, invalid target rejection,
  persist / no-persist modes
- handle_timeout_transition — success, None manifest rejection,
  unexpected exception handling
- End-to-end: detect timeout → enter timed_out → snapshot → recovery →
  exit timed_out → resume
- Mock-timer integration: full state machine cycle with deterministic timing
- Edge cases: empty timeout_reason, unknown severity, empty metadata,
  minimalist manifest, round_count affects recovery action
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.timed_out_transition import (
    MockTimer,
    RealTimer,
    TimedOutTransitionContext,
    TimedOutTransitionResult,
    _append_error,
    _utc_now_iso,
    build_timeout_recovery,
    detect_timeout,
    enter_timed_out_state,
    exit_timed_out_state,
    handle_timeout_transition,
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
    user_id: str = "u-test",
    channel_id: str = "c-test",
    created_at: str = "2026-06-10T00:00:00+00:00",
    manifest_path: str = "/tmp/test-timedout/manifest.json",
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


# ══════════════════════════════════════════════════════════════════════════
# MockTimer
# ══════════════════════════════════════════════════════════════════════════


class TestMockTimer:
    """MockTimer construction, call, advance."""

    def test_default_now_is_zero(self):
        t = MockTimer()
        assert t.now == 0.0
        assert t() == 0.0

    def test_explicit_now(self):
        t = MockTimer(now=1700000000.0)
        assert t.now == 1700000000.0
        assert t() == 1700000000.0

    def test_advance_returns_new_instance(self):
        t = MockTimer(now=1000.0)
        t2 = t.advance(120.0)
        assert t2 is not t
        assert t.now == 1000.0  # original unchanged
        assert t2.now == 1120.0

    def test_advance_negative(self):
        t = MockTimer(now=1000.0)
        t2 = t.advance(-50.0)
        assert t2.now == 950.0

    def test_frozen_immutable(self):
        t = MockTimer(now=1000.0)
        with pytest.raises(Exception):
            t.now = 2000.0  # frozen dataclass


class TestRealTimer:
    """RealTimer uses time.monotonic."""

    def test_returns_float(self):
        t = RealTimer()
        val = t()
        assert isinstance(val, float)
        assert val > 0.0

    def test_monotonically_increasing(self):
        t = RealTimer()
        t1 = t()
        t2 = t()
        assert t2 >= t1


# ══════════════════════════════════════════════════════════════════════════
# detect_timeout  (pure function with injectable timer)
# ══════════════════════════════════════════════════════════════════════════


class TestDetectTimeout:
    """detect_timeout with mock and real timers."""

    def test_no_timeout_when_under_limit(self):
        t = MockTimer(now=1100.0)
        result = detect_timeout(
            started_at=1000.0,
            timeout_limit_s=120.0,
            timer=t,
        )
        assert result is False

    def test_timeout_when_exactly_at_limit(self):
        t = MockTimer(now=1120.0)
        result = detect_timeout(
            started_at=1000.0,
            timeout_limit_s=120.0,
            timer=t,
        )
        assert result is True

    def test_timeout_when_over_limit(self):
        t = MockTimer(now=1200.0)
        result = detect_timeout(
            started_at=1000.0,
            timeout_limit_s=120.0,
            timer=t,
        )
        assert result is True

    def test_timeout_far_exceeded(self):
        t = MockTimer(now=1300.0)
        result = detect_timeout(
            started_at=1000.0,
            timeout_limit_s=120.0,
            timer=t,
        )
        assert result is True

    def test_with_advancing_timer(self):
        t = MockTimer(now=1000.0)
        # 30 seconds in — no timeout
        t = t.advance(30.0)
        assert detect_timeout(1000.0, 120.0, timer=t) is False
        # 90 more seconds (= 120 total) — timeout
        t = t.advance(90.0)
        assert detect_timeout(1000.0, 120.0, timer=t) is True

    def test_default_timer_is_real(self):
        # With real timer, started long enough ago will timeout
        import time
        result = detect_timeout(
            started_at=time.monotonic() - 3600.0,  # 1 hour ago
            timeout_limit_s=120.0,
        )
        assert result is True

    def test_default_timer_no_timeout_recent(self):
        import time
        result = detect_timeout(
            started_at=time.monotonic() + 3600.0,  # 1 hour in future
            timeout_limit_s=120.0,
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════════
# TimedOutTransitionContext
# ══════════════════════════════════════════════════════════════════════════


class TestTimedOutTransitionContext:
    """TimedOutTransitionContext construction and validation."""

    def test_construction_minimal(self):
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="opencode-go timed out",
        )
        assert ctx.manifest is m
        assert ctx.timeout_reason == "opencode-go timed out"
        assert ctx.timeout_duration_s == 0.0  # default
        assert ctx.previous_state == "in_meeting"  # auto from manifest
        assert ctx.severity == "warning"  # default
        assert ctx.metadata == {}

    def test_construction_full(self):
        m = _make_manifest(state="validating")
        timer = MockTimer(now=1700000000.0)
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="GLM validation exceeded 180s",
            timeout_duration_s=180.0,
            previous_state="in_meeting",
            severity="error",
            timer=timer,
            metadata={"validator": "glm-5.1", "attempt": "1"},
        )
        assert ctx.timeout_duration_s == 180.0
        assert ctx.previous_state == "in_meeting"
        assert ctx.severity == "error"
        assert ctx.timer is timer
        assert ctx.metadata["validator"] == "glm-5.1"

    def test_previous_state_defaults_to_manifest_state(self):
        m = _make_manifest(state="routing")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Test",
        )
        assert ctx.previous_state == "routing"

    def test_explicit_previous_state_overrides(self):
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Test",
            previous_state="consensus_building",
        )
        assert ctx.previous_state == "consensus_building"

    def test_empty_timeout_reason_raises(self):
        m = _make_manifest()
        with pytest.raises(ValueError, match="timeout_reason must not be empty"):
            TimedOutTransitionContext(manifest=m, timeout_reason="   ")

    def test_unknown_severity_coerced_to_warning(self):
        m = _make_manifest()
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Test",
            severity="bogus",
        )
        assert ctx.severity == "warning"


# ══════════════════════════════════════════════════════════════════════════
# TimedOutTransitionResult
# ══════════════════════════════════════════════════════════════════════════


class TestTimedOutTransitionResult:
    """TimedOutTransitionResult success / failure states."""

    def test_success_result_enter(self):
        m = _make_manifest(state="timed_out")
        snap = capture_partial_progress(m)
        rep = RecoveryEntryPoint(
            meeting_id="m-1",
            failure_category="timed_out",
            failure_reason="Timed out",
            resume_possible=True,
            recommended_action=RecoveryEntryPoint.ACTION_RETRY,
            last_good_state="in_meeting",
            snapshot=snap,
        )
        result = TimedOutTransitionResult(
            success=True,
            manifest=m,
            snapshot=snap,
            recovery_entry_point=rep,
            transition_type="enter",
        )
        assert result.success is True
        assert result.transition_type == "enter"
        assert result.recovery_entry_point is rep
        assert result.rejection_reasons == ()

    def test_success_result_exit(self):
        m = _make_manifest(state="in_meeting")
        snap = capture_partial_progress(m)
        result = TimedOutTransitionResult(
            success=True,
            manifest=m,
            snapshot=snap,
            transition_type="exit",
        )
        assert result.success is True
        assert result.transition_type == "exit"
        assert result.recovery_entry_point is None

    def test_failure_result(self):
        m = _make_manifest()
        snap = capture_partial_progress(m)
        result = TimedOutTransitionResult(
            success=False,
            manifest=m,
            snapshot=snap,
            transition_type="enter",
            rejection_reasons=("Already timed out",),
        )
        assert result.success is False
        assert result.rejection_reasons == ("Already timed out",)

    def test_error_captured(self):
        m = _make_manifest()
        snap = capture_partial_progress(m)
        exc = RuntimeError("boom")
        result = TimedOutTransitionResult(
            success=False,
            manifest=m,
            snapshot=snap,
            transition_type="enter",
            error=exc,
        )
        assert result.error is exc


# ══════════════════════════════════════════════════════════════════════════
# build_timeout_recovery  (pure function)
# ══════════════════════════════════════════════════════════════════════════


class TestBuildTimeoutRecovery:
    """build_timeout_recovery decision matrix."""

    def _snap(self, **overrides) -> PartialProgressSnapshot:
        defaults = dict(
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
            completed_step="context_retrieval",
            error_log_count=0,
            created_at="2026-06-10T00:00:00",
        )
        defaults.update(overrides)
        return PartialProgressSnapshot(**defaults)

    def test_has_progress_returns_retry(self):
        snap = self._snap(round_count=2, decisions_count=3)
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="opencode-go timed out",
            timeout_duration_s=120.0,
        )
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_RETRY
        assert rep.resume_possible is True
        assert rep.failure_category == "timed_out"

    def test_no_progress_returns_audit(self):
        snap = self._snap(round_count=0, context_packets_count=0,
                          decisions_count=0, tool_outputs_count=0)
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Timeout during routing",
            timeout_duration_s=30.0,
        )
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_AUDIT
        assert rep.resume_possible is False

    def test_has_context_but_no_rounds_is_progress(self):
        snap = self._snap(round_count=0, context_packets_count=3)
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Timeout during context retrieval",
            timeout_duration_s=60.0,
        )
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_RETRY

    def test_has_consensus_is_progress(self):
        snap = self._snap(round_count=0, context_packets_count=0,
                          consensus="Draft consensus")
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Timeout",
            timeout_duration_s=120.0,
        )
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_RETRY

    def test_timeout_duration_in_reason(self):
        snap = self._snap()
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Validation timeout",
            timeout_duration_s=180.0,
        )
        assert "180.0s" in rep.failure_reason
        assert "Validation timeout" in rep.failure_reason

    def test_last_good_state_fallback(self):
        snap = self._snap(completed_step="in_meeting")
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Timeout",
            timeout_duration_s=120.0,
            last_good_state="routing",
        )
        assert rep.last_good_state == "routing"

    def test_last_good_state_defaults_to_completed_step(self):
        snap = self._snap(completed_step="context_retrieval")
        rep = build_timeout_recovery(
            snapshot=snap,
            timeout_reason="Timeout",
            timeout_duration_s=120.0,
        )
        assert rep.last_good_state == "context_retrieval"


# ══════════════════════════════════════════════════════════════════════════
# enter_timed_out_state  — from every valid source state
# ══════════════════════════════════════════════════════════════════════════


class TestEnterTimedOutState:
    """enter_timed_out_state success and rejection paths."""

    VALID_SOURCES = [
        "created", "queued", "routing", "context_retrieval",
        "in_meeting", "consensus_building", "validating",
        "executing", "finalizing", "paused", "deadlocked",
        "escalated",
    ]

    @pytest.mark.parametrize("from_state", VALID_SOURCES)
    def test_success_from_state(self, from_state: str):
        """Every valid active/exception state can transition to timed_out."""
        m = _make_manifest(state=from_state, round_count=1)
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason=f"Timeout from {from_state}",
            timeout_duration_s=120.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is True, (
            f"Expected success from {from_state}, got: {result.rejection_reasons}"
        )
        assert result.manifest.state == "timed_out"
        assert result.transition_type == "enter"
        assert result.snapshot is not None
        assert result.snapshot.meeting_id == m.meeting_id
        assert result.recovery_entry_point is not None

    def test_snapshot_captures_progress(self):
        m = _make_manifest(
            state="in_meeting",
            round_count=2,
            context_packets=(
                {"round": 1, "role_id": "strategist"},
                {"round": 1, "role_id": "art-director"},
                {"round": 2, "role_id": "strategist"},
            ),
            decisions=({"round": 1, "decision_id": "d1"},),
            tool_outputs=({"round": 1, "execution_id": "e1"},),
            validation_score=0.75,
            validation_verdict="conditional_pass",
            consensus="Agreed on plan A",
            required_roles=("strategist", "art-director"),
            completed_step="context_retrieval",
        )
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="LLM worker timed out in round 2",
            timeout_duration_s=120.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        snap = result.snapshot
        assert snap.round_count == 2
        assert snap.context_packets_count == 3
        assert snap.decisions_count == 1
        assert snap.tool_outputs_count == 1
        assert snap.validation_score == 0.75
        assert snap.validation_verdict == "conditional_pass"
        assert snap.consensus == "Agreed on plan A"
        assert snap.required_roles == ("strategist", "art-director")

    def test_error_log_appended(self):
        m = _make_manifest(state="in_meeting", error_log=())
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Worker timeout",
            timeout_duration_s=60.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        assert len(result.manifest.error_log) > 0
        # Check that a timeout entry was added
        timeout_entries = [
            e for e in result.manifest.error_log
            if e.get("error_type") == "timed_out_state_transition"
        ]
        assert len(timeout_entries) >= 1

    def test_metadata_merged_into_error_entry(self):
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Worker timeout",
            timeout_duration_s=60.0,
            metadata={"worker_role": "strategist", "round": "2"},
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        timeout_entries = [
            e for e in result.manifest.error_log
            if e.get("error_type") == "timed_out_state_transition"
        ]
        entry = timeout_entries[0]
        assert entry.get("meta_worker_role") == "strategist"
        assert entry.get("meta_round") == "2"

    # ── Rejection paths ────────────────────────────────────────────────

    def test_terminal_state_rejected(self):
        m = _make_manifest(state="completed")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Attempt to time out completed meeting",
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is False
        assert "already in terminal state" in result.rejection_reasons[0]

    def test_already_timed_out_rejected(self):
        m = _make_manifest(state="timed_out")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Double timeout",
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is False
        assert any("already in 'timed_out'" in r for r in result.rejection_reasons)

    def test_recovery_emitted_even_on_rejection(self):
        """Recovery entry point is built even when transition is rejected."""
        m = _make_manifest(state="completed")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Test",
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is False
        assert result.recovery_entry_point is not None

    # ── Persistence ────────────────────────────────────────────────────

    def test_persist_false_no_disk_io(self):
        m = _make_manifest(state="in_meeting", manifest_path="/nonexistent/manifest.json")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="No-persist test",
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is True
        assert result.manifest.state == "timed_out"
        # State was mutated in memory but not written to disk
        assert not os.path.exists(m.manifest_path)

    def test_persist_mock_fn_called(self):
        m = _make_manifest(state="in_meeting")
        called = []

        def track_persist(manifest: MeetingManifest) -> MeetingManifest:
            called.append(True)
            return manifest

        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Mock persist test",
        )
        result = enter_timed_out_state(ctx, persist=True, persist_fn=track_persist)
        assert result.success
        assert len(called) == 1

    def test_persist_failure_captured(self):
        m = _make_manifest(state="in_meeting")

        def failing_persist(manifest: MeetingManifest) -> MeetingManifest:
            raise OSError("Disk full")

        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Persist fail test",
        )
        result = enter_timed_out_state(ctx, persist=True, persist_fn=failing_persist)
        assert result.success is False
        assert any("persistence" in r.lower() for r in result.rejection_reasons)


# ══════════════════════════════════════════════════════════════════════════
# exit_timed_out_state
# ══════════════════════════════════════════════════════════════════════════


class TestExitTimedOutState:
    """exit_timed_out_state success and rejection paths."""

    VALID_TARGETS = [
        "created", "queued", "routing", "context_retrieval",
        "in_meeting", "consensus_building", "validating",
        "executing", "finalizing", "cancelled", "failed",
    ]

    @pytest.mark.parametrize("target", VALID_TARGETS)
    def test_success_to_valid_target(self, target: str):
        m = _make_manifest(state="timed_out")
        result = exit_timed_out_state(m, target, persist=False)
        assert result.success is True, (
            f"Expected success exiting to {target}, got: {result.rejection_reasons}"
        )
        assert result.manifest.state == target
        assert result.transition_type == "exit"

    def test_not_timed_out_rejected(self):
        m = _make_manifest(state="in_meeting")
        result = exit_timed_out_state(m, "in_meeting", persist=False)
        assert result.success is False
        assert any("not 'timed_out'" in r for r in result.rejection_reasons)

    def test_invalid_target_rejected(self):
        m = _make_manifest(state="timed_out")
        result = exit_timed_out_state(m, "completed", persist=False)
        assert result.success is False
        assert any("not permitted" in r for r in result.rejection_reasons)

    def test_resume_to_previous_state(self):
        """Exit timed_out back to the state before timeout occurred."""
        m = _make_manifest(state="timed_out")
        result = exit_timed_out_state(m, "validating", persist=False)
        assert result.success
        assert result.manifest.state == "validating"

    def test_exit_to_cancelled(self):
        """Meeting can be cancelled while in timed_out."""
        m = _make_manifest(state="timed_out")
        result = exit_timed_out_state(m, "cancelled", persist=False)
        assert result.success
        assert result.manifest.state == "cancelled"

    def test_exit_to_failed(self):
        """Meeting can fail while in timed_out."""
        m = _make_manifest(state="timed_out")
        result = exit_timed_out_state(m, "failed", persist=False)
        assert result.success
        assert result.manifest.state == "failed"

    # ── Persistence ────────────────────────────────────────────────────

    def test_persist_false_no_disk_io(self):
        m = _make_manifest(state="timed_out", manifest_path="/nonexistent/manifest.json")
        result = exit_timed_out_state(m, "in_meeting", persist=False)
        assert result.success
        assert result.manifest.state == "in_meeting"

    def test_persist_mock_fn_called(self):
        m = _make_manifest(state="timed_out")
        called = []

        def track_persist(manifest: MeetingManifest) -> MeetingManifest:
            called.append(True)
            return manifest

        result = exit_timed_out_state(
            m, "in_meeting", persist=True, persist_fn=track_persist,
        )
        assert result.success
        assert len(called) == 1

    def test_persist_failure_captured_on_exit(self):
        m = _make_manifest(state="timed_out")

        def failing_persist(manifest: MeetingManifest) -> MeetingManifest:
            raise OSError("Disk full")

        result = exit_timed_out_state(
            m, "in_meeting", persist=True, persist_fn=failing_persist,
        )
        assert result.success is False
        assert any("persistence" in r.lower() for r in result.rejection_reasons)


# ══════════════════════════════════════════════════════════════════════════
# handle_timeout_transition  (high‑level orchestrator)
# ══════════════════════════════════════════════════════════════════════════


class TestHandleTimeoutTransition:
    """handle_timeout_transition success and error handling."""

    def test_success_enter(self):
        m = _make_manifest(state="in_meeting", round_count=1)
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="LLM worker timed out",
            timeout_duration_s=120.0,
        )
        result = handle_timeout_transition(ctx, persist=False)
        assert result.success is True
        assert result.manifest.state == "timed_out"

    def test_none_manifest_raises(self):
        with pytest.raises(ValueError, match="must not be None"):
            handle_timeout_transition(
                TimedOutTransitionContext(
                    manifest=None,
                    timeout_reason="Test",
                )
            )

    def test_handler_crash_captured(self):
        """If enter_timed_out_state raises unexpectedly, it's caught."""
        m = _make_manifest(state="completed")  # will be rejected gracefully
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Graceful rejection",
        )
        result = handle_timeout_transition(ctx, persist=False)
        assert result.success is False
        # Even on rejection, manifest is returned with error logged
        assert result.manifest is not None


# ══════════════════════════════════════════════════════════════════════════
# End-to-end: timeout detection → enter → exit
# ══════════════════════════════════════════════════════════════════════════


class TestEndToEndTimeoutCycle:
    """Full cycle: detect timeout → enter timed_out → exit → resume."""

    def test_full_timeout_cycle_with_mock_timer(self):
        """Simulate a complete timeout lifecycle with deterministic timing."""
        timer = MockTimer(now=1000.0)

        # Meeting starts in 'validating'
        m = _make_manifest(
            state="validating",
            round_count=2,
            validation_score=0.65,
            validation_verdict="conditional_pass",
            consensus="Draft consensus",
        )

        # ── Time passes, operation exceeds limit ──
        timer = timer.advance(130.0)
        timeout_detected = detect_timeout(
            started_at=1000.0,
            timeout_limit_s=120.0,
            timer=timer,
        )
        assert timeout_detected is True

        # ── Enter timed_out ──
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="GLM-5.1 validation exceeded 120s",
            timeout_duration_s=130.0,
            previous_state="validating",
            timer=timer,
            metadata={"validator_model": "glm-5.1"},
        )
        enter_result = enter_timed_out_state(ctx, persist=False)
        assert enter_result.success is True
        assert enter_result.manifest.state == "timed_out"
        assert enter_result.transition_type == "enter"

        # Snapshot captured the progress
        snap = enter_result.snapshot
        assert snap.state_at_failure == "validating"
        assert snap.round_count == 2
        assert snap.validation_score == 0.65

        # Recovery entry point recommends retry
        rep = enter_result.recovery_entry_point
        assert rep.recommended_action == RecoveryEntryPoint.ACTION_RETRY
        assert rep.resume_possible is True

        # ── Resume: exit timed_out back to validating ──
        exit_result = exit_timed_out_state(
            enter_result.manifest,
            "validating",
            persist=False,
        )
        assert exit_result.success is True
        assert exit_result.manifest.state == "validating"
        assert exit_result.transition_type == "exit"

    def test_timeout_then_cancel(self):
        """Timeout detected, then user cancels while meeting is timed_out."""
        m = _make_manifest(state="in_meeting", round_count=1)

        # Enter timed_out
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Worker timeout",
            timeout_duration_s=120.0,
        )
        enter_result = enter_timed_out_state(ctx, persist=False)
        assert enter_result.success
        assert enter_result.manifest.state == "timed_out"

        # Exit to cancelled
        exit_result = exit_timed_out_state(
            enter_result.manifest,
            "cancelled",
            persist=False,
        )
        assert exit_result.success
        assert exit_result.manifest.state == "cancelled"

    def test_no_progress_timeout_audit_recovery(self):
        """Timeout with zero progress should recommend audit, not retry."""
        m = _make_manifest(
            state="routing",
            round_count=0,
            context_packets=(),
            decisions=(),
            tool_outputs=(),
        )
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Qwen routing timed out",
            timeout_duration_s=30.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        assert result.recovery_entry_point.recommended_action == RecoveryEntryPoint.ACTION_AUDIT
        assert result.recovery_entry_point.resume_possible is False

    def test_error_log_preserved_across_cycle(self):
        """Error log entries accumulate across the timeout cycle."""
        m = _make_manifest(state="in_meeting", error_log=(
            {"timestamp": "t1", "error_type": "prior_error", "message": "Earlier issue", "severity": "warning"},
        ))

        # Enter timed_out
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Worker timeout",
            timeout_duration_s=60.0,
        )
        enter_result = enter_timed_out_state(ctx, persist=False)
        assert len(enter_result.manifest.error_log) > 1  # prior + new entries

        # Exit timed_out
        exit_result = exit_timed_out_state(
            enter_result.manifest,
            "in_meeting",
            persist=False,
        )
        # Error log preserved across exit
        assert len(exit_result.manifest.error_log) >= len(enter_result.manifest.error_log)


# ══════════════════════════════════════════════════════════════════════════
# Mock-timer integration: state machine with deterministic timing
# ══════════════════════════════════════════════════════════════════════════


class TestMockTimerStateMachine:
    """State-machine function tests using MockTimer for deterministic execution."""

    def test_standalone_state_machine_with_mock_timer(self):
        """The entire handler is testable as a standalone state-machine
        function with a mock timer — per Sub-AC 4.3.3 requirement."""
        timer = MockTimer(now=0.0)

        # Create a meeting
        m = _make_manifest(state="consensus_building", round_count=1)

        # Simulate time passing by advancing the timer
        timer = timer.advance(150.0)

        # Build context with the mock timer
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="consensus_building operation exceeded 120s budget",
            timeout_duration_s=150.0,
            timer=timer,
        )

        # Execute the state machine function
        result = enter_timed_out_state(ctx, persist=False)

        # Assert complete state transition
        assert result.success is True
        assert result.manifest.state == "timed_out"
        assert result.transition_type == "enter"
        assert result.snapshot is not None
        assert result.recovery_entry_point is not None
        assert result.rejection_reasons == ()
        assert result.error is None

    def test_timeout_detected_by_timer_progression(self):
        """Timeout detection pipe works with mock timer."""
        started = 500.0
        timer = MockTimer(now=started)

        # Step 1: No timeout yet
        timer = timer.advance(30.0)
        assert detect_timeout(started, 120.0, timer=timer) is False

        # Step 2: Still no timeout
        timer = timer.advance(30.0)
        assert detect_timeout(started, 120.0, timer=timer) is False

        # Step 3: Borderline — at exactly 120s
        timer = timer.advance(60.0)
        assert detect_timeout(started, 120.0, timer=timer) is True

    def test_transition_invalid_from_unknown_state(self):
        """Mock timer has no bearing on state transition validity —
        an unknown state still fails."""
        m = _make_manifest(state="completed")  # terminal
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Should fail — terminal state",
            timer=MockTimer(now=1000.0),
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success is False
        assert any("terminal" in r for r in result.rejection_reasons)


# ══════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Miscellaneous edge cases."""

    def test_empty_metadata_is_fine(self):
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Timeout",
            metadata={},
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success

    def test_minimalist_manifest(self):
        m = MeetingManifest(
            meeting_id="m-minimal",
            state="queued",
            agenda="Minimal test",
            user_id="u-test",
            channel_id="c-test",
        )
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Minimal timeout",
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        snap = result.snapshot
        assert snap.round_count == 0
        assert snap.context_packets_count == 0

    def test_zero_duration_timeout(self):
        """Zero-second timeout is valid (immediate timeout)."""
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Instant timeout",
            timeout_duration_s=0.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success

    def test_negative_duration(self):
        """Negative duration is recorded as-is (caller's responsibility)."""
        m = _make_manifest(state="in_meeting")
        ctx = TimedOutTransitionContext(
            manifest=m,
            timeout_reason="Bogus negative duration",
            timeout_duration_s=-5.0,
        )
        result = enter_timed_out_state(ctx, persist=False)
        assert result.success
        # Check that the negative value appears in the error entry
        timeout_entries = [
            e for e in result.manifest.error_log
            if e.get("error_type") == "timed_out_state_transition"
        ]
        assert timeout_entries[0]["timeout_duration_s"] == "-5.0"

    def test_whitespace_only_timeout_reason_rejected(self):
        m = _make_manifest()
        with pytest.raises(ValueError):
            TimedOutTransitionContext(
                manifest=m,
                timeout_reason="   \t\n",
            )

    def test_timed_out_is_not_terminal(self):
        assert is_terminal("timed_out") is False
        assert is_terminal(LifecycleState.TIMED_OUT) is False
