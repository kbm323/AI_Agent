"""Tests for Manifest Serializer (Sub-AC 4.4.1).

Verifies:
- Entry builder dict shapes match expected format
- Append operations produce correct manifest mutations
- Speaker state management
- Full round-trip serialization: create state → write manifest → load → verify
- completed_step tracking on state transitions
- Append-only semantics for decisions
- New fields survive load/save round-trip
- Integration: full meeting round state serialization
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.manifest_serializer import (
    append_context_packet,
    append_decision,
    append_tool_output,
    build_context_packet_entry,
    build_decision_entry,
    build_tool_output_entry,
    ReconstructedLifecyclePosition,
    reconstruct_lifecycle_position,
    reconstruct_from_file,
    serialize_meeting_state,
    set_speaker,
)
from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingManifest,
    create_meeting,
    load_manifest,
)
from src.shared.lifecycle import LifecycleState
from src.transition_engine import execute_transition

# ── Test constants ─────────────────────────────────────────────────────────

_VALID_AGENDA = "신규 캐릭터 '루나'의 비주얼 디자인 회의"
_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_request(**overrides: str) -> MeetingCommandRequest:
    defaults = {
        "agenda": _VALID_AGENDA,
        "user_id": _VALID_USER_ID,
        "channel_id": _VALID_CHANNEL_ID,
        "priority": "p1",
    }
    defaults.update(overrides)
    return MeetingCommandRequest(**defaults)


def _tmp_meetings_root() -> str:
    return tempfile.mkdtemp(prefix="ai_agent_test_manifest_serializer_")


def _create_test_manifest(root: str) -> MeetingManifest:
    """Create a meeting and return its manifest at state='created'."""
    ctx = create_meeting(_make_request(), meetings_root=root)
    return ctx.manifest


def _advance_state(
    manifest: MeetingManifest, target: LifecycleState | str
) -> MeetingManifest:
    """Advance a manifest through successive states to reach *target*."""
    target_str = str(target)
    current = manifest
    path = [
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.FINALIZING,
        LifecycleState.COMPLETED,
    ]
    for state in path:
        if current.state == target_str:
            break
        result = execute_transition(current, state)
        if not result.success:
            raise RuntimeError(
                f"Failed to advance to {state}: {result.rejection_reasons}"
            )
        current = result.manifest
        if current.state == target_str:
            break
    if current.state != target_str:
        raise RuntimeError(
            f"Could not reach {target_str}; ended at {current.state}"
        )
    return current


def _cleanup_dir(path: str) -> None:
    """Remove a directory tree, ignoring errors."""
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _load_manifest_json(path: str) -> dict:
    """Read manifest.json as a raw dict."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 1. Entry builder dict shape tests ──────────────────────────────────────


class TestEntryBuilders:
    """Verify that builder functions produce correctly-structured dicts."""

    # ── Context packet entries ──

    def test_build_context_packet_entry_all_fields(self):
        entry = build_context_packet_entry(
            round_num=1,
            role_id="producer-kim",
            model_provider="qwen",
            model_name="qwen3-max",
            token_count=8500,
            packet_path="rounds/round_1/producer-kim.json",
            opinion_summary="Budget cap of ₩50M recommended",
        )
        assert entry["round"] == 1
        assert entry["role_id"] == "producer-kim"
        assert entry["model_provider"] == "qwen"
        assert entry["model_name"] == "qwen3-max"
        assert entry["token_count"] == 8500
        assert entry["packet_path"] == "rounds/round_1/producer-kim.json"
        assert entry["opinion_summary"] == "Budget cap of ₩50M recommended"
        assert "created_at" in entry
        assert entry["created_at"].endswith("+00:00") or "Z" in entry["created_at"] or "T" in entry["created_at"]

    def test_build_context_packet_entry_minimal(self):
        """Minimal entry: only required params."""
        entry = build_context_packet_entry(round_num=2, role_id="director-lee")
        assert entry["round"] == 2
        assert entry["role_id"] == "director-lee"
        assert entry["model_provider"] == ""
        assert entry["model_name"] == ""
        assert entry["token_count"] == 0
        assert entry["packet_path"] == ""
        assert entry["opinion_summary"] == ""

    def test_build_context_packet_entry_custom_timestamp(self):
        ts = "2026-06-10T15:00:00.000000+00:00"
        entry = build_context_packet_entry(
            round_num=1,
            role_id="finance-park",
            created_at=ts,
        )
        assert entry["created_at"] == ts

    # ── Decision entries ──

    def test_build_decision_entry_all_fields(self):
        entry = build_decision_entry(
            round_num=2,
            decision_id="d_042",
            role_id="producer-kim",
            content="Final budget approved: ₩45M",
            superseded_by="",
        )
        assert entry["round"] == 2
        assert entry["decision_id"] == "d_042"
        assert entry["role_id"] == "producer-kim"
        assert entry["content"] == "Final budget approved: ₩45M"
        assert entry["superseded_by"] == ""
        assert "created_at" in entry

    def test_build_decision_entry_superseded(self):
        """A superseded decision references the replacing decision_id."""
        entry = build_decision_entry(
            round_num=1,
            decision_id="d_001",
            role_id="director-lee",
            content="Use location A for MV shoot",
            superseded_by="d_015",
        )
        assert entry["superseded_by"] == "d_015"
        # Original content is preserved (append-only)
        assert entry["content"] == "Use location A for MV shoot"

    # ── Tool output entries ──

    def test_build_tool_output_entry_all_fields(self):
        entry = build_tool_output_entry(
            round_num=3,
            execution_id="exec_a1b2c3",
            action_type="deploy",
            role_id="openclaw-executor",
            status="success",
            output="Deployed to staging environment",
            risk_level="medium",
            human_approved=True,
        )
        assert entry["round"] == 3
        assert entry["execution_id"] == "exec_a1b2c3"
        assert entry["action_type"] == "deploy"
        assert entry["role_id"] == "openclaw-executor"
        assert entry["status"] == "success"
        assert entry["output"] == "Deployed to staging environment"
        assert entry["risk_level"] == "medium"
        assert entry["human_approved"] is True

    def test_build_tool_output_entry_high_risk_no_approval(self):
        """High-risk action without explicit approval records None."""
        entry = build_tool_output_entry(
            round_num=1,
            execution_id="exec_high_risk",
            action_type="delete_production_data",
            role_id="openclaw-executor",
            status="pending_approval",
            risk_level="critical",
            # human_approved not passed → None
        )
        assert entry["risk_level"] == "critical"
        assert entry["human_approved"] is None

    def test_build_tool_output_entry_low_risk_defaults(self):
        entry = build_tool_output_entry(
            round_num=1,
            execution_id="exec_low",
            action_type="read_logs",
            role_id="openclaw-executor",
            status="success",
        )
        assert entry["risk_level"] == "low"
        assert entry["human_approved"] is None


