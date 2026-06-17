"""Lifecycle state enum and transition validator for meeting lifecycle management.

Defines the complete set of 15 lifecycle states for meetings in the
AI_Agent multi-agent meeting system, the permitted state transitions,
and a validator that enforces the state machine rules.

Valid states (15 total, per design doc Track B):
    created           – Meeting record initialised, manifest written to disk.
    queued            – Queue slot secured, waiting for processing.
    routing           – Qwen LLM classified agenda, roles assigned.
    context_retrieval – Context Packet generated from knowledge layers.
    in_meeting        – Minimum quorum + required role responses secured.
    consensus_building– Consensus draft created from round opinions.
    validating        – Consensus sent to GLM/Codex validation.
    executing         – OpenClaw tool-use execution (optional, loops back to validating).
    finalizing        – Final report generation.
    completed         – Consensus reached or conditional pass accepted.

Exception states:
    paused            – P0 higher-priority meeting preempted this one.
    deadlocked        – Consensus impossible after 3+1 rounds.
    escalated         – High-risk or unresolvable, sent to human/Codex escalation.
    cancelled         – User issued /cancel from Discord.
    failed            – Irrecoverable (validation failure, crash, resource exhaustion).
    stale             – Meeting abandoned without explicit cancel (coordinator
                        restart with no resumable state).
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import FrozenSet


@unique
class LifecycleState(StrEnum):
    """The fifteen valid lifecycle states for a meeting.

    Values are identical to member names — the enum serves as both
    a symbolic constant set and a string-comparable value.
    """

    CREATED = "created"
    QUEUED = "queued"
    ROUTING = "routing"
    CONTEXT_RETRIEVAL = "context_retrieval"
    IN_MEETING = "in_meeting"
    CONSENSUS_BUILDING = "consensus_building"
    VALIDATING = "validating"
    EXECUTING = "executing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    PAUSED = "paused"
    DEADLOCKED = "deadlocked"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
    FAILED = "failed"
    STALE = "stale"
    TIMED_OUT = "timed_out"


# ── State category constants ──────────────────────────────────────────────
ALL_LIFECYCLE_STATES: FrozenSet[LifecycleState] = frozenset(LifecycleState)

#: Terminal states — no further transitions are possible.
TERMINAL_STATES: FrozenSet[LifecycleState] = frozenset(
    {
        LifecycleState.COMPLETED,
        LifecycleState.CANCELLED,
        LifecycleState.FAILED,
        LifecycleState.STALE,
    }
)

#: Active states — the meeting is still in-flight and may transition.
ACTIVE_STATES: FrozenSet[LifecycleState] = frozenset(
    {
        LifecycleState.CREATED,
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.EXECUTING,
        LifecycleState.FINALIZING,
        LifecycleState.PAUSED,
        LifecycleState.DEADLOCKED,
        LifecycleState.ESCALATED,
        LifecycleState.TIMED_OUT,
    }
)

# ── State transition map ──────────────────────────────────────────────────

#: Permitted state transitions for the meeting lifecycle.
#:
#: Each key maps to the set of states that can be legally transitioned to.
#: Terminal states (completed, cancelled, failed, stale) have empty sets.
MEETING_TRANSITIONS: dict[LifecycleState, FrozenSet[LifecycleState]] = {
    # --- Normal forward flow ---
    LifecycleState.CREATED: frozenset(
        {
            LifecycleState.QUEUED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.QUEUED: frozenset(
        {
            LifecycleState.ROUTING,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.ROUTING: frozenset(
        {
            LifecycleState.CONTEXT_RETRIEVAL,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.CONTEXT_RETRIEVAL: frozenset(
        {
            LifecycleState.IN_MEETING,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.IN_MEETING: frozenset(
        {
            LifecycleState.CONSENSUS_BUILDING,
            LifecycleState.DEADLOCKED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.CONSENSUS_BUILDING: frozenset(
        {
            LifecycleState.VALIDATING,
            LifecycleState.DEADLOCKED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    # --- Validation and its branches ---
    LifecycleState.VALIDATING: frozenset(
        {
            # Pass / conditional_pass
            LifecycleState.FINALIZING,
            # Execution needed
            LifecycleState.EXECUTING,
            # Disagreement → re-meeting
            LifecycleState.IN_MEETING,
            # Missing info → re-retrieve
            LifecycleState.CONTEXT_RETRIEVAL,
            # High risk → escalate
            LifecycleState.ESCALATED,
            # After 3+1 rounds without consensus
            LifecycleState.DEADLOCKED,
            # Exception states
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    # --- Execution loop ---
    LifecycleState.EXECUTING: frozenset(
        {
            # Post-execution validation
            LifecycleState.VALIDATING,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    # --- Finalization ---
    LifecycleState.FINALIZING: frozenset(
        {
            LifecycleState.COMPLETED,
            LifecycleState.ESCALATED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.PAUSED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    # --- Terminal states ---
    LifecycleState.COMPLETED: frozenset(),
    LifecycleState.CANCELLED: frozenset(),
    LifecycleState.FAILED: frozenset(),
    LifecycleState.STALE: frozenset(),
    # --- Exception states with outgoing paths ---
    LifecycleState.PAUSED: frozenset(
        {
            # Resume to any state a meeting could have been paused from
            LifecycleState.QUEUED,
            LifecycleState.ROUTING,
            LifecycleState.CONTEXT_RETRIEVAL,
            LifecycleState.IN_MEETING,
            LifecycleState.CONSENSUS_BUILDING,
            LifecycleState.VALIDATING,
            LifecycleState.EXECUTING,
            LifecycleState.FINALIZING,
            # Also can be cancelled or fail while paused
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.DEADLOCKED: frozenset(
        {
            # Chair resolves → finalize
            LifecycleState.FINALIZING,
            # Escalate to human / Codex
            LifecycleState.ESCALATED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    LifecycleState.ESCALATED: frozenset(
        {
            # Resolved at escalation level
            LifecycleState.COMPLETED,
            # Unresolvable
            LifecycleState.FAILED,
            # User cancels even during escalation
            LifecycleState.CANCELLED,
            LifecycleState.STALE,
            LifecycleState.TIMED_OUT,
        }
    ),
    # --- Timed out: non-terminal exception, resumable ---
    LifecycleState.TIMED_OUT: frozenset(
        {
            # Resume to any state the meeting timed out from
            LifecycleState.CREATED,
            LifecycleState.QUEUED,
            LifecycleState.ROUTING,
            LifecycleState.CONTEXT_RETRIEVAL,
            LifecycleState.IN_MEETING,
            LifecycleState.CONSENSUS_BUILDING,
            LifecycleState.VALIDATING,
            LifecycleState.EXECUTING,
            LifecycleState.FINALIZING,
            # Can transition to terminal states while timed out
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
        }
    ),
}


# ── State Transition Legality Matrix ──────────────────────────────────────
# Sub-AC 4.1: Testable state-to-state legality matrix


class StateTransitionMatrix:
    """Complete N×N boolean matrix of all state-to-state transition legality.

    Provides O(1) lookup for any state pair, matrix export for inspection,
    and summary statistics about the state machine.

    The matrix is built once from ``MEETING_TRANSITIONS`` and is immutable
    after construction.  It covers all 16 ``LifecycleState`` members,
    including terminal states (which have all-false rows).

    This satisfies the Sub-AC 4.1 requirement for a *testable state-to-state
    legality matrix* — every possible transition can be queried and verified
    without running the full validator machinery.

    Usage::

        matrix = StateTransitionMatrix()
        assert matrix.is_legal("created", "queued") is True
        assert matrix.is_legal("completed", "queued") is False

        # Export the full 16×16 matrix as a dict of dicts
        grid = matrix.to_dict()
        assert grid["created"]["queued"] is True

        # Summary statistics
        stats = matrix.summary()
        print(stats["total_valid_transitions"])  # e.g. 85

    Attributes:
        _states: Tuple of all LifecycleState members in definition order.
        _matrix: Dict-of-dicts mapping (from_state.value -> to_state.value -> bool).
        _state_set: Frozen set for membership tests.
    """

    __slots__ = ("_states", "_matrix", "_state_set")

    def __init__(
        self,
        transitions: dict[LifecycleState, FrozenSet[LifecycleState]]
        | None = None,
        states: FrozenSet[LifecycleState] | None = None,
    ) -> None:
        """Build the complete N×N legality matrix.

        Args:
            transitions: Transition map.  Defaults to ``MEETING_TRANSITIONS``.
            states: Set of all states.  Defaults to ``ALL_LIFECYCLE_STATES``.
        """
        _transitions = transitions if transitions is not None else MEETING_TRANSITIONS
        _states = states if states is not None else ALL_LIFECYCLE_STATES

        # Deterministic ordering for reproducible matrix output
        self._states = tuple(sorted(_states, key=lambda s: s.value))
        self._state_set = frozenset(self._states)

        # Build the complete boolean grid
        self._matrix: dict[str, dict[str, bool]] = {}
        for from_state in self._states:
            legal_targets = _transitions.get(from_state, frozenset())
            row: dict[str, bool] = {}
            for to_state in self._states:
                row[to_state.value] = to_state in legal_targets
            self._matrix[from_state.value] = row

    # ── Core lookup ──────────────────────────────────────────────────

    def is_legal(
        self,
        from_state: LifecycleState | str,
        to_state: LifecycleState | str,
    ) -> bool:
        """O(1) lookup: return True if the transition is permitted.

        Args:
            from_state: Source state (member or string value).
            to_state: Target state (member or string value).

        Returns:
            True if the transition exists in the matrix.

        Raises:
            KeyError: If either state is not a valid ``LifecycleState`` value.
        """
        fv = from_state.value if isinstance(from_state, LifecycleState) else from_state
        tv = to_state.value if isinstance(to_state, LifecycleState) else to_state

        if fv not in self._matrix:
            raise KeyError(
                f"Unknown from_state: {fv!r}. Valid states: "
                f"{sorted(self._matrix.keys())}"
            )
        row = self._matrix[fv]
        if tv not in row:
            raise KeyError(
                f"Unknown to_state: {tv!r}. Valid states: "
                f"{sorted(row.keys())}"
            )
        return row[tv]

    def legal_targets(
        self,
        from_state: LifecycleState | str,
    ) -> tuple[str, ...]:
        """Return all legal target states reachable from *from_state*.

        Args:
            from_state: Source state.

        Returns:
            Tuple of state value strings that can be transitioned to,
            in alphabetical order for determinism.
        """
        fv = from_state.value if isinstance(from_state, LifecycleState) else from_state
        if fv not in self._matrix:
            raise KeyError(
                f"Unknown from_state: {fv!r}. Valid states: "
                f"{sorted(self._matrix.keys())}"
            )
        return tuple(
            sorted(tv for tv, legal in self._matrix[fv].items() if legal)
        )

    def legal_sources(
        self,
        to_state: LifecycleState | str,
    ) -> tuple[str, ...]:
        """Return all states that can legally transition TO *to_state*.

        Args:
            to_state: Target state.

        Returns:
            Tuple of state value strings that can transition to it.
        """
        tv = to_state.value if isinstance(to_state, LifecycleState) else to_state
        sources: list[str] = []
        for fv, row in self._matrix.items():
            if row.get(tv, False):
                sources.append(fv)
        return tuple(sorted(sources))

    # ── Export ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, dict[str, bool]]:
        """Return the complete N×N matrix as a dict of dicts.

        Outer keys are from-state values, inner keys are to-state values.
        Suitable for JSON serialization or test assertions.

        Returns:
            A copy of the internal matrix (safe to mutate).
        """
        return {
            fv: dict(row) for fv, row in self._matrix.items()
        }

    def to_adjacency_list(self) -> dict[str, list[str]]:
        """Return a compact adjacency list of valid transitions.

        Only includes states that have at least one outgoing transition.

        Returns:
            Dict mapping from-state value to a sorted list of to-state values.
        """
        return {
            fv: list(self.legal_targets(fv))
            for fv in self._matrix
            if any(self._matrix[fv].values())
        }

    def to_csv(self, delimiter: str = ",") -> str:
        """Return the matrix as CSV text with a header row.

        Args:
            delimiter: Field delimiter (default: comma).

        Returns:
            CSV-formatted string.
        """
        state_names = [s.value for s in self._states]
        lines = [delimiter.join([""] + state_names)]
        for fv in state_names:
            row_vals = [fv] + [
                "1" if self._matrix[fv][tv] else "0"
                for tv in state_names
            ]
            lines.append(delimiter.join(row_vals))
        return "\n".join(lines)

    # ── Summary statistics ───────────────────────────────────────────

    def summary(self) -> dict[str, int | float | dict[str, int]]:
        """Return summary statistics about the state machine.

        Returns a dict with keys:
            - total_states: number of states
            - total_valid_transitions: count of True cells in the matrix
            - total_possible_pairs: N × (N-1), self-transitions excluded
            - density: ratio of valid to possible (float as int)
            - terminal_states: count of states with zero outgoing
            - per_state_outgoing: dict of state -> outgoing transition count
        """
        n = len(self._states)
        total_valid = 0
        per_state: dict[str, int] = {}
        terminal_count = 0

        for fv, row in self._matrix.items():
            count = sum(1 for v in row.values() if v)
            total_valid += count
            per_state[fv] = count
            if count == 0:
                terminal_count += 1

        total_possible = n * (n - 1)  # exclude self-transitions

        return {
            "total_states": n,
            "total_valid_transitions": total_valid,
            "total_possible_pairs": total_possible,
            "density_percent": round(
                (total_valid / total_possible) * 100, 1
            ) if total_possible > 0 else 0.0,
            "terminal_states": terminal_count,
            "per_state_outgoing": per_state,
        }

    # ── Dunder methods ───────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"StateTransitionMatrix(states={len(self._states)}, "
            f"valid_transitions={self.summary()['total_valid_transitions']})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StateTransitionMatrix):
            return NotImplemented
        return self._matrix == other._matrix

    def __hash__(self) -> int:
        # Immutable after construction, so we can hash the frozen matrix
        frozen = tuple(
            (fv, tuple(sorted(row.items())))
            for fv, row in sorted(self._matrix.items())
        )
        return hash(frozen)

    def __contains__(
        self, pair: tuple[LifecycleState | str, LifecycleState | str]
    ) -> bool:
        """Allow ``(from_state, to_state) in matrix`` syntax."""
        if len(pair) != 2:
            return False
        try:
            return self.is_legal(pair[0], pair[1])
        except KeyError:
            return False

    @property
    def states(self) -> tuple[str, ...]:
        """Return the ordered tuple of state value strings."""
        return tuple(s.value for s in self._states)


# Module-level singleton — built once at import time
STATE_TRANSITION_MATRIX: StateTransitionMatrix = StateTransitionMatrix()
"""The global state transition legality matrix singleton.

