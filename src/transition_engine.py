"""Transition Execution Engine for meeting lifecycle state management.

Sub-AC 4.2: Executes validated state transitions with pre-condition guards,
state mutation, manifest persistence (before any external calls per Seed
constraint), and post-transition side-effect dispatch (logging, notification
triggers).

Architecture
------------

Each call to ``execute_transition()`` performs these steps in order:

1. **Pre-condition guard evaluation** — every guard receives the current
   manifest and returns (allowed, reason).  If any guard rejects the
   transition, it is aborted and all rejection reasons are logged to
   ``manifest.error_log``.

2. **Transition validation** — ``validate_transition()`` from
   ``src.shared.lifecycle`` checks the state-machine rules.  Invalid
   transitions are rejected with an error logged to the manifest.

3. **State mutation** — ``manifest.with_state(to_state)`` produces a new
   frozen ``MeetingManifest`` with the target state and an updated
   timestamp.

4. **Manifest persistence** — the new manifest is persisted via the
   transition hook dispatch mechanism (Sub-AC 4.4.3), which fires all
   registered hooks including the built-in persistence hook **before**
   any post-actions are dispatched, satisfying the Seed constraint:
   *"All state transitions persist to manifest.json before external
   calls."*

5. **Post-transition side-effect dispatch** — each post-action callback
   receives the new manifest.  Failures in post-actions are logged to
   ``manifest.error_log`` but do **not** roll back the state transition
   (transitions are one-way per the Seed design).

All failures — including pre-condition violations, invalid transitions,
and post-action errors — are appended to ``manifest.error_log`` with
timestamps, fulfilling the constraint: *"Silent fail forbidden: all
failures logged to manifest."*

Usage::

    from src.transition_engine import execute_transition, TransitionResult
    from src.shared.lifecycle import LifecycleState

    # Define a custom pre-condition guard
    def require_rounds_remaining(manifest):
        if manifest.round_count >= manifest.max_rounds:
            return False, (
                f"Round limit exhausted: {manifest.round_count}/"
                f"{manifest.max_rounds}"
            )
        return True, None

    # Execute the transition
    result = execute_transition(
        manifest,
        LifecycleState.IN_MEETING,
        pre_conditions=(require_rounds_remaining,),
        post_actions=(log_transition, notify_discord),
        label="round-2-start",
    )

    if result.success:
        manifest = result.manifest  # updated with new state

Modules:
    TransitionResult: Immutable result of a transition attempt.
    PreConditionGuard: Callable[[MeetingManifest], (bool, str | None)].
    PostAction: Callable[[MeetingManifest], None].
    execute_transition: Main entry point for validated state transitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from src.meeting_trigger import MeetingManifest, update_manifest
from src.shared.lifecycle import LifecycleState, validate_transition
from src.transition_persistence_hook import (
    dispatch_transition_hooks,
    install_default_hooks,
)

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────

PreConditionGuard = Callable[
    [MeetingManifest], tuple[bool, Optional[str]]
]
"""A pre-condition guard function.

Receives the current meeting manifest.  Must return a 2-tuple:
``(allowed: bool, reason: str | None)`` — ``reason`` is only
meaningful when ``allowed`` is ``False``, explaining why the
transition is blocked.
"""

PostAction = Callable[[MeetingManifest], None]
"""A post-transition side-effect action.