# ── 2. Append operations (manifest mutation) ───────────────────────────────


class TestAppendOperations:
    """Verify append operations produce correct manifest mutations."""

    def test_append_context_packet_adds_entry(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert len(manifest.context_packets) == 0

            entry = build_context_packet_entry(
                round_num=1,
                role_id="producer-kim",
                model_provider="qwen",
                model_name="qwen3-max",
                token_count=8500,
                packet_path="rounds/round_1/producer-kim.json",
            )
            # persist=False for faster tests
            result = append_context_packet(manifest, entry, persist=False)

            assert len(result.context_packets) == 1
            assert result.context_packets[0]["role_id"] == "producer-kim"
            assert result.context_packets[0]["round"] == 1
            # Original manifest unchanged (frozen)
            assert len(manifest.context_packets) == 0
        finally:
            _cleanup_dir(root)

    def test_append_context_packet_multiple_entries(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            roles = ["producer-kim", "director-lee", "finance-park"]
            current = manifest
            for i, role_id in enumerate(roles):
                entry = build_context_packet_entry(
                    round_num=1, role_id=role_id
                )
                current = append_context_packet(current, entry, persist=False)

            assert len(current.context_packets) == 3
            assert current.context_packets[0]["role_id"] == "producer-kim"
            assert current.context_packets[1]["role_id"] == "director-lee"
            assert current.context_packets[2]["role_id"] == "finance-park"
        finally:
            _cleanup_dir(root)

    def test_append_decision_adds_entry(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert len(manifest.decisions) == 0

            entry = build_decision_entry(
                round_num=1,
                decision_id="d_001",
                role_id="producer-kim",
                content="Budget ₩50M approved",
            )
            result = append_decision(manifest, entry, persist=False)

            assert len(result.decisions) == 1
            assert result.decisions[0]["decision_id"] == "d_001"
            assert result.decisions[0]["content"] == "Budget ₩50M approved"
        finally:
            _cleanup_dir(root)

    def test_append_decision_superseded_record_preserved(self):
        """Append-only: original record stays when superseded."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            original = build_decision_entry(
                round_num=1,
                decision_id="d_001",
                role_id="producer-kim",
                content="Budget ₩60M",
            )
            m = append_decision(manifest, original, persist=False)

            superseding = build_decision_entry(
                round_num=2,
                decision_id="d_005",
                role_id="producer-kim",
                content="Budget revised to ₩45M",
                superseded_by="",  # this IS the superseding decision
            )
            m = append_decision(m, superseding, persist=False)

            assert len(m.decisions) == 2
            assert m.decisions[0]["content"] == "Budget ₩60M"
            assert m.decisions[0]["decision_id"] == "d_001"
            assert m.decisions[1]["content"] == "Budget revised to ₩45M"
            assert m.decisions[1]["decision_id"] == "d_005"
        finally:
            _cleanup_dir(root)

    def test_append_tool_output_adds_entry(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert len(manifest.tool_outputs) == 0

            entry = build_tool_output_entry(
                round_num=1,
                execution_id="exec_abc",
                action_type="deploy",
                role_id="openclaw-executor",
                status="success",
                output="Done",
            )
            result = append_tool_output(manifest, entry, persist=False)

            assert len(result.tool_outputs) == 1
            assert result.tool_outputs[0]["execution_id"] == "exec_abc"
            assert result.tool_outputs[0]["status"] == "success"
        finally:
            _cleanup_dir(root)

    def test_append_multiple_types_accumulate_independently(self):
        """Context packets, decisions, and tool outputs are separate lists."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = manifest

            # Add context packets
            for i in range(2):
                entry = build_context_packet_entry(
                    round_num=1, role_id=f"role-{i}"
                )
                current = append_context_packet(current, entry, persist=False)

            # Add decisions
            for i in range(3):
                entry = build_decision_entry(
                    round_num=1,
                    decision_id=f"d_{i}",
                    role_id="producer-kim",
                    content=f"Decision {i}",
                )
                current = append_decision(current, entry, persist=False)

            # Add tool outputs
            for i in range(1):
                entry = build_tool_output_entry(
                    round_num=1,
                    execution_id=f"exec_{i}",
                    action_type="test",
                    role_id="openclaw-executor",
                    status="success",
                )
                current = append_tool_output(current, entry, persist=False)

            assert len(current.context_packets) == 2
            assert len(current.decisions) == 3
            assert len(current.tool_outputs) == 1
            # Other fields untouched
            assert current.state == "created"
            assert current.agenda == _VALID_AGENDA
        finally:
            _cleanup_dir(root)


# ── 3. Speaker state management ───────────────────────────────────────────


class TestSpeakerManagement:
    """Verify set_speaker() correctly updates speaker state."""

    def test_set_speaker_basic(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert manifest.current_speaker == ""
            assert manifest.speaker_queue == ()

            result = set_speaker(manifest, "producer-kim", persist=False)
            assert result.current_speaker == "producer-kim"
            assert result.speaker_queue == ()  # unchanged when not provided
        finally:
            _cleanup_dir(root)

    def test_set_speaker_with_queue(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            queue = ("producer-kim", "director-lee", "finance-park")
            result = set_speaker(
                manifest, "producer-kim", speaker_queue=queue, persist=False
            )
            assert result.current_speaker == "producer-kim"
            assert result.speaker_queue == queue
        finally:
            _cleanup_dir(root)

    def test_set_speaker_clear(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Set first
            m = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee"),
                persist=False,
            )
            # Clear
            m = set_speaker(m, "", persist=False)
            assert m.current_speaker == ""
            # Queue persists (not cleared with speaker)
            assert m.speaker_queue == ("producer-kim", "director-lee")
        finally:
            _cleanup_dir(root)

    def test_set_speaker_advances_queue(self):
        """Simulate a round-robin speaker progression."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            full_queue = ("producer-kim", "director-lee", "finance-park")

            # Round 1: Start with producer-kim
            m = set_speaker(
                manifest, "producer-kim", speaker_queue=full_queue, persist=False
            )
            assert m.current_speaker == "producer-kim"

            # Advance to director-lee (remove first from queue)
            new_queue = full_queue[1:]
            m = set_speaker(m, "director-lee", speaker_queue=new_queue, persist=False)
            assert m.current_speaker == "director-lee"
            assert m.speaker_queue == ("director-lee", "finance-park")

            # Advance to finance-park
            m = set_speaker(m, "finance-park", speaker_queue=("finance-park",), persist=False)
            assert m.current_speaker == "finance-park"
            assert m.speaker_queue == ("finance-park",)

            # Last speaker done
            m = set_speaker(m, "", speaker_queue=(), persist=False)
            assert m.current_speaker == ""
            assert m.speaker_queue == ()
        finally:
            _cleanup_dir(root)


# ── 4. Serialization and persistence ──────────────────────────────────────


class TestSerialization:
    """Verify manifest serialization and persistence to disk."""

    def test_serialize_meeting_state_writes_to_disk(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Add some state
            m = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee"),
                persist=False,
            )
            m = append_context_packet(
                m,
                build_context_packet_entry(round_num=1, role_id="producer-kim"),
                persist=False,
            )
            m = append_decision(
                m,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_001",
                    role_id="producer-kim",
                    content="Go with option A",
                ),
                persist=False,
            )

            # Serialize to disk
            result = serialize_meeting_state(m)

            # Load and verify
            loaded = load_manifest(path)
            assert loaded.current_speaker == "producer-kim"
            assert loaded.speaker_queue == ("producer-kim", "director-lee")
            assert len(loaded.context_packets) == 1
            assert len(loaded.decisions) == 1
            assert loaded.context_packets[0]["role_id"] == "producer-kim"
            assert loaded.decisions[0]["decision_id"] == "d_001"
        finally:
            _cleanup_dir(root)

    def test_append_with_persist_writes_to_disk(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            entry = build_context_packet_entry(
                round_num=1, role_id="producer-kim"
            )
            result = append_context_packet(manifest, entry, persist=True)

            # Verify on disk
            raw = _load_manifest_json(path)
            assert len(raw.get("context_packets", [])) == 1
            assert raw["context_packets"][0]["role_id"] == "producer-kim"
        finally:
            _cleanup_dir(root)

    def test_set_speaker_with_persist_writes_to_disk(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            result = set_speaker(
                manifest,
                "director-lee",
                speaker_queue=("director-lee", "finance-park"),
                persist=True,
            )

            raw = _load_manifest_json(path)
            assert raw["current_speaker"] == "director-lee"
            assert raw["speaker_queue"] == ["director-lee", "finance-park"]
        finally:
            _cleanup_dir(root)

    def test_manifest_json_contains_new_fields(self):
        """After serialization, the manifest JSON must contain all new fields."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            m = set_speaker(manifest, "producer-kim", persist=True)
            raw = _load_manifest_json(path)

            # All new fields must be present
            assert "current_speaker" in raw
            assert "speaker_queue" in raw
            assert "completed_step" in raw
            assert "context_packets" in raw
            assert "decisions" in raw
            assert "tool_outputs" in raw
        finally:
            _cleanup_dir(root)

    def test_empty_manifest_has_default_new_fields(self):
        """A fresh manifest has empty defaults for new fields."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert manifest.current_speaker == ""
            assert manifest.speaker_queue == ()
            assert manifest.completed_step == ""
            assert manifest.context_packets == ()
            assert manifest.decisions == ()
            assert manifest.tool_outputs == ()
        finally:
            _cleanup_dir(root)


# ── 5. completed_step tracking ────────────────────────────────────────────


class TestCompletedStep:
    """Verify that completed_step is set correctly on state transitions."""

    def test_with_state_sets_completed_step_to_previous_state(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            assert manifest.state == "created"
            assert manifest.completed_step == ""

            # Transition created → queued
            new_m = manifest.with_state(LifecycleState.QUEUED)
            assert new_m.state == "queued"
            assert new_m.completed_step == "created"
        finally:
            _cleanup_dir(root)

    def test_full_chain_completed_step_tracks_progress(self):
        """Each transition sets completed_step to the previous state."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            states = [
                LifecycleState.QUEUED,
                LifecycleState.ROUTING,
                LifecycleState.CONTEXT_RETRIEVAL,
                LifecycleState.IN_MEETING,
            ]
            current = manifest
            for state in states:
                new_m = current.with_state(state)
                assert new_m.completed_step == str(current.state), (
                    f"After {current.state} → {state}, "
                    f"expected completed_step='{current.state}', "
                    f"got '{new_m.completed_step}'"
                )
                current = new_m

            # Final check
            assert current.completed_step == "context_retrieval"
        finally:
            _cleanup_dir(root)

    def test_completed_step_persists_through_serialization(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            # Transition via with_state
            m = manifest.with_state(LifecycleState.QUEUED)
            m = serialize_meeting_state(m)

            raw = _load_manifest_json(m.manifest_path)
            assert raw["completed_step"] == "created"
        finally:
            _cleanup_dir(root)

    def test_completed_step_via_transition_engine(self):
        """execute_transition should also set completed_step via with_state."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = execute_transition(
                manifest, LifecycleState.QUEUED, label="test"
            )
            assert result.success
            assert result.manifest.completed_step == "created"

            # Verify on disk
            loaded = load_manifest(result.manifest.manifest_path)
            assert loaded.completed_step == "created"
        finally:
            _cleanup_dir(root)


# ── 6. Round-trip: full meeting state persistence ─────────────────────────


class TestFullRoundTrip:
    """End-to-end: create state, serialize, load, verify all content."""

    def test_full_round_trip_with_all_entry_types(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path
            current = manifest

            # Step 1: Set speaker queue for round 1
            current = set_speaker(
                current,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee", "finance-park"),
                persist=False,
            )

            # Step 2: Add context packets for each speaker
            for role_id in ("producer-kim", "director-lee", "finance-park"):
                current = set_speaker(current, role_id, persist=False)
                entry = build_context_packet_entry(
                    round_num=1,
                    role_id=role_id,
                    model_provider="qwen",
                    model_name="qwen3-max",
                    token_count=8000 + hash(role_id) % 3000,
                    packet_path=f"rounds/round_1/{role_id}.json",
                    opinion_summary=f"Opinion from {role_id}",
                )
                current = append_context_packet(current, entry, persist=False)

            # Step 3: Clear speaker
            current = set_speaker(current, "", speaker_queue=(), persist=False)

            # Step 4: Add decisions from the round
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_r1_001",
                    role_id="producer-kim",
                    content="Budget cap: ₩50M",
                ),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_r1_002",
                    role_id="director-lee",
                    content="Location: Jeju Island",
                ),
                persist=False,
            )

            # Step 5: Add tool output
            current = append_tool_output(
                current,
                build_tool_output_entry(
                    round_num=1,
                    execution_id="exec_create_calendar_invite",
                    action_type="create_calendar_event",
                    role_id="openclaw-executor",
                    status="success",
                    output="Calendar invite created for 2026-06-15T10:00:00Z",
                    risk_level="low",
                ),
                persist=False,
            )

            # Step 6: Serialize to disk
            current = serialize_meeting_state(current)

            # Step 7: Load and verify
            loaded = load_manifest(path)

            # Verify identity fields survived
            assert loaded.meeting_id == manifest.meeting_id
            assert loaded.agenda == _VALID_AGENDA
            assert loaded.state == "created"

            # Verify speaker state
            assert loaded.current_speaker == ""
            assert loaded.speaker_queue == ()

            # Verify context packets
            assert len(loaded.context_packets) == 3
            role_ids = [p["role_id"] for p in loaded.context_packets]
            assert "producer-kim" in role_ids
            assert "director-lee" in role_ids
            assert "finance-park" in role_ids
            for pkt in loaded.context_packets:
                assert pkt["round"] == 1
                assert "model_provider" in pkt
                assert "model_name" in pkt
                assert "token_count" in pkt
                assert "packet_path" in pkt
                assert "opinion_summary" in pkt
                assert "created_at" in pkt

            # Verify decisions
            assert len(loaded.decisions) == 2
            assert loaded.decisions[0]["decision_id"] == "d_r1_001"
            assert loaded.decisions[1]["decision_id"] == "d_r1_002"
            assert loaded.decisions[0]["round"] == 1
            assert loaded.decisions[1]["round"] == 1

            # Verify tool outputs
            assert len(loaded.tool_outputs) == 1
            assert loaded.tool_outputs[0]["execution_id"] == "exec_create_calendar_invite"
            assert loaded.tool_outputs[0]["status"] == "success"
            assert loaded.tool_outputs[0]["risk_level"] == "low"
        finally:
            _cleanup_dir(root)

    def test_multi_round_accumulation(self):
        """Entries from multiple rounds accumulate correctly."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = manifest

            # Round 1
            current = append_context_packet(
                current,
                build_context_packet_entry(round_num=1, role_id="role-a"),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_r1",
                    role_id="role-a",
                    content="R1 decision",
                ),
                persist=False,
            )

            # Round 2
            current = append_context_packet(
                current,
                build_context_packet_entry(round_num=2, role_id="role-b"),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=2,
                    decision_id="d_r2",
                    role_id="role-b",
                    content="R2 decision",
                ),
                persist=False,
            )

            # Round 3
            current = append_context_packet(
                current,
                build_context_packet_entry(round_num=3, role_id="role-c"),
                persist=False,
            )

            current = serialize_meeting_state(current)
            loaded = load_manifest(current.manifest_path)

            assert len(loaded.context_packets) == 3
            assert len(loaded.decisions) == 2

            # Verify round ordering
            rounds_cp = [p["round"] for p in loaded.context_packets]
            assert rounds_cp == [1, 2, 3]
            rounds_d = [d["round"] for d in loaded.decisions]
            assert rounds_d == [1, 2]
        finally:
            _cleanup_dir(root)

    def test_raw_json_matches_expected_format(self):
        """The AC requirement: verifying written manifest content matches expected format."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            current = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee"),
                persist=False,
            )
            current = append_context_packet(
                current,
                build_context_packet_entry(
                    round_num=1,
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    token_count=8500,
                    packet_path="rounds/round_1/producer-kim.json",
                    opinion_summary="Budget ₩50M",
                    created_at="2026-06-10T15:00:00.000000+00:00",
                ),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_001",
                    role_id="producer-kim",
                    content="Budget approved: ₩50M",
                    created_at="2026-06-10T15:05:00.000000+00:00",
                ),
                persist=False,
            )
            current = append_tool_output(
                current,
                build_tool_output_entry(
                    round_num=1,
                    execution_id="exec_test",
                    action_type="send_email",
                    role_id="openclaw-executor",
                    status="success",
                    output="Email sent to team@example.com",
                    risk_level="low",
                    created_at="2026-06-10T15:10:00.000000+00:00",
                ),
                persist=False,
            )

            # Serialize
            serialize_meeting_state(current)

            # Load raw JSON and verify exact structure
            raw = _load_manifest_json(path)

            # Verify context_packets format
            assert isinstance(raw["context_packets"], list)
            assert len(raw["context_packets"]) == 1
            cp = raw["context_packets"][0]
            assert cp["round"] == 1
            assert cp["role_id"] == "producer-kim"
            assert cp["model_provider"] == "qwen"
            assert cp["model_name"] == "qwen3-max"
            assert cp["token_count"] == 8500
            assert cp["packet_path"] == "rounds/round_1/producer-kim.json"
            assert cp["opinion_summary"] == "Budget ₩50M"
            assert cp["created_at"] == "2026-06-10T15:00:00.000000+00:00"

            # Verify decisions format
            assert isinstance(raw["decisions"], list)
            assert len(raw["decisions"]) == 1
            d = raw["decisions"][0]
            assert d["round"] == 1
            assert d["decision_id"] == "d_001"
            assert d["role_id"] == "producer-kim"
            assert d["content"] == "Budget approved: ₩50M"
            assert d["superseded_by"] == ""
            assert d["created_at"] == "2026-06-10T15:05:00.000000+00:00"

            # Verify tool_outputs format
            assert isinstance(raw["tool_outputs"], list)
            assert len(raw["tool_outputs"]) == 1
            t = raw["tool_outputs"][0]
            assert t["round"] == 1
            assert t["execution_id"] == "exec_test"
            assert t["action_type"] == "send_email"
            assert t["role_id"] == "openclaw-executor"
            assert t["status"] == "success"
            assert t["output"] == "Email sent to team@example.com"
            assert t["risk_level"] == "low"
            assert t["human_approved"] is None
            assert t["created_at"] == "2026-06-10T15:10:00.000000+00:00"

            # Verify speaker state
            assert raw["current_speaker"] == "producer-kim"
            assert raw["speaker_queue"] == ["producer-kim", "director-lee"]

            # Verify completed_step and other base fields
            assert "completed_step" in raw
            assert raw["meeting_id"].startswith("meeting_")
            assert raw["state"] == "created"
            assert raw["agenda"] == _VALID_AGENDA
        finally:
            _cleanup_dir(root)


# ── 7. Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case and robustness tests."""

    def test_append_empty_entries_allowed(self):
        """Appending no entries should leave manifest unchanged structurally."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            m = append_context_packet(
                manifest,
                {},
                persist=False,
            )
            assert len(m.context_packets) == 1
            # Empty dict is still an entry — this is user's responsibility
        finally:
            _cleanup_dir(root)

    def test_speaker_queue_empty_is_valid(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            m = set_speaker(manifest, "solo-speaker", speaker_queue=(), persist=False)
            assert m.current_speaker == "solo-speaker"
            assert m.speaker_queue == ()
        finally:
            _cleanup_dir(root)

    def test_high_risk_tool_output_human_rejected(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            entry = build_tool_output_entry(
                round_num=1,
                execution_id="exec_rejected",
                action_type="delete_database",
                role_id="openclaw-executor",
                status="rejected",
                output="Human rejected: too risky",
                risk_level="critical",
                human_approved=False,
            )
            m = append_tool_output(manifest, entry, persist=False)

            assert m.tool_outputs[0]["human_approved"] is False
            assert m.tool_outputs[0]["risk_level"] == "critical"
            assert m.tool_outputs[0]["status"] == "rejected"
        finally:
            _cleanup_dir(root)

    def test_decision_superseded_chain(self):
        """A chain of superseded decisions: A → B → C."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)

            # Original
            m = append_decision(
                manifest,
                build_decision_entry(
                    round_num=1, decision_id="d_A", role_id="role-x",
                    content="Decision A", superseded_by="d_B",
                ),
                persist=False,
            )
            # Superseding
            m = append_decision(
                m,
                build_decision_entry(
                    round_num=2, decision_id="d_B", role_id="role-x",
                    content="Decision B", superseded_by="d_C",
                ),
                persist=False,
            )
            # Final
            m = append_decision(
                m,
                build_decision_entry(
                    round_num=3, decision_id="d_C", role_id="role-x",
                    content="Decision C (final)",
                ),
                persist=False,
            )

            assert len(m.decisions) == 3
            assert m.decisions[0]["superseded_by"] == "d_B"
            assert m.decisions[1]["superseded_by"] == "d_C"
            assert m.decisions[2]["superseded_by"] == ""
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Sub-AC 4.4.2: Manifest deserialization and state reconstruction
# ═══════════════════════════════════════════════════════════════════════════


class TestReconstructedLifecyclePosition:
    """Verify ReconstructedLifecyclePosition dataclass properties."""

    def test_create_at_created_state(self):
        """A meeting just created has phase=created, round_index=0, no speaker."""
        pos = ReconstructedLifecyclePosition(
            meeting_id="meeting_20260610_abc123def456",
            phase=LifecycleState.CREATED,
            round_index=0,
            active_speaker="",
            pending_queue=(),
            completed_step="",
        )
        assert pos.meeting_id == "meeting_20260610_abc123def456"
        assert pos.phase == LifecycleState.CREATED
        assert pos.round_index == 0
        assert pos.active_speaker == ""
        assert pos.pending_queue == ()
        assert pos.completed_step == ""
        assert pos.is_active is True
        assert pos.is_terminal is False
        # Valid next states from CREATED: queued, cancelled, failed, stale
        assert LifecycleState.QUEUED in pos.valid_next_states
        assert LifecycleState.CANCELLED in pos.valid_next_states
        assert LifecycleState.COMPLETED not in pos.valid_next_states

    def test_in_meeting_with_speaker_and_queue(self):
        """Mid-meeting: active speaker, pending queue, round 2 completed."""
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.IN_MEETING,
            round_index=2,
            active_speaker="producer-kim",
            pending_queue=("director-lee", "finance-park"),
            completed_step="context_retrieval",
        )
        assert pos.phase == LifecycleState.IN_MEETING
        assert pos.round_index == 2
        assert pos.active_speaker == "producer-kim"
        assert pos.pending_queue == ("director-lee", "finance-park")
        assert pos.completed_step == "context_retrieval"
        assert pos.is_active is True
        assert pos.is_terminal is False
        # From IN_MEETING you can go to consensus_building, deadlocked, etc.
        assert LifecycleState.CONSENSUS_BUILDING in pos.valid_next_states
        assert LifecycleState.DEADLOCKED in pos.valid_next_states

    def test_terminal_state_completed(self):
        """A completed meeting is terminal with no valid next states."""
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.COMPLETED,
            round_index=3,
            active_speaker="",
            pending_queue=(),
            completed_step="finalizing",
        )
        assert pos.is_active is False
        assert pos.is_terminal is True
        assert pos.valid_next_states == frozenset()

    def test_terminal_state_failed(self):
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.FAILED,
            round_index=1,
            active_speaker="",
            pending_queue=(),
        )
        assert pos.is_terminal is True
        assert pos.is_active is False

    def test_terminal_state_cancelled(self):
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.CANCELLED,
            round_index=0,
            active_speaker="",
            pending_queue=(),
        )
        assert pos.is_terminal is True

    def test_exception_state_deadlocked(self):
        """Deadlocked is active (not terminal) and can transition to finalized/escalated."""
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.DEADLOCKED,
            round_index=3,
            active_speaker="",
            pending_queue=(),
        )
        assert pos.is_terminal is False
        assert pos.is_active is True
        assert LifecycleState.FINALIZING in pos.valid_next_states
        assert LifecycleState.ESCALATED in pos.valid_next_states

    def test_valid_next_states_from_paused(self):
        """Paused state can resume to many states."""
        pos = ReconstructedLifecyclePosition(
            meeting_id="m1",
            phase=LifecycleState.PAUSED,
            round_index=1,
            active_speaker="producer-kim",
            pending_queue=("producer-kim",),
        )
        assert LifecycleState.IN_MEETING in pos.valid_next_states
        assert LifecycleState.ROUTING in pos.valid_next_states
        assert LifecycleState.CANCELLED in pos.valid_next_states


