"""Cancelled state transition handler — Sub-AC 4.3.2.

Enter the ``cancelled`` lifecycle state, trigger a partial-progress
snapshot capturing everything accomplished before cancellation, and
emit a structured *recovery entry point* so operators can understand
what was preserved and how to resume if desired.

The module is designed as a **standalone state-machine function** — every
entry point accepts a ``MeetingManifest`` (or mock equivalent) and returns
a frozen result.  No disk I/O is performed unless the caller explicitly
requests persistence.  This makes the entire module testable with mock
meeting contexts.

Architecture
------------
::

    MeetingManifest  ──►  capture_partial_progress()  ──►  PartialProgressSnapshot
              │
              ▼
       build_cancellation_recovery()  ──►  RecoveryEntryPoint
              │
              ▼
       enter_cancelled_state()  ──►  CancelledTransitionResult
              │
              ▼
       handle_cancelled_transition()  ──►  CancelledTransitionResult  (orchestrator)

Every cancellation event is logged to the manifest's ``error_log``
(**silent fail forbidden** per the Seed constraint).  The meeting's
outputs are preserved on disk as a partial-progress snapshot per the
Seed exit condition: *"user_cancelled: outputs preserved on disk."*

Usage::

    from src.cancelled_transition import (
        CancelledTransitionContext,
        CancelledTransitionResult,
        enter_cancelled_state,
        handle_cancelled_transition,
    )
    from src.shared.lifecycle import LifecycleState

    ctx = CancelledTransitionContext(
        manifest=manifest,
        cancel_reason="User issued /cancel from Discord",
        cancelled_by="u-discord-12345",
    )
    result = handle_cancelled_transition(ctx, persist=True)
    if result.success:
        print(f"Meeting {result.manifest.meeting_id} transitioned to cancelled")
        print(f"Recovery entry: {result.recovery_entry_point}")

Modules:
    CancelledTransitionContext: Input — everything needed to execute the handler.
    CancelledTransitionResult: Immutable result of the cancelled-state transition.
    enter_cancelled_state: Execute the single-step cancelled-state transition.
    handle_cancelled_transition: High-level orchestrator combining all steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

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

# ── Type alias ────────────────────────────────────────────────────────────

PersistFn = Callable[[MeetingManifest], MeetingManifest]
"""Injectable persistence function for testing without disk I/O."""


# ── Internal helper ───────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════
# Input: CancelledTransitionContext
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CancelledTransitionContext:
    """Immutable context carrying everything needed to execute a
    cancelled-state transition.

    This is the single input object for both ``enter_cancelled_state()``
    and ``handle_cancelled_transition()``.

    Attributes:
        manifest: The meeting manifest at the point of cancellation.
        cancel_reason: Human-readable explanation of why the meeting
                       was cancelled (e.g. "User issued /cancel from
                       Discord", "Admin override", "Scheduled shutdown").
        cancelled_by: Identifier of who or what cancelled the meeting
                      (e.g. Discord user ID, "system", "admin-override").
        severity: One of ``info``, ``warning`` (default: ``info``).
        metadata: Arbitrary key-value pairs for logging / auditing
                  (e.g. ``{'discord_message_id': '12345'}``).
    """

    manifest: MeetingManifest
    cancel_reason: str
    cancelled_by: str = "unknown"
    severity: str = "info"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cancel_reason.strip():
            raise ValueError("cancel_reason must not be empty")
        if self.severity not in ("info", "warning", "error", "critical"):
            logger.warning(
                "Unknown severity=%r for meeting_id=%s — using 'info'",
                self.severity,
                self.manifest.meeting_id,
            )
            object.__setattr__(self, "severity", "info")


# ══════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CancelledTransitionResult:
    """Immutable result of a cancelled-state transition attempt.

    Attributes:
        success: True if the manifest was successfully transitioned
                 to the ``cancelled`` state and persisted.
        manifest: The manifest after the transition (``state='cancelled'``
                  on success; original manifest on failure).
        snapshot: The partial-progress snapshot captured before the
                  transition was executed.
        recovery_entry_point: Recovery instructions describing what was
                              preserved and how to resume if desired,
                              or None if the transition itself failed.
        rejection_reasons: Reasons the transition was rejected (empty
                           when ``success`` is True).
        error: Exception raised during the transition, or None.
    """

    success: bool
    manifest: MeetingManifest
    snapshot: PartialProgressSnapshot
    recovery_entry_point: Optional[RecoveryEntryPoint] = None
    rejection_reasons: tuple[str, ...] = ()
    error: Optional[Exception] = None


# ══════════════════════════════════════════════════════════════════════════
# 1.  build_cancellation_recovery  (pure function)
# ══════════════════════════════════════════════════════════════════════════


def build_cancellation_recovery(
    snapshot: PartialProgressSnapshot,
    cancel_reason: str,
    cancelled_by: str,
    *,
    last_good_state: str = "",
) -> RecoveryEntryPoint:
    """Derive a recovery entry point for a cancelled meeting.

    Unlike a failed meeting (where data may be corrupted), a cancelled
    meeting's data is intact — the user deliberately stopped the process.
    The recovery entry point captures what was preserved and provides
    instructions for resuming the meeting if desired.

    Decision matrix:

    ====================  ========================  =====================
    Snapshot state         recommended_action        resume_possible
    ====================  ========================  =====================
    rounds > 0             retry_from_snapshot       True
    rounds == 0, context   retry_from_snapshot       True
    rounds == 0, no ctx    manual_audit_only         False
    ====================  ========================  =====================

    Args:
        snapshot: The partial-progress snapshot at cancellation time.
        cancel_reason: Human-readable cancellation reason.
        cancelled_by: Identifier of who cancelled.
        last_good_state: The last known-good lifecycle state before
                         cancellation.  Defaults to ``snapshot.completed_step``
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
        # Progress was made — meeting outputs are preserved and resumable
        action = RecoveryEntryPoint.ACTION_RETRY
        resume = True
    else:
        # No progress — nothing to resume, but audit trail exists
        action = RecoveryEntryPoint.ACTION_AUDIT
        resume = False

    return RecoveryEntryPoint(
        meeting_id=snapshot.meeting_id,
        failure_category="user_cancelled",
        failure_reason=(
            f"Cancelled by {cancelled_by}: {cancel_reason}"
        ),
        resume_possible=resume,
        recommended_action=action,
        last_good_state=good_state,
        snapshot=snapshot,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2.  enter_cancelled_state  (orchestrator — transition + snapshot + emit)
# ══════════════════════════════════════════════════════════════════════════


def enter_cancelled_state(
    ctx: CancelledTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> CancelledTransitionResult:
    """Execute the complete cancelled-state transition for a meeting.

    This is the core state-machine function for Sub-AC 4.3.2.  It
    performs these steps in order:

    1. **Capture snapshot** — call ``capture_partial_progress()`` on
       the current manifest to preserve all accumulated work.
    2. **Validate transition** — check that ``manifest.state → cancelled``
       is a legal transition.
    3. **Log cancellation event** — append a structured entry to the
       manifest's ``error_log``.
    4. **Mutate state** — transition the manifest to ``state='cancelled'``.
    5. **Persist** — write the manifest to disk (unless ``persist=False``),
       preserving outputs per the Seed exit condition.
    6. **Build recovery entry point** — derive recovery instructions
       from the snapshot and cancellation context.

    All errors are logged to ``manifest.error_log`` — silent fail is
    impossible per the Seed constraint.

    Args:
        ctx: The ``CancelledTransitionContext`` with the manifest,
             cancellation reason, and identity of who cancelled.
        persist: If True (default), the manifest is written to disk
                 after the transition.  Set to False for dry-run or
                 testing scenarios where disk I/O is undesirable.
        persist_fn: Injectable persistence function for testing.
                    When None and ``persist=True``, uses the built-in
                    ``update_manifest()``.  For testing, pass a mock
                    that simply returns the manifest.

    Returns:
        A ``CancelledTransitionResult`` with success status, the (possibly
        updated) manifest, the progress snapshot, and recovery instructions.

    Raises:
        ValueError: If ``ctx.manifest`` is already in a terminal state.

    Example:
        >>> from src.meeting_trigger import MeetingManifest
        >>> from src.cancelled_transition import (
        ...     CancelledTransitionContext, enter_cancelled_state,
        ... )
        >>> manifest = MeetingManifest(
        ...     meeting_id="m-test", state="in_meeting",
        ...     round_count=2,
        ... )
        >>> ctx = CancelledTransitionContext(
        ...     manifest=manifest,
        ...     cancel_reason="User issued /cancel from Discord",
        ...     cancelled_by="u-discord-12345",
        ... )
        >>> result = enter_cancelled_state(ctx, persist=False)
        >>> result.success
        True
        >>> result.manifest.state
        'cancelled'
        >>> result.snapshot.round_count
        2
        >>> result.recovery_entry_point.recommended_action
        'retry_from_snapshot'
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
            f"'{manifest.state}' — cannot transition to cancelled"
        )
        logger.warning(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "already_terminal", reason, "warning")
        recovery_entry = build_cancellation_recovery(
            snapshot=snapshot,
            cancel_reason=ctx.cancel_reason,
            cancelled_by=ctx.cancelled_by,
            last_good_state=snapshot.completed_step,
        )
        return CancelledTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 2: Validate the transition ────────────────────────────────
    if not validate_transition(manifest.state, "cancelled"):
        reason = (
            f"Invalid transition: {manifest.state} → cancelled is not "
            f"permitted by the meeting lifecycle state machine"
        )
        logger.error(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "invalid_transition", reason, "error")
        recovery_entry = build_cancellation_recovery(
            snapshot=snapshot,
            cancel_reason=ctx.cancel_reason,
            cancelled_by=ctx.cancelled_by,
            last_good_state=snapshot.completed_step,
        )
        return CancelledTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 3: Log the cancellation event to error_log ────────────────
    cancel_entry: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": "cancelled_state_transition",
        "message": ctx.cancel_reason,
        "severity": ctx.severity,
        "cancelled_by": ctx.cancelled_by,
        "recovery": (
            f"Cancelled by {ctx.cancelled_by}. "
            f"Snapshot captured with {snapshot.round_count} round(s), "
            f"{snapshot.decisions_count} decision(s), "
            f"{snapshot.context_packets_count} context packet(s). "
            f"Outputs preserved on disk."
        ),
    }
    # Merge caller metadata into the error entry
    for key, value in ctx.metadata.items():
        if key not in cancel_entry:
            cancel_entry[f"meta_{key}"] = value

    # Include snapshot summary fields
    for key, value in snapshot.to_dict().items():
        sn_key = f"snapshot_{key}"
        if sn_key not in cancel_entry:
            cancel_entry[sn_key] = str(value)

    manifest = manifest.with_error(cancel_entry)
    logger.info(
        "Logged cancellation event for meeting_id=%s: cancelled_by=%s",
        manifest.meeting_id,
        ctx.cancelled_by,
    )

    # ── Step 4: Mutate state to cancelled ──────────────────────────────
    try:
        manifest = manifest.with_state(LifecycleState.CANCELLED)
    except Exception as exc:
        reason = f"State mutation to 'cancelled' raised: {exc}"
        logger.exception(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(
            manifest, "state_mutation_error", reason, "critical"
        )
        return CancelledTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            rejection_reasons=tuple(rejection_reasons),
            error=exc,
        )

    logger.info(
        "Meeting %s transitioned to state='cancelled'", manifest.meeting_id
    )

    # ── Step 5: Persist manifest before external calls ─────────────────
    # Seed constraint: "All state transitions persist to manifest.json
    # before external calls."
    # Seed exit condition: "user_cancelled: outputs preserved on disk."
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
            return CancelledTransitionResult(
                success=False,
                manifest=manifest,
                snapshot=snapshot,
                rejection_reasons=tuple(rejection_reasons),
                error=exc,
            )

    # ── Step 6: Build recovery entry point ─────────────────────────────
    recovery_entry = build_cancellation_recovery(
        snapshot=snapshot,
        cancel_reason=ctx.cancel_reason,
        cancelled_by=ctx.cancelled_by,
        last_good_state=snapshot.completed_step,
    )

    logger.info(
        "Recovery entry point for meeting_id=%s: action=%s resume_possible=%s",
        recovery_entry.meeting_id,
        recovery_entry.recommended_action,
        recovery_entry.resume_possible,
    )

    return CancelledTransitionResult(
        success=True,
        manifest=manifest,
        snapshot=snapshot,
        recovery_entry_point=recovery_entry,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3.  handle_cancelled_transition  (high-level orchestrator)
# ══════════════════════════════════════════════════════════════════════════


def handle_cancelled_transition(
    ctx: CancelledTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> CancelledTransitionResult:
    """High-level orchestrator for cancelled-state transition handling.

    Convenience wrapper around ``enter_cancelled_state()`` that adds:

    * Basic input validation (manifest is not None, cancel_reason is set).
    * Catching of unexpected exceptions so that even if the handler
      itself crashes, the manifest is annotated with the error.

    This is the recommended entry point for production code.  Use
    ``enter_cancelled_state()`` directly when you need finer control
    (e.g. testing individual steps).

    Args:
        ctx: The ``CancelledTransitionContext``.
        persist: Forwarded to ``enter_cancelled_state()``.
        persist_fn: Forwarded to ``enter_cancelled_state()``.

    Returns:
        A ``CancelledTransitionResult``.

    Raises:
        ValueError: If ``ctx.manifest`` is None.
    """
    if ctx.manifest is None:
        raise ValueError(
            "CancelledTransitionContext.manifest must not be None"
        )

    if not ctx.cancel_reason.strip():
        logger.warning(
            "handle_cancelled_transition called with empty cancel_reason "
            "for meeting_id=%s",
            ctx.manifest.meeting_id,
        )

    try:
        return enter_cancelled_state(
            ctx, persist=persist, persist_fn=persist_fn
        )
    except Exception as exc:
        logger.exception(
            "Unexpected exception in handle_cancelled_transition for "
            "meeting_id=%s: %s",
            ctx.manifest.meeting_id,
            exc,
        )
        # Try to log the error before giving up
        try:
            annotated = _append_error(
                ctx.manifest,
                "handler_exception",
                f"handle_cancelled_transition crashed: {exc}",
                "critical",
            )
        except Exception:
            annotated = ctx.manifest

        snapshot = capture_partial_progress(ctx.manifest)
        return CancelledTransitionResult(
            success=False,
            manifest=annotated,
            snapshot=snapshot,
            recovery_entry_point=None,
            rejection_reasons=(f"Handler crashed: {exc}",),
            error=exc,
        )


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════


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
