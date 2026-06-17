"""Manifest Serializer for the multi-agent meeting system.

Sub-AC 4.4.1: Serialize meeting state (round, speaker, context packets,
decisions, tool outputs) to a structured manifest file on each transition.
All serialization goes through the MeetingManifest's to_dict/to_json methods;
this module provides the higher-level API for building rich state entries
and appending them to the manifest with atomic persistence.

Architecture
------------

Every operation that modifies the manifest follows the pattern:

1. **Build an entry dict** — structured dict with the required keys for
   that entry type (context packet, decision, tool output).

2. **Append to manifest** — a new frozen ``MeetingManifest`` is produced
   with the entry appended to the appropriate tuple field.

3. **Persist** — the manifest is written to disk via the transition hook
   dispatch mechanism (``dispatch_transition_hooks()`` from Sub-AC 4.4.3),
   which fires all registered hooks including the built-in persistence
   hook, satisfying the Seed constraint that *"All state transitions
   persist to manifest.json before external calls."*

The module also provides factory functions that produce entry dicts with
standard schemas and timestamps, ensuring consistency across all producers.

Usage::

    from src.manifest_serializer import (
        serialize_meeting_state,
        build_context_packet_entry,
        build_decision_entry,
        build_tool_output_entry,
        append_context_packet,
        append_decision,
        append_tool_output,
        set_speaker,
    )

    # Add a context packet entry
    entry = build_context_packet_entry(
        round_num=1,
        role_id="producer-kim",
        model_provider="qwen",
        model_name="qwen3-max",
        token_count=8500,
        packet_path="/path/to/round_1/producer-kim.json",
    )
    manifest = append_context_packet(manifest, entry)

    # Set current speaker
    manifest = set_speaker(manifest, "producer-kim",
                           speaker_queue=("producer-kim", "director-lee", "finance-park"))

    # Add a decision
    decision = build_decision_entry(
        round_num=1,
        decision_id="d_001",
        role_id="producer-kim",
        content="Budget for Luna's MV: ₩50M",
    )
    manifest = append_decision(manifest, decision)

Modules:
    build_context_packet_entry: Create a structured context-packet dict.
    build_decision_entry: Create a structured decision dict.
    build_tool_output_entry: Create a structured tool-output dict.
    append_context_packet: Append a context packet to the manifest.
    append_decision: Append a decision to the manifest.
    append_tool_output: Append a tool output to the manifest.
    set_speaker: Set the current speaker and optionally the speaker queue.
    serialize_meeting_state: Write the full manifest to disk.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.meeting_trigger import MeetingManifest, update_manifest
from src.transition_persistence_hook import dispatch_transition_hooks

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Timestamp helper ──────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Entry builders ────────────────────────────────────────────────────────


def build_context_packet_entry(
    round_num: int,
    role_id: str,
    *,
    model_provider: str = "",
    model_name: str = "",
    token_count: int = 0,
    packet_path: str = "",
    opinion_summary: str = "",
    created_at: Optional[str] = None,
) -> dict:
    """Build a structured context-packet entry dict.

    Each entry records a context packet that was generated and sent to
    a worker role during a meeting round.  These are accumulated in the
    manifest's ``context_packets`` tuple across all rounds.

    Args:
        round_num: Meeting round number (1-based).
        role_id: Unique role identifier (kebab-case).
        model_provider: LLM provider name (e.g. 'qwen', 'deepseek').
        model_name: Primary model identifier (e.g. 'qwen3-max').
        token_count: Token count of the context packet.
        packet_path: Relative path to the packet JSON file on disk.
        opinion_summary: Short summary of the worker's opinion.
        created_at: ISO 8601 timestamp (auto-generated if omitted).

    Returns:
        A dict with keys: round, role_id, model_provider, model_name,
        token_count, packet_path, opinion_summary, created_at.
    """
    return {
        "round": round_num,
        "role_id": role_id,
        "model_provider": model_provider,
        "model_name": model_name,
        "token_count": token_count,
        "packet_path": packet_path,
        "opinion_summary": opinion_summary,
        "created_at": created_at or _utc_now_iso(),
    }


def build_decision_entry(
    round_num: int,
    decision_id: str,
    role_id: str,
    content: str,
    *,
    superseded_by: str = "",
    created_at: Optional[str] = None,
) -> dict:
    """Build a structured decision entry dict.

    Decisions are append-only per the Seed constraint.  When a decision
    is superseded by a later one, the ``superseded_by`` field references
    the new decision's ``decision_id`` — the original record is never
    removed or mutated.

    Args:
        round_num: Meeting round number (1-based).
        decision_id: Unique decision identifier within the meeting.
        role_id: Role that made the decision.
        content: Decision text.
        superseded_by: ID of the decision that supersedes this one
                       (empty if the decision stands).
        created_at: ISO 8601 timestamp (auto-generated if omitted).

    Returns:
        A dict with keys: round, decision_id, role_id, content,
        superseded_by, created_at.
    """
    return {
        "round": round_num,
        "decision_id": decision_id,
        "role_id": role_id,
        "content": content,
        "superseded_by": superseded_by,
        "created_at": created_at or _utc_now_iso(),
    }


def build_tool_output_entry(
    round_num: int,
    execution_id: str,
    action_type: str,
    role_id: str,
    status: str,
    output: str = "",
    *,
    risk_level: str = "low",
    human_approved: Optional[bool] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Build a structured tool-output entry dict.

    Records the result of an OpenClaw tool-use execution.  High-risk
    actions must have ``human_approved`` explicitly set — the field
    is ``None`` (unknown) for low-risk actions that bypass the
    human-in-the-loop gate.

    Args:
        round_num: Meeting round number (1-based).
        execution_id: Unique execution identifier from OpenClaw.
        action_type: Type of execution action (e.g. 'deploy', 'email').
        role_id: Role that requested the execution.
        status: Execution status (e.g. 'success', 'failed', 'timeout').
        output: Execution output or error message.
        risk_level: 'low', 'medium', 'high', or 'critical'.
        human_approved: True if human approved, False if rejected,
                        None if not applicable.
        created_at: ISO 8601 timestamp (auto-generated if omitted).

    Returns:
        A dict with keys: round, execution_id, action_type, role_id,
        status, output, risk_level, human_approved, created_at.
    """
    return {
        "round": round_num,
        "execution_id": execution_id,
        "action_type": action_type,
        "role_id": role_id,
        "status": status,
        "output": output,
        "risk_level": risk_level,
        "human_approved": human_approved,
        "created_at": created_at or _utc_now_iso(),
    }


