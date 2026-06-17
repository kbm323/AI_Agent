"""Timeout monitor for meeting operations — Sub-AC 4.3.3.

Monitors operation duration against configurable thresholds, detects
timeout breaches, and triggers stale state transitions.  The clock is
injectable for deterministic unit testing.

Architecture
------------

1. **Clock injection** — ``TimeoutMonitor`` accepts a ``clock`` callable
   (default ``time.monotonic``).  Tests inject a fake clock to simulate
   time passage without wall-clock delays.

2. **Operation tracking** — ``start_operation()`` records the monotonic
   timestamp and an optional timeout override for a named operation.
   ``check_timeout()`` returns True if the operation has exceeded its
   threshold.

3. **Bulk timeout scan** — ``check_all_timeouts()`` scans every tracked
   operation and returns a list of operation IDs that have exceeded
   their timeouts, optionally transitioning the meeting to ``stale``.

4. **Stale transition** — ``transition_to_stale()`` uses the
   ``TransitionEngine`` to move the meeting to ``LifecycleState.STALE``
   with an error log entry recording the timeout details.

Usage::

    from src.timeout_monitor import TimeoutMonitor
    from src.meeting_trigger import MeetingManifest

    monitor = TimeoutMonitor(default_timeout_seconds=300.0)
    monitor.start_operation("round-2", manifest.meeting_id)

    # ... later, check for timeout ...
    if monitor.check_timeout("round-2"):
        result = monitor.transition_to_stale(manifest, "round-2")
        # result.manifest.state == "stale"

Modules:
    TimeoutMonitor: Injectable-clock timeout detector with stale transition.
    TimeoutResult: Immutable result of a timeout check + stale transition.
    default_timeout_seconds: System-wide default (600 s = 10 min).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.meeting_trigger import MeetingManifest
from src.shared.lifecycle import LifecycleState
from src.transition_engine import (
    TransitionResult,
    execute_transition,
    guard_is_active,
)

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS: float = 600.0
"""System-wide default operation timeout (10 minutes).

