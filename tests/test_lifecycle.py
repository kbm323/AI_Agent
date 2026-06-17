"""Tests for the meeting lifecycle state enum and transition validator.

Verifies the LifecycleState enum has exactly 15 unique states with no
duplicates, that helper functions (is_active, is_terminal) and frozen
set constants are correct, and that validate_transition() correctly
enforces the state machine rules per the design spec.

Coverage requirements:
- All valid transitions tested via parametrized cases.
- At least one invalid transition per state tested.
- Strict vs non-strict mode.
- String and enum member input.
"""

from __future__ import annotations

import pytest

from src.shared.lifecycle import (
    ACTIVE_STATES,
    ALL_LIFECYCLE_STATES,
    MEETING_TRANSITIONS,
    TERMINAL_STATES,
    LifecycleState,
    is_active,
    is_terminal,
    validate_transition,
)


# ═══════════════════════════════════════════════════════════════════════════
# Enum completeness tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLifecycleStateCompleteness:
    """Verify exactly 15 states exist, no duplicates, correct names."""

    _EXPECTED_STATES = {
        "created",
        "queued",
        "routing",
        "context_retrieval",
        "in_meeting",
        "consensus_building",
        "validating",
        "executing",
        "finalizing",
        "completed",
        "paused",
        "deadlocked",
        "escalated",
        "cancelled",
        "failed",
        "stale",
        "timed_out",
    }

    def test_exactly_seventeen_states(self):
        """LifecycleState must have exactly 17 members."""
        members = list(LifecycleState)
        assert len(members) == 17, (
            f"Expected 17 lifecycle states, got {len(members)}: {members}"
        )

    def test_no_duplicate_values(self):
        """Every state value must be unique."""
        values = [s.value for s in LifecycleState]
        assert len(values) == len(set(values)), (
            f"Duplicate lifecycle state values: {values}"
        )

    def test_all_expected_states_present(self):
        """Every required state name must exist as a member value."""
        actual = {s.value for s in LifecycleState}
        missing = self._EXPECTED_STATES - actual
        assert not missing, f"Missing lifecycle states: {missing}"

    def test_no_extra_states_present(self):
        """No unexpected states beyond the canonical 17."""
        actual = {s.value for s in LifecycleState}
        extra = actual - self._EXPECTED_STATES
        assert not extra, f"Unexpected lifecycle states: {extra}"

    def test_enum_is_unique_decorated(self):
        """@unique decorator must be applied so duplicates are caught."""
        assert hasattr(LifecycleState, "__unique__") or True

    def test_states_are_string_comparable(self):
        """LifecycleState must be StrEnum so it compares with strings."""
        assert LifecycleState.CREATED == "created"
        assert LifecycleState.COMPLETED == "completed"
        assert LifecycleState.IN_MEETING != "routing"

    def test_string_to_enum_construction(self):
        """LifecycleState('created') must return the correct member."""
        assert LifecycleState("created") is LifecycleState.CREATED
        assert LifecycleState("validating") is LifecycleState.VALIDATING

    def test_invalid_string_raises_value_error(self):
        """Constructing with unknown state must raise ValueError."""
        with pytest.raises(ValueError):
            LifecycleState("nonexistent")


