"""Tests for the Timeout Monitor (Sub-AC 4.3.3).

Verifies:
1. Clock injection — fake clock enables deterministic timeout testing
2. Operation tracking — start, elapsed, remaining, remove
3. Timeout breach detection at threshold boundary
4. Stale state transition with timestamp in error_log
5. State assertion — verify state is "stale" after timeout
6. Per-state timeout overrides
7. Edge cases: terminal states, duplicates, zero timeout, bulk scans
"""

from __future__ import annotations

import os
import tempfile
import time
from unittest.mock import patch

import pytest

from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingManifest,
    create_meeting,
    load_manifest,
)
from src.shared.lifecycle import (
    LifecycleState,
    is_terminal,
)
from src.timeout_monitor import (
    DEFAULT_TIMEOUT_SECONDS,
    STATE_TIMEOUT_OVERRIDES,
    TimeoutMonitor,
    TimeoutResult,
    get_timeout_for_state,
)

# ── Test fixtures ─────────────────────────────────────────────────────────

_VALID_AGENDA = "신규 캐릭터 '루나'의 비주얼 디자인 회의"
_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


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
    return tempfile.mkdtemp(prefix="ai_agent_test_timeout_")


def _create_test_manifest(root: str) -> MeetingManifest:
    """Create a meeting and return its manifest at state='created'."""
    ctx = create_meeting(_make_request(), meetings_root=root)
    return ctx.manifest


def _advance_to(
    manifest: MeetingManifest, target: LifecycleState
) -> MeetingManifest:
    """Advance a manifest through states to reach *target*."""
    from src.transition_engine import execute_transition

    forward_path = [
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.FINALIZING,
        LifecycleState.COMPLETED,
    ]

    exception_targets = {
        LifecycleState.PAUSED,
        LifecycleState.DEADLOCKED,
        LifecycleState.ESCALATED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
        LifecycleState.STALE,
    }

    current = manifest

    if target in exception_targets:
        if target == LifecycleState.PAUSED:
            current = _advance_to(manifest, LifecycleState.QUEUED)
        elif target == LifecycleState.DEADLOCKED:
            current = _advance_to(manifest, LifecycleState.IN_MEETING)
        elif target == LifecycleState.ESCALATED:
            current = _advance_to(manifest, LifecycleState.VALIDATING)
        elif target == LifecycleState.CANCELLED:
            current = _advance_to(manifest, LifecycleState.QUEUED)
        elif target in (LifecycleState.FAILED, LifecycleState.STALE):
            current = _advance_to(manifest, LifecycleState.QUEUED)

        if current.state != str(target):
            result = execute_transition(
                current, target, label=f"test-advance-{target.value}"
            )
            if not result.success:
                raise RuntimeError(
                    f"Failed to advance to {target.value}: "
                    f"{result.rejection_reasons}"
                )
            current = result.manifest
        return current

    for state in forward_path:
        if current.state == str(state):
            continue
        if current.state == str(target):
            break

        result = execute_transition(
            current, state, label=f"test-advance-{state.value}"
        )
        if not result.success:
            raise RuntimeError(
                f"Failed to advance from {current.state} to {state.value}: "
                f"{result.rejection_reasons}"
            )
        current = result.manifest

        if current.state == str(target):
            break

    if current.state != str(target):
        raise RuntimeError(
            f"Could not advance to {target.value}; stuck at {current.state}"
        )
    return current


def _cleanup_dir(path: str) -> None:
    """Recursively remove a test directory."""
    import shutil

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


# ── Helper: fake clock factory ────────────────────────────────────────────


def _fake_clock(initial: float = 0.0) -> list[float]:
    """Return a mutable list wrapping a fake monotonic clock value.

    Usage::

        clock = _fake_clock()
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op", "m1")
        clock[0] = 999.0  # advance time
        assert monitor.check_timeout("op")
    """
    return [initial]


