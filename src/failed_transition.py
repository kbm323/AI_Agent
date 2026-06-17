"""Failed state transition handler — Sub‑AC 4.3.1.

Enter / exit the ``failed`` lifecycle state, trigger a partial‑progress
snapshot capturing everything that was accomplished before the failure,
and emit a structured *recovery entry point* so that operators (or the
crash‑recovery subsystem) can understand what remains to be done.

The module is designed as a **standalone state‑machine function** — every
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
       build_recovery_entry_point()  ──►  RecoveryEntryPoint
              │
              ▼
       enter_failed_state()  ──►  FailedTransitionResult
              │
              ▼
       handle_failed_transition()  ──►  FailedTransitionResult  (orchestrator)

Every failure event is logged to the manifest's ``error_log``
(**silent fail forbidden** per the Seed constraint).

Usage::

    from src.failed_transition import (
        FailedTransitionContext,
        FailedTransitionResult,
        enter_failed_state,
        handle_failed_transition,
    )
    from src.shared.lifecycle import LifecycleState

    ctx = FailedTransitionContext(
        manifest=manifest,
        failure_reason="GLM-5.1 validation returned 'fail' verdict",
        failure_category="validation_failed_irrecoverable",
    )
    result = handle_failed_transition(ctx, persist=True)
    if result.success:
        print(f"Meeting {result.manifest.meeting_id} transitioned to failed")
        print(f"Recovery entry: {result.recovery_entry_point}")

Modules:
    FailedTransitionContext: Input — everything needed to execute the handler.
    PartialProgressSnapshot: Snapshot of completed work at failure time.
    RecoveryEntryPoint: Instructions for recovery / auditing.
    FailedTransitionResult: Immutable result of the failed‑state transition.
    capture_partial_progress: Build a snapshot from a manifest.
    build_recovery_entry_point: Derive recovery instructions from a snapshot.
    enter_failed_state: Execute the single‑step failed‑state transition.
    handle_failed_transition: High‑level orchestrator combining all steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from src.meeting_trigger import MeetingManifest, update_manifest
from src.shared.lifecycle import (
    LifecycleState,
    validate_transition,
    is_terminal,
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
# Input: FailedTransitionContext
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FailedTransitionContext:
    """Immutable context carrying everything needed to execute a
    failed‑state transition.

    This is the single input object for both ``enter_failed_state()``
    and ``handle_failed_transition()``.

    Attributes:
        manifest: The meeting manifest at the point of failure.
        failure_reason: Human‑readable explanation of why the meeting failed.
        failure_category: Machine‑readable exit condition from the Seed
                          ontology (e.g. ``validation_failed_irrecoverable``,
                          ``resource_exhausted``, ``coordinator_crash_unrecoverable``).
        severity: One of ``error``, ``critical`` (default: ``error``).
        metadata: Arbitrary key‑value pairs for logging / auditing
                  (e.g. ``{'validation_score': '0.42'}``).
    """

    manifest: MeetingManifest
    failure_reason: str
    failure_category: str = "failed"
    severity: str = "error"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.failure_reason.strip():
            raise ValueError("failure_reason must not be empty")
        if self.severity not in ("error", "critical"):
            logger.warning(
                "Unknown severity=%r for meeting_id=%s — using 'error'",
                self.severity,
                self.manifest.meeting_id,
            )
            object.__setattr__(self, "severity", "error")


# ══════════════════════════════════════════════════════════════════════════
# Partial-progress snapshot
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PartialProgressSnapshot:
    """Immutable snapshot of what was accomplished before the meeting failed.

    Captures every measurable piece of progress so that operators
    (or the crash‑recovery subsystem) can understand exactly what
    was done and what remains.

    Attributes:
        meeting_id: The meeting that produced this snapshot.
        state_at_failure: Lifecycle state at the moment of failure.
        round_count: Number of completed rounds.
        context_packets_count: Number of context packets accumulated.
        decisions_count: Number of decisions accumulated.
        tool_outputs_count: Number of tool outputs accumulated.
        validation_score: Validation score at failure time (0.0 if no
                          validation occurred).
        validation_verdict: Verdict at failure time (empty if none).
        consensus: Consensus text at failure time (empty if none).
        required_roles: Roles that were required for quorum.
        completed_step: Last completed lifecycle step.
        error_log_count: Number of errors already logged before the
                         failure entry.
        created_at: ISO 8601 creation timestamp of the original meeting.
        snapshot_at: ISO 8601 timestamp of this snapshot.
    """

    meeting_id: str
    state_at_failure: str
    round_count: int
    context_packets_count: int
    decisions_count: int
    tool_outputs_count: int
    validation_score: float
    validation_verdict: str
    consensus: str
    required_roles: tuple[str, ...]
    completed_step: str
    error_log_count: int
    created_at: str
    snapshot_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict:
        """Return a JSON‑compatible dictionary for logging / persistence."""
        return {
            "meeting_id": self.meeting_id,
            "state_at_failure": self.state_at_failure,
            "round_count": self.round_count,
            "context_packets_count": self.context_packets_count,
            "decisions_count": self.decisions_count,
            "tool_outputs_count": self.tool_outputs_count,
            "validation_score": self.validation_score,
            "validation_verdict": self.validation_verdict,
            "consensus": self.consensus[:200],
            "required_roles": list(self.required_roles),
            "completed_step": self.completed_step,
            "error_log_count": self.error_log_count,
            "created_at": self.created_at,
            "snapshot_at": self.snapshot_at,
        }

    @property
    def was_validating(self) -> bool:
        """True if the meeting failed during or after validation."""
        return self.validation_verdict not in ("",)

    @property
    def had_consensus(self) -> bool:
        """True if consensus was reached before failure."""
        return bool(self.consensus and self.consensus.strip())

    @property
    def had_context(self) -> bool:
        """True if context packets were accumulated."""
        return self.context_packets_count > 0


# ══════════════════════════════════════════════════════════════════════════
# Recovery entry point
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RecoveryEntryPoint:
    """Structured instructions for recovery after a meeting failure.

    Describes what was accomplished, why the meeting failed, and what
    a recovery attempt should do.  This is emitted as part of the
    ``FailedTransitionResult`` and persisted to the manifest's
    ``error_log``.

    Attributes:
        meeting_id: The failed meeting.
        failure_category: Machine‑readable category from the Seed ontology.
        failure_reason: Human‑readable explanation.
        resume_possible: True if the meeting data is intact enough that
                         a recovery attempt is feasible.
        recommended_action: Suggested next step (``retry_from_snapshot``,
                            ``escalate_to_human``, ``manual_audit_only``,
                            ``discard``).
        last_good_state: The lifecycle state at which the meeting was
                         last stable (typically ``state_at_failure``
                         minus one step).
        snapshot: The partial‑progress snapshot at failure time.
    """

    meeting_id: str
    failure_category: str
    failure_reason: str
    resume_possible: bool
    recommended_action: str
    last_good_state: str
    snapshot: PartialProgressSnapshot

    # ── Valid recommended actions ──────────────────────────────────────

    ACTION_RETRY = "retry_from_snapshot"
    ACTION_ESCALATE = "escalate_to_human"
    ACTION_AUDIT = "manual_audit_only"
    ACTION_DISCARD = "discard"

    VALID_ACTIONS = frozenset(
        {ACTION_RETRY, ACTION_ESCALATE, ACTION_AUDIT, ACTION_DISCARD}
    )

    def to_dict(self) -> dict:
        """Return a JSON‑compatible dictionary for logging / persistence."""
        return {
            "meeting_id": self.meeting_id,
            "failure_category": self.failure_category,
            "failure_reason": self.failure_reason,
            "resume_possible": self.resume_possible,
            "recommended_action": self.recommended_action,
            "last_good_state": self.last_good_state,
            "snapshot": self.snapshot.to_dict(),
        }

    @property
    def is_retryable(self) -> bool:
        """Convenience: True if a retry is recommended."""
        return self.recommended_action == self.ACTION_RETRY


# ══════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FailedTransitionResult:
    """Immutable result of a failed‑state transition attempt.

    Attributes:
        success: True if the manifest was successfully transitioned
                 to the ``failed`` state and persisted.
        manifest: The manifest after the transition (``state='failed'``
                  on success; original manifest on failure).
        snapshot: The partial‑progress snapshot captured before the
                  transition was executed.
        recovery_entry_point: Recovery instructions, or None if the
                              transition itself failed.
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
# 1.  capture_partial_progress  (pure function)
# ══════════════════════════════════════════════════════════════════════════