# ── Manifest mutation helpers ─────────────────────────────────────────────


def append_context_packet(
    manifest: MeetingManifest,
    entry: dict,
    *,
    persist: bool = True,
) -> MeetingManifest:
    """Append a context-packet entry to the manifest and optionally persist.

    Args:
        manifest: Current meeting manifest.
        entry: A context-packet dict (use ``build_context_packet_entry()``).
        persist: If True, write to disk before returning.

    Returns:
        A new ``MeetingManifest`` with the entry appended.
    """
    new_packets = (*manifest.context_packets, entry)
    new_manifest = MeetingManifest(
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
        updated_at=_utc_now_iso(),
    )
    if persist:
        return dispatch_transition_hooks(new_manifest, "context_packet")
    return new_manifest


def append_decision(
    manifest: MeetingManifest,
    entry: dict,
    *,
    persist: bool = True,
) -> MeetingManifest:
    """Append a decision entry to the manifest and optionally persist.

    Args:
        manifest: Current meeting manifest.
        entry: A decision dict (use ``build_decision_entry()``).
        persist: If True, write to disk before returning.

    Returns:
        A new ``MeetingManifest`` with the entry appended.
    """
    new_decisions = (*manifest.decisions, entry)
    new_manifest = MeetingManifest(
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
        context_packets=manifest.context_packets,
        decisions=new_decisions,
        tool_outputs=manifest.tool_outputs,
        created_at=manifest.created_at,
        updated_at=_utc_now_iso(),
    )
    if persist:
        return dispatch_transition_hooks(new_manifest, "decision_commit")
    return new_manifest


