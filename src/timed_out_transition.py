"""Timed out state transition handler — Sub-AC 4.3.3.

Enter / exit the ``timed_out`` lifecycle state for meeting timeouts,
trigger a partial-progress snapshot capturing everything accomplished
before the timeout, and emit a structured *recovery entry point* so
operators can understand what was preserved and how to resume.

The module is designed as a **standalone state-machine function** — every
entry point accepts a ``MeetingManifest`` (or mock equivalent) and
returns a frozen result.  A *mock timer* is injectable for testing
without real wall-clock waits, satisfying the Sub-AC requirement:
*"testable as a standalone state-machine function with mock timer."*

Architecture
------------
::

    MeetingManifest  ──►  capture_partial_progress()  ──►  PartialProgressSnapshot
              │
              ▼
       build_timeout_recovery()  ──►  RecoveryEntryPoint
              │
              ▼
       detect_timeout()  ──►  bool  (mock-timer injectable)
              │
              ▼
       enter_timed_out_state()  ──►  TimedOutTransitionResult
              │
              ▼
       exit_timed_out_state()  ──►  TimedOutTransitionResult
              │
              ▼
       handle_timeout_transition()  ──►  TimedOutTransitionResult  (orchestrator)

Every timeout event is logged to the manifest's ``error_log``
(**silent fail forbidden** per the Seed constraint).  The meeting's
outputs are preserved on disk as a partial-progress snapshot.

Usage::

    from src.timed_out_transition import (
        TimedOutTransitionContext,
        TimedOutTransitionResult,
        MockTimer,
        enter_timed_out_state,
        exit_timed_out_state,
        handle_timeout_transition,
    )
    from src.shared.lifecycle import LifecycleState

    # Enter timed_out on a timeout detection
    timer = MockTimer(now=1700000100.0)
    ctx = TimedOutTransitionContext(
        manifest=manifest,
        timeout_reason="opencode-go GLM-5.1 call exceeded 120s",
        timeout_duration_s=120.0,
        previous_state=manifest.state,
        timer=timer,
    )
    result = enter_timed_out_state(ctx, persist=False)
    if result.success:
        print(f"Meeting {result.manifest.meeting_id} transitioned to timed_out")
        print(f"Recovery entry: {result.recovery_entry_point}")

    # Later, exit timed_out back to previous state
    exit_result = exit_timed_out_state(
        manifest=result.manifest,
        target_state=ctx.previous_state,
        persist=False,
    )

Modules:
    MockTimer: Injectable timer for deterministic testing.
    TimedOutTransitionContext: Input — everything needed to execute the handler.
    TimedOutTransitionResult: Immutable result of the timed-out state transition.
    detect_timeout: Pure function with injectable timer.
    enter_timed_out_state: Core entry — snapshot + transition + emit recovery.
    exit_timed_out_state: Exit timed_out → resume or terminal state.
    handle_timeout_transition: High-level orchestrator combining all steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from src.failed_transition import (
    PartialProgressSnapshot,
    RecoveryEntryPoint,
    capture_partial_progress,
)
from src.meeting_trigger import MeetingManifest, update_manifest
from src.shared.lifecycle import (
    LifecycleState,
    is_terminal,
    validate_transition,
)

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────

PersistFn = Callable[[MeetingManifest], MeetingManifest]
"""Injectable persistence function for testing without disk I/O."""


# ══════════════════════════════════════════════════════════════════════════
# Mock Timer
# ══════════════════════════════════════════════════════════════════════════


class Timer(Protocol):
    """Protocol for a time source — real or mock."""

    def __call__(self) -> float:
        """Return the current time as a Unix-epoch float."""
        ...


@dataclass(frozen=True)
class MockTimer:
    """Deterministic mock timer for testing timeout detection.

    Provides full control over the current time so that timeout
    conditions can be triggered without real wall-clock waits.
    The *now* value is advanced via ``advance(seconds)`` which
    returns a **new** ``MockTimer`` (immutable).

    Usage::

        timer = MockTimer(now=1000.0)
        assert timer() == 1000.0
        timer = timer.advance(120.0)
        assert timer() == 1120.0
    """

    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> MockTimer:
        """Return a new MockTimer with *now* advanced by *seconds*."""
        return MockTimer(now=self.now + seconds)


@dataclass(frozen=True)
class RealTimer:
    """Real wall-clock timer using ``time.monotonic()``.

    Used in production.  For testing, inject ``MockTimer`` instead.
    """

    def __call__(self) -> float:
        import time
        return time.monotonic()


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _append_error(
    manifest: MeetingManifest,
    error_type: str,
    message: str,
    severity: str = "error",
) -> MeetingManifest:
    """Append a structured error entry to the manifest's error_log.

    Every failure flows through this function, guaranteeing that
    "silent fail" is impossible per the Seed constraint.
    """
    entry: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": error_type,
        "message": message,
        "severity": severity,
    }
    return manifest.with_error(entry)


# ══════════════════════════════════════════════════════════════════════════
# Input: TimedOutTransitionContext
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TimedOutTransitionContext:
    """Immutable context carrying everything needed to execute a
    timed-out state transition.

    This is the single input object for ``enter_timed_out_state()``
    and ``handle_timeout_transition()``.

    Attributes:
        manifest: The meeting manifest at the point of timeout.
        timeout_reason: Human-readable explanation (e.g.
                        "opencode-go GLM-5.1 call exceeded 120s").
        timeout_duration_s: How long the operation ran before timing out.
        previous_state: The lifecycle state the meeting was in before
                        the timeout occurred.  Defaults to ``manifest.state``
                        when not explicitly provided.
        severity: One of ``warning``, ``error`` (default: ``warning``).
        timer: Injectable timer callable.  When None, uses ``RealTimer()``.
               Pass ``MockTimer(now=...)`` for deterministic tests.
        metadata: Arbitrary key-value pairs for logging / auditing.
    """

    manifest: MeetingManifest
    timeout_reason: str
    timeout_duration_s: float = 0.0
    previous_state: str = ""
    severity: str = "warning"
    timer: Timer | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timeout_reason.strip():
            raise ValueError("timeout_reason must not be empty")
        if self.severity not in ("warning", "error", "critical"):
            meeting_label = (
                self.manifest.meeting_id
                if self.manifest is not None
                else "<None manifest>"
            )
            logger.warning(
                "Unknown severity=%r for meeting_id=%s — using 'warning'",
                self.severity,
                meeting_label,
            )
            object.__setattr__(self, "severity", "warning")
        if not self.previous_state:
            if self.manifest is not None:
                object.__setattr__(
                    self, "previous_state", str(self.manifest.state)
                )


# ══════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TimedOutTransitionResult:
    """Immutable result of a timed-out state transition attempt.

    Attributes:
        success: True if the manifest was successfully transitioned
                 to / from the ``timed_out`` state and persisted.
        manifest: The manifest after the transition (``state='timed_out'``
                  on enter-success; original or target state on exit).
        snapshot: The partial-progress snapshot captured before the
                  transition was executed.
        recovery_entry_point: Recovery instructions, or None if the
                              transition itself failed.
        transition_type: Either ``'enter'`` or ``'exit'`` indicating
                         which direction the transition was.
        rejection_reasons: Reasons the transition was rejected (empty
                           when ``success`` is True).
        error: Exception raised during the transition, or None.
    """

    success: bool
    manifest: MeetingManifest
    snapshot: PartialProgressSnapshot
    recovery_entry_point: Optional[RecoveryEntryPoint] = None
    transition_type: str = "enter"
    rejection_reasons: tuple[str, ...] = ()
    error: Optional[Exception] = None


# ══════════════════════════════════════════════════════════════════════════
# 1.  detect_timeout  (pure function with injectable timer)
# ══════════════════════════════════════════════════════════════════════════


def detect_timeout(
    started_at: float,
    timeout_limit_s: float,
    *,
    timer: Timer | None = None,
) -> bool:
    """Detect whether a timeout condition has occurred.

    This is a **pure function** that is fully testable with a mock timer.
    No side-effects, no I/O — just a deterministic time comparison.

    Args:
        started_at: Unix epoch when the operation began (from the timer).
        timeout_limit_s: Maximum allowed duration in seconds.
        timer: Injectable timer.  When None, uses ``RealTimer()``.

    Returns:
        True if ``timer() - started_at >= timeout_limit_s``.

    Example:
        >>> t = MockTimer(now=1000.0)
        >>> detect_timeout(started_at=1000.0, timeout_limit_s=120.0, timer=t)
        False
        >>> t = t.advance(120.0)
        >>> detect_timeout(started_at=1000.0, timeout_limit_s=120.0, timer=t)
        True
        >>> detect_timeout(started_at=1000.0, timeout_limit_s=120.0, timer=t)
        True
    """
    _timer = timer if timer is not None else RealTimer()
    elapsed = _timer() - started_at
    return elapsed >= timeout_limit_s


# ══════════════════════════════════════════════════════════════════════════
# 2.  build_timeout_recovery  (pure function)
# ══════════════════════════════════════════════════════════════════════════


def build_timeout_recovery(
    snapshot: PartialProgressSnapshot,
    timeout_reason: str,
    timeout_duration_s: float,
    *,
    last_good_state: str = "",
) -> RecoveryEntryPoint:
    """Derive a recovery entry point for a timed-out meeting.

    A timed-out meeting's data is intact — the operation simply exceeded
    its wall-clock budget.  The recovery entry point captures what was
    preserved and provides instructions for resuming.

    Decision matrix:

    ===================  ========================  =====================
    Snapshot state        recommended_action        resume_possible
    ===================  ========================  =====================
    rounds > 0            retry_from_snapshot       True
    rounds == 0, context   retry_from_snapshot       True
    rounds == 0, no ctx    manual_audit_only         False
    ===================  ========================  =====================

    Args:
        snapshot: The partial-progress snapshot at timeout.
        timeout_reason: Human-readable timeout reason.
        timeout_duration_s: How long the operation ran before timing out.
        last_good_state: The last known-good lifecycle state before
                         timeout.  Defaults to ``snapshot.completed_step``
                         when empty.

    Returns:
        A ``RecoveryEntryPoint`` with recommended recovery action.
    """
    good_state = last_good_state or snapshot.completed_step or snapshot.state_at_failure

    # Determine if any meaningful progress was made
    has_progress = (
        snapshot.round_count > 0
        or snapshot.had_context
        or snapshot.decisions_count > 0
        or snapshot.tool_outputs_count > 0
        or snapshot.had_consensus
    )

    if has_progress:
        action = RecoveryEntryPoint.ACTION_RETRY
        resume = True
    else:
        action = RecoveryEntryPoint.ACTION_AUDIT
        resume = False

    return RecoveryEntryPoint(
        meeting_id=snapshot.meeting_id,
        failure_category="timed_out",
        failure_reason=(
            f"Timed out after {timeout_duration_s:.1f}s: {timeout_reason}"
        ),
        resume_possible=resume,
        recommended_action=action,
        last_good_state=good_state,
        snapshot=snapshot,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3.  enter_timed_out_state  (orchestrator — detect + snapshot + transition)
# ══════════════════════════════════════════════════════════════════════════


def enter_timed_out_state(
    ctx: TimedOutTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> TimedOutTransitionResult:
    """Enter the ``timed_out`` lifecycle state for a meeting timeout.

    This is the core entry function for Sub-AC 4.3.3.  It performs
    these steps in order:

    1. **Capture snapshot** — call ``capture_partial_progress()`` on
       the current manifest to preserve all accumulated work.
    2. **Validate transition** — check that ``manifest.state → timed_out``
       is a legal transition.
    3. **Log timeout event** — append a structured entry to the
       manifest's ``error_log``.
    4. **Mutate state** — transition the manifest to ``state='timed_out'``.
    5. **Persist** — write the manifest to disk (unless ``persist=False``),
       preserving outputs.
    6. **Build recovery entry point** — derive recovery instructions
       from the snapshot and timeout context.

    All errors are logged to ``manifest.error_log`` — silent fail is
    impossible per the Seed constraint.

    Args:
        ctx: The ``TimedOutTransitionContext`` with the manifest,
             timeout reason, duration, and previous state.
        persist: If True (default), the manifest is written to disk
                 after the transition.  Set to False for dry-run or
                 testing scenarios where disk I/O is undesirable.
        persist_fn: Injectable persistence function for testing.
                    When None and ``persist=True``, uses the built-in
                    ``update_manifest()``.

    Returns:
        A ``TimedOutTransitionResult`` with success status, the (possibly
        updated) manifest, the progress snapshot, and recovery instructions.

    Example:
        >>> from src.meeting_trigger import MeetingManifest
        >>> from src.timed_out_transition import (
        ...     TimedOutTransitionContext, enter_timed_out_state,
        ... )
        >>> manifest = MeetingManifest(
        ...     meeting_id="m-test", state="in_meeting",
        ...     round_count=1,
        ... )
        >>> ctx = TimedOutTransitionContext(
        ...     manifest=manifest,
        ...     timeout_reason="opencode-go call exceeded 120s",
        ...     timeout_duration_s=120.0,
        ... )
        >>> result = enter_timed_out_state(ctx, persist=False)
        >>> result.success
        True
        >>> result.manifest.state
        'timed_out'
        >>> result.transition_type
        'enter'
    """
    manifest = ctx.manifest
    rejection_reasons: list[str] = []

    # ── Step 1: Capture partial-progress snapshot ──────────────────────
    snapshot = capture_partial_progress(manifest)
    logger.info(
        "Captured partial-progress snapshot for meeting_id=%s: "
        "rounds=%d decisions=%d context_packets=%d tool_outputs=%d "
        "validation_score=%.2f verdict=%s",
        snapshot.meeting_id,
        snapshot.round_count,
        snapshot.decisions_count,
        snapshot.context_packets_count,
        snapshot.tool_outputs_count,
        snapshot.validation_score,
        snapshot.validation_verdict,
    )

    # ── Guard: already terminal ────────────────────────────────────────
    if is_terminal(manifest.state):
        reason = (
            f"Meeting {manifest.meeting_id} is already in terminal state "
            f"'{manifest.state}' — cannot transition to timed_out"
        )
        logger.warning(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "already_terminal", reason, "error")
        recovery_entry = build_timeout_recovery(
            snapshot=snapshot,
            timeout_reason=ctx.timeout_reason,
            timeout_duration_s=ctx.timeout_duration_s,
            last_good_state=snapshot.completed_step,
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            transition_type="enter",
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Guard: already timed_out ───────────────────────────────────────
    if str(manifest.state) == "timed_out":
        reason = (
            f"Meeting {manifest.meeting_id} is already in 'timed_out' state"
        )
        logger.warning(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "already_timed_out", reason, "warning")
        recovery_entry = build_timeout_recovery(
            snapshot=snapshot,
            timeout_reason=ctx.timeout_reason,
            timeout_duration_s=ctx.timeout_duration_s,
            last_good_state=snapshot.completed_step,
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            transition_type="enter",
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 2: Validate the transition ────────────────────────────────
    if not validate_transition(manifest.state, "timed_out"):
        reason = (
            f"Invalid transition: {manifest.state} → timed_out is not "
            f"permitted by the meeting lifecycle state machine"
        )
        logger.error(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "invalid_transition", reason, "error")
        recovery_entry = build_timeout_recovery(
            snapshot=snapshot,
            timeout_reason=ctx.timeout_reason,
            timeout_duration_s=ctx.timeout_duration_s,
            last_good_state=snapshot.completed_step,
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            transition_type="enter",
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 3: Log the timeout event to error_log ─────────────────────
    timeout_entry: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": "timed_out_state_transition",
        "message": ctx.timeout_reason,
        "severity": ctx.severity,
        "timeout_duration_s": str(ctx.timeout_duration_s),
        "previous_state": ctx.previous_state,
        "recovery": (
            f"Timed out after {ctx.timeout_duration_s:.1f}s. "
            f"Snapshot captured with {snapshot.round_count} round(s), "
            f"{snapshot.decisions_count} decision(s), "
            f"{snapshot.context_packets_count} context packet(s). "
            f"Outputs preserved on disk."
        ),
    }
    # Merge caller metadata into the error entry
    for key, value in ctx.metadata.items():
        if key not in timeout_entry:
            timeout_entry[f"meta_{key}"] = value

    # Include snapshot summary fields
    for key, value in snapshot.to_dict().items():
        sn_key = f"snapshot_{key}"
        if sn_key not in timeout_entry:
            timeout_entry[sn_key] = str(value)

    manifest = manifest.with_error(timeout_entry)
    logger.info(
        "Logged timeout event for meeting_id=%s: duration=%.1fs reason=%s",
        manifest.meeting_id,
        ctx.timeout_duration_s,
        ctx.timeout_reason,
    )

    # ── Step 4: Mutate state to timed_out ──────────────────────────────
    # Store the previous_state as completed_step so exit_timed_out_state
    # knows where to resume.
    try:
        # Update completed_step before state mutation to capture
        # the state we were in.
        manifest = manifest.with_error({
            "timestamp": _utc_now_iso(),
            "error_type": "timed_out_previous_state",
            "message": f"Previous state before timeout: {ctx.previous_state}",
            "severity": "info",
            "previous_state": ctx.previous_state,
        })
        manifest = manifest.with_state(LifecycleState.TIMED_OUT)
    except Exception as exc:
        reason = f"State mutation to 'timed_out' raised: {exc}"
        logger.exception(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(
            manifest, "state_mutation_error", reason, "critical"
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            transition_type="enter",
            rejection_reasons=tuple(rejection_reasons),
            error=exc,
        )

    logger.info(
        "Meeting %s transitioned to state='timed_out'", manifest.meeting_id
    )

    # ── Step 5: Persist manifest before external calls ─────────────────
    if persist:
        try:
            if persist_fn is not None:
                manifest = persist_fn(manifest)
            else:
                manifest = update_manifest(manifest)
            logger.info(
                "Manifest persisted for meeting_id=%s at %s",
                manifest.meeting_id,
                manifest.manifest_path,
            )
        except Exception as exc:
            reason = f"Manifest persistence failed: {exc}"
            logger.exception(reason)
            manifest = _append_error(
                manifest, "persistence_error", reason, "critical"
            )
            rejection_reasons.append(reason)
            return TimedOutTransitionResult(
                success=False,
                manifest=manifest,
                snapshot=snapshot,
                transition_type="enter",
                rejection_reasons=tuple(rejection_reasons),
                error=exc,
            )

    # ── Step 6: Build recovery entry point ─────────────────────────────
    recovery_entry = build_timeout_recovery(
        snapshot=snapshot,
        timeout_reason=ctx.timeout_reason,
        timeout_duration_s=ctx.timeout_duration_s,
        last_good_state=ctx.previous_state,
    )

    logger.info(
        "Recovery entry point for meeting_id=%s: action=%s resume_possible=%s",
        recovery_entry.meeting_id,
        recovery_entry.recommended_action,
        recovery_entry.resume_possible,
    )

    return TimedOutTransitionResult(
        success=True,
        manifest=manifest,
        snapshot=snapshot,
        recovery_entry_point=recovery_entry,
        transition_type="enter",
    )


# ══════════════════════════════════════════════════════════════════════════
# 4.  exit_timed_out_state  (resume or degrade)
# ══════════════════════════════════════════════════════════════════════════


def exit_timed_out_state(
    manifest: MeetingManifest,
    target_state: LifecycleState | str,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> TimedOutTransitionResult:
    """Exit the ``timed_out`` state and transition to *target_state*.

    This function performs the **exit** half of the timed-out state
    machine.  It validates that the manifest is currently in the
    ``timed_out`` state and that the target transition is legal.

    Steps:
    1. **Guard: must be in timed_out** — reject if not.
    2. **Validate transition** — ``timed_out → target_state`` must be legal.
    3. **Mutate state** — transition to *target_state*.
    4. **Persist** — write the manifest to disk (unless ``persist=False``).

    Args:
        manifest: The manifest currently in ``state='timed_out'``.
        target_state: The lifecycle state to resume to (e.g. the
                      ``previous_state`` from the timeout context).
        persist: If True (default), persist after transition.
        persist_fn: Injectable persistence function for testing.

    Returns:
        A ``TimedOutTransitionResult`` with ``transition_type='exit'``.

    Example:
        >>> from src.meeting_trigger import MeetingManifest
        >>> from src.timed_out_transition import exit_timed_out_state
        >>> manifest = MeetingManifest(
        ...     meeting_id="m-test", state="timed_out",
        ...     round_count=1,
        ... )
        >>> result = exit_timed_out_state(
        ...     manifest, "in_meeting", persist=False,
        ... )
        >>> result.success
        True
        >>> result.manifest.state
        'in_meeting'
        >>> result.transition_type
        'exit'
    """
    target_str = str(target_state)
    rejection_reasons: list[str] = []

    # ── Guard: must be in timed_out ────────────────────────────────────
    if str(manifest.state) != "timed_out":
        reason = (
            f"Cannot exit timed_out: manifest is in state "
            f"'{manifest.state}', not 'timed_out'"
        )
        logger.warning(reason)
        rejection_reasons.append(reason)
        snapshot = capture_partial_progress(manifest)
        manifest = _append_error(
            manifest, "not_timed_out", reason, "error"
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            transition_type="exit",
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 2: Validate the transition ────────────────────────────────
    if not validate_transition("timed_out", target_str):
        reason = (
            f"Invalid exit transition: timed_out → {target_str} is not "
            f"permitted by the meeting lifecycle state machine"
        )
        logger.error(reason)
        rejection_reasons.append(reason)
        snapshot = capture_partial_progress(manifest)
        manifest = _append_error(manifest, "invalid_transition", reason, "error")
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            transition_type="exit",
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 3: Mutate state ───────────────────────────────────────────
    try:
        manifest = manifest.with_state(target_str)
    except Exception as exc:
        reason = f"State mutation to '{target_str}' raised: {exc}"
        logger.exception(reason)
        rejection_reasons.append(reason)
        snapshot = capture_partial_progress(manifest)
        manifest = _append_error(
            manifest, "state_mutation_error", reason, "critical"
        )
        return TimedOutTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            transition_type="exit",
            rejection_reasons=tuple(rejection_reasons),
            error=exc,
        )

    logger.info(
        "Meeting %s exited timed_out → %s", manifest.meeting_id, target_str
    )

    # ── Step 4: Persist ────────────────────────────────────────────────
    if persist:
        try:
            if persist_fn is not None:
                manifest = persist_fn(manifest)
            else:
                manifest = update_manifest(manifest)
            logger.info(
                "Manifest persisted for meeting_id=%s at %s (exit timed_out)",
                manifest.meeting_id,
                manifest.manifest_path,
            )
        except Exception as exc:
            reason = f"Manifest persistence failed on exit: {exc}"
            logger.exception(reason)
            manifest = _append_error(
                manifest, "persistence_error", reason, "critical"
            )
            snapshot = capture_partial_progress(manifest)
            rejection_reasons.append(reason)
            return TimedOutTransitionResult(
                success=False,
                manifest=manifest,
                snapshot=snapshot,
                transition_type="exit",
                rejection_reasons=tuple(rejection_reasons),
                error=exc,
            )

    snapshot = capture_partial_progress(manifest)

    return TimedOutTransitionResult(
        success=True,
        manifest=manifest,
        snapshot=snapshot,
        transition_type="exit",
    )


# ══════════════════════════════════════════════════════════════════════════
# 5.  handle_timeout_transition  (high-level orchestrator)
# ══════════════════════════════════════════════════════════════════════════


def handle_timeout_transition(
    ctx: TimedOutTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> TimedOutTransitionResult:
    """High-level orchestrator for timeout state transition handling.

    Convenience wrapper around ``enter_timed_out_state()`` that adds:

    * Basic input validation (manifest is not None, timeout_reason is set).
    * Catching of unexpected exceptions so that even if the handler
      itself crashes, the manifest is annotated with the error.

    This is the recommended entry point for production code.  Use
    ``enter_timed_out_state()`` directly when you need finer control
    (e.g. testing individual steps).

    Args:
        ctx: The ``TimedOutTransitionContext``.
        persist: Forwarded to ``enter_timed_out_state()``.
        persist_fn: Forwarded to ``enter_timed_out_state()``.

    Returns:
        A ``TimedOutTransitionResult``.

    Raises:
        ValueError: If ``ctx.manifest`` is None.
    """
    if ctx.manifest is None:
        raise ValueError(
            "TimedOutTransitionContext.manifest must not be None"
        )

    if not ctx.timeout_reason.strip():
        logger.warning(
            "handle_timeout_transition called with empty timeout_reason "
            "for meeting_id=%s",
            ctx.manifest.meeting_id,
        )

    try:
        return enter_timed_out_state(
            ctx, persist=persist, persist_fn=persist_fn
        )
    except Exception as exc:
        logger.exception(
            "Unexpected exception in handle_timeout_transition for "
            "meeting_id=%s: %s",
            ctx.manifest.meeting_id,
            exc,
        )
        # Try to log the error before giving up
        try:
            annotated = _append_error(
                ctx.manifest,
                "handler_exception",
                f"handle_timeout_transition crashed: {exc}",
                "critical",
            )
        except Exception:
            annotated = ctx.manifest

        snapshot = capture_partial_progress(ctx.manifest)
        return TimedOutTransitionResult(
            success=False,
            manifest=annotated,
            snapshot=snapshot,
            transition_type="enter",
            rejection_reasons=(f"Handler crashed: {exc}",),
            error=exc,
        )