Built from ``MEETING_TRANSITIONS`` at module import time.  Use this
for O(1) transition lookups without constructing a new matrix.

Example:
    >>> from src.shared.lifecycle import STATE_TRANSITION_MATRIX
    >>> STATE_TRANSITION_MATRIX.is_legal("created", "queued")
    True
"""


# ── Helper functions ──────────────────────────────────────────────────────


def is_terminal(state: LifecycleState | str) -> bool:
    """Return True if *state* is a terminal lifecycle state."""
    state = LifecycleState(state) if isinstance(state, str) else state
    return state in TERMINAL_STATES


def is_active(state: LifecycleState | str) -> bool:
    """Return True if *state* is an active (in-flight) lifecycle state."""
    state = LifecycleState(state) if isinstance(state, str) else state
    return state in ACTIVE_STATES


def validate_transition(
    from_state: LifecycleState | str,
    to_state: LifecycleState | str,
    *,
    strict: bool = True,
) -> bool:
    """Return True if transitioning from *from_state* to *to_state* is permitted.

    Checks the MEETING_TRANSITIONS map.  When *strict* is True (default),
    the function also verifies that both states are valid LifecycleState
    members — invalid names raise ValueError.  When *strict* is False,
    invalid names simply return False.

    Args:
        from_state: Current lifecycle state (member or string).
        to_state: Target lifecycle state (member or string).
        strict: If True, raise ValueError for invalid state names.
                If False, return False for invalid state names.

    Returns:
        True if the transition is permitted.

    Raises:
        ValueError: If either state is not a valid LifecycleState member
                    and *strict* is True.

    Examples:
        >>> validate_transition("created", "queued")
        True
        >>> validate_transition("completed", "queued")
        False
        >>> validate_transition("invalid", "queued", strict=False)
        False
    """
    if isinstance(from_state, str):
        try:
            from_state = LifecycleState(from_state)
        except ValueError:
            if strict:
                raise
            return False

    if isinstance(to_state, str):
        try:
            to_state = LifecycleState(to_state)
        except ValueError:
            if strict:
                raise
            return False

    allowed = MEETING_TRANSITIONS.get(from_state, frozenset())
    return to_state in allowed