def append_tool_output(
    manifest: MeetingManifest,
    entry: dict,
    *,
    persist: bool = True,
) -> MeetingManifest:
    """Append a tool-output entry to the manifest and optionally persist.

    Args:
        manifest: Current meeting manifest.
        entry: A tool-output dict (use ``build_tool_output_entry()``).
        persist: If True, write to disk before returning.

    Returns:
        A new ``MeetingManifest`` with the entry appended.
    """
    new_outputs = (*manifest.tool_outputs, entry)
    new_manifest = MeetingManifest(
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
        context_packets=manifest.context_packets,
        decisions=manifest.decisions,
        tool_outputs=new_outputs,
        created_at=manifest.created_at,
        updated_at=_utc_now_iso(),
    )
    if persist:
        return dispatch_transition_hooks(new_manifest, "tool_output")
    return new_manifest


def set_speaker(
    manifest: MeetingManifest,
    speaker: str,
    *,
    speaker_queue: Optional[tuple[str, ...]] = None,
    persist: bool = True,
) -> MeetingManifest:
    """Set the current speaker and optionally update the speaker queue.

    This is used before dispatching a context packet to a worker — the
    speaker is recorded in the manifest before the external opencode-go
    call, satisfying the "persist before external calls" constraint.

    Args:
        manifest: Current meeting manifest.
        speaker: Role ID of the current speaker (empty string to clear).
        speaker_queue: If provided, replaces the speaker queue.  If None,
                       the existing queue is unchanged.
        persist: If True, write to disk before returning.

    Returns:
        A new ``MeetingManifest`` with updated speaker state.
    """
    new_queue = speaker_queue if speaker_queue is not None else manifest.speaker_queue
    new_manifest = MeetingManifest(
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
        current_speaker=speaker,
        speaker_queue=new_queue,
        completed_step=manifest.completed_step,
        context_packets=manifest.context_packets,
        decisions=manifest.decisions,
        tool_outputs=manifest.tool_outputs,
        created_at=manifest.created_at,
        updated_at=_utc_now_iso(),
    )
    if persist:
        return dispatch_transition_hooks(new_manifest, "speaker_change")
    return new_manifest


def serialize_meeting_state(manifest: MeetingManifest) -> MeetingManifest:
    """Persist the current meeting state to manifest.json via transition hooks.

    This is the canonical serialization entry point — it dispatches all
    registered transition hooks (including the built-in persistence hook)
    and returns the updated manifest with a fresh ``updated_at`` timestamp.

    Args:
        manifest: Current meeting manifest to persist.

    Returns:
        A new ``MeetingManifest`` with ``updated_at`` refreshed and
        written to disk.
    """
    return dispatch_transition_hooks(manifest, "generic")


# ═══════════════════════════════════════════════════════════════════════════
# Sub-AC 4.4.2: Manifest deserialization and state reconstruction
# ═══════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass

from src.shared.lifecycle import (
    LifecycleState,
    MEETING_TRANSITIONS,
    is_active,
    is_terminal,
)


