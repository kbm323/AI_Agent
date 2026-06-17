"""Normal Lifecycle Progression Orchestrator (Sub-AC 4.2).

Implements sequential transitions through the meeting lifecycle happy path
from created to completed, with guard conditions at every step.

Each transition step:
1. Evaluates step-specific guard conditions against the current manifest
2. Validates the transition against the state machine rules
3. Mutates state via execute_transition()
4. Persists manifest before any external calls

The orchestrator is a single independently-testable function that accepts
a MeetingManifest and returns a structured LifecycleProgressionResult.

Usage::

    from src.shared.lifecycle_progression import progress_lifecycle, LifecycleProgressionResult

    result = progress_lifecycle(manifest, persist=False)
    if result.success:
        print(f"Meeting {result.manifest.meeting_id} reached completed")
    else:
        print(f"Progression stopped at {result.last_state}: {result.failure_reason}")

Modules:
    LifecycleProgressionResult: Immutable result of a progression attempt.
    NormalLifecycleStep: Metadata about each step in the happy path.
    guard_meeting_has_id: Guard: meeting_id must be non-empty.
    guard_agenda_non_empty: Guard: agenda must be non-empty.
    guard_roles_assigned: Guard: required_roles must be populated (routing phase done).
    guard_agenda_type_set: Guard: agenda_type must be populated.
    guard_context_populated: Guard: at least one context packet exists.
    guard_rounds_completed: Guard: round_count > 0 (at least one round done).
    guard_consensus_non_empty: Guard: consensus text is set.
    guard_validation_passed: Guard: validation_score >= 0.85 and verdict is pass/conditional_pass.
    is_happy_path_state: Check if a state is in the normal forward flow.
    HAPPY_PATH_STATES: Tuple of states in the normal forward flow order.
    progress_lifecycle: Main entry point for normal lifecycle progression.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from src.shared.lifecycle import LifecycleState, validate_transition

if TYPE_CHECKING:
    from src.meeting_trigger import MeetingManifest
    from src.transition_engine import PreConditionGuard, TransitionResult

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────

# Re-exported for callers (must match transition_engine.PreConditionGuard)
PreConditionGuard = Callable[
    [object], tuple[bool, Optional[str]]
]
"""A pre-condition guard function.
Receives a MeetingManifest and returns (allowed, reason)."""


def _get_transition_engine():
    """Lazy import to avoid circular dependency (meeting_trigger → shared)."""
    from src.transition_engine import execute_transition  # noqa: F811
    return execute_transition

# ── Happy path sequence ───────────────────────────────────────────────────

HAPPY_PATH_STATES: tuple[LifecycleState, ...] = (
    LifecycleState.CREATED,
    LifecycleState.QUEUED,
    LifecycleState.ROUTING,
    LifecycleState.CONTEXT_RETRIEVAL,
    LifecycleState.IN_MEETING,
    LifecycleState.CONSENSUS_BUILDING,
    LifecycleState.VALIDATING,
    LifecycleState.FINALIZING,
    LifecycleState.COMPLETED,
)
"""The canonical normal forward flow — the nine states from created to completed."""


def is_happy_path_state(state: LifecycleState | str) -> bool:
    """Return True if *state* is part of the normal forward flow."""
    if isinstance(state, str):
        try:
            state = LifecycleState(state)
        except ValueError:
            return False
    return state in HAPPY_PATH_STATES


# ── Step metadata ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalLifecycleStep:
    """Metadata about a single step in the normal lifecycle progression.

    Attributes:
        index: 0-based position in HAPPY_PATH_STATES.
        from_state: Source lifecycle state.
        to_state: Target lifecycle state.
        label: Human-readable label for logging.
        guards: Tuple of pre-condition guard functions for this step.
    """

    index: int
    from_state: LifecycleState
    to_state: LifecycleState
    label: str
    guards: tuple[PreConditionGuard, ...] = ()


def _build_step(
    index: int,
    from_state: LifecycleState,
    to_state: LifecycleState,
    label: str,
    guards: tuple[PreConditionGuard, ...] = (),
) -> NormalLifecycleStep:
    """Construct a NormalLifecycleStep."""
    return NormalLifecycleStep(
        index=index,
        from_state=from_state,
        to_state=to_state,
        label=label,
        guards=guards,
    )


# ── Step-specific guard conditions ────────────────────────────────────────


def guard_meeting_has_id(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: meeting_id must be non-empty.

    Used at: created → queued
    A meeting cannot enter the queue without a valid identifier.
    """
    if not manifest.meeting_id or not manifest.meeting_id.strip():
        return False, "Cannot transition: meeting_id is empty"
    return True, None