# ── 1. Clock injection ────────────────────────────────────────────────────


class TestClockInjection:
    """Verify that the injected clock controls time for the monitor."""

    def test_fake_clock_no_timeout_below_threshold(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(default_timeout_seconds=10.0, clock=lambda: clock[0])
        monitor.start_operation("op-1", "m1", timeout_seconds=10.0)

        # At exactly threshold — NOT timed out (strict >)
        clock[0] = 10.0
        assert monitor.check_timeout("op-1") is False

        # Just above threshold — timed out
        clock[0] = 10.001
        assert monitor.check_timeout("op-1") is True

    def test_fake_clock_simulates_long_wait(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(default_timeout_seconds=60.0, clock=lambda: clock[0])
        monitor.start_operation("long-op", "m1", timeout_seconds=60.0)

        clock[0] = 3600.0  # 1 hour later
        assert monitor.check_timeout("long-op") is True
        assert monitor.elapsed("long-op") == 3600.0

    def test_elapsed_tracks_clock_progression(self):
        clock = _fake_clock(100.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op", "m1", timeout_seconds=300.0)

        assert monitor.elapsed("op") == 0.0

        clock[0] = 150.0  # 50s elapsed
        assert monitor.elapsed("op") == 50.0

        clock[0] = 250.0  # 150s elapsed
        assert monitor.elapsed("op") == 150.0

    def test_remaining_decreases_with_clock(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op", "m1", timeout_seconds=100.0)

        assert monitor.remaining("op") == 100.0

        clock[0] = 70.0
        assert monitor.remaining("op") == 30.0

        clock[0] = 100.0
        assert monitor.remaining("op") == 0.0

        clock[0] = 200.0
        assert monitor.remaining("op") == 0.0  # floors at 0

    def test_real_clock_works_for_smoke_test(self):
        """Smoke test: real clock with very short timeout."""
        monitor = TimeoutMonitor(default_timeout_seconds=0.001)
        monitor.start_operation("fast", "m1", timeout_seconds=0.001)
        time.sleep(0.01)
        assert monitor.check_timeout("fast") is True


# ── 2. Operation tracking ─────────────────────────────────────────────────


class TestOperationTracking:
    """Verify start, elapsed, remaining, remove lifecycle."""

    def test_start_operation_returns_monotonic_time(self):
        clock = _fake_clock(42.5)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        t = monitor.start_operation("op", "m1")
        assert t == 42.5

    def test_start_duplicate_raises_value_error(self):
        monitor = TimeoutMonitor()
        monitor.start_operation("dup", "m1")
        with pytest.raises(ValueError, match="already being tracked"):
            monitor.start_operation("dup", "m1")

    def test_remove_operation_stops_tracking(self):
        monitor = TimeoutMonitor()
        monitor.start_operation("op", "m1")
        assert monitor.active_operations == 1

        monitor.remove_operation("op")
        assert monitor.active_operations == 0

        with pytest.raises(KeyError):
            monitor.check_timeout("op")

    def test_remove_nonexistent_is_noop(self):
        monitor = TimeoutMonitor()
        monitor.remove_operation("nonexistent")  # no error

    def test_multiple_operations_independent(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op-a", "m1", timeout_seconds=10.0)
        monitor.start_operation("op-b", "m1", timeout_seconds=20.0)

        clock[0] = 15.0
        assert monitor.check_timeout("op-a") is True
        assert monitor.check_timeout("op-b") is False

    def test_operation_ids_returns_sorted(self):
        monitor = TimeoutMonitor()
        monitor.start_operation("z-op", "m1")
        monitor.start_operation("a-op", "m1")
        monitor.start_operation("m-op", "m1")
        assert monitor.operation_ids() == ["a-op", "m-op", "z-op"]


# ── 3. Timeout detection ──────────────────────────────────────────────────


class TestTimeoutDetection:
    """Verify timeout breach detection at threshold boundaries."""

    def test_exactly_at_threshold_not_timed_out(self):
        """Timeout uses strict > (not >=), so exactly at threshold is OK."""
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op", "m1", timeout_seconds=5.0)
        clock[0] = 5.0
        assert monitor.check_timeout("op") is False

    def test_just_above_threshold_timed_out(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        monitor.start_operation("op", "m1", timeout_seconds=5.0)
        clock[0] = 5.000000001
        assert monitor.check_timeout("op") is True

    def test_check_timeout_nonexistent_raises_key_error(self):
        monitor = TimeoutMonitor()
        with pytest.raises(KeyError):
            monitor.check_timeout("nonexistent")

    def test_default_timeout_used_when_no_override(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(default_timeout_seconds=50.0, clock=lambda: clock[0])
        monitor.start_operation("op", "m1")  # no timeout_seconds override
        clock[0] = 51.0
        assert monitor.check_timeout("op") is True


# ── 4. Per-state timeout overrides ────────────────────────────────────────


class TestPerStateTimeouts:
    """Verify state-specific timeout overrides are applied correctly."""

    def test_state_override_used_when_state_provided(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(default_timeout_seconds=10.0, clock=lambda: clock[0])

        # "in_meeting" has 900s override — put clock at 500s,
        # which would time out the default but not the override
        monitor.start_operation("op", "m1", state="in_meeting")
        clock[0] = 500.0
        assert monitor.check_timeout("op") is False

        clock[0] = 901.0
        assert monitor.check_timeout("op") is True

    def test_explicit_timeout_overrides_state(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])
        # Explicit 5s timeout beats the "in_meeting" 900s override
        monitor.start_operation(
            "op", "m1", timeout_seconds=5.0, state="in_meeting"
        )
        clock[0] = 6.0
        assert monitor.check_timeout("op") is True

    def test_get_timeout_for_state(self):
        assert get_timeout_for_state("in_meeting") == 900.0
        assert get_timeout_for_state("created") == 120.0
        assert get_timeout_for_state("queued") == 300.0

    def test_get_timeout_for_unknown_state_falls_back(self):
        assert get_timeout_for_state("nonexistent_state") == DEFAULT_TIMEOUT_SECONDS

    def test_get_timeout_for_state_accepts_enum(self):
        assert get_timeout_for_state(LifecycleState.VALIDATING) == 600.0


# ── 5. State transition to stale ──────────────────────────────────────────


class TestStaleTransition:
    """Verify transition to stale state with timestamp in error_log."""

    def test_transition_to_stale_changes_state(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("round-1", manifest.meeting_id, timeout_seconds=60.0)
            clock[0] = 61.0  # exceed timeout

            result = monitor.transition_to_stale(manifest, "round-1")
            assert result.timed_out is True
            assert result.stale_transition is not None
            assert result.stale_transition.success is True
            assert result.stale_transition.manifest.state == "stale"
        finally:
            _cleanup_dir(root)

    def test_stale_transition_persists_to_disk(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 6.0

            result = monitor.transition_to_stale(manifest, "op", persist=True)
            assert result.stale_transition is not None
            assert result.stale_transition.success is True

            # Verify on disk
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == "stale"
        finally:
            _cleanup_dir(root)

    def test_stale_transition_no_persist(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 6.0

            result = monitor.transition_to_stale(manifest, "op", persist=False)
            assert result.stale_transition is not None
            assert result.stale_transition.success is True

            # Disk must still have original state (created)
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == "created"
        finally:
            _cleanup_dir(root)

    def test_no_timeout_returns_timed_out_false(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=60.0)
            clock[0] = 30.0  # still within threshold

            result = monitor.transition_to_stale(manifest, "op")
            assert result.timed_out is False
            assert result.stale_transition is None
            assert result.elapsed_seconds == 30.0
            assert result.timeout_threshold == 60.0
        finally:
            _cleanup_dir(root)

    def test_timeout_result_contains_error_message(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("round-3", manifest.meeting_id, timeout_seconds=10.0)
            clock[0] = 15.0

            result = monitor.transition_to_stale(manifest, "round-3")
            assert result.timed_out is True
            assert "round-3" in result.error_message
            assert "15.0s" in result.error_message
            assert "10.0s" in result.error_message
        finally:
            _cleanup_dir(root)

    def test_stale_from_already_terminal_blocked(self):
        """Attempting stale transition from an already-terminal state should
        be blocked by guard_is_active."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Advance to stale first
            m = _advance_to(manifest, LifecycleState.STALE)

            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])
            monitor.start_operation("op", m.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(m, "op")
            assert result.timed_out is True
            # The stale transition itself should fail because we're
            # already in a terminal state
            assert result.stale_transition is not None
            assert result.stale_transition.success is False
        finally:
            _cleanup_dir(root)

    def test_stale_state_is_terminal_no_further_transitions(self):
        """Once stale, no further transitions should be possible."""
        assert is_terminal(LifecycleState.STALE) is True
        assert is_terminal("stale") is True


# ── 6. Bulk operations ────────────────────────────────────────────────────


class TestBulkOperations:
    """Verify check_all_timeouts and scan_and_transition_stale_all."""

    def test_check_all_timeouts_returns_timed_out_ids(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])

        monitor.start_operation("op-a", "m1", timeout_seconds=10.0)
        monitor.start_operation("op-b", "m1", timeout_seconds=20.0)
        monitor.start_operation("op-c", "m1", timeout_seconds=30.0)

        clock[0] = 25.0
        timed_out = monitor.check_all_timeouts()
        assert sorted(timed_out) == ["op-a", "op-b"]
        # op-c still has 5s remaining
        assert "op-c" not in timed_out

    def test_check_all_timeouts_empty_when_none_timed_out(self):
        clock = _fake_clock(0.0)
        monitor = TimeoutMonitor(clock=lambda: clock[0])

        monitor.start_operation("op", "m1", timeout_seconds=100.0)
        clock[0] = 50.0
        assert monitor.check_all_timeouts() == []

    def test_scan_and_transition_stale_all(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation(
                "op-1", manifest.meeting_id, timeout_seconds=5.0
            )
            monitor.start_operation(
                "op-2", manifest.meeting_id, timeout_seconds=100.0
            )

            clock[0] = 10.0  # op-1 timed out, op-2 still OK

            results = monitor.scan_and_transition_stale_all(
                manifest, persist=False
            )
            assert len(results) == 1
            assert results[0].timed_out is True
            assert results[0].operation_id == "op-1"
            assert results[0].stale_transition is not None
            assert results[0].stale_transition.success is True
            assert results[0].stale_transition.manifest.state == "stale"
        finally:
            _cleanup_dir(root)

    def test_scan_and_transition_stale_all_multiple_timeouts(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation(
                "op-1", manifest.meeting_id, timeout_seconds=5.0
            )
            monitor.start_operation(
                "op-2", manifest.meeting_id, timeout_seconds=8.0
            )

            clock[0] = 10.0  # both timed out

            results = monitor.scan_and_transition_stale_all(
                manifest, persist=False
            )
            # Both should trigger, but after the first stale transition
            # the manifest is in stale state, so the second's transition
            # should fail (terminal state guard).
            assert len(results) == 2
            # First one should succeed
            assert results[0].stale_transition is not None
            assert results[0].stale_transition.success is True
            assert results[0].stale_transition.manifest.state == "stale"
            # Second one tries from stale → stale which is blocked
            assert results[1].stale_transition is not None
            assert results[1].stale_transition.success is False
        finally:
            _cleanup_dir(root)


# ── 7. TimeoutResult immutability ─────────────────────────────────────────


class TestTimeoutResultImmutability:
    """TimeoutResult must be a frozen dataclass."""

    def test_cannot_mutate_fields(self):
        result = TimeoutResult(
            timed_out=True,
            operation_id="op",
            elapsed_seconds=10.0,
            timeout_threshold=5.0,
            error_message="test",
        )
        with pytest.raises(Exception):
            result.timed_out = False  # type: ignore[misc]

    def test_defaults(self):
        result = TimeoutResult(
            timed_out=False,
            operation_id="op",
            elapsed_seconds=0.0,
            timeout_threshold=10.0,
        )
        assert result.stale_transition is None
        assert result.error_message == ""


# ── 8. Error cases ────────────────────────────────────────────────────────


class TestErrorCases:
    """Verify error handling and edge cases."""

    def test_zero_default_timeout_raises(self):
        with pytest.raises(ValueError, match="must be > 0"):
            TimeoutMonitor(default_timeout_seconds=0)

    def test_negative_default_timeout_raises(self):
        with pytest.raises(ValueError, match="must be > 0"):
            TimeoutMonitor(default_timeout_seconds=-5)

    def test_zero_operation_timeout_raises(self):
        monitor = TimeoutMonitor()
        with pytest.raises(ValueError, match="must be > 0"):
            monitor.start_operation("op", "m1", timeout_seconds=0)

    def test_negative_operation_timeout_raises(self):
        monitor = TimeoutMonitor()
        with pytest.raises(ValueError, match="must be > 0"):
            monitor.start_operation("op", "m1", timeout_seconds=-1)

    def test_state_override_zero_not_allowed(self):
        """Even if STATE_TIMEOUT_OVERRIDES had a zero (it doesn't),
        the resolved timeout must be positive."""
        monitor = TimeoutMonitor()
        # Simulate a bad state override by patching
        with patch.dict(
            "src.timeout_monitor.STATE_TIMEOUT_OVERRIDES",
            {"bad_state": 0.0},
        ):
            with pytest.raises(ValueError, match="must be > 0"):
                monitor.start_operation("op", "m1", state="bad_state")

    def test_elapsed_on_nonexistent_raises(self):
        monitor = TimeoutMonitor()
        with pytest.raises(KeyError):
            monitor.elapsed("nonexistent")

    def test_remaining_on_nonexistent_raises(self):
        monitor = TimeoutMonitor()
        with pytest.raises(KeyError):
            monitor.remaining("nonexistent")

    def test_transition_to_stale_nonexistent_raises(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            monitor = TimeoutMonitor()
            with pytest.raises(KeyError):
                monitor.transition_to_stale(manifest, "nonexistent")
        finally:
            _cleanup_dir(root)


# ── 9. Meeting state after stale ──────────────────────────────────────────


class TestPostStaleState:
    """Verify the system state after a stale transition."""

    def test_stale_manifest_has_state_field(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(manifest, "op", persist=False)
            assert result.stale_transition is not None
            stale_manifest = result.stale_transition.manifest
            assert stale_manifest.state == "stale"
        finally:
            _cleanup_dir(root)

    def test_stale_manifest_preserves_meeting_id(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(manifest, "op", persist=False)
            assert result.stale_transition is not None
            assert result.stale_transition.manifest.meeting_id == manifest.meeting_id
        finally:
            _cleanup_dir(root)

    def test_stale_manifest_preserves_agenda(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(manifest, "op", persist=False)
            assert result.stale_transition is not None
            assert result.stale_transition.manifest.agenda == manifest.agenda
        finally:
            _cleanup_dir(root)

    def test_stale_transition_updates_timestamp(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(manifest, "op", persist=False)
            assert result.stale_transition is not None
            assert (
                result.stale_transition.manifest.updated_at
                != manifest.updated_at
            )
        finally:
            _cleanup_dir(root)


# ── 10. Korean content ────────────────────────────────────────────────────


class TestKoreanContent:
    """Verify Korean content in timeout scenarios."""

    def test_korean_agenda_survives_stale_transition(self):
        root = _tmp_meetings_root()
        try:
            req = MeetingCommandRequest(
                agenda="신규 유닛 '스텔라' 데뷔 전략 회의 ✨",
                user_id=_VALID_USER_ID,
                channel_id=_VALID_CHANNEL_ID,
                priority="p1",
            )
            ctx = create_meeting(req, meetings_root=root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("op", ctx.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(ctx.manifest, "op", persist=False)
            assert result.stale_transition is not None
            assert result.stale_transition.success is True
            assert (
                result.stale_transition.manifest.agenda
                == "신규 유닛 '스텔라' 데뷔 전략 회의 ✨"
            )
        finally:
            _cleanup_dir(root)

    def test_korean_error_message_in_timeout_result(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("회의-1차", manifest.meeting_id, timeout_seconds=5.0)
            clock[0] = 10.0

            result = monitor.transition_to_stale(manifest, "회의-1차")
            assert result.timed_out is True
            assert "회의-1차" in result.error_message
        finally:
            _cleanup_dir(root)


# ── 11. Integration: timeout → stale → terminal ───────────────────────────


class TestIntegrationTimeoutStaleTerminal:
    """End-to-end: timeout detection leads to stale terminal state."""

    def test_full_timeout_to_stale_pipeline(self):
        """The complete flow: create meeting → start operation →
        clock advance past threshold → timeout detected →
        stale transition → state is 'stale' → no further transitions."""
        root = _tmp_meetings_root()
        try:
            # 1. Create meeting
            manifest = _create_test_manifest(root)
            assert manifest.state == "created"

            # 2. Advance to a mid-flow state (e.g. queued)
            m = _advance_to(manifest, LifecycleState.QUEUED)
            assert m.state == "queued"

            # 3. Start monitoring an operation
            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])
            monitor.start_operation(
                "routing-op",
                m.meeting_id,
                timeout_seconds=30.0,
            )

            # 4. Advance clock past threshold
            clock[0] = 45.0
            assert monitor.check_timeout("routing-op") is True

            # 5. Execute stale transition
            result = monitor.transition_to_stale(m, "routing-op", persist=False)
            assert result.timed_out is True
            assert result.stale_transition is not None
            assert result.stale_transition.success is True

            # 6. State assertion: must be stale
            stale_manifest = result.stale_transition.manifest
            assert stale_manifest.state == "stale"

            # 7. Stale is terminal — verify no outgoing transitions
            assert is_terminal("stale") is True
            assert is_terminal(LifecycleState.STALE) is True

            # 8. Verify the stale manifest is a terminal meeting
            from src.transition_engine import execute_transition

            stale_again = execute_transition(
                stale_manifest, LifecycleState.QUEUED, persist=False
            )
            assert stale_again.success is False, (
                "Terminal stale state must reject all transitions"
            )
        finally:
            _cleanup_dir(root)

    def test_multiple_operations_one_timeout(self):
        """Multiple operations tracked; only the timed-out one triggers stale."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            m = _advance_to(manifest, LifecycleState.ROUTING)

            clock = _fake_clock(0.0)
            monitor = TimeoutMonitor(clock=lambda: clock[0])

            monitor.start_operation("routing-main", m.meeting_id, timeout_seconds=10.0)
            monitor.start_operation("routing-fallback", m.meeting_id, timeout_seconds=60.0)

            clock[0] = 25.0
            # routing-main timed out, routing-fallback still OK
            assert monitor.check_timeout("routing-main") is True
            assert monitor.check_timeout("routing-fallback") is False

            result = monitor.transition_to_stale(m, "routing-main", persist=False)
            assert result.timed_out is True
            assert result.stale_transition is not None
            assert result.stale_transition.success is True
            assert result.stale_transition.manifest.state == "stale"
        finally:
            _cleanup_dir(root)