@dataclass(frozen=True)
class ReconstructedLifecyclePosition:
    """The complete lifecycle position of a meeting, reconstructed from a manifest.

    Sub-AC 4.4.2: This is the canonical deserialization target — parsing a
    manifest file and reconstructing the meeting's position (phase, round
    index, active speaker, pending queue) into a structured object that
    can be verified against the original.

    Required properties (from the AC):
        meeting_id: Unique meeting identifier.
        phase: Current lifecycle state (one of 15 LifecycleState values).
        round_index: Number of completed rounds (0-based).
        active_speaker: Currently speaking role_id (empty if none).
        pending_queue: Ordered speaker queue for the current round.

    Derived properties:
        is_terminal: True if the phase is a terminal state.
        is_active: True if the phase is an active (in-flight) state.
        completed_step: Last completed lifecycle step (for crash recovery).
        valid_next_states: Frozen set of LifecycleState values reachable
                           from the current phase per the transition map.
    """

    meeting_id: str
    """Unique meeting identifier."""

    phase: LifecycleState
    """Current lifecycle state (one of 15 LifecycleState values)."""

    round_index: int
    """Number of completed rounds (0 before the first round)."""

    active_speaker: str
    """Currently speaking role_id (empty string when no speaker is active)."""

    pending_queue: tuple[str, ...]
    """Ordered speaker queue for the current round."""

    completed_step: str = ""
    """Last completed lifecycle step — used for crash recovery resume."""

    @property
    def is_terminal(self) -> bool:
        """True if the meeting phase is a terminal state."""
        return is_terminal(self.phase)

    @property
    def is_active(self) -> bool:
        """True if the meeting phase is an active (in-flight) state."""
        return is_active(self.phase)

    @property
    def valid_next_states(self) -> frozenset[LifecycleState]:
        """Frozen set of LifecycleState values reachable from the current phase."""
        return MEETING_TRANSITIONS.get(self.phase, frozenset())

    def matches_manifest(self, manifest: "MeetingManifest") -> bool:
        """Return True if this reconstructed position matches *manifest*.

        Compares the four required AC fields (phase, round_index,
        active_speaker, pending_queue) plus completed_step against
        the manifest.  Useful for round-trip verification tests.
        """
        return (
            str(self.phase) == manifest.state
            and self.round_index == manifest.round_count
            and self.active_speaker == manifest.current_speaker
            and self.pending_queue == manifest.speaker_queue
            and self.completed_step == manifest.completed_step
        )


def reconstruct_lifecycle_position(manifest: MeetingManifest) -> ReconstructedLifecyclePosition:
    """Reconstruct the complete lifecycle position from a MeetingManifest.

    Sub-AC 4.4.2: Takes an in-memory manifest and produces a structured
    ``ReconstructedLifecyclePosition`` that captures the meeting phase,
    round index, active speaker, and pending queue.

    Args:
        manifest: A loaded or constructed MeetingManifest.

    Returns:
        A ``ReconstructedLifecyclePosition`` with all required and derived
        lifecycle fields populated from the manifest.
    """
    phase = LifecycleState(manifest.state) if manifest.state else LifecycleState.CREATED
    return ReconstructedLifecyclePosition(
        meeting_id=manifest.meeting_id,
        phase=phase,
        round_index=manifest.round_count,
        active_speaker=manifest.current_speaker,
        pending_queue=manifest.speaker_queue,
        completed_step=manifest.completed_step,
    )


def reconstruct_from_file(manifest_path: str) -> ReconstructedLifecyclePosition:
    """Parse a manifest file from disk and reconstruct the lifecycle position.

    Sub-AC 4.4.2: This is the file-based entry point — reads a
    ``manifest.json``, deserializes it into a ``MeetingManifest`` via
    ``load_manifest()``, and then reconstructs the lifecycle position.

    This is the function called during crash recovery when the Coordinator
    needs to resume a meeting from its on-disk state.

    Args:
        manifest_path: Absolute path to the manifest.json file.

    Returns:
        A ``ReconstructedLifecyclePosition`` with the meeting phase,
        round index, active speaker, and pending queue.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        json.JSONDecodeError: If the manifest is not valid JSON.
        KeyError: If required fields are missing (corrupted manifest).
        ValueError: If the ``state`` field is not a valid LifecycleState.
    """
    from src.meeting_trigger import load_manifest

    manifest = load_manifest(manifest_path)
    return reconstruct_lifecycle_position(manifest)