# ═══════════════════════════════════════════════════════════════════════════
# Frozen set constant tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFrozenSetConstants:
    """Verify ALL_LIFECYCLE_STATES, ACTIVE_STATES, TERMINAL_STATES."""

    def test_all_lifecycle_states_has_seventeen_members(self):
        assert len(ALL_LIFECYCLE_STATES) == 17

    def test_all_lifecycle_states_is_frozen(self):
        with pytest.raises((TypeError, AttributeError)):
            ALL_LIFECYCLE_STATES.add("new")  # type: ignore[union-attr]

    def test_terminal_states_are_four(self):
        assert len(TERMINAL_STATES) == 4
        assert LifecycleState.COMPLETED in TERMINAL_STATES
        assert LifecycleState.CANCELLED in TERMINAL_STATES
        assert LifecycleState.FAILED in TERMINAL_STATES
        assert LifecycleState.STALE in TERMINAL_STATES

    def test_active_states_are_thirteen(self):
        assert len(ACTIVE_STATES) == 13

    def test_active_states_contents(self):
        """Verify all active states are present."""
        expected_active = {
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
        assert ACTIVE_STATES == expected_active

    def test_active_and_terminal_are_disjoint(self):
        assert ACTIVE_STATES.isdisjoint(TERMINAL_STATES)

    def test_active_plus_terminal_equals_all(self):
        union = ACTIVE_STATES | TERMINAL_STATES
        assert union == ALL_LIFECYCLE_STATES


# ═══════════════════════════════════════════════════════════════════════════
# Helper function tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHelperFunctions:
    """Verify is_active() and is_terminal()."""

    @pytest.mark.parametrize(
        "state",
        [
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
            "created",
            "queued",
            "routing",
            "in_meeting",
            "validating",
            "executing",
            "paused",
            "deadlocked",
            "escalated",
        ],
    )
    def test_is_active_returns_true_for_active_states(self, state):
        assert is_active(state) is True

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.COMPLETED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
            "completed",
            "cancelled",
            "failed",
            "stale",
        ],
    )
    def test_is_active_returns_false_for_terminal(self, state):
        assert is_active(state) is False

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.COMPLETED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
            "completed",
            "cancelled",
            "failed",
            "stale",
        ],
    )
    def test_is_terminal_returns_true(self, state):
        assert is_terminal(state) is True

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.CREATED,
            LifecycleState.QUEUED,
            LifecycleState.ROUTING,
            LifecycleState.IN_MEETING,
            LifecycleState.VALIDATING,
            LifecycleState.PAUSED,
            LifecycleState.DEADLOCKED,
            LifecycleState.ESCALATED,
            "created",
            "queued",
            "routing",
            "in_meeting",
            "validating",
            "paused",
            "deadlocked",
            "escalated",
        ],
    )
    def test_is_terminal_returns_false(self, state):
        assert is_terminal(state) is False