def guard_agenda_non_empty(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: agenda must be non-empty.

    Used at: created → queued
    A meeting with no agenda should not be processed.
    """
    if not manifest.agenda or not manifest.agenda.strip():
        return False, "Cannot transition: agenda is empty"
    return True, None


def guard_manifest_path_valid(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: manifest_path must be non-empty.

    Used at: created → queued
    Persistence requires a known target path.
    """
    if not manifest.manifest_path:
        return False, "Cannot transition: manifest_path is empty"
    return True, None


def guard_roles_assigned(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: required_roles must be populated (routing completed).

    Used at: routing → context_retrieval
    The Qwen router must have assigned at least one required role before
    context retrieval can begin.
    """
    if not manifest.required_roles:
        return (
            False,
            "Cannot transition: required_roles is empty — "
            "Qwen routing must assign roles first",
        )
    return True, None


def guard_agenda_type_set(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: agenda_type must be populated (routing completed).

    Used at: routing → context_retrieval
    The meeting type must be classified before knowledge retrieval.
    """
    if not manifest.agenda_type:
        return (
            False,
            "Cannot transition: agenda_type is empty — "
            "Qwen router must classify the agenda first",
        )
    return True, None


def guard_context_populated(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: at least one context packet must exist.

    Used at: context_retrieval → in_meeting
    Context packets are the knowledge foundation for the meeting discussion.
    """
    if not manifest.context_packets:
        return (
            False,
            "Cannot transition: context_packets is empty — "
            "context retrieval must produce at least one packet",
        )
    return True, None


def guard_rounds_completed(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: round_count must be > 0 (at least one meeting round completed).

    Used at: in_meeting → consensus_building
    Consensus building requires that at least one round of discussion
    has occurred and opinions have been collected.
    """
    if manifest.round_count < 1:
        return (
            False,
            f"Cannot transition: round_count is {manifest.round_count}, "
            f"need at least 1 completed round to build consensus",
        )
    return True, None


def guard_consensus_non_empty(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: consensus text must be non-empty.

    Used at: consensus_building → validating
    Validation cannot proceed without a draft consensus to evaluate.
    """
    if not manifest.consensus or not manifest.consensus.strip():
        return (
            False,
            "Cannot transition: consensus is empty — "
            "a consensus draft must be produced before validation",
        )
    return True, None


def guard_validation_passed(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: validation must have passed with score >= 0.85.

    Used at: validating → finalizing
    Only pass or conditional_pass verdicts with sufficient score
    can proceed to finalization.
    """
    valid_verdicts = frozenset({"pass", "conditional_pass"})
    if manifest.validation_verdict not in valid_verdicts:
        return (
            False,
            f"Cannot transition: validation_verdict is "
            f"'{manifest.validation_verdict}', "
            f"must be 'pass' or 'conditional_pass'",
        )
    if manifest.validation_score < 0.85:
        return (
            False,
            f"Cannot transition: validation_score is "
            f"{manifest.validation_score}, "
            f"must be >= 0.85 to finalize",
        )
    return True, None


def guard_not_already_terminal(
    manifest: MeetingManifest,
) -> tuple[bool, Optional[str]]:
    """Guard: meeting must not already be in a terminal state.

    Terminal states (completed, cancelled, failed, stale) have no
    valid outgoing transitions, so any attempt to progress from them
    should be caught immediately.
    """
    from src.shared.lifecycle import is_terminal

    if is_terminal(manifest.state):
        return (
            False,
            f"Meeting is already in terminal state '{manifest.state}' — "
            f"no further progression is possible",
        )
    return True, None


# ── Step definitions ──────────────────────────────────────────────────────

#: The ordered sequence of normal-lifecycle steps, each with its
#: specific guard conditions.  Index 0 = created→queued, index 7 = finalizing→completed.
NORMAL_LIFECYCLE_STEPS: tuple[NormalLifecycleStep, ...] = (
    _build_step(
        0,
        LifecycleState.CREATED,
        LifecycleState.QUEUED,
        "enqueue-meeting",
        guards=(
            guard_not_already_terminal,
            guard_meeting_has_id,
            guard_agenda_non_empty,
            guard_manifest_path_valid,
        ),
    ),
    _build_step(
        1,
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        "route-meeting",
    ),
    _build_step(
        2,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        "retrieve-context",
        guards=(
            guard_roles_assigned,
            guard_agenda_type_set,
        ),
    ),
    _build_step(
        3,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        "start-meeting",
        guards=(guard_context_populated,),
    ),
    _build_step(
        4,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        "build-consensus",
        guards=(guard_rounds_completed,),
    ),
    _build_step(
        5,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        "validate-consensus",
        guards=(guard_consensus_non_empty,),
    ),
    _build_step(
        6,
        LifecycleState.VALIDATING,
        LifecycleState.FINALIZING,
        "finalize-meeting",
        guards=(guard_validation_passed,),
    ),
    _build_step(
        7,
        LifecycleState.FINALIZING,
        LifecycleState.COMPLETED,
        "complete-meeting",
    ),
)


# ── Result dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class LifecycleProgressionResult:
    """Immutable result of a normal lifecycle progression attempt.

    Attributes:
        success: True if the manifest reached the ``completed`` state.
        manifest: The manifest at the end of progression — in the
                  ``completed`` state when ``success`` is True, or in
                  the last successfully-reached state when ``success``
                  is False.
        starting_state: State before progression began.
        ending_state: State after progression (completed on success,
                      the stuck state on failure).
        steps_completed: Number of transitions that succeeded.
        steps_total: Total number of transitions in the happy path
                     from the starting state (9 total from created).
        failure_step: The step that failed (None on success).
        failure_reason: Human-readable reason for failure (None on success).
    """

    success: bool
    manifest: MeetingManifest
    starting_state: str
    ending_state: str
    steps_completed: int
    steps_total: int
    failure_step: Optional[NormalLifecycleStep] = None
    failure_reason: Optional[str] = None


# ── Public API ────────────────────────────────────────────────────────────


def progress_lifecycle(
    manifest: MeetingManifest,
    *,
    persist: bool = True,
    extra_guards: Optional[dict[str, tuple[PreConditionGuard, ...]]] = None,
) -> LifecycleProgressionResult:
    """Progress a meeting manifest through the normal lifecycle happy path.

    Starting from the manifest's current state, this function walks
    forward through the happy-path states (created → queued → routing →
    context_retrieval → in_meeting → consensus_building → validating →
    finalizing → completed), executing each transition with its
    step-specific guard conditions.

    Progression stops at the first failing guard — the returned result
    captures which step failed and why.  The caller can fix the condition
    and call again to continue from the last good state.

    If the manifest is already in the ``completed`` state, the function
    returns success immediately with zero steps taken.

    If the manifest is in a state **not** on the happy path (e.g.
    ``paused``, ``deadlocked``, ``escalated``), progression is not
    attempted — the orchestrator only handles the normal forward flow.
    Callers should resolve exception states before calling this function.

    Args:
        manifest: Current meeting manifest.  Not mutated in-place.
        persist: If True (default), the manifest is written to disk
                 after each successful transition.  Set to False for
                 dry-run or testing scenarios.
        extra_guards: Optional dict mapping step labels to additional
                      pre-condition guard tuples.  These are appended
                      to the built-in guards for each step.  Useful
                      for testing or injecting domain-specific checks.

    Returns:
        A ``LifecycleProgressionResult`` describing the outcome.

    Example:
        >>> from src.meeting_trigger import create_meeting, MeetingCommandRequest
        >>> from src.shared.lifecycle_progression import progress_lifecycle
        >>>
        >>> req = MeetingCommandRequest(
        ...     agenda="Test meeting",
        ...     user_id="u1",
        ...     channel_id="c1",
        ... )
        >>> ctx = create_meeting(req, meetings_root="/tmp/test-prog")
        >>> # Simulate routing, context retrieval, rounds, consensus, validation...
        >>> result = progress_lifecycle(ctx.manifest, persist=False)
    """
    starting_state = str(manifest.state)
    current = manifest
    total_steps = len(NORMAL_LIFECYCLE_STEPS)
    steps_completed = 0

    # ── Fast path: already completed ──────────────────────────────────
    if current.state == LifecycleState.COMPLETED:
        return LifecycleProgressionResult(
            success=True,
            manifest=current,
            starting_state=starting_state,
            ending_state=str(current.state),
            steps_completed=0,
            steps_total=total_steps,
        )

    # ── Validate starting state is on the happy path ──────────────────
    if not is_happy_path_state(current.state):
        return LifecycleProgressionResult(
            success=False,
            manifest=current,
            starting_state=starting_state,
            ending_state=starting_state,
            steps_completed=0,
            steps_total=total_steps,
            failure_reason=(
                f"Current state '{current.state}' is not on the normal "
                f"happy path.  Exception states (paused, deadlocked, "
                f"escalated) must be resolved before progression."
            ),
        )

    # ── Find the first step whose from_state matches the current state ─
    start_index: int | None = None
    for step in NORMAL_LIFECYCLE_STEPS:
        if step.from_state == current.state:
            start_index = step.index
            break

    if start_index is None:
        # We're on the happy path but at a state with no outgoing
        # happy-path step (should not happen — completed is handled above)
        return LifecycleProgressionResult(
            success=False,
            manifest=current,
            starting_state=starting_state,
            ending_state=str(current.state),
            steps_completed=0,
            steps_total=total_steps,
            failure_reason=(
                f"No happy-path transition defined from state "
                f"'{current.state}'"
            ),
        )

    # ── Walk forward through the remaining happy-path steps ───────────

    for step in NORMAL_LIFECYCLE_STEPS[start_index:]:
        # Build the combined guard list for this step
        guards = list(step.guards)
        if extra_guards and step.label in extra_guards:
            guards.extend(extra_guards[step.label])

        label = step.label
        if current.state != step.from_state:
            # The manifest state drifted — likely due to a mutation
            # from a post-action in a prior transition.  Re-sync by
            # checking if we can still reach the target.
            if current.state == step.to_state:
                # Already at the target — skip this step
                steps_completed += 1
                continue
            # State mismatch — stop progression
            return LifecycleProgressionResult(
                success=False,
                manifest=current,
                starting_state=starting_state,
                ending_state=str(current.state),
                steps_completed=steps_completed,
                steps_total=total_steps,
                failure_step=step,
                failure_reason=(
                    f"State mismatch at step '{step.label}': "
                    f"expected from_state='{step.from_state.value}', "
                    f"got '{current.state}'"
                ),
            )

        # Execute the transition
        _execute = _get_transition_engine()
        result: TransitionResult = _execute(
            current,
            step.to_state,
            pre_conditions=tuple(guards),
            persist=persist,
            label=step.label,
        )

        if not result.success:
            return LifecycleProgressionResult(
                success=False,
                manifest=result.manifest,
                starting_state=starting_state,
                ending_state=str(result.manifest.state),
                steps_completed=steps_completed,
                steps_total=total_steps,
                failure_step=step,
                failure_reason="; ".join(result.rejection_reasons),
            )

        # Transition succeeded — advance to the new state
        current = result.manifest
        steps_completed += 1

        # If we just reached completed, we're done
        if current.state == LifecycleState.COMPLETED:
            return LifecycleProgressionResult(
                success=True,
                manifest=current,
                starting_state=starting_state,
                ending_state=str(current.state),
                steps_completed=steps_completed,
                steps_total=total_steps,
            )

    # We exhausted all steps but didn't reach completed (should not happen
    # if NORMAL_LIFECYCLE_STEPS covers the full path)
    return LifecycleProgressionResult(
        success=(current.state == LifecycleState.COMPLETED),
        manifest=current,
        starting_state=starting_state,
        ending_state=str(current.state),
        steps_completed=steps_completed,
        steps_total=total_steps,
    )


# ── Convenience: single-step progression ──────────────────────────────────


def progress_one_step(
    manifest: MeetingManifest,
    *,
    persist: bool = True,
) -> TransitionResult:
    """Advance the manifest exactly one step along the happy path.

    If the current state is not on the happy path, the transition
    will fail with an appropriate rejection reason.

    This is a convenience wrapper that delegates to ``execute_transition``
    with the appropriate pre-conditions for the current state.

    Args:
        manifest: Current meeting manifest.
        persist: If True, write the manifest to disk after the transition.

    Returns:
        A ``TransitionResult`` describing the outcome.

    Raises:
        ValueError: If the current state has no happy-path successor.
    """
    for step in NORMAL_LIFECYCLE_STEPS:
        if step.from_state == manifest.state:
            _execute = _get_transition_engine()
            return _execute(
                manifest,
                step.to_state,
                pre_conditions=step.guards,
                persist=persist,
                label=step.label,
            )
    raise ValueError(
        f"No happy-path successor defined for state '{manifest.state}'. "
        f"The meeting may be in a terminal or exception state."
    )