Individual operations may override via ``timeout_seconds`` in
``start_operation()``.
"""

# Per-state timeout overrides (seconds).  Longer-running states
# (in_meeting, consensus_building, validating, executing) get
# proportionally larger windows.
STATE_TIMEOUT_OVERRIDES: dict[str, float] = {
    "created": 120.0,            # 2 min — should transition quickly
    "queued": 300.0,             # 5 min — waiting for slot
    "routing": 120.0,            # 2 min — Qwen LLM classification
    "context_retrieval": 180.0,  # 3 min — knowledge layer queries
    "in_meeting": 900.0,         # 15 min — multi-round discussion
    "consensus_building": 600.0, # 10 min — synthesis
    "validating": 600.0,        # 10 min — GLM/Codex validation
    "executing": 900.0,         # 15 min — tool-use execution
    "finalizing": 300.0,        # 5 min — report generation
}


# ── Operation record ──────────────────────────────────────────────────────


@dataclass
class _Operation:
    """Internal mutable record for a tracked operation."""

    operation_id: str
    meeting_id: str
    start_time: float  # monotonic seconds
    timeout_seconds: float


# ── Timeout result ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimeoutResult:
    """Immutable result of a timeout check and stale transition attempt.

    Attributes:
        timed_out: True if the operation exceeded its timeout threshold.
        operation_id: The operation that was checked.
        elapsed_seconds: Wall-clock (or injected-clock) seconds elapsed.
        timeout_threshold: The threshold that was compared against.
        stale_transition: The TransitionResult from attempting the stale
                          transition (None if ``timed_out`` is False).
        error_message: Human-readable timeout description (empty if no
                       timeout occurred).
    """

    timed_out: bool
    operation_id: str
    elapsed_seconds: float
    timeout_threshold: float
    stale_transition: Optional[TransitionResult] = None
    error_message: str = ""


# ── Timeout monitor ──────────────────────────────────────────────────────


class TimeoutMonitor:
    """Injectable-clock timeout detector with stale transition integration.

    Tracks named operations with start timestamps and configurable
    timeout thresholds.  When a timeout is detected, it can optionally
    transition the associated meeting to ``LifecycleState.STALE`` via
    the ``TransitionEngine``.

    Clock injection::

        # Test with a fake clock
        fake_time = [0.0]
        def fake_clock() -> float:
            return fake_time[0]

        monitor = TimeoutMonitor(clock=fake_clock)
        monitor.start_operation("op-1", "meeting-1")
        fake_time[0] = 999.0  # advance past default 600s timeout
        assert monitor.check_timeout("op-1") is True

    State transition integration::

        result = monitor.transition_to_stale(manifest, "op-1")
        if result.success:
            # result.manifest.state == "stale"
            # Timeout details logged to result.manifest.error_log

    Attributes:
        default_timeout_seconds: Fallback timeout when no per-operation
                                 or per-state override is specified.
        clock: Callable that returns the current monotonic time.
               Defaults to ``time.monotonic``.
    """

    def __init__(
        self,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise the timeout monitor.

        Args:
            default_timeout_seconds: Default timeout for operations
                                     that don't specify an override.
            clock: Injectable time source.  Must return monotonically
                   increasing float seconds.  Default: ``time.monotonic``.
        """
        if default_timeout_seconds <= 0:
            raise ValueError(
                f"default_timeout_seconds must be > 0, "
                f"got {default_timeout_seconds}"
            )
        self.default_timeout_seconds = default_timeout_seconds
        self.clock = clock
        self._operations: dict[str, _Operation] = {}

    # ── Operation lifecycle ───────────────────────────────────────────

    def start_operation(
        self,
        operation_id: str,
        meeting_id: str,
        *,
        timeout_seconds: Optional[float] = None,
        state: Optional[str] = None,
    ) -> float:
        """Begin tracking an operation's duration.

        Args:
            operation_id: Unique name for this operation within the
                          monitor instance (e.g. 'round-2-consensus').
            meeting_id: The meeting this operation belongs to.
            timeout_seconds: Per-operation timeout override.
                             If None, uses the per-state override
                             (if *state* is provided) or the monitor's
                             ``default_timeout_seconds``.
            state: Current meeting lifecycle state, used to look up
                   the per-state timeout override from
                   ``STATE_TIMEOUT_OVERRIDES``.

        Returns:
            The monotonic start time (from the injected clock).

        Raises:
            ValueError: If *operation_id* is already being tracked.
        """
        if operation_id in self._operations:
            raise ValueError(
                f"Operation '{operation_id}' is already being tracked. "
                f"Call check_timeout() or remove_operation() first."
            )

        # Resolve timeout: explicit > per-state > default
        if timeout_seconds is not None:
            resolved = timeout_seconds
        elif state is not None and state in STATE_TIMEOUT_OVERRIDES:
            resolved = STATE_TIMEOUT_OVERRIDES[state]
        else:
            resolved = self.default_timeout_seconds

        if resolved <= 0:
            raise ValueError(
                f"Resolved timeout must be > 0, got {resolved} "
                f"(operation={operation_id}, state={state})"
            )

        now = self.clock()
        self._operations[operation_id] = _Operation(
            operation_id=operation_id,
            meeting_id=meeting_id,
            start_time=now,
            timeout_seconds=resolved,
        )
        logger.debug(
            "TimeoutMonitor: started operation '%s' (meeting=%s, "
            "timeout=%.1fs, start=%.3f)",
            operation_id,
            meeting_id,
            resolved,
            now,
        )
        return now

    def remove_operation(self, operation_id: str) -> None:
        """Stop tracking an operation (no-op if not tracked).

        Call this when an operation completes successfully to avoid
        false-positive timeout detection.
        """
        self._operations.pop(operation_id, None)

    # ── Timeout detection ─────────────────────────────────────────────

    def check_timeout(self, operation_id: str) -> bool:
        """Check whether *operation_id* has exceeded its timeout.

        Args:
            operation_id: The operation to check.

        Returns:
            True if the operation has timed out.

        Raises:
            KeyError: If *operation_id* is not being tracked.
        """
        op = self._operations[operation_id]
        elapsed = self.clock() - op.start_time
        return elapsed > op.timeout_seconds

    def elapsed(self, operation_id: str) -> float:
        """Return elapsed seconds for *operation_id*.

        Args:
            operation_id: The operation to query.

        Returns:
            Monotonic seconds since ``start_operation()`` was called.

        Raises:
            KeyError: If *operation_id* is not being tracked.
        """
        op = self._operations[operation_id]
        return self.clock() - op.start_time

    def remaining(self, operation_id: str) -> float:
        """Return remaining seconds before timeout, or 0.0 if already timed out.

        Args:
            operation_id: The operation to query.

        Returns:
            Non-negative float.  0.0 means the operation has timed out
            (or exactly hit the threshold).

        Raises:
            KeyError: If *operation_id* is not being tracked.
        """
        op = self._operations[operation_id]
        elapsed = self.clock() - op.start_time
        remaining = op.timeout_seconds - elapsed
        return max(0.0, remaining)

    def check_all_timeouts(self) -> list[str]:
        """Scan every tracked operation and return IDs that timed out.

        Does NOT remove timed-out operations — the caller owns
        cleanup via ``remove_operation()``.

        Returns:
            List of operation IDs that have exceeded their timeout.
        """
        timed_out: list[str] = []
        for op_id in list(self._operations):
            if self.check_timeout(op_id):
                timed_out.append(op_id)
        return timed_out

    # ── Stale transition ──────────────────────────────────────────────

    def transition_to_stale(
        self,
        manifest: MeetingManifest,
        operation_id: str,
        *,
        persist: bool = True,
    ) -> TimeoutResult:
        """Check for timeout and transition meeting to STALE if breached.

        If the operation has timed out AND the meeting is in an active
        state, executes a validated transition to ``LifecycleState.STALE``
        via the ``TransitionEngine``.

        The timeout breach details are recorded in the manifest's
        ``error_log`` regardless of whether the stale transition
        succeeds.

        Args:
            manifest: Current meeting manifest.
            operation_id: The operation to check for timeout.
            persist: If True, persist the manifest after transition
                     (default).  Set to False for test scenarios.

        Returns:
            A ``TimeoutResult`` with timeout status, elapsed time,
            and (if timeout occurred) the stale transition result.

        Raises:
            KeyError: If *operation_id* is not being tracked.
        """
        op = self._operations[operation_id]
        elapsed = self.clock() - op.start_time
        timed_out = elapsed > op.timeout_seconds

        if not timed_out:
            return TimeoutResult(
                timed_out=False,
                operation_id=operation_id,
                elapsed_seconds=elapsed,
                timeout_threshold=op.timeout_seconds,
            )

        # ── Timeout detected: attempt stale transition ────────────────
        logger.warning(
            "TimeoutMonitor: operation '%s' timed out after %.1fs "
            "(threshold=%.1fs, meeting=%s, state=%s)",
            operation_id,
            elapsed,
            op.timeout_seconds,
            manifest.meeting_id,
            manifest.state,
        )

        # Try the stale transition.  guard_is_active prevents
        # transitioning from already-terminal states (stale, completed,
        # etc.) — which is correct: a meeting already in stale shouldn't
        # be re-transitioned.
        stale_result = execute_transition(
            manifest,
            LifecycleState.STALE,
            pre_conditions=(guard_is_active,),
            persist=persist,
            label=f"timeout-{operation_id}",
        )

        error_msg = (
            f"Operation '{operation_id}' timed out after {elapsed:.1f}s "
            f"(threshold: {op.timeout_seconds:.1f}s). "
            f"Meeting state was '{manifest.state}' at timeout detection."
        )

        return TimeoutResult(
            timed_out=True,
            operation_id=operation_id,
            elapsed_seconds=elapsed,
            timeout_threshold=op.timeout_seconds,
            stale_transition=stale_result,
            error_message=error_msg,
        )

    # ── Bulk stale transition ─────────────────────────────────────────

    def scan_and_transition_stale_all(
        self,
        manifest: MeetingManifest,
        *,
        persist: bool = True,
    ) -> list[TimeoutResult]:
        """Scan all tracked operations and stale-transition any that timed out.

        This is the primary polling entry point for the coordinator loop:
        call it periodically and handle any returned stale transitions.

        Args:
            manifest: Current meeting manifest.
            persist: Forwarded to ``transition_to_stale()``.

        Returns:
            List of ``TimeoutResult`` for operations that timed out.
            Empty list if no timeouts were detected.
        """
        results: list[TimeoutResult] = []
        for op_id in self.check_all_timeouts():
            result = self.transition_to_stale(
                manifest, op_id, persist=persist
            )
            results.append(result)
            # Update manifest reference for subsequent operations
            if result.stale_transition is not None and result.stale_transition.success:
                manifest = result.stale_transition.manifest
        return results

    # ── Inspection ────────────────────────────────────────────────────

    @property
    def active_operations(self) -> int:
        """Number of operations currently being tracked."""
        return len(self._operations)

    def operation_ids(self) -> list[str]:
        """Return a sorted list of tracked operation IDs."""
        return sorted(self._operations)


# ── Convenience: per-state timeout lookup ─────────────────────────────────


def get_timeout_for_state(state: str | LifecycleState) -> float:
    """Return the timeout threshold for a given lifecycle state.

    Args:
        state: The lifecycle state (member or string).

    Returns:
        Timeout in seconds.  Falls back to ``DEFAULT_TIMEOUT_SECONDS``
        if the state has no specific override.
    """
    state_str = str(state)
    return STATE_TIMEOUT_OVERRIDES.get(state_str, DEFAULT_TIMEOUT_SECONDS)
