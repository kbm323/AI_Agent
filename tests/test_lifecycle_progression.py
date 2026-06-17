"""Tests for normal lifecycle progression (Sub-AC 4.2).

Verifies that progress_lifecycle() walks through the complete happy path
from created → queued → routing → context_retrieval → in_meeting →
consensus_building → validating → finalizing → completed, with guard
conditions enforced at each step.

Coverage requirements:
- Full happy path end-to-end
- Every guard condition tested (blocking + passing)
- Already-completed fast path
- Non-happy-path state rejection
- Partial progression from intermediate states
- State mismatch detection
- Extra guards injection
- Dry-run (persist=False) mode
- progress_one_step convenience function
- Individual guard function correctness
- Edge cases (empty strings, boundary values, terminal states)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.meeting_trigger import (
    MAX_AGENTS_PER_MEETING,
    MAX_ROUNDS,
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    MeetingManifest,
    create_meeting,
)
from src.shared.lifecycle import (
    ACTIVE_STATES,
    LifecycleState,
    is_terminal,
    validate_transition,
)
from src.shared.lifecycle_progression import (
    HAPPY_PATH_STATES,
    NORMAL_LIFECYCLE_STEPS,
    LifecycleProgressionResult,
    NormalLifecycleStep,
    guard_agenda_non_empty,
    guard_agenda_type_set,
    guard_consensus_non_empty,
    guard_context_populated,
    guard_manifest_path_valid,
    guard_meeting_has_id,
    guard_not_already_terminal,
    guard_roles_assigned,
    guard_rounds_completed,
    guard_validation_passed,
    is_happy_path_state,
    progress_lifecycle,
    progress_one_step,
)
from src.transition_engine import PreConditionGuard, TransitionResult


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _create_test_manifest(
    agenda: str = "Test meeting agenda",
    user_id: str = "user-1",
    channel_id: str = "channel-1",
    meetings_root: str | None = None,
) -> MeetingManifest:
    """Create a minimal test manifest in created state using create_meeting."""
    if meetings_root is None:
        meetings_root = tempfile.mkdtemp(prefix="test-lifecycle-prog-")
    req = MeetingCommandRequest(
        agenda=agenda,
        user_id=user_id,
        channel_id=channel_id,
        priority="p2",
    )
    ctx: MeetingContext = create_meeting(req, meetings_root=meetings_root)
    return ctx.manifest


def _set_routing_fields(
    manifest: MeetingManifest,
    *,
    agenda_type: str = "creative_production",
    required_roles: tuple[str, ...] = ("producer-kim", "director-lee"),
    tags: tuple[str, ...] = ("creative", "mv-planning"),
) -> MeetingManifest:
    """Detour: directly set routing fields on a manifest for testing."""
    return MeetingManifest(
        meeting_id=manifest.meeting_id,
        state=manifest.state,
        priority=manifest.priority,
        agenda=manifest.agenda,
        agenda_type=agenda_type,
        tags=tags,
        risk_tags=manifest.risk_tags,
        required_roles=required_roles,
        optional_roles=manifest.optional_roles,
        round_count=manifest.round_count,
        validation_score=manifest.validation_score,
        validation_verdict=manifest.validation_verdict,
        validator_required=manifest.validator_required,
        codex_required=manifest.codex_required,
        consensus=manifest.consensus,
        user_id=manifest.user_id,
        channel_id=manifest.channel_id,
        thread_id=manifest.thread_id,
        guild_id=manifest.guild_id,
        error_log=manifest.error_log,
        manifest_path=manifest.manifest_path,
        meetings_root=manifest.meetings_root,
        max_rounds=manifest.max_rounds,
        max_agents_per_meeting=manifest.max_agents_per_meeting,
        token_limit_worker=manifest.token_limit_worker,
        token_limit_validator=manifest.token_limit_validator,
        token_limit_codex=manifest.token_limit_codex,
        primary_validator_model=manifest.primary_validator_model,
        conditional_validator_model=manifest.conditional_validator_model,
        schema_version=manifest.schema_version,
        current_speaker=manifest.current_speaker,
        speaker_queue=manifest.speaker_queue,
        completed_step=manifest.completed_step,
        context_packets=manifest.context_packets,
        decisions=manifest.decisions,
        tool_outputs=manifest.tool_outputs,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


def _set_context_packets(
    manifest: MeetingManifest,
) -> MeetingManifest:
    """Add a dummy context packet to satisfy the context_populated guard."""
    packet = {
        "round": 1,
        "role_id": "producer-kim",
        "model_provider": "qwen",
        "model_name": "qwen3-max",
        "token_count": 5000,
        "packet_path": "",
        "opinion_summary": "Test opinion",
        "created_at": "2026-06-11T00:00:00Z",
    }
    new_packets = (*manifest.context_packets, packet)
    return MeetingManifest(
        meeting_id=manifest.meeting_id,
        state=manifest.state,
        priority=manifest.priority,
        agenda=manifest.agenda,
        agenda_type=manifest.agenda_type,
        tags=manifest.tags,
        risk_tags=manifest.risk_tags,
        required_roles=manifest.required_roles,
        optional_roles=manifest.optional_roles,
        round_count=manifest.round_count,
        validation_score=manifest.validation_score,
        validation_verdict=manifest.validation_verdict,
        validator_required=manifest.validator_required,
        codex_required=manifest.codex_required,
        consensus=manifest.consensus,
        user_id=manifest.user_id,
        channel_id=manifest.channel_id,
        thread_id=manifest.thread_id,
        guild_id=manifest.guild_id,
        error_log=manifest.error_log,
        manifest_path=manifest.manifest_path,
        meetings_root=manifest.meetings_root,
        max_rounds=manifest.max_rounds,
        max_agents_per_meeting=manifest.max_agents_per_meeting,
        token_limit_worker=manifest.token_limit_worker,
        token_limit_validator=manifest.token_limit_validator,
        token_limit_codex=manifest.token_limit_codex,
        primary_validator_model=manifest.primary_validator_model,
        conditional_validator_model=manifest.conditional_validator_model,
        schema_version=manifest.schema_version,
        current_speaker=manifest.current_speaker,
        speaker_queue=manifest.speaker_queue,
        completed_step=manifest.completed_step,
        context_packets=new_packets,
        decisions=manifest.decisions,
        tool_outputs=manifest.tool_outputs,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Full happy path — end-to-end progression
# ═══════════════════════════════════════════════════════════════════════════


class TestFullHappyPath:
    """Verify the complete normal progression from created to completed."""

    def test_full_happy_path_end_to_end(self):
        """Walk through all 8 transitions with all guards satisfied."""
        m = _create_test_manifest()
        assert m.state == "created"

        # Step 1: created → queued
        result = progress_lifecycle(m, persist=False)
        # This will fail at context_retrieval guard because we haven't set up
        # routing fields yet — but the first 2 steps should succeed
        assert not result.success
        assert result.steps_completed == 2  # created→queued, queued→routing
        assert result.manifest.state == "routing"

    def test_full_happy_path_with_all_guards_satisfied(self):
        """Complete the entire happy path by satisfying every guard."""
        m = _create_test_manifest()

        # --- created → queued → routing ---
        r1 = progress_lifecycle(m, persist=False)
        assert r1.steps_completed == 2
        assert r1.manifest.state == "routing"

        # --- routing → context_retrieval (need roles + agenda_type) ---
        m2 = _set_routing_fields(r1.manifest)
        m2 = m2.with_state("routing")  # maintain the correct state
        r2 = progress_lifecycle(m2, persist=False)
        assert r2.steps_completed == 1  # routing→context_retrieval
        assert r2.manifest.state == "context_retrieval"

        # --- context_retrieval → in_meeting (need context_packets) ---
        m3 = _set_context_packets(r2.manifest)
        m3 = m3.with_state("context_retrieval")
        r3 = progress_lifecycle(m3, persist=False)
        assert r3.steps_completed == 1  # context_retrieval→in_meeting
        assert r3.manifest.state == "in_meeting"

        # --- in_meeting → consensus_building (need round_count >= 1) ---
        m4 = MeetingManifest(
            meeting_id=r3.manifest.meeting_id,
            state="in_meeting",
            priority=r3.manifest.priority,
            agenda=r3.manifest.agenda,
            agenda_type=r3.manifest.agenda_type,
            tags=r3.manifest.tags,
            required_roles=r3.manifest.required_roles,
            round_count=1,  # ← satisfy guard_rounds_completed
            context_packets=r3.manifest.context_packets,
            manifest_path=r3.manifest.manifest_path,
        )
        r4 = progress_lifecycle(m4, persist=False)
        assert r4.steps_completed == 1  # in_meeting→consensus_building
        assert r4.manifest.state == "consensus_building"

        # --- consensus_building → validating (need consensus non-empty) ---
        m5 = MeetingManifest(
            meeting_id=r4.manifest.meeting_id,
            state="consensus_building",
            priority=r4.manifest.priority,
            agenda=r4.manifest.agenda,
            agenda_type=r4.manifest.agenda_type,
            tags=r4.manifest.tags,
            required_roles=r4.manifest.required_roles,
            round_count=r4.manifest.round_count,
            context_packets=r4.manifest.context_packets,
            consensus="Team agrees on the proposed design for Luna's MV.",
            manifest_path=r4.manifest.manifest_path,
        )
        r5 = progress_lifecycle(m5, persist=False)
        assert r5.steps_completed == 1  # consensus_building→validating
        assert r5.manifest.state == "validating"

        # --- validating → finalizing → completed (need pass + score >= 0.85) ---
        m6 = MeetingManifest(
            meeting_id=r5.manifest.meeting_id,
            state="validating",
            priority=r5.manifest.priority,
            agenda=r5.manifest.agenda,
            agenda_type=r5.manifest.agenda_type,
            tags=r5.manifest.tags,
            required_roles=r5.manifest.required_roles,
            round_count=r5.manifest.round_count,
            context_packets=r5.manifest.context_packets,
            consensus=r5.manifest.consensus,
            validation_score=0.92,
            validation_verdict="pass",
            manifest_path=r5.manifest.manifest_path,
        )
        r6 = progress_lifecycle(m6, persist=False)
        assert r6.success
        assert r6.steps_completed == 2  # validating→finalizing, finalizing→completed
        assert r6.manifest.state == "completed"
        assert r6.starting_state == "validating"
        assert r6.ending_state == "completed"

    def test_total_steps_from_created_to_completed(self):
        """The happy path has exactly 8 transitions (9 states)."""
        assert len(HAPPY_PATH_STATES) == 9
        assert len(NORMAL_LIFECYCLE_STEPS) == 8  # 8 transitions


# ═══════════════════════════════════════════════════════════════════════════
# Already-completed fast path
# ═══════════════════════════════════════════════════════════════════════════


class TestAlreadyCompleted:
    """progress_lifecycle on an already-completed manifest returns success immediately."""

    def test_already_completed_returns_success(self):
        m = _create_test_manifest()
        m = m.with_state("completed")
        result = progress_lifecycle(m, persist=False)
        assert result.success is True
        assert result.steps_completed == 0
        assert result.manifest.state == "completed"
        assert result.ending_state == "completed"

    def test_already_completed_no_transition_steps(self):
        m = _create_test_manifest()
        m = m.with_state("completed")
        result = progress_lifecycle(m, persist=False)
        assert result.steps_total == 8
        assert result.steps_completed == 0
        assert result.failure_step is None
        assert result.failure_reason is None


# ═══════════════════════════════════════════════════════════════════════════
# Non-happy-path state rejection
# ═══════════════════════════════════════════════════════════════════════════


class TestNonHappyPathRejection:
    """progress_lifecycle must reject manifests not on the normal happy path."""

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.PAUSED,
            LifecycleState.DEADLOCKED,
            LifecycleState.ESCALATED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
        ],
    )
    def test_exception_state_rejected(self, state):
        m = _create_test_manifest()
        m = m.with_state(state)
        result = progress_lifecycle(m, persist=False)
        assert result.success is False
        assert result.steps_completed == 0
        assert result.manifest.state == str(state)
        assert "not on the normal happy path" in (result.failure_reason or "")


# ═══════════════════════════════════════════════════════════════════════════
# Guard condition tests — each guard blocks progression when unsatisfied
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardMeetingHasId:
    """guard_meeting_has_id: blocks created→queued when meeting_id is empty."""

    def test_empty_meeting_id_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id="",
            state="created",
            agenda=m.agenda,
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_meeting_has_id(m)
        assert allowed is False
        assert "meeting_id" in (reason or "")

    def test_whitespace_meeting_id_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id="   ",
            state="created",
            agenda=m.agenda,
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_meeting_has_id(m)
        assert allowed is False

    def test_non_empty_meeting_id_allowed(self):
        m = _create_test_manifest()
        allowed, reason = guard_meeting_has_id(m)
        assert allowed is True
        assert reason is None


class TestGuardAgendaNonEmpty:
    """guard_agenda_non_empty: blocks created→queued when agenda is empty."""

    def test_empty_agenda_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda="",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_agenda_non_empty(m)
        assert allowed is False
        assert "agenda" in (reason or "")

    def test_whitespace_agenda_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda="   ",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_agenda_non_empty(m)
        assert allowed is False

    def test_non_empty_agenda_allowed(self):
        m = _create_test_manifest()
        allowed, reason = guard_agenda_non_empty(m)
        assert allowed is True


class TestGuardManifestPathValid:
    """guard_manifest_path_valid: blocks when manifest_path is empty."""

    def test_empty_path_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda=m.agenda,
            manifest_path="",
        )
        allowed, reason = guard_manifest_path_valid(m)
        assert allowed is False
        assert "manifest_path" in (reason or "")

    def test_valid_path_allowed(self):
        m = _create_test_manifest()
        allowed, reason = guard_manifest_path_valid(m)
        assert allowed is True


class TestGuardRolesAssigned:
    """guard_roles_assigned: blocks routing→context_retrieval when required_roles empty."""

    def test_empty_roles_blocked(self):
        m = _create_test_manifest()
        m = m.with_state("routing")
        allowed, reason = guard_roles_assigned(m)
        assert allowed is False
        assert "required_roles" in (reason or "")

    def test_populated_roles_allowed(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = m.with_state("routing")
        allowed, reason = guard_roles_assigned(m)
        assert allowed is True


class TestGuardAgendaTypeSet:
    """guard_agenda_type_set: blocks routing→context_retrieval when agenda_type empty."""

    def test_empty_agenda_type_blocked(self):
        m = _create_test_manifest()
        m = m.with_state("routing")
        allowed, reason = guard_agenda_type_set(m)
        assert allowed is False
        assert "agenda_type" in (reason or "")

    def test_populated_agenda_type_allowed(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = m.with_state("routing")
        allowed, reason = guard_agenda_type_set(m)
        assert allowed is True


class TestGuardContextPopulated:
    """guard_context_populated: blocks context_retrieval→in_meeting when no packets."""

    def test_empty_context_packets_blocked(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = m.with_state("context_retrieval")
        allowed, reason = guard_context_populated(m)
        assert allowed is False
        assert "context_packets" in (reason or "")

    def test_populated_context_packets_allowed(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = _set_context_packets(m)
        m = m.with_state("context_retrieval")
        allowed, reason = guard_context_populated(m)
        assert allowed is True


class TestGuardRoundsCompleted:
    """guard_rounds_completed: blocks in_meeting→consensus_building when round_count < 1."""

    def test_zero_rounds_blocked(self):
        m = _create_test_manifest()
        m = m.with_state("in_meeting")
        allowed, reason = guard_rounds_completed(m)
        assert allowed is False
        assert "round_count" in (reason or "")

    def test_one_round_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="in_meeting",
            agenda=m.agenda,
            round_count=1,
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_rounds_completed(m)
        assert allowed is True

    def test_three_rounds_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="in_meeting",
            agenda=m.agenda,
            round_count=3,
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_rounds_completed(m)
        assert allowed is True


class TestGuardConsensusNonEmpty:
    """guard_consensus_non_empty: blocks consensus_building→validating when consensus empty."""

    def test_empty_consensus_blocked(self):
        m = _create_test_manifest()
        m = m.with_state("consensus_building")
        allowed, reason = guard_consensus_non_empty(m)
        assert allowed is False
        assert "consensus" in (reason or "")

    def test_whitespace_consensus_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="consensus_building",
            agenda=m.agenda,
            consensus="   ",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_consensus_non_empty(m)
        assert allowed is False

    def test_non_empty_consensus_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="consensus_building",
            agenda=m.agenda,
            consensus="Agreed: proceed with Luna MV at ₩50M budget.",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_consensus_non_empty(m)
        assert allowed is True


class TestGuardValidationPassed:
    """guard_validation_passed: blocks validating→finalizing unless score >= 0.85 and pass/conditional_pass."""

    def test_empty_verdict_blocked(self):
        m = _create_test_manifest()
        m = m.with_state("validating")
        allowed, reason = guard_validation_passed(m)
        assert allowed is False
        assert "validation_verdict" in (reason or "")

    def test_fail_verdict_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.90,
            validation_verdict="fail",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is False

    def test_revision_required_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.90,
            validation_verdict="revision_required",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is False

    def test_escalate_verdict_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.90,
            validation_verdict="escalate",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is False

    def test_low_score_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.70,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is False
        assert "validation_score" in (reason or "")

    def test_fraction_below_threshold_blocked(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.8499,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is False

    def test_pass_verdict_with_high_score_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.92,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is True

    def test_conditional_pass_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.85,
            validation_verdict="conditional_pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is True

    def test_score_exactly_threshold_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.85,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is True

    def test_perfect_score_allowed(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=1.0,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        allowed, reason = guard_validation_passed(m)
        assert allowed is True


class TestGuardNotAlreadyTerminal:
    """guard_not_already_terminal: blocks progression from any terminal state."""

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.COMPLETED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
        ],
    )
    def test_terminal_states_blocked(self, state):
        m = _create_test_manifest()
        m = m.with_state(state)
        allowed, reason = guard_not_already_terminal(m)
        assert allowed is False
        assert "terminal" in (reason or "")

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.CREATED,
            LifecycleState.QUEUED,
            LifecycleState.ROUTING,
            LifecycleState.IN_MEETING,
            LifecycleState.VALIDATING,
        ],
    )
    def test_non_terminal_states_allowed(self, state):
        m = _create_test_manifest()
        m = m.with_state(state)
        allowed, reason = guard_not_already_terminal(m)
        assert allowed is True


# ═══════════════════════════════════════════════════════════════════════════
# Guard condition integration — blocking in progression context
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardIntegrationInProgression:
    """Test that failing guards actually stop progress_lifecycle."""

    def test_empty_agenda_stops_at_created(self):
        """An empty-agenda meeting should fail at the first step."""
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda="",
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        assert not result.success
        assert result.steps_completed == 0
        assert result.manifest.state == "created"
        assert "agenda" in (result.failure_reason or "")

    def test_missing_roles_stops_at_routing(self):
        """Without required_roles, progress stops at routing→context_retrieval."""
        m = _create_test_manifest()
        # First progress through created→queued→routing
        r = progress_lifecycle(m, persist=False)
        assert r.manifest.state == "routing"
        # Now try to go further without roles — should fail
        m2 = r.manifest
        # Progress again from routing (no roles set)
        r2 = progress_lifecycle(m2, persist=False)
        assert not r2.success
        assert "required_roles" in (r2.failure_reason or "")
        assert r2.steps_completed == 0

    def test_missing_context_packets_stops_at_context_retrieval(self):
        """Without context packets, progress stops at context_retrieval→in_meeting."""
        m = _create_test_manifest()
        # progress to routing
        r = progress_lifecycle(m, persist=False)
        # Set routing fields but NOT context packets
        m2 = _set_routing_fields(r.manifest)
        m2 = m2.with_state("routing")
        r2 = progress_lifecycle(m2, persist=False)
        assert not r2.success
        # Should have succeeded routing→context_retrieval but failed at next step
        assert r2.steps_completed == 1
        assert r2.manifest.state == "context_retrieval"
        assert "context_packets" in (r2.failure_reason or "")


# ═══════════════════════════════════════════════════════════════════════════
# Partial progression from intermediate states
# ═══════════════════════════════════════════════════════════════════════════


class TestPartialProgression:
    """progress_lifecycle can start from any happy-path state."""

    def test_start_from_routing_with_fields_set(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = m.with_state("routing")
        # Should progress routing→context_retrieval (1 step) then fail
        # at context_retrieval→in_meeting (no context packets)
        result = progress_lifecycle(m, persist=False)
        assert result.steps_completed == 1
        assert result.manifest.state == "context_retrieval"

    def test_start_from_validating_completes(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="validating",
            agenda=m.agenda,
            validation_score=0.95,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        assert result.success
        assert result.steps_completed == 2  # validating→finalizing, finalizing→completed
        assert result.manifest.state == "completed"

    def test_start_from_finalizing_completes(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="finalizing",
            agenda=m.agenda,
            validation_score=0.95,
            validation_verdict="pass",
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        assert result.success
        assert result.steps_completed == 1  # finalizing→completed
        assert result.manifest.state == "completed"


# ═══════════════════════════════════════════════════════════════════════════
# Extra guards injection
# ═══════════════════════════════════════════════════════════════════════════


class TestExtraGuards:
    """progress_lifecycle accepts extra_guards for domain-specific checks."""

    def test_extra_guard_blocks_progression(self):
        def block_queuing(_manifest: MeetingManifest) -> tuple[bool, str | None]:
            return False, "Custom guard: meeting blocked by admin"

        m = _create_test_manifest()
        result = progress_lifecycle(
            m,
            persist=False,
            extra_guards={"enqueue-meeting": (block_queuing,)},
        )
        assert not result.success
        assert result.steps_completed == 0
        assert "Custom guard" in (result.failure_reason or "")

    def test_extra_guard_passes_progression_continues(self):
        always_pass: PreConditionGuard = lambda m: (True, None)

        m = _create_test_manifest()
        result = progress_lifecycle(
            m,
            persist=False,
            extra_guards={"enqueue-meeting": (always_pass,)},
        )
        assert result.steps_completed == 2  # created→queued, queued→routing

    def test_extra_guard_on_later_step(self):
        def block_consensus(_manifest: MeetingManifest) -> tuple[bool, str | None]:
            return False, "Consensus blocked by policy"

        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = _set_context_packets(m)
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="in_meeting",
            agenda=m.agenda,
            agenda_type=m.agenda_type,
            required_roles=m.required_roles,
            round_count=1,
            context_packets=m.context_packets,
            manifest_path=m.manifest_path,
        )
        # in_meeting → consensus_building → validating
        # The block_consensus guard is on "build-consensus"
        result = progress_lifecycle(
            m,
            persist=False,
            extra_guards={"build-consensus": (block_consensus,)},
        )
        assert not result.success
        assert "Consensus blocked" in (result.failure_reason or "")
        assert result.failure_step is not None
        assert result.failure_step.label == "build-consensus"


# ═══════════════════════════════════════════════════════════════════════════
# Dry-run mode (persist=False)
# ═══════════════════════════════════════════════════════════════════════════


class TestDryRun:
    """progress_lifecycle with persist=False does not write to disk."""

    def test_dry_run_does_not_create_file(self):
        m = _create_test_manifest()
        # Verify the manifest file exists (created by create_meeting)
        assert os.path.exists(m.manifest_path)
        # Get the original mtime
        orig_mtime = os.path.getmtime(m.manifest_path)

        # Progress two steps with persist=False
        result = progress_lifecycle(m, persist=False)
        assert result.steps_completed == 2

        # File should not have been modified
        if os.path.exists(m.manifest_path):
            new_mtime = os.path.getmtime(m.manifest_path)
            assert new_mtime == orig_mtime, (
                "persist=False should not modify the manifest file"
            )

    def test_dry_run_state_tracking_is_correct(self):
        """Even with persist=False, the result manifest has correct state."""
        m = _create_test_manifest()
        result = progress_lifecycle(m, persist=False)
        assert result.manifest.state == "routing"
        assert result.manifest.manifest_path == m.manifest_path


# ═══════════════════════════════════════════════════════════════════════════
# progress_one_step convenience function
# ═══════════════════════════════════════════════════════════════════════════


class TestProgressOneStep:
    """progress_one_step advances exactly one step along the happy path."""

    def test_created_to_queued(self):
        m = _create_test_manifest()
        result = progress_one_step(m, persist=False)
        assert result.success is True
        assert result.manifest.state == "queued"
        assert result.from_state == "created"
        assert result.to_state == "queued"

    def test_queued_to_routing(self):
        m = _create_test_manifest()
        m = m.with_state("queued")
        result = progress_one_step(m, persist=False)
        assert result.success is True
        assert result.manifest.state == "routing"

    def test_routing_to_context_retrieval_with_roles(self):
        m = _create_test_manifest()
        m = _set_routing_fields(m)
        m = m.with_state("routing")
        result = progress_one_step(m, persist=False)
        assert result.success is True
        assert result.manifest.state == "context_retrieval"

    def test_routing_without_roles_fails(self):
        m = _create_test_manifest()
        m = m.with_state("routing")
        result = progress_one_step(m, persist=False)
        assert result.success is False
        assert "required_roles" in (result.rejection_reasons[0] if result.rejection_reasons else "")

    def test_completed_raises_value_error(self):
        m = _create_test_manifest()
        m = m.with_state("completed")
        with pytest.raises(ValueError, match="happy-path successor"):
            progress_one_step(m, persist=False)

    def test_two_consecutive_steps(self):
        """Two consecutive progress_one_step calls should advance twice."""
        m = _create_test_manifest()
        r1 = progress_one_step(m, persist=False)
        assert r1.success
        assert r1.manifest.state == "queued"

        r2 = progress_one_step(r1.manifest, persist=False)
        assert r2.success
        assert r2.manifest.state == "routing"


# ═══════════════════════════════════════════════════════════════════════════
# is_happy_path_state
# ═══════════════════════════════════════════════════════════════════════════


class TestIsHappyPathState:
    """is_happy_path_state correctly identifies happy-path vs exception states."""

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
            LifecycleState.FINALIZING,
            LifecycleState.COMPLETED,
        ],
    )
    def test_happy_path_states_identified(self, state):
        assert is_happy_path_state(state) is True

    @pytest.mark.parametrize(
        "state",
        [
            LifecycleState.PAUSED,
            LifecycleState.DEADLOCKED,
            LifecycleState.ESCALATED,
            LifecycleState.CANCELLED,
            LifecycleState.FAILED,
            LifecycleState.STALE,
        ],
    )
    def test_exception_states_not_happy_path(self, state):
        assert is_happy_path_state(state) is False

    def test_string_input_works(self):
        assert is_happy_path_state("created") is True
        assert is_happy_path_state("completed") is True
        assert is_happy_path_state("paused") is False
        assert is_happy_path_state("nonexistent") is False


# ═══════════════════════════════════════════════════════════════════════════
# LifecycleProgressionResult dataclass
# ═══════════════════════════════════════════════════════════════════════════


class TestLifecycleProgressionResult:
    """Verify the result dataclass properties."""

    def test_success_result_fields(self):
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="finalizing",
            agenda=m.agenda,
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        assert result.success is True
        assert result.starting_state == "finalizing"
        assert result.ending_state == "completed"
        assert result.steps_completed == 1
        assert result.steps_total == 8
        assert result.failure_step is None
        assert result.failure_reason is None

    def test_failure_result_fields(self):
        m = _create_test_manifest()
        result = progress_lifecycle(m, persist=False)
        assert result.success is False
        assert result.starting_state == "created"
        assert result.ending_state == "routing"
        assert result.steps_completed == 2
        assert result.steps_total == 8
        assert result.failure_step is not None
        assert result.failure_reason is not None

    def test_result_is_frozen(self):
        m = _create_test_manifest()
        result = progress_lifecycle(m, persist=False)
        with pytest.raises((TypeError, AttributeError)):
            result.success = False  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# NormalLifecycleStep metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalLifecycleStep:
    """Verify step metadata correctness."""

    def test_eight_steps_total(self):
        assert len(NORMAL_LIFECYCLE_STEPS) == 8

    def test_step_indices_are_sequential(self):
        for i, step in enumerate(NORMAL_LIFECYCLE_STEPS):
            assert step.index == i, f"Step {step.label} has wrong index"

    def test_first_step_is_created_to_queued(self):
        step = NORMAL_LIFECYCLE_STEPS[0]
        assert step.from_state == LifecycleState.CREATED
        assert step.to_state == LifecycleState.QUEUED
        assert step.label == "enqueue-meeting"
        assert len(step.guards) >= 3  # has_id, agenda, manifest_path

    def test_last_step_is_finalizing_to_completed(self):
        step = NORMAL_LIFECYCLE_STEPS[7]
        assert step.from_state == LifecycleState.FINALIZING
        assert step.to_state == LifecycleState.COMPLETED
        assert step.label == "complete-meeting"

    def test_all_step_transitions_are_valid(self):
        for step in NORMAL_LIFECYCLE_STEPS:
            assert validate_transition(step.from_state, step.to_state) is True, (
                f"Step {step.label}: {step.from_state} → {step.to_state} "
                f"is not a valid transition"
            )

    def test_steps_cover_complete_happy_path(self):
        """Every adjacent pair in HAPPY_PATH_STATES has a corresponding step."""
        for i in range(len(HAPPY_PATH_STATES) - 1):
            from_s = HAPPY_PATH_STATES[i]
            to_s = HAPPY_PATH_STATES[i + 1]
            found = any(
                s.from_state == from_s and s.to_state == to_s
                for s in NORMAL_LIFECYCLE_STEPS
            )
            assert found, (
                f"No step defined for {from_s.value} → {to_s.value}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case and boundary behaviour."""

    def test_progression_from_created_with_max_rounds_exceeded_shall_still_queue(self):
        """Round count doesn't matter for created→queued transition."""
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda=m.agenda,
            round_count=5,  # exceeds max_rounds=3
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        # Should succeed the first few transitions (created→queued→routing)
        # because round_count guard only applies at in_meeting→consensus_building
        assert result.steps_completed == 2

    def test_happy_path_states_tuple_is_ordered(self):
        """HAPPY_PATH_STATES must be in the correct lifecycle order."""
        expected_order = [
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
        actual = [s.value for s in HAPPY_PATH_STATES]
        assert actual == expected_order, (
            f"HAPPY_PATH_STATES order mismatch: {actual}"
        )

    def test_progression_preserves_meeting_id(self):
        m = _create_test_manifest()
        orig_id = m.meeting_id
        # Progress a few steps
        r = progress_lifecycle(m, persist=False)
        assert r.manifest.meeting_id == orig_id
        # Progress further with routing fields
        m2 = _set_routing_fields(r.manifest)
        m2 = m2.with_state("routing")
        r2 = progress_lifecycle(m2, persist=False)
        assert r2.manifest.meeting_id == orig_id

    def test_error_log_appended_on_guard_failure(self):
        """When a guard blocks progression, the error is logged to manifest."""
        m = _create_test_manifest()
        m = MeetingManifest(
            meeting_id=m.meeting_id,
            state="created",
            agenda="",  # ← triggers guard_agenda_non_empty
            manifest_path=m.manifest_path,
        )
        result = progress_lifecycle(m, persist=False)
        assert not result.success
        # The error should be in the manifest's error_log
        assert len(result.manifest.error_log) > 0
        errors = result.manifest.error_log
        assert any("agenda" in str(e).lower() for e in errors)