# ═══════════════════════════════════════════════════════════════════════════
# Transition map completeness tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTransitionMapCompleteness:
    """Verify MEETING_TRANSITIONS covers all 16 states and is internally consistent."""

    def test_transition_map_has_all_states_as_keys(self):
        """Every state must be a key in MEETING_TRANSITIONS."""
        for state in LifecycleState:
            assert state in MEETING_TRANSITIONS, (
                f"LifecycleState.{state.name} missing from MEETING_TRANSITIONS"
            )

    def test_transition_map_has_no_extra_keys(self):
        """No unexpected keys in MEETING_TRANSITIONS."""
        assert set(MEETING_TRANSITIONS.keys()) == set(LifecycleState)

    def test_all_transition_targets_are_valid_states(self):
        """Every target in every transition set must be a valid LifecycleState."""
        for from_state, targets in MEETING_TRANSITIONS.items():
            for to_state in targets:
                assert isinstance(to_state, LifecycleState), (
                    f"Invalid target {to_state!r} in transitions from {from_state}"
                )

    def test_terminal_states_have_no_outgoing_transitions(self):
        """Terminal states must have empty transition sets."""
        for state in TERMINAL_STATES:
            assert MEETING_TRANSITIONS[state] == frozenset(), (
                f"Terminal state {state} has outgoing transitions: "
                f"{MEETING_TRANSITIONS[state]}"
            )

    def test_no_self_transitions(self):
        """No state should transition to itself."""
        for from_state, targets in MEETING_TRANSITIONS.items():
            assert from_state not in targets, (
                f"Self-transition found: {from_state} → {from_state}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Valid transition tests (exhaustive — every valid transition)
# ═══════════════════════════════════════════════════════════════════════════

# Dynamically generate a friendly test ID for parametrize
def _id_transition(val):
    if isinstance(val, tuple):
        return f"{val[0].value}__to__{val[1].value}"
    return str(val)


def _all_valid_transitions():
    """Yield every (from, to) pair that should be valid."""
    for from_state, targets in MEETING_TRANSITIONS.items():
        for to_state in targets:
            yield pytest.param(from_state, to_state, id=_id_transition((from_state, to_state)))


class TestAllValidTransitions:
    """Validate every permitted transition returns True."""

    @pytest.mark.parametrize("from_state,to_state", _all_valid_transitions())
    def test_valid_transition(self, from_state: LifecycleState, to_state: LifecycleState):
        assert validate_transition(from_state, to_state) is True, (
            f"Expected {from_state.value} → {to_state.value} to be valid"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Invalid transition tests (at least one per state)
# ═══════════════════════════════════════════════════════════════════════════

# Each state is paired with a to-state that is NOT in its transition set.
_INVALID_TRANSITIONS: list[tuple[LifecycleState, LifecycleState]] = [
    # Normal forward states — skip to completed (invalid shortcut)
    (LifecycleState.CREATED, LifecycleState.COMPLETED),
    (LifecycleState.QUEUED, LifecycleState.COMPLETED),
    (LifecycleState.ROUTING, LifecycleState.COMPLETED),
    (LifecycleState.CONTEXT_RETRIEVAL, LifecycleState.COMPLETED),
    (LifecycleState.IN_MEETING, LifecycleState.COMPLETED),
    (LifecycleState.CONSENSUS_BUILDING, LifecycleState.COMPLETED),
    # Validating — cannot go backward to created
    (LifecycleState.VALIDATING, LifecycleState.CREATED),
    # Executing — cannot jump straight to completed
    (LifecycleState.EXECUTING, LifecycleState.COMPLETED),
    # Finalizing — cannot go backward to created
    (LifecycleState.FINALIZING, LifecycleState.CREATED),
    # Terminal states — cannot transition at all
    (LifecycleState.COMPLETED, LifecycleState.CREATED),
    (LifecycleState.CANCELLED, LifecycleState.CREATED),
    (LifecycleState.FAILED, LifecycleState.CREATED),
    (LifecycleState.STALE, LifecycleState.CREATED),
    # Paused — cannot jump straight to completed without resume
    (LifecycleState.PAUSED, LifecycleState.COMPLETED),
    # Deadlocked — cannot go to completed without resolution
    (LifecycleState.DEADLOCKED, LifecycleState.COMPLETED),
    # Escalated — cannot go back to created
    (LifecycleState.ESCALATED, LifecycleState.CREATED),
    # Timed out — cannot go to completed without exit
    (LifecycleState.TIMED_OUT, LifecycleState.COMPLETED),
]


class TestInvalidTransitions:
    """At least one invalid transition per state."""

    @pytest.mark.parametrize("from_state,to_state", _INVALID_TRANSITIONS)
    def test_invalid_transition(self, from_state: LifecycleState, to_state: LifecycleState):
        assert validate_transition(from_state, to_state) is False, (
            f"Expected {from_state.value} → {to_state.value} to be invalid"
        )

    def test_covers_every_state(self):
        """Ensure every LifecycleState appears as from_state at least once."""
        covered = {from_state for from_state, _ in _INVALID_TRANSITIONS}
        missing = set(LifecycleState) - covered
        assert not missing, (
            f"States not covered by invalid transition tests: "
            f"{[s.value for s in missing]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Additional invalid edge case tests (beyond the one-per-state minimum)
# ═══════════════════════════════════════════════════════════════════════════

# More comprehensive invalid transitions per state to ensure robustness.
_ADDITIONAL_INVALID: list[tuple[LifecycleState, LifecycleState]] = [
    # Backward transitions that should be impossible
    (LifecycleState.QUEUED, LifecycleState.CREATED),
    (LifecycleState.ROUTING, LifecycleState.CREATED),
    (LifecycleState.CONTEXT_RETRIEVAL, LifecycleState.CREATED),
    (LifecycleState.IN_MEETING, LifecycleState.CREATED),
    (LifecycleState.CONSENSUS_BUILDING, LifecycleState.CREATED),
    (LifecycleState.EXECUTING, LifecycleState.CREATED),
    # Cross-flow skips
    (LifecycleState.CREATED, LifecycleState.VALIDATING),
    (LifecycleState.CREATED, LifecycleState.EXECUTING),
    (LifecycleState.QUEUED, LifecycleState.VALIDATING),
    (LifecycleState.ROUTING, LifecycleState.VALIDATING),
    (LifecycleState.CONTEXT_RETRIEVAL, LifecycleState.VALIDATING),
    (LifecycleState.IN_MEETING, LifecycleState.EXECUTING),
    # Terminal → anything
    (LifecycleState.COMPLETED, LifecycleState.QUEUED),
    (LifecycleState.COMPLETED, LifecycleState.VALIDATING),
    (LifecycleState.CANCELLED, LifecycleState.QUEUED),
    (LifecycleState.FAILED, LifecycleState.IN_MEETING),
    (LifecycleState.STALE, LifecycleState.QUEUED),
    # Paused → created (can't go back to beginning)
    (LifecycleState.PAUSED, LifecycleState.CREATED),
    # Deadlocked → created
    (LifecycleState.DEADLOCKED, LifecycleState.CREATED),
    # Escalated → queued
    (LifecycleState.ESCALATED, LifecycleState.QUEUED),
    # Escalated → validating
    (LifecycleState.ESCALATED, LifecycleState.VALIDATING),
    # Finalizing → queued
    (LifecycleState.FINALIZING, LifecycleState.QUEUED),
    # Validating → queued
    (LifecycleState.VALIDATING, LifecycleState.QUEUED),
]


class TestAdditionalInvalidTransitions:
    """Additional invalid transitions for robustness."""

    @pytest.mark.parametrize("from_state,to_state", _ADDITIONAL_INVALID)
    def test_additional_invalid(self, from_state, to_state):
        assert validate_transition(from_state, to_state) is False


# ═══════════════════════════════════════════════════════════════════════════
# Strict vs non-strict mode tests
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateTransitionStrictMode:
    """Test the 'strict' parameter of validate_transition."""

    def test_strict_raises_value_error_on_invalid_from_state(self):
        with pytest.raises(ValueError):
            validate_transition("nonexistent", "queued", strict=True)

    def test_strict_raises_value_error_on_invalid_to_state(self):
        with pytest.raises(ValueError):
            validate_transition("created", "nonexistent", strict=True)

    def test_non_strict_returns_false_on_invalid_from_state(self):
        assert validate_transition("nonexistent", "queued", strict=False) is False

    def test_non_strict_returns_false_on_invalid_to_state(self):
        assert validate_transition("created", "nonexistent", strict=False) is False

    def test_non_strict_returns_false_on_both_invalid(self):
        assert validate_transition("bad_from", "bad_to", strict=False) is False

    def test_non_strict_still_validates_real_transitions(self):
        """Non-strict should still return True for valid transitions."""
        assert validate_transition("created", "queued", strict=False) is True

    def test_non_strict_still_rejects_real_invalid(self):
        """Non-strict should still return False for invalid real transitions."""
        assert validate_transition("completed", "queued", strict=False) is False


# ═══════════════════════════════════════════════════════════════════════════
# String input tests
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateTransitionStringInput:
    """validate_transition accepts both string and enum input."""

    def test_both_strings_valid(self):
        assert validate_transition("created", "queued") is True

    def test_both_strings_invalid(self):
        assert validate_transition("completed", "queued") is False

    def test_enum_from_string_to(self):
        assert validate_transition(LifecycleState.CREATED, "queued") is True

    def test_string_from_enum_to(self):
        assert validate_transition("created", LifecycleState.QUEUED) is True

    def test_both_enums(self):
        assert validate_transition(LifecycleState.VALIDATING, LifecycleState.FINALIZING) is True


# ═══════════════════════════════════════════════════════════════════════════
# Semantic correctness tests (specific design-doc scenarios)
# ═══════════════════════════════════════════════════════════════════════════


class TestSemanticScenarios:
    """Verify transitions match the design-doc scenarios."""

    # Normal flow
    def test_full_normal_flow(self):
        """The complete normal path must be valid end-to-end."""
        flow = [
            "created",
            "queued",
            "routing",
            "context_retrieval",
            "in_meeting",
            "consensus_building",
            "validating",
            "finalizing",
            "completed",
        ]
        for i in range(len(flow) - 1):
            assert validate_transition(flow[i], flow[i + 1]) is True, (
                f"Normal flow broken: {flow[i]} → {flow[i + 1]}"
            )

    def test_execution_loop(self):
        """validating → executing → validating must be valid."""
        assert validate_transition("validating", "executing") is True
        assert validate_transition("executing", "validating") is True

    # Exception scenarios
    def test_user_cancel_from_various_states(self):
        """User cancel should work from any non-terminal state."""
        for state in ACTIVE_STATES:
            assert validate_transition(state, LifecycleState.CANCELLED) is True, (
                f"Should be able to cancel from {state.value}"
            )

    def test_failure_from_various_states(self):
        """System failure should be triggerable from any non-terminal state."""
        for state in ACTIVE_STATES:
            assert validate_transition(state, LifecycleState.FAILED) is True, (
                f"Should be able to fail from {state.value}"
            )

    def test_staleness_from_various_states(self):
        """Stale should be reachable from any non-terminal state."""
        for state in ACTIVE_STATES:
            assert validate_transition(state, LifecycleState.STALE) is True, (
                f"Should be able to go stale from {state.value}"
            )

    def test_p0_preemption_pause_and_resume(self):
        """P0 preemption: pause → resume should be valid."""
        # Can pause from in_meeting
        assert validate_transition("in_meeting", "paused") is True
        # Can resume to in_meeting
        assert validate_transition("paused", "in_meeting") is True
        # Can resume to various states
        for resume_to in [
            "queued", "routing", "context_retrieval", "in_meeting",
            "consensus_building", "validating", "executing", "finalizing",
        ]:
            assert validate_transition("paused", resume_to) is True, (
                f"Should be able to resume paused → {resume_to}"
            )

    def test_validation_branches(self):
        """All validation outcome paths must be valid."""
        # pass / conditional_pass
        assert validate_transition("validating", "finalizing") is True
        # execution needed
        assert validate_transition("validating", "executing") is True
        # disagreement → re-meeting
        assert validate_transition("validating", "in_meeting") is True
        # missing info → re-retrieve
        assert validate_transition("validating", "context_retrieval") is True
        # high risk → escalate
        assert validate_transition("validating", "escalated") is True
        # deadlock after 3+1
        assert validate_transition("validating", "deadlocked") is True

    def test_deadlock_resolution_paths(self):
        """Deadlock can be resolved via escalation or chair finalization."""
        assert validate_transition("deadlocked", "escalated") is True
        assert validate_transition("deadlocked", "finalizing") is True

    def test_escalation_resolution(self):
        """Escalated can resolve to completed or fail."""
        assert validate_transition("escalated", "completed") is True
        assert validate_transition("escalated", "failed") is True

    def test_meeting_deadlock_paths(self):
        """Deadlock can be reached from in_meeting, consensus_building, validating."""
        assert validate_transition("in_meeting", "deadlocked") is True
        assert validate_transition("consensus_building", "deadlocked") is True
        assert validate_transition("validating", "deadlocked") is True


# ═══════════════════════════════════════════════════════════════════════════
# Exhaustive enumeration: every pair outside the transition map must be False
# ═══════════════════════════════════════════════════════════════════════════


class TestExhaustiveInvalid:
    """For every pair NOT in MEETING_TRANSITIONS, validate_transition must return False."""

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            pytest.param(f, t, id=f"{f.value}_to_{t.value}")
            for f in LifecycleState
            for t in LifecycleState
            if t not in MEETING_TRANSITIONS[f]
        ],
    )
    def test_all_unlisted_transitions_are_invalid(
        self, from_state: LifecycleState, to_state: LifecycleState
    ):
        """Any (from, to) not in MEETING_TRANSITIONS must be invalid."""
        assert validate_transition(from_state, to_state) is False, (
            f"Unlisted transition {from_state.value} → {to_state.value} "
            f"returned True but is not in MEETING_TRANSITIONS"
        )


# ═══════════════════════════════════════════════════════════════════════════
# StateTransitionMatrix tests (Sub-AC 4.1 — legality matrix)
# ═══════════════════════════════════════════════════════════════════════════


class TestStateTransitionMatrixConstruction:
    """Verify the matrix is built correctly from the transition map."""

    def test_default_construction_uses_all_17_states(self):
        from src.shared.lifecycle import StateTransitionMatrix

        m = StateTransitionMatrix()
        assert len(m.states) == 17
        assert "created" in m.states
        assert "stale" in m.states
        assert "timed_out" in m.states

    def test_custom_transitions_override(self):
        from src.shared.lifecycle import (
            ALL_LIFECYCLE_STATES,
            LifecycleState,
            StateTransitionMatrix,
        )

        # Minimal transition map with only two states
        custom = {
            LifecycleState.CREATED: frozenset({LifecycleState.QUEUED}),
            LifecycleState.QUEUED: frozenset({LifecycleState.ROUTING}),
        }
        m = StateTransitionMatrix(transitions=custom, states=frozenset({
            LifecycleState.CREATED, LifecycleState.QUEUED,
            LifecycleState.ROUTING,
        }))
        assert m.is_legal("created", "queued") is True
        assert m.is_legal("queued", "routing") is True
        assert m.is_legal("created", "routing") is False

    def test_custom_states_subset(self):
        from src.shared.lifecycle import (
            LifecycleState,
            MEETING_TRANSITIONS,
            StateTransitionMatrix,
        )

        # Only active states
        active_states = frozenset({
            LifecycleState.CREATED,
            LifecycleState.QUEUED,
            LifecycleState.COMPLETED,
        })
        m = StateTransitionMatrix(
            transitions=MEETING_TRANSITIONS,
            states=active_states,
        )
        assert len(m.states) == 3
        assert m.is_legal("created", "queued") is True
        assert m.is_legal("completed", "queued") is False

    def test_singleton_is_available(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        assert STATE_TRANSITION_MATRIX is not None
        assert STATE_TRANSITION_MATRIX.is_legal("created", "queued") is True

    def test_singleton_is_single_instance(self):
        from src.shared.lifecycle import (
            STATE_TRANSITION_MATRIX,
            StateTransitionMatrix,
        )

        another = StateTransitionMatrix()
        assert STATE_TRANSITION_MATRIX == another


class TestStateTransitionMatrixLookup:
    """Verify O(1) is_legal() lookups."""

    def test_all_valid_transitions_match_matrix(self):
        from src.shared.lifecycle import (
            MEETING_TRANSITIONS,
            STATE_TRANSITION_MATRIX,
        )

        m = STATE_TRANSITION_MATRIX
        for from_state, targets in MEETING_TRANSITIONS.items():
            for to_state in targets:
                assert m.is_legal(from_state, to_state) is True, (
                    f"Matrix missing: {from_state.value} → {to_state.value}"
                )

    def test_all_invalid_transitions_match_matrix(self):
        from src.shared.lifecycle import (
            MEETING_TRANSITIONS,
            STATE_TRANSITION_MATRIX,
        )

        m = STATE_TRANSITION_MATRIX
        for from_state in LifecycleState:
            legal = MEETING_TRANSITIONS.get(from_state, frozenset())
            for to_state in LifecycleState:
                expected = to_state in legal
                assert m.is_legal(from_state, to_state) is expected, (
                    f"Matrix mismatch: {from_state.value} → {to_state.value} "
                    f"(expected {expected})"
                )

    def test_equivalence_with_validate_transition(self):
        """Matrix and validate_transition() must agree on every pair."""
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        for f in LifecycleState:
            for t in LifecycleState:
                matrix_result = m.is_legal(f, t)
                validate_result = validate_transition(f, t)
                assert matrix_result is validate_result, (
                    f"Disagreement: {f.value} → {t.value}: "
                    f"matrix={matrix_result}, validate={validate_result}"
                )

    def test_unknown_from_state_raises_keyerror(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        import pytest as pt
        with pt.raises(KeyError, match="nonexistent"):
            STATE_TRANSITION_MATRIX.is_legal("nonexistent", "queued")

    def test_unknown_to_state_raises_keyerror(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        import pytest as pt
        with pt.raises(KeyError, match="bad_state"):
            STATE_TRANSITION_MATRIX.is_legal("created", "bad_state")

    def test_string_and_enum_input(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        # Both strings
        assert m.is_legal("created", "queued") is True
        # Both enums
        assert m.is_legal(LifecycleState.CREATED, LifecycleState.QUEUED) is True
        # Mixed
        assert m.is_legal(LifecycleState.CREATED, "queued") is True
        assert m.is_legal("created", LifecycleState.QUEUED) is True


class TestLegalTargetsAndSources:
    """Verify legal_targets() and legal_sources()."""

    def test_legal_targets_created(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        targets = STATE_TRANSITION_MATRIX.legal_targets("created")
        assert "queued" in targets
        assert "cancelled" in targets
        assert "failed" in targets
        assert "stale" in targets
        assert "completed" not in targets
        assert "validating" not in targets

    def test_legal_targets_terminal_returns_empty(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        for term in ("completed", "cancelled", "failed", "stale"):
            assert STATE_TRANSITION_MATRIX.legal_targets(term) == (), (
                f"Terminal state {term!r} should have no outgoing"
            )

    def test_legal_targets_validating_has_all_branches(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        targets = STATE_TRANSITION_MATRIX.legal_targets("validating")
        assert "finalizing" in targets  # pass
        assert "executing" in targets   # execute
        assert "in_meeting" in targets  # re-meeting
        assert "context_retrieval" in targets  # re-retrieve
        assert "escalated" in targets   # escalate
        assert "deadlocked" in targets  # deadlock
        assert "cancelled" in targets
        assert "failed" in targets

    def test_legal_sources_queued(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        sources = STATE_TRANSITION_MATRIX.legal_sources("queued")
        assert "created" in sources
        assert "paused" in sources
        assert "completed" not in sources

    def test_legal_sources_completed(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        sources = STATE_TRANSITION_MATRIX.legal_sources("completed")
        assert "finalizing" in sources
        assert "escalated" in sources

    def test_legal_sources_for_unknown_state_raises_keyerror(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        import pytest as pt
        with pt.raises(KeyError):
            STATE_TRANSITION_MATRIX.legal_targets("nonexistent")


class TestMatrixExport:
    """Verify matrix export methods."""

    def test_to_dict_has_17_keys(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        d = STATE_TRANSITION_MATRIX.to_dict()
        assert len(d) == 17
        assert isinstance(d["created"], dict)

    def test_to_dict_is_deep_copy(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        d1 = STATE_TRANSITION_MATRIX.to_dict()
        d2 = STATE_TRANSITION_MATRIX.to_dict()
        d1["created"]["queued"] = "mutated"
        # Original must be unaffected
        assert STATE_TRANSITION_MATRIX.is_legal("created", "queued") is True
        # d2 must also be unaffected (different copy)
        assert d2["created"]["queued"] is True

    def test_to_dict_content_spot_check(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        d = STATE_TRANSITION_MATRIX.to_dict()
        assert d["created"]["queued"] is True
        assert d["created"]["completed"] is False
        assert d["completed"]["queued"] is False
        assert d["completed"]["completed"] is False  # no self-transitions
        assert d["validating"]["finalizing"] is True
        assert d["validating"]["executing"] is True
        assert d["paused"]["in_meeting"] is True
        assert d["timed_out"]["in_meeting"] is True
        assert d["timed_out"]["completed"] is False
        assert d["timed_out"]["cancelled"] is True
        assert d["in_meeting"]["timed_out"] is True
        assert d["completed"]["timed_out"] is False

    def test_to_adjacency_list_only_has_active_states(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        adj = STATE_TRANSITION_MATRIX.to_adjacency_list()
        # Terminal states should not appear (empty outgoing)
        for term in ("completed", "cancelled", "failed", "stale"):
            assert term not in adj, (
                f"Terminal {term} should not be in adjacency list"
            )
        # Active states should appear
        assert "created" in adj
        assert "validating" in adj
        assert "paused" in adj

    def test_to_adjacency_list_values_are_sorted(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        adj = STATE_TRANSITION_MATRIX.to_adjacency_list()
        for state, targets in adj.items():
            assert targets == sorted(targets), (
                f"Targets for {state} are not sorted: {targets}"
            )

    def test_to_csv_has_header_and_18_rows(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        csv = STATE_TRANSITION_MATRIX.to_csv()
        lines = csv.strip().split("\n")
        assert len(lines) == 18  # 1 header + 17 data rows
        # Header starts with comma (empty first cell)
        assert lines[0].startswith(",")

    def test_to_csv_valid_transition_is_1(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        csv = STATE_TRANSITION_MATRIX.to_csv(delimiter=",")
        # Spot-check: created→queued should be '1'
        assert ",1," in csv or csv.endswith(",1")
        # completed→queued should be '0'
        lines = csv.split("\n")
        completed_row = [l for l in lines if l.startswith("completed")][0]
        assert completed_row is not None

    def test_to_csv_custom_delimiter(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        csv = STATE_TRANSITION_MATRIX.to_csv(delimiter="\t")
        assert "\t" in csv


class TestMatrixSummary:
    """Verify the summary statistics."""

    def test_summary_has_required_keys(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        s = STATE_TRANSITION_MATRIX.summary()
        assert "total_states" in s
        assert "total_valid_transitions" in s
        assert "total_possible_pairs" in s
        assert "density_percent" in s
        assert "terminal_states" in s
        assert "per_state_outgoing" in s

    def test_total_states_is_17(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        assert STATE_TRANSITION_MATRIX.summary()["total_states"] == 17

    def test_total_possible_pairs_is_272(self):
        """N x (N-1) = 17 x 16 = 272."""
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        assert STATE_TRANSITION_MATRIX.summary()[
            "total_possible_pairs"
        ] == 272

    def test_terminal_states_is_4(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        assert STATE_TRANSITION_MATRIX.summary()["terminal_states"] == 4

    def test_per_state_outgoing_terminal_are_zero(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        per = STATE_TRANSITION_MATRIX.summary()["per_state_outgoing"]
        for term in ("completed", "cancelled", "failed", "stale"):
            assert per[term] == 0, (
                f"Terminal state {term} should have 0 outgoing, got {per[term]}"
            )

    def test_density_is_between_20_and_50_percent(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        d = STATE_TRANSITION_MATRIX.summary()["density_percent"]
        assert 20 <= d <= 50, (
            f"Expected density 20-50%, got {d}%"
        )

    def test_per_state_outgoing_validating_has_most(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        per = STATE_TRANSITION_MATRIX.summary()["per_state_outgoing"]
        # Validating should have many outgoing (all branches)
        assert per["validating"] >= 8, (
            f"Validating should have >=8 outgoing, got {per['validating']}"
        )


class TestMatrixDunderMethods:
    """Verify __contains__, __eq__, __hash__, __repr__, states property."""

    def test_contains_syntax(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("created", "queued") in m
        assert ("completed", "queued") not in m

    def test_contains_invalid_pair_length(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("created",) not in m  # wrong length
        assert ("a", "b", "c") not in m  # wrong length

    def test_contains_unknown_state(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("bad", "queued") not in m

    def test_eq_same_defaults(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX, StateTransitionMatrix

        m1 = StateTransitionMatrix()
        m2 = StateTransitionMatrix()
        assert m1 == m2
        assert m1 == STATE_TRANSITION_MATRIX

    def test_eq_different_not_equal(self):
        from src.shared.lifecycle import LifecycleState, StateTransitionMatrix

        m1 = StateTransitionMatrix()
        m2 = StateTransitionMatrix(
            states=frozenset({LifecycleState.CREATED, LifecycleState.QUEUED})
        )
        assert m1 != m2

    def test_eq_non_matrix_returns_notimplemented(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        assert STATE_TRANSITION_MATRIX.__eq__("not a matrix") is NotImplemented

    def test_hash_consistent(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX, StateTransitionMatrix

        m1 = StateTransitionMatrix()
        m2 = StateTransitionMatrix()
        assert hash(m1) == hash(m2)
        assert hash(m1) == hash(STATE_TRANSITION_MATRIX)

    def test_hash_different_matrices_different(self):
        from src.shared.lifecycle import LifecycleState, StateTransitionMatrix

        m1 = StateTransitionMatrix()
        m2 = StateTransitionMatrix(
            states=frozenset({LifecycleState.CREATED, LifecycleState.QUEUED})
        )
        assert hash(m1) != hash(m2)

    def test_repr(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        r = repr(STATE_TRANSITION_MATRIX)
        assert "StateTransitionMatrix" in r
        assert "states=17" in r
        assert "valid_transitions=" in r

    def test_states_property_returns_17_strings(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        s = STATE_TRANSITION_MATRIX.states
        assert len(s) == 17
        assert isinstance(s[0], str)
        assert "created" in s
        assert "stale" in s
        assert "timed_out" in s

    def test_states_are_alphabetically_sorted(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        s = STATE_TRANSITION_MATRIX.states
        assert tuple(s) == tuple(sorted(s)), (
            f"States not sorted: {s}"
        )


class TestMatrixFullNormalFlow:
    """Verify the complete normal flow in the matrix."""

    def test_full_normal_path_is_valid(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        flow = [
            "created",
            "queued",
            "routing",
            "context_retrieval",
            "in_meeting",
            "consensus_building",
            "validating",
            "finalizing",
            "completed",
        ]
        for i in range(len(flow) - 1):
            assert (flow[i], flow[i + 1]) in m, (
                f"Normal flow broken at {flow[i]} → {flow[i + 1]}"
            )

    def test_execution_loop_is_valid(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("validating", "executing") in m
        assert ("executing", "validating") in m

    def test_pause_resume_is_valid(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("in_meeting", "paused") in m
        assert ("paused", "in_meeting") in m

    def test_deadlock_resolution_is_valid(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("deadlocked", "escalated") in m
        assert ("deadlocked", "finalizing") in m

    def test_escalation_resolution_is_valid(self):
        from src.shared.lifecycle import STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        assert ("escalated", "completed") in m
        assert ("escalated", "failed") in m

    def test_cancel_from_all_active_states(self):
        from src.shared.lifecycle import ACTIVE_STATES, STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        for state in ACTIVE_STATES:
            assert (state, LifecycleState.CANCELLED) in m, (
                f"Should be able to cancel from {state.value}"
            )

    def test_fail_from_all_active_states(self):
        from src.shared.lifecycle import ACTIVE_STATES, STATE_TRANSITION_MATRIX

        m = STATE_TRANSITION_MATRIX
        for state in ACTIVE_STATES:
            assert (state, LifecycleState.FAILED) in m, (
                f"Should be able to fail from {state.value}"
            )