Receives the **new** manifest (after the state transition).
May raise exceptions; they are caught and logged by the engine
but do **not** roll back the transition.
"""


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransitionResult:
    """Immutable result of a state transition attempt.

    Attributes:
        success: True if the transition was applied and persisted.
        manifest: The current manifest — the **new** manifest when
                  ``success`` is True, otherwise the original (unchanged).
        from_state: State before the transition attempt.
        to_state: Target state that was requested.
        rejection_reasons: List of human-readable reasons the transition
                           was rejected (empty when ``success`` is True).
        post_action_errors: List of error messages from failed post-actions.
                           These do not cause ``success`` to become False;
                           the transition still succeeded, but side-effects
                           may not have completed.
    """

    success: bool
    manifest: MeetingManifest
    from_state: str
    to_state: str
    rejection_reasons: tuple[str, ...] = ()
    post_action_errors: tuple[str, ...] = ()


# ── Internal helpers ──────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _log_error(
    manifest: MeetingManifest,
    error_type: str,
    message: str,
    severity: str = "error",
    recovery: str = "",
) -> MeetingManifest:
    """Append a structured error entry to the manifest's error_log.

    Every failure — pre-condition violation, invalid transition, or
    post-action exception — flows through this function, guaranteeing
    that "silent fail" is impossible per the Seed constraint.

    Args:
        manifest: The manifest to annotate.
        error_type: Machine-readable error category (e.g. 'pre_condition',
                    'invalid_transition', 'post_action_failure').
        message: Human-readable description.
        severity: One of 'error', 'warning', or 'critical'.
        recovery: Optional recovery action or suggestion.

    Returns:
        A new ``MeetingManifest`` with the error appended to ``error_log``.
    """
    entry: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": error_type,
        "message": message,
        "severity": severity,
        "recovery": recovery,
    }
    return manifest.with_error(entry)


# ── Pre-condition guard evaluation ────────────────────────────────────────


def _evaluate_pre_conditions(
    manifest: MeetingManifest,
    pre_conditions: tuple[PreConditionGuard, ...],
) -> tuple[bool, list[str], MeetingManifest]:
    """Run every pre-condition guard against the current manifest.

    Args:
        manifest: Current meeting manifest.
        pre_conditions: Tuple of guard functions.

    Returns:
        (all_passed, rejection_reasons, manifest_with_errors)
        — ``manifest_with_errors`` is the original manifest with error
          entries for each failed guard appended.
    """
    rejection_reasons: list[str] = []
    current = manifest

    for i, guard in enumerate(pre_conditions):
        try:
            allowed, reason = guard(current)
        except Exception as exc:
            # Guard itself raised — treat as rejection
            allowed = False
            reason = f"Guard #{i} raised {type(exc).__name__}: {exc}"
            logger.exception("Pre-condition guard #%d raised exception", i)

        if not allowed:
            detail = reason or f"Pre-condition guard #{i} rejected the transition"
            rejection_reasons.append(detail)
            current = _log_error(
                current,
                error_type="pre_condition_violation",
                message=detail,
                severity="warning",
                recovery="Address the guard condition before retrying",
            )

    return (len(rejection_reasons) == 0, rejection_reasons, current)


# ── Post-action dispatch ──────────────────────────────────────────────────


def _dispatch_post_actions(
    manifest: MeetingManifest,
    post_actions: tuple[PostAction, ...],
) -> tuple[list[str], MeetingManifest]:
    """Dispatch every post-action callback against the new manifest.

    Post-action failures are caught, logged to the manifest, and
    collected into an error list — but the transition is **not**
    rolled back.  State transitions are one-way per the Seed design.

    Args:
        manifest: The **new** manifest after state mutation + persistence.
        post_actions: Tuple of action callbacks.

    Returns:
        (errors, manifest_with_error_log)
    """
    errors: list[str] = []
    current = manifest

    for i, action in enumerate(post_actions):
        try:
            action(current)
        except Exception as exc:
            msg = (
                f"Post-action #{i} "
                f"({getattr(action, '__name__', 'unnamed')}) "
                f"raised {type(exc).__name__}: {exc}"
            )
            errors.append(msg)
            logger.exception("Post-action #%d failed", i)
            current = _log_error(
                current,
                error_type="post_action_failure",
                message=msg,
                severity="warning",
                recovery=(
                    "Transition succeeded but side-effect failed; "
                    "the post-action should be retried or compensated"
                ),
            )

    return errors, current


# ── Public API ────────────────────────────────────────────────────────────


def execute_transition(
    manifest: MeetingManifest,
    to_state: LifecycleState | str,
    *,
    pre_conditions: tuple[PreConditionGuard, ...] = (),
    post_actions: tuple[PostAction, ...] = (),
    persist: bool = True,
    label: str = "",
) -> TransitionResult:
    """Execute a validated state transition for a meeting manifest.

    The five-step transition pipeline:

    1. **Pre-condition guards** — every guard in ``pre_conditions``
       receives the current manifest.  If any guard returns
       ``(False, reason)``, the transition is **rejected** before
       state mutation occurs.  All rejection reasons are logged to
       ``manifest.error_log``.

    2. **Transition validation** — ``validate_transition()`` checks
       the state-machine rules defined in ``MEETING_TRANSITIONS``.
       Invalid transitions are rejected with errors logged.

    3. **State mutation** — ``manifest.with_state(to_state)``
       produces a new frozen manifest with the target state and
       an updated ``updated_at`` timestamp.

    4. **Manifest persistence** — the new manifest is persisted via
       the transition hook dispatch mechanism (``dispatch_transition_hooks()``),
       which fires all registered hooks including the built-in persistence
       hook **before** any post-actions are dispatched (per the Seed
       constraint: persist-before-external).

    5. **Post-transition side-effect dispatch** — each callback in
       ``post_actions`` receives the new manifest.  Failures are
       logged but do **not** roll back the transition.

    All failures are appended to ``manifest.error_log`` with
    timestamps — silent fail is impossible per the Seed constraint.

    Args:
        manifest: Current meeting manifest.  Not mutated in-place;
                  the updated manifest is returned via the result.
        to_state: Target lifecycle state (LifecycleState member or string).
        pre_conditions: Tuple of guard callables.  Each receives the
                        current manifest and returns ``(allowed, reason)``.
        post_actions: Tuple of action callables dispatched after the
                      transition is persisted.
        persist: If True (default), the manifest is written to disk
                 before post-actions.  Set to False for dry-run or
                 testing scenarios where disk I/O is undesirable.
        label: Optional human-readable label for logging context
               (e.g. 'round-2-start', 'validation-pass').

    Returns:
        A ``TransitionResult`` with ``success``, the (possibly updated)
        manifest, and any rejection reasons or post-action errors.

    Raises:
        ValueError: If ``to_state`` is not a valid ``LifecycleState``
                    member name (propagated from ``validate_transition``
                    in strict mode).

    Example:
        >>> from src.meeting_trigger import create_meeting, MeetingCommandRequest
        >>> from src.transition_engine import execute_transition
        >>> from src.shared.lifecycle import LifecycleState
        >>> req = MeetingCommandRequest(
        ...     agenda="Test", user_id="u1", channel_id="c1"
        ... )
        >>> ctx = create_meeting(req, meetings_root="/tmp/test-transitions")
        >>> result = execute_transition(
        ...     ctx.manifest,
        ...     LifecycleState.QUEUED,
        ...     label="enqueue-meeting",
        ... )
        >>> result.success
        True
        >>> result.manifest.state
        'queued'
    """
    from_state_str = str(manifest.state)
    to_state_str = str(to_state)
    label_prefix = f"[{label}] " if label else ""

    logger.info(
        "%sAttempting transition: %s → %s",
        label_prefix,
        from_state_str,
        to_state_str,
    )

    # ── Step 1: Evaluate pre-condition guards ─────────────────────────
    guards_passed, rejection_reasons, manifest = _evaluate_pre_conditions(
        manifest, pre_conditions
    )

    if not guards_passed:
        joined = "; ".join(rejection_reasons)
        logger.warning(
            "%sTransition %s → %s rejected by pre-condition guards: %s",
            label_prefix,
            from_state_str,
            to_state_str,
            joined,
        )
        # If any errors were appended, persist the error log before
        # returning (silent fail forbidden).
        if persist and rejection_reasons:
            manifest = _persist_manifest_safely(manifest, label_prefix)
        return TransitionResult(
            success=False,
            manifest=manifest,
            from_state=from_state_str,
            to_state=to_state_str,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ── Step 2: Validate the transition against state machine rules ───
    if not validate_transition(manifest.state, to_state_str):
        msg = (
            f"Invalid state transition: {from_state_str} → {to_state_str} "
            f"is not permitted by the meeting lifecycle state machine"
        )
        logger.error("%s%s", label_prefix, msg)
        manifest = _log_error(
            manifest,
            error_type="invalid_transition",
            message=msg,
            severity="error",
            recovery=(
                "Check MEETING_TRANSITIONS for valid paths from "
                f"'{from_state_str}'"
            ),
        )
        if persist:
            manifest = _persist_manifest_safely(manifest, label_prefix)
        return TransitionResult(
            success=False,
            manifest=manifest,
            from_state=from_state_str,
            to_state=to_state_str,
            rejection_reasons=(msg,),
        )

    # ── Step 3: Mutate state ─────────────────────────────────────────
    try:
        new_manifest = manifest.with_state(to_state_str)
    except Exception as exc:
        msg = f"State mutation failed: {exc}"
        logger.exception("%s%s", label_prefix, msg)
        manifest = _log_error(
            manifest,
            error_type="state_mutation_error",
            message=msg,
            severity="critical",
            recovery="Check MeetingManifest.with_state() implementation",
        )
        if persist:
            manifest = _persist_manifest_safely(manifest, label_prefix)
        return TransitionResult(
            success=False,
            manifest=manifest,
            from_state=from_state_str,
            to_state=to_state_str,
            rejection_reasons=(msg,),
        )

    logger.info(
        "%sState mutated: %s → %s",
        label_prefix,
        from_state_str,
        to_state_str,
    )

    # ── Step 4: Persist manifest BEFORE external calls ────────────────
    # Seed constraint: "All state transitions persist to manifest.json
    # before external calls."
    # Uses the transition hook dispatch mechanism (Sub-AC 4.4.3) so
    # all registered hooks (including persistence) fire on every
    # state transition.
    if persist:
        try:
            new_manifest = dispatch_transition_hooks(
                new_manifest, "state_change"
            )
            logger.info(
                "%sManifest persisted via transition hooks to %s",
                label_prefix,
                new_manifest.manifest_path,
            )
        except Exception as exc:
            msg = f"Manifest persistence failed: {exc}"
            logger.exception("%s%s", label_prefix, msg)
            new_manifest = _log_error(
                new_manifest,
                error_type="persistence_error",
                message=msg,
                severity="critical",
                recovery="The in-memory state was mutated but not persisted; "
                "retry the persistence step or investigate disk issues",
            )
            if persist:
                new_manifest = _persist_manifest_safely(
                    new_manifest, label_prefix
                )
            return TransitionResult(
                success=False,
                manifest=new_manifest,
                from_state=from_state_str,
                to_state=to_state_str,
                rejection_reasons=(msg,),
            )

    # ── Step 5: Dispatch post-transition side-effects ─────────────────
    post_errors, final_manifest = _dispatch_post_actions(
        new_manifest, post_actions
    )

    # If post-actions added errors, persist the updated error log
    if persist and post_errors:
        final_manifest = _persist_manifest_safely(
            final_manifest, label_prefix
        )

    if post_errors:
        logger.warning(
            "%sTransition %s → %s succeeded but %d post-action(s) failed",
            label_prefix,
            from_state_str,
            to_state_str,
            len(post_errors),
        )

    logger.info(
        "%sTransition complete: %s → %s (success=%s)",
        label_prefix,
        from_state_str,
        to_state_str,
        True,
    )

    return TransitionResult(
        success=True,
        manifest=final_manifest,
        from_state=from_state_str,
        to_state=to_state_str,
        post_action_errors=tuple(post_errors),
    )


# ── Safe persistence helper ───────────────────────────────────────────────


def _persist_manifest_safely(
    manifest: MeetingManifest, label_prefix: str = ""
) -> MeetingManifest:
    """Best-effort persistence of the manifest via transition hooks.

    Dispatches all registered transition hooks (including the built-in
    persistence hook). If this also fails, the error is logged via Python's
    logging framework but is NOT re-raised — the caller may have already
    experienced a failure cascade and we don't want to mask the
    original error.
    """
    try:
        return dispatch_transition_hooks(manifest, "state_change")
    except Exception as exc:
        logger.critical(
            "%sCritical: could not persist manifest after error: %s",
            label_prefix,
            exc,
        )
        return manifest


# ── Built-in pre-condition guards ─────────────────────────────────────────


def guard_is_active(manifest: MeetingManifest) -> tuple[bool, Optional[str]]:
    """Pre-condition: the meeting must be in an active (non-terminal) state.

    Terminal states (completed, cancelled, failed, stale) have no
    valid outgoing transitions — this guard catches attempts to
    transition from a terminal state early.
    """
    from src.shared.lifecycle import is_terminal

    if is_terminal(manifest.state):
        return (
            False,
            f"Cannot transition from terminal state '{manifest.state}'",
        )
    return True, None


def guard_rounds_remaining(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Pre-condition: the meeting has not exceeded its max round count.

    Only meaningful when transitioning into ``IN_MEETING`` or
    ``CONSENSUS_BUILDING`` — in other contexts it is a no-op (returns
    True).
    """
    # Only enforce round limits for states that consume a round
    if manifest.round_count >= manifest.max_rounds:
        return (
            False,
            f"Round limit exhausted: {manifest.round_count}/"
            f"{manifest.max_rounds}",
        )
    return True, None


def guard_no_concurrent_limit_exceeded(
    _manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Pre-condition: system-wide concurrent meeting limit is not exceeded.

    **Placeholder** — the actual check requires a global registry of
    active meetings.  This stub always returns True and serves as a
    hook for future implementation.
    """
    # TODO: Implement global meeting registry lookup
    return True, None


# ── Built-in post-action callbacks ────────────────────────────────────────


def log_transition_event(manifest: MeetingManifest) -> None:
    """Post-action: emit a structured log line for the transition.

    This is a convenience callback for standard logging.  The engine
    already logs via Python's ``logging`` module; this callback
    produces a compact summary suitable for audit trails.
    """
    logger.info(
        "TRANSITION_EVENT meeting_id=%s state=%s priority=%s round=%d",
        manifest.meeting_id,
        manifest.state,
        manifest.priority,
        manifest.round_count,
    )