class TestReconstructLifecyclePosition:
    """Verify reconstruct_lifecycle_position() from in-memory manifests."""

    def test_reconstruct_from_created_manifest(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            pos = reconstruct_lifecycle_position(manifest)

            assert pos.meeting_id == manifest.meeting_id
            assert pos.phase == LifecycleState.CREATED
            assert pos.round_index == 0
            assert pos.active_speaker == ""
            assert pos.pending_queue == ()
            assert pos.completed_step == ""
        finally:
            _cleanup_dir(root)

    def test_reconstruct_after_state_transitions(self):
        """Reconstructing after several state transitions captures all changes."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Advance to in_meeting via transition engine
            current = _advance_state(manifest, LifecycleState.IN_MEETING)
            assert current.state == "in_meeting"

            pos = reconstruct_lifecycle_position(current)
            assert pos.phase == LifecycleState.IN_MEETING
            assert pos.is_active is True
            assert pos.is_terminal is False
        finally:
            _cleanup_dir(root)

    def test_reconstruct_with_speaker_and_queue(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee", "finance-park"),
                persist=False,
            )
            current = _advance_state(current, LifecycleState.IN_MEETING)

            pos = reconstruct_lifecycle_position(current)
            assert pos.active_speaker == "producer-kim"
            assert pos.pending_queue == ("producer-kim", "director-lee", "finance-park")
            assert pos.phase == LifecycleState.IN_MEETING
        finally:
            _cleanup_dir(root)

    def test_reconstruct_after_full_round_with_entries(self):
        """Lifecycle position is correct even when context packets and decisions exist."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = manifest

            # Add entries
            current = append_context_packet(
                current,
                build_context_packet_entry(round_num=1, role_id="role-a"),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1, decision_id="d1", role_id="role-a", content="X"
                ),
                persist=False,
            )
            current = set_speaker(
                current,
                "role-a",
                speaker_queue=("role-a", "role-b"),
                persist=False,
            )

            pos = reconstruct_lifecycle_position(current)
            assert pos.active_speaker == "role-a"
            assert pos.pending_queue == ("role-a", "role-b")
            # Non-lifecycle fields (context_packets, decisions) don't affect
            # the reconstructed position but the manifest still holds them
            assert pos.phase == LifecycleState.CREATED
        finally:
            _cleanup_dir(root)

    def test_matches_manifest_positive(self):
        """matches_manifest returns True for matching manifest."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = set_speaker(
                manifest,
                "director-lee",
                speaker_queue=("director-lee",),
                persist=False,
            )
            current = _advance_state(current, LifecycleState.IN_MEETING)

            pos = reconstruct_lifecycle_position(current)
            assert pos.matches_manifest(current) is True
        finally:
            _cleanup_dir(root)

    def test_matches_manifest_negative_speaker_mismatch(self):
        """matches_manifest returns False when speaker differs."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            current = set_speaker(manifest, "role-a", persist=False)

            pos = reconstruct_lifecycle_position(current)
            # Modify the manifest: create a new one with different speaker
            alt = set_speaker(current, "role-b", persist=False)
            assert pos.matches_manifest(alt) is False
        finally:
            _cleanup_dir(root)

    def test_reconstruct_with_completed_step(self):
        """completed_step is correctly captured in the reconstructed position."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Transition created → queued (completed_step becomes "created")
            current = manifest.with_state(LifecycleState.QUEUED)

            pos = reconstruct_lifecycle_position(current)
            assert pos.completed_step == "created"
            assert pos.phase == LifecycleState.QUEUED
        finally:
            _cleanup_dir(root)


class TestReconstructFromFile:
    """Verify reconstruct_from_file() reads manifest.json from disk."""

    def test_reconstruct_from_fresh_manifest(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            pos = reconstruct_from_file(path)
            assert pos.meeting_id == manifest.meeting_id
            assert pos.phase == LifecycleState.CREATED
            assert pos.round_index == 0
            assert pos.active_speaker == ""
            assert pos.pending_queue == ()
        finally:
            _cleanup_dir(root)

    def test_reconstruct_after_serialize_with_entries(self):
        """Write manifest to disk with rich state, then reconstruct from file."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Build rich state
            current = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee", "finance-park"),
                persist=False,
            )
            current = append_context_packet(
                current,
                build_context_packet_entry(
                    round_num=1,
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    token_count=8500,
                    packet_path="rounds/round_1/producer-kim.json",
                    opinion_summary="Budget ₩50M",
                ),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_001",
                    role_id="producer-kim",
                    content="Budget approved",
                ),
                persist=False,
            )
            # Persist to disk
            serialize_meeting_state(current)

            # Reconstruct from file
            pos = reconstruct_from_file(path)

            # Verify lifecycle position
            assert pos.active_speaker == "producer-kim"
            assert pos.pending_queue == ("producer-kim", "director-lee", "finance-park")
            assert pos.phase == LifecycleState.CREATED
            assert pos.round_index == 0

            # Verify the reconstructed position matches the loaded manifest
            loaded = load_manifest(path)
            assert pos.matches_manifest(loaded) is True
        finally:
            _cleanup_dir(root)

    def test_reconstruct_after_state_transitions_persisted(self):
        """Transitions persisted to disk are correctly reconstructed."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Advance to in_meeting and persist
            current = _advance_state(manifest, LifecycleState.IN_MEETING)
            current = set_speaker(
                current,
                "director-lee",
                speaker_queue=("director-lee", "finance-park"),
                persist=True,
            )

            # Reconstruct from file
            pos = reconstruct_from_file(path)
            assert pos.phase == LifecycleState.IN_MEETING
            assert pos.active_speaker == "director-lee"
            assert pos.pending_queue == ("director-lee", "finance-park")
            assert pos.is_active is True
        finally:
            _cleanup_dir(root)

    def test_reconstruct_completed_meeting(self):
        """A completed meeting reconstructed from disk is terminal."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Advance through full pipeline to completed
            current = _advance_state(manifest, LifecycleState.COMPLETED)
            serialize_meeting_state(current)

            pos = reconstruct_from_file(path)
            assert pos.phase == LifecycleState.COMPLETED
            assert pos.is_terminal is True
            assert pos.is_active is False
            assert pos.valid_next_states == frozenset()
        finally:
            _cleanup_dir(root)

    def test_reconstruct_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            reconstruct_from_file("/nonexistent/path/manifest.json")

    def test_reconstruct_corrupted_manifest_missing_meeting_id(self):
        """A manifest without meeting_id raises KeyError."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Write a corrupted manifest (missing meeting_id)
            import json
            corrupt_path = path + ".corrupt"
            data = {
                "state": "created",
                "priority": "p2",
                "agenda": "Test",
                # deliberately missing meeting_id
            }
            with open(corrupt_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            with pytest.raises(KeyError):
                reconstruct_from_file(corrupt_path)
        finally:
            _cleanup_dir(root)

    def test_reconstruct_invalid_state_value(self):
        """A manifest with an invalid state value raises ValueError."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Write a manifest with an invalid state
            import json
            corrupt_path = path + ".corrupt"
            data = manifest.to_dict()
            data["state"] = "nonexistent_state"
            with open(corrupt_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            with pytest.raises(ValueError):
                reconstruct_from_file(corrupt_path)
        finally:
            _cleanup_dir(root)


class TestReconstructionRoundTrip:
    """Full round-trip: serialize → reconstruct → verify match.

    This is the canonical AC test: write a known manifest, reconstruct
    the state, and verify it matches the original.
    """

    def test_full_round_trip_created(self):
        """Write a 'created' manifest → reconstruct → verify all fields match."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Reconstruct
            pos = reconstruct_from_file(path)

            # Verify every reconstruction field matches the original manifest
            assert pos.meeting_id == manifest.meeting_id
            assert str(pos.phase) == manifest.state
            assert pos.round_index == manifest.round_count
            assert pos.active_speaker == manifest.current_speaker
            assert pos.pending_queue == manifest.speaker_queue
            assert pos.completed_step == manifest.completed_step
            assert pos.matches_manifest(manifest) is True
        finally:
            _cleanup_dir(root)

    def test_full_round_trip_in_meeting(self):
        """Write an 'in_meeting' manifest with speakers → reconstruct → verify."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Build and persist a mid-meeting state
            current = manifest
            current = set_speaker(
                current,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee", "finance-park"),
                persist=False,
            )
            current = append_context_packet(
                current,
                build_context_packet_entry(
                    round_num=1,
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    token_count=8500,
                    packet_path="rounds/round_1/producer-kim.json",
                    opinion_summary="Budget cap ₩50M",
                ),
                persist=False,
            )
            current = _advance_state(current, LifecycleState.IN_MEETING)
            # Persist after all mutations
            serialize_meeting_state(current)

            # Reconstruct from file
            pos = reconstruct_from_file(path)

            # Verify reconstruction matches loaded manifest
            loaded = load_manifest(path)
            assert loaded.state == "in_meeting"
            assert pos.phase == LifecycleState.IN_MEETING
            assert pos.round_index == 0  # round_count still 0
            assert pos.active_speaker == "producer-kim"
            assert pos.pending_queue == ("producer-kim", "director-lee", "finance-park")
            assert pos.matches_manifest(loaded) is True
        finally:
            _cleanup_dir(root)

    def test_full_round_trip_validating_with_entries(self):
        """Write a 'validating' manifest with decisions → reconstruct → verify."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            current = manifest
            # Add decisions
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_001",
                    role_id="producer-kim",
                    content="Budget ₩50M approved",
                ),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=2,
                    decision_id="d_002",
                    role_id="director-lee",
                    content="Jeju Island location confirmed",
                ),
                persist=False,
            )
            current = set_speaker(current, "", speaker_queue=(), persist=False)
            current = _advance_state(current, LifecycleState.VALIDATING)
            serialize_meeting_state(current)

            # Reconstruct
            pos = reconstruct_from_file(path)

            loaded = load_manifest(path)
            assert loaded.state == "validating"
            assert pos.phase == LifecycleState.VALIDATING
            assert pos.active_speaker == ""
            assert pos.pending_queue == ()
            assert pos.is_active is True
            assert pos.matches_manifest(loaded) is True

            # Non-lifecycle fields (decisions) were preserved on disk
            assert len(loaded.decisions) == 2
        finally:
            _cleanup_dir(root)

    def test_round_trip_verifies_all_four_required_fields(self):
        """The AC specifically requires: meeting phase, round index,
        active speaker, pending queue — all four are verified."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Build a fully populated mid-meeting state
            current = manifest
            current = set_speaker(
                current,
                "finance-park",
                speaker_queue=("finance-park", "producer-kim"),
                persist=False,
            )
            current = current.with_state(LifecycleState.CONSENSUS_BUILDING)
            serialize_meeting_state(current)

            pos = reconstruct_from_file(path)

            # AC 4.4.2 fields
            assert pos.phase == LifecycleState.CONSENSUS_BUILDING  # meeting phase
            assert pos.round_index == 0                            # round index
            assert pos.active_speaker == "finance-park"            # active speaker
            assert pos.pending_queue == ("finance-park", "producer-kim")  # pending queue
        finally:
            _cleanup_dir(root)

    def test_crash_recovery_scenario(self):
        """Simulate crash recovery: manifest written mid-meeting,
        Coordinator restarts, reads manifest, reconstructs state."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            path = manifest.manifest_path

            # Simulate a meeting that was in progress when Coordinator crashed
            current = manifest
            current = _advance_state(current, LifecycleState.IN_MEETING)
            current = set_speaker(
                current,
                "director-lee",
                speaker_queue=("director-lee", "producer-kim", "finance-park"),
                persist=False,
            )
            current = append_context_packet(
                current,
                build_context_packet_entry(
                    round_num=1,
                    role_id="producer-kim",
                    model_provider="qwen",
                    model_name="qwen3-max",
                    token_count=8200,
                    packet_path="rounds/round_1/producer-kim.json",
                    opinion_summary="Option A recommended",
                ),
                persist=False,
            )
            current = append_decision(
                current,
                build_decision_entry(
                    round_num=1,
                    decision_id="d_r1",
                    role_id="producer-kim",
                    content="Preliminary budget: ₩45M",
                ),
                persist=False,
            )
            # Persist — this is the last state before the "crash"
            serialize_meeting_state(current)

            # ── "Coordinator restarts" ──
            # Reconstruct state from disk
            pos = reconstruct_from_file(path)

            # Coordinator now knows:
            # - The meeting was in IN_MEETING phase
            assert pos.phase == LifecycleState.IN_MEETING
            # - director-lee was the active speaker
            assert pos.active_speaker == "director-lee"
            # - Two more speakers pending
            assert pos.pending_queue == ("director-lee", "producer-kim", "finance-park")
            # - completed_step tells us the last completed lifecycle step
            assert pos.completed_step == "context_retrieval"
            # - No rounds completed yet
            assert pos.round_index == 0
            # - Meeting is active, not terminal
            assert pos.is_active is True

            # Also verify non-lifecycle content survived
            loaded = load_manifest(path)
            assert len(loaded.context_packets) == 1
            assert len(loaded.decisions) == 1
            assert loaded.context_packets[0]["role_id"] == "producer-kim"
            assert loaded.decisions[0]["content"] == "Preliminary budget: ₩45M"
        finally:
            _cleanup_dir(root)