def capture_partial_progress(
    manifest: MeetingManifest,
) -> PartialProgressSnapshot:
    """Build a partial‑progress snapshot from the current manifest.

    This is a **pure function** — no I/O, no side effects.  It reads
    the manifest's current state fields and packages them into an
    immutable snapshot suitable for logging and recovery.

    Args:
        manifest: The meeting manifest at the point of failure.

    Returns:
        A ``PartialProgressSnapshot`` capturing all measurable progress.

    Example:
        >>> from src.meeting_trigger import MeetingManifest
        >>> m = MeetingManifest(meeting_id="m-1", state="validating",
        ...     round_count=2, validation_score=0.42,
        ...     validation_verdict="fail")
        >>> snap = capture_partial_progress(m)
        >>> snap.round_count
        2
        >>> snap.was_validating
        True
    """
    return PartialProgressSnapshot(
        meeting_id=manifest.meeting_id,
        state_at_failure=str(manifest.state),
        round_count=manifest.round_count,
        context_packets_count=len(manifest.context_packets),
        decisions_count=len(manifest.decisions),
        tool_outputs_count=len(manifest.tool_outputs),
        validation_score=manifest.validation_score,
        validation_verdict=manifest.validation_verdict,
        consensus=manifest.consensus,
        required_roles=manifest.required_roles,
        completed_step=manifest.completed_step,
        error_log_count=len(manifest.error_log),
        created_at=manifest.created_at,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2.  build_recovery_entry_point  (pure function)
# ══════════════════════════════════════════════════════════════════════════


def build_recovery_entry_point(
    snapshot: PartialProgressSnapshot,
    failure_category: str,
    failure_reason: str,
    *,
    last_good_state: str = "",
) -> RecoveryEntryPoint:
    """Derive a recovery entry point from the partial‑progress snapshot.

    This is a **pure function** — no I/O, no side effects.  It examines
    the snapshot and the failure metadata to recommend one of four actions:

    * ``retry_from_snapshot`` — snapshot is complete enough to resume.
    * ``escalate_to_human`` — failure category requires human judgment.
    * ``manual_audit_only`` — data preserved but not automatically resumable.
    * ``discard`` — nothing worth recovering.

    Decision matrix:

    =============================  ========================  ==================
    Failure category               Snapshot state            Recommended action
    =============================  ========================  ==================
    validation_failed_irrecoverable rounds > 0                retry_from_snapshot
    validation_failed_irrecoverable rounds == 0               manual_audit_only
    resource_exhausted             —                          retry_from_snapshot
    coordinator_crash_unrecoverable —                         escale_to_human
    user_cancelled                 —                          discard
    rate_limit_paused              —                          retry_from_snapshot
    deadlock_unresolvable          —                          escale_to_human
    (anything else)                rounds > 0                manual_audit_only
    (anything else)                rounds == 0               discard
    =============================  ========================  ==================

    Args:
        snapshot: The partial‑progress snapshot.
        failure_category: Machine‑readable category.
        failure_reason: Human‑readable description.
        last_good_state: The last known‑good lifecycle state before
                         failure.  Defaults to ``snapshot.completed_step``
                         when empty.

    Returns:
        A ``RecoveryEntryPoint`` with recommended recovery action.
    """
    category = failure_category
    good_state = last_good_state or snapshot.completed_step or snapshot.state_at_failure

    # ── Explicitly resumable categories ────────────────────────────────

    if category in (
        "validation_failed_irrecoverable",
        "resource_exhausted",
        "rate_limit_paused",
    ):
        if snapshot.round_count > 0 or snapshot.had_context:
            action = RecoveryEntryPoint.ACTION_RETRY
            resume = True
        else:
            action = RecoveryEntryPoint.ACTION_AUDIT
            resume = False

    # ── Categories that need human judgment ────────────────────────────

    elif category in (
        "coordinator_crash_unrecoverable",
        "deadlock_unresolvable",
    ):
        action = RecoveryEntryPoint.ACTION_ESCALATE
        resume = snapshot.round_count > 0

    # ── Explicitly terminal ────────────────────────────────────────────

    elif category == "user_cancelled":
        action = RecoveryEntryPoint.ACTION_DISCARD
        resume = False

    # ── Unknown / catch‑all ────────────────────────────────────────────

    else:
        if snapshot.round_count > 0:
            action = RecoveryEntryPoint.ACTION_AUDIT
            resume = True
        else:
            action = RecoveryEntryPoint.ACTION_DISCARD
            resume = False

    return RecoveryEntryPoint(
        meeting_id=snapshot.meeting_id,
        failure_category=category,
        failure_reason=failure_reason,
        resume_possible=resume,
        recommended_action=action,
        last_good_state=good_state,
        snapshot=snapshot,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3.  enter_failed_state  (orchestrator — transition + snapshot + emit)
# ══════════════════════════════════════════════════════════════════════════


def enter_failed_state(
    ctx: FailedTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> FailedTransitionResult:
    """Execute the complete failed‑state transition for a meeting.

    This is the core state‑machine function for Sub‑AC 4.3.1.  It
    performs these steps in order:

    1. **Capture snapshot** — call ``capture_partial_progress()`` on
       the current manifest.
    2. **Validate transition** — check that ``manifest.state → failed``
       is a legal transition.
    3. **Log failure event** — append a structured error entry to the
       manifest's ``error_log``.
    4. **Mutate state** — transition the manifest to ``state='failed'``.
    5. **Persist** — write the manifest to disk (unless ``persist=False``).
    6. **Build recovery entry point** — derive recovery instructions
       from the snapshot and failure context.

    All errors are logged to ``manifest.error_log`` — silent fail is
    impossible per the Seed constraint.

    Args:
        ctx: The ``FailedTransitionContext`` with the manifest, failure
             reason, and category.
        persist: If True (default), the manifest is written to disk
                 after the transition.  Set to False for dry‑run or
                 testing scenarios where disk I/O is undesirable.
        persist_fn: Injectable persistence function for testing.
                    When None and ``persist=True``, uses the built‑in
                    ``update_manifest()``.  For testing, pass a mock
                    that simply returns the manifest.

    Returns:
        A ``FailedTransitionResult`` with success status, the (possibly
        updated) manifest, the progress snapshot, and recovery instructions.

    Raises:
        ValueError: If ``ctx.manifest`` is already in a terminal state.

    Example:
        >>> from src.meeting_trigger import MeetingManifest
        >>> from src.failed_transition import (
        ...     FailedTransitionContext, enter_failed_state,
        ... )
        >>> manifest = MeetingManifest(
        ...     meeting_id="m-test", state="validating",
        ...     round_count=2, validation_score=0.42,
        ...     validation_verdict="fail",
        ... )
        >>> ctx = FailedTransitionContext(
        ...     manifest=manifest,
        ...     failure_reason="Validation returned 'fail'",
        ...     failure_category="validation_failed_irrecoverable",
        ... )
        >>> result = enter_failed_state(ctx, persist=False)
        >>> result.success
        True
        >>> result.manifest.state
        'failed'
        >>> result.snapshot.round_count
        2
        >>> result.recovery_entry_point.recommended_action
        'retry_from_snapshot'
    """
    manifest = ctx.manifest
    rejection_reasons: list[str] = []

    # ── Step 1: Capture partial‑progress snapshot ──────────────────────
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
            f"'{manifest.state}' — cannot transition to failed"
        )
        logger.warning(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "already_terminal", reason, "error")
        # Build recovery entry point even on rejection
        recovery_entry = build_recovery_entry_point(
            snapshot=snapshot,
            failure_category=ctx.failure_category,
            failure_reason=ctx.failure_reason,
            last_good_state=snapshot.completed_step,
        )
        return FailedTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 2: Validate the transition ────────────────────────────────
    if not validate_transition(manifest.state, "failed"):
        reason = (
            f"Invalid transition: {manifest.state} → failed is not "
            f"permitted by the meeting lifecycle state machine"
        )
        logger.error(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "invalid_transition", reason, "error")
        recovery_entry = build_recovery_entry_point(
            snapshot=snapshot,
            failure_category=ctx.failure_category,
            failure_reason=ctx.failure_reason,
            last_good_state=snapshot.completed_step,
        )
        return FailedTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            recovery_entry_point=recovery_entry,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 3: Log the failure event to error_log ─────────────────────
    failure_entry: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": "failed_state_transition",
        "message": ctx.failure_reason,
        "severity": ctx.severity,
        "failure_category": ctx.failure_category,
        "recovery": (
            f"Snapshot captured with {snapshot.round_count} round(s), "
            f"{snapshot.decisions_count} decision(s), "
            f"{snapshot.context_packets_count} context packet(s)"
        ),
    }
    # Merge caller metadata into the error entry
    for key, value in ctx.metadata.items():
        if key not in failure_entry:
            failure_entry[f"meta_{key}"] = value

    for key, value in snapshot.to_dict().items():
        failure_entry[f"snapshot_{key}"] = str(value) if not isinstance(value, (str, int, float, list)) else value  # type: ignore[assignment]

    manifest = manifest.with_error(failure_entry)
    logger.info(
        "Logged failure event for meeting_id=%s: category=%s",
        manifest.meeting_id,
        ctx.failure_category,
    )

    # ── Step 4: Mutate state to failed ─────────────────────────────────
    try:
        manifest = manifest.with_state(LifecycleState.FAILED)
    except Exception as exc:
        reason = f"State mutation to 'failed' raised: {exc}"
        logger.exception(reason)
        rejection_reasons.append(reason)
        manifest = _append_error(manifest, "state_mutation_error", reason, "critical")
        return FailedTransitionResult(
            success=False,
            manifest=manifest,
            snapshot=snapshot,
            rejection_reasons=tuple(rejection_reasons),
            error=exc,
        )

    logger.info(
        "Meeting %s transitioned to state='failed'", manifest.meeting_id
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
            manifest = _append_error(manifest, "persistence_error", reason, "critical")
            # Transition still counts as "success" because state was
            # mutated — but the caller should be aware persistence failed.
            # We return success=False to signal a partial failure.
            rejection_reasons.append(reason)
            return FailedTransitionResult(
                success=False,
                manifest=manifest,
                snapshot=snapshot,
                rejection_reasons=tuple(rejection_reasons),
                error=exc,
            )

    # ── Step 6: Build recovery entry point ─────────────────────────────
    recovery_entry = build_recovery_entry_point(
        snapshot=snapshot,
        failure_category=ctx.failure_category,
        failure_reason=ctx.failure_reason,
        last_good_state=snapshot.completed_step,
    )

    logger.info(
        "Recovery entry point for meeting_id=%s: action=%s resume_possible=%s",
        recovery_entry.meeting_id,
        recovery_entry.recommended_action,
        recovery_entry.resume_possible,
    )

    return FailedTransitionResult(
        success=True,
        manifest=manifest,
        snapshot=snapshot,
        recovery_entry_point=recovery_entry,
    )


# ══════════════════════════════════════════════════════════════════════════
# 4.  handle_failed_transition  (high‑level orchestrator)
# ══════════════════════════════════════════════════════════════════════════


def handle_failed_transition(
    ctx: FailedTransitionContext,
    *,
    persist: bool = True,
    persist_fn: Optional[PersistFn] = None,
) -> FailedTransitionResult:
    """High‑level orchestrator for failed‑state transition handling.

    Convenience wrapper around ``enter_failed_state()`` that adds:

    * Basic input validation (manifest is not None, failure_reason is set).
    * Catching of unexpected exceptions so that even if the handler
      itself crashes, the manifest is annotated with the error.

    This is the recommended entry point for production code.  Use
    ``enter_failed_state()`` directly when you need finer control
    (e.g. testing individual steps).

    Args:
        ctx: The ``FailedTransitionContext``.
        persist: Forwarded to ``enter_failed_state()``.
        persist_fn: Forwarded to ``enter_failed_state()``.

    Returns:
        A ``FailedTransitionResult``.

    Raises:
        ValueError: If ``ctx.manifest`` is None.
    """
    if ctx.manifest is None:
        raise ValueError("FailedTransitionContext.manifest must not be None")

    if not ctx.failure_reason.strip():
        logger.warning(
            "handle_failed_transition called with empty failure_reason "
            "for meeting_id=%s",
            ctx.manifest.meeting_id,
        )

    try:
        return enter_failed_state(ctx, persist=persist, persist_fn=persist_fn)
    except Exception as exc:
        logger.exception(
            "Unexpected exception in handle_failed_transition for "
            "meeting_id=%s: %s",
            ctx.manifest.meeting_id,
            exc,
        )
        # Try to log the error before giving up
        try:
            annotated = _append_error(
                ctx.manifest,
                "handler_exception",
                f"handle_failed_transition crashed: {exc}",
                "critical",
            )
        except Exception:
            annotated = ctx.manifest

        snapshot = capture_partial_progress(ctx.manifest)
        return FailedTransitionResult(
            success=False,
            manifest=annotated,
            snapshot=snapshot,
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
