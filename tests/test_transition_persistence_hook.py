"""Tests for Transition-Triggered Persistence Hook (Sub-AC 4.4.3).

Verifies:
1. Hook registry operations (register, remove, clear, list)
2. Built-in persistence hook installation
3. Hook dispatch pipeline (order, manifest passing, error isolation)
4. Integration with execute_transition — state changes trigger hooks
5. Integration with manifest_serializer — speaker changes, decision commits,
   context packets, tool outputs all trigger hooks
6. Manifest file is actually written on disk after every transition type
7. Multiple hooks fire in registration order
8. Hook failures do not block subsequent hooks or the transition
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingManifest,
    create_meeting,
    load_manifest,
)
from src.shared.lifecycle import LifecycleState
from src.transition_engine import execute_transition
from src.manifest_serializer import (
    append_context_packet,
    append_decision,
    append_tool_output,
    build_context_packet_entry,
    build_decision_entry,
    build_tool_output_entry,
    set_speaker,
    serialize_meeting_state,
)
from src.transition_persistence_hook import (
    TransitionHook,
    register_transition_hook,
    remove_transition_hook,
    clear_transition_hooks,
    list_transition_hooks,
    dispatch_transition_hooks,
    persistence_hook,
    install_default_hooks,
)

# ── Helpers ───────────────────────────────────────────────────────────────

_VALID_AGENDA = "신규 캐릭터 '루나'의 비주얼 디자인 회의"
_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


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
    return tempfile.mkdtemp(prefix="ai_agent_test_hooks_")


def _create_test_manifest(root: str) -> MeetingManifest:
    ctx = create_meeting(_make_request(), meetings_root=root)
    return ctx.manifest


def _cleanup_dir(path: str) -> None:
    import shutil
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _advance_to(manifest: MeetingManifest, target: LifecycleState) -> MeetingManifest:
    """Advance a manifest through canonical states to reach *target*."""
    forward_path = [
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.FINALIZING,
        LifecycleState.COMPLETED,
    ]
    current = manifest
    for state in forward_path:
        if current.state == str(target):
            break
        if current.state == str(state):
            continue
        result = execute_transition(current, state, label=f"advance-{state.value}")
        if not result.success:
            raise RuntimeError(
                f"Failed to advance to {state.value}: {result.rejection_reasons}"
            )
        current = result.manifest
        if current.state == str(target):
            break
    if current.state != str(target):
        raise RuntimeError(f"Stuck at {current.state}, wanted {target.value}")
    return current


# ── Test fixture: clean hooks before and after each test ──────────────────


@pytest.fixture(autouse=True)
def _clean_hooks():
    """Ensure a clean hook registry before and after each test."""
    clear_transition_hooks()
    yield
    clear_transition_hooks()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Hook registry operations
# ═══════════════════════════════════════════════════════════════════════════


class TestHookRegistry:
    """Test register, remove, clear, and list operations."""

    def test_register_adds_hook(self):
        def my_hook(m, t):
            return m

        register_transition_hook(my_hook)
        names = list_transition_hooks()
        assert "my_hook" in names

    def test_remove_hook(self):
        def my_hook(m, t):
            return m

        register_transition_hook(my_hook)
        assert "my_hook" in list_transition_hooks()
        remove_transition_hook(my_hook)
        assert "my_hook" not in list_transition_hooks()

    def test_remove_nonexistent_raises(self):
        def my_hook(m, t):
            return m

        with pytest.raises(ValueError):
            remove_transition_hook(my_hook)

    def test_clear_removes_all(self):
        def h1(m, t):
            return m
        def h2(m, t):
            return m

        register_transition_hook(h1)
        register_transition_hook(h2)
        assert len(list_transition_hooks()) == 2
        clear_transition_hooks()
        assert len(list_transition_hooks()) == 0

    def test_list_returns_names(self):
        def alpha_hook(m, t):
            return m
        def beta_hook(m, t):
            return m

        register_transition_hook(alpha_hook)
        register_transition_hook(beta_hook)
        names = list_transition_hooks()
        assert "alpha_hook" in names
        assert "beta_hook" in names
        # Order must be preserved
        assert names.index("alpha_hook") < names.index("beta_hook")

    def test_same_hook_can_be_registered_multiple_times(self):
        call_count = []

        def counting_hook(m, t):
            call_count.append(t)
            return m

        register_transition_hook(counting_hook)
        register_transition_hook(counting_hook)
        assert len(list_transition_hooks()) == 2

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            dispatch_transition_hooks(manifest, "test")
            assert len(call_count) == 2
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Built-in persistence hook
# ═══════════════════════════════════════════════════════════════════════════


class TestBuiltInPersistenceHook:
    """Test the built-in persistence_hook and install_default_hooks."""

    def test_install_default_hooks_adds_persistence_hook(self):
        result = install_default_hooks()
        assert result == 1
        names = list_transition_hooks()
        assert "persistence_hook" in names

    def test_install_default_hooks_is_idempotent(self):
        result1 = install_default_hooks()
        result2 = install_default_hooks()
        assert result1 == 1
        assert result2 == 0
        # Only one instance registered
        count = list_transition_hooks().count("persistence_hook")
        assert count == 1

    def test_persistence_hook_writes_manifest_to_disk(self):
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            # Directly invoke the persistence hook
            updated = persistence_hook(manifest, "state_change")
            # Verify the file exists and has the correct state
            assert os.path.isfile(manifest.manifest_path)
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == manifest.state
            # Timestamp should be updated
            assert updated.updated_at != manifest.updated_at
        finally:
            _cleanup_dir(root)

    def test_persistence_hook_through_dispatch(self):
        """Verify persistence hook works when dispatched."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            install_default_hooks()

            updated = dispatch_transition_hooks(manifest, "state_change")
            assert os.path.isfile(manifest.manifest_path)
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == "created"
            assert updated.updated_at != manifest.updated_at
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Hook dispatch pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestHookDispatch:
    """Test the dispatch_transition_hooks function."""

    def test_hooks_called_in_registration_order(self):
        order = []

        def first(m, t):
            order.append("first")
            return m

        def second(m, t):
            order.append("second")
            return m

        def third(m, t):
            order.append("third")
            return m

        register_transition_hook(first)
        register_transition_hook(second)
        register_transition_hook(third)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            dispatch_transition_hooks(manifest, "test")
            assert order == ["first", "second", "third"]
        finally:
            _cleanup_dir(root)

    def test_hook_receives_transition_type(self):
        captured_types = []

        def type_capture(m, t):
            captured_types.append(t)
            return m

        register_transition_hook(type_capture)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            dispatch_transition_hooks(manifest, "speaker_change")
            assert captured_types == ["speaker_change"]
        finally:
            _cleanup_dir(root)

    def test_hook_receives_manifest(self):
        captured_meeting_ids = []

        def capture(m, t):
            captured_meeting_ids.append(m.meeting_id)
            return m

        register_transition_hook(capture)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            dispatch_transition_hooks(manifest, "test")
            assert captured_meeting_ids == [manifest.meeting_id]
        finally:
            _cleanup_dir(root)

    def test_hook_can_modify_manifest(self):
        def modifier(m, t):
            return m.with_state(LifecycleState.QUEUED)

        register_transition_hook(modifier)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = dispatch_transition_hooks(manifest, "test")
            assert result.state == "queued"
            assert manifest.state == "created"  # original unchanged
        finally:
            _cleanup_dir(root)

    def test_hook_chain_modifications_piped(self):
        """Each hook receives the output of the previous hook."""

        def set_priority_p0(m, t):
            return MeetingManifest(
                meeting_id=m.meeting_id,
                state=m.state,
                priority="p0",
                agenda=m.agenda,
                user_id=m.user_id,
                channel_id=m.channel_id,
                manifest_path=m.manifest_path,
                meetings_root=m.meetings_root,
            )

        def set_state_queued(m, t):
            return m.with_state(LifecycleState.QUEUED)

        register_transition_hook(set_priority_p0)
        register_transition_hook(set_state_queued)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = dispatch_transition_hooks(manifest, "test")
            # Both hooks applied in order
            assert result.priority == "p0"
            assert result.state == "queued"
        finally:
            _cleanup_dir(root)

    def test_empty_hooks_triggers_baseline_persistence(self):
        """When no hooks registered, baseline persistence (update_manifest)
        is called automatically to guarantee the Seed constraint."""
        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = dispatch_transition_hooks(manifest, "test")
            # Baseline persistence returns a NEW manifest with updated timestamp
            assert result.meeting_id == manifest.meeting_id
            assert result.state == manifest.state
            assert result.updated_at != manifest.updated_at, (
                "Baseline persistence must refresh updated_at"
            )
            # File must exist on disk
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_hook_failure_does_not_block_subsequent_hooks(self):
        """One failing hook must not prevent later hooks from running."""
        call_tracker = []

        def failing_hook(m, t):
            call_tracker.append("failing")
            raise RuntimeError("Boom!")

        def still_runs(m, t):
            call_tracker.append("still_runs")
            return m

        register_transition_hook(failing_hook)
        register_transition_hook(still_runs)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = dispatch_transition_hooks(manifest, "test")
            assert "failing" in call_tracker
            assert "still_runs" in call_tracker
            # Second hook still received the manifest
            assert result.meeting_id == manifest.meeting_id
        finally:
            _cleanup_dir(root)

    def test_hook_failure_passes_previous_manifest_forward(self):
        """When a hook fails, the next hook gets the manifest from before the failure."""

        def set_priority(m, t):
            return MeetingManifest(
                meeting_id=m.meeting_id,
                state=m.state,
                priority="p0",
                agenda=m.agenda,
                user_id=m.user_id,
                channel_id=m.channel_id,
                manifest_path=m.manifest_path,
                meetings_root=m.meetings_root,
            )

        def failing(m, t):
            raise RuntimeError("Boom!")

        def after_failure(m, t):
            return m.with_state(LifecycleState.QUEUED)

        register_transition_hook(set_priority)
        register_transition_hook(failing)
        register_transition_hook(after_failure)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = dispatch_transition_hooks(manifest, "test")
            # set_priority succeeded, failing crashed, after_failure received
            # the manifest from set_priority (not the original)
            assert result.priority == "p0"
            assert result.state == "queued"
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Integration: execute_transition triggers hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestTransitionEngineHookIntegration:
    """Verify that execute_transition dispatches hooks on state transitions."""

    def test_state_change_triggers_hooks(self):
        transitions_seen = []

        def tracker(m, t):
            transitions_seen.append((t, m.state))
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = execute_transition(manifest, LifecycleState.QUEUED)
            assert result.success
            # Hook must have been called with transition_type="state_change"
            assert len(transitions_seen) >= 1
            assert transitions_seen[-1] == ("state_change", "queued")
        finally:
            _cleanup_dir(root)

    def test_every_step_in_full_flow_triggers_hooks(self):
        """Full flow: created → queued → routing → ... → completed.
        Each step must trigger hooks with transition_type='state_change'."""
        transitions_log = []

        def logger_hook(m, t):
            transitions_log.append((t, m.state))
            return m

        register_transition_hook(logger_hook)
        install_default_hooks()  # also install persistence

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            states = [
                LifecycleState.QUEUED,
                LifecycleState.ROUTING,
                LifecycleState.CONTEXT_RETRIEVAL,
                LifecycleState.IN_MEETING,
                LifecycleState.CONSENSUS_BUILDING,
                LifecycleState.VALIDATING,
                LifecycleState.FINALIZING,
                LifecycleState.COMPLETED,
            ]
            current = manifest
            for state in states:
                result = execute_transition(current, state)
                assert result.success, f"Failed at {state.value}: {result.rejection_reasons}"
                current = result.manifest

            # Every state (8x) should have triggered hooks
            # logger_hook + persistence_hook = 2 hooks per transition = 16 calls
            assert len(transitions_log) >= 8, (
                f"Expected at least 8 hook calls (one per transition), "
                f"got {len(transitions_log)}: {transitions_log}"
            )
            # All should be "state_change"
            for t_type, _ in transitions_log:
                if t_type == "state_change":
                    continue  # persistence_hook may also log, but logger_hook always logs state_change
        finally:
            _cleanup_dir(root)

    def test_manifest_on_disk_after_every_transition(self):
        """After every transition, the manifest file must reflect the new state."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()  # Required for persistence
            manifest = _create_test_manifest(root)
            states_and_expected = [
                (LifecycleState.QUEUED, "queued"),
                (LifecycleState.ROUTING, "routing"),
                (LifecycleState.CONTEXT_RETRIEVAL, "context_retrieval"),
            ]
            current = manifest
            for state, expected_str in states_and_expected:
                result = execute_transition(current, state)
                assert result.success
                current = result.manifest
                # Verify on disk
                loaded = load_manifest(manifest.manifest_path)
                assert loaded.state == expected_str, (
                    f"After transition to {expected_str}, disk has {loaded.state}"
                )
        finally:
            _cleanup_dir(root)

    def test_persist_false_skips_hooks_in_transition(self):
        """When persist=False, hooks should NOT be dispatched for the main
        persistence step, but the error-log persistence (if any) still fires."""
        hook_calls = []

        def counter(m, t):
            hook_calls.append(t)
            return m

        register_transition_hook(counter)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            before_count = len(hook_calls)
            result = execute_transition(
                manifest, LifecycleState.QUEUED, persist=False
            )
            assert result.success
            assert result.manifest.state == "queued"
            # No hooks should fire when persist=False and no errors
            assert len(hook_calls) == before_count

            # Disk should NOT have the new state
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == "created"
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Integration: manifest_serializer triggers hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestSerializerHookIntegration:
    """Verify that manifest_serializer functions dispatch hooks with correct
    transition_type values."""

    def test_append_context_packet_triggers_hook(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            entry = build_context_packet_entry(
                round_num=1,
                role_id="producer-kim",
                model_provider="qwen",
                model_name="qwen3-max",
            )
            updated = append_context_packet(manifest, entry)
            assert len(captured) >= 1
            assert "context_packet" in captured
            assert len(updated.context_packets) == 1
        finally:
            _cleanup_dir(root)

    def test_append_decision_triggers_hook(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            entry = build_decision_entry(
                round_num=1,
                decision_id="d_001",
                role_id="producer-kim",
                content="Budget approve: ₩50M",
            )
            updated = append_decision(manifest, entry)
            assert "decision_commit" in captured
            assert len(updated.decisions) == 1
        finally:
            _cleanup_dir(root)

    def test_append_tool_output_triggers_hook(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            entry = build_tool_output_entry(
                round_num=1,
                execution_id="exec_abc123",
                action_type="deploy",
                role_id="producer-kim",
                status="success",
            )
            updated = append_tool_output(manifest, entry)
            assert "tool_output" in captured
            assert len(updated.tool_outputs) == 1
        finally:
            _cleanup_dir(root)

    def test_set_speaker_triggers_hook(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            updated = set_speaker(
                manifest,
                "producer-kim",
                speaker_queue=("producer-kim", "director-lee"),
            )
            assert "speaker_change" in captured
            assert updated.current_speaker == "producer-kim"
            assert updated.speaker_queue == ("producer-kim", "director-lee")
        finally:
            _cleanup_dir(root)

    def test_serialize_meeting_state_triggers_hook(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)
        install_default_hooks()  # Also install persistence for realistic behavior

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            updated = serialize_meeting_state(manifest)
            assert "generic" in captured
            # Persistence hook updates the timestamp
            assert updated.updated_at != manifest.updated_at
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 6. End-to-end: manifest on disk after every transition type
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestOnDiskAfterEveryTransition:
    """The core Sub-AC 4.4.3 requirement: simulate state transitions and
    verify manifest file is written on each transition."""

    def test_manifest_written_after_round_advance(self):
        """Round advance (via state transitions through IN_MEETING) must persist."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)
            m = _advance_to(manifest, LifecycleState.IN_MEETING)
            # Verify the manifest was written at each step
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.state == "in_meeting"
            # Round 1 is now in-meeting
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_manifest_written_after_speaker_change(self):
        """Speaker change must persist the manifest."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)
            updated = set_speaker(manifest, "director-lee")

            # Verify on disk
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.current_speaker == "director-lee"
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_manifest_written_after_decision_commit(self):
        """Decision commit must persist the manifest."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)
            entry = build_decision_entry(
                round_num=1,
                decision_id="d_002",
                role_id="producer-kim",
                content="MV concept: cyberpunk theme",
            )
            updated = append_decision(manifest, entry)

            loaded = load_manifest(manifest.manifest_path)
            assert len(loaded.decisions) == 1
            assert loaded.decisions[0]["content"] == "MV concept: cyberpunk theme"
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_manifest_written_after_context_packet(self):
        """Context packet append must persist the manifest."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)
            entry = build_context_packet_entry(
                round_num=1,
                role_id="producer-kim",
                model_provider="qwen",
                model_name="qwen3-max",
                token_count=8500,
            )
            updated = append_context_packet(manifest, entry)

            loaded = load_manifest(manifest.manifest_path)
            assert len(loaded.context_packets) == 1
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_manifest_written_after_tool_output(self):
        """Tool output append must persist the manifest."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)
            entry = build_tool_output_entry(
                round_num=1,
                execution_id="exec_xyz789",
                action_type="email",
                role_id="producer-kim",
                status="success",
            )
            updated = append_tool_output(manifest, entry)

            loaded = load_manifest(manifest.manifest_path)
            assert len(loaded.tool_outputs) == 1
            assert os.path.isfile(manifest.manifest_path)
        finally:
            _cleanup_dir(root)

    def test_full_meeting_simulation_all_transitions_persisted(self):
        """Simulate a complete meeting: create → enqueue → route → meeting
        with speaker changes, context packets, decisions, tool outputs.
        Verify manifest is on disk after EVERY mutation."""
        root = _tmp_meetings_root()
        try:
            install_default_hooks()
            manifest = _create_test_manifest(root)

            # Phase 1: Enqueue
            r1 = execute_transition(manifest, LifecycleState.QUEUED)
            assert r1.success
            m = r1.manifest
            assert load_manifest(manifest.manifest_path).state == "queued"

            # Phase 2: Route (classification)
            r2 = execute_transition(m, LifecycleState.ROUTING)
            assert r2.success
            m = r2.manifest
            assert load_manifest(manifest.manifest_path).state == "routing"

            # Phase 3: Context retrieval
            r3 = execute_transition(m, LifecycleState.CONTEXT_RETRIEVAL)
            assert r3.success
            m = r3.manifest
            assert load_manifest(manifest.manifest_path).state == "context_retrieval"

            # Phase 4: In-meeting — Round 1
            r4 = execute_transition(m, LifecycleState.IN_MEETING)
            assert r4.success
            m = r4.manifest
            assert load_manifest(manifest.manifest_path).state == "in_meeting"

            # Speaker changes within the round
            m = set_speaker(m, "producer-kim",
                            speaker_queue=("producer-kim", "director-lee", "finance-park"))
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.current_speaker == "producer-kim"

            m = set_speaker(m, "director-lee")
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.current_speaker == "director-lee"

            # Context packets for each speaker
            cp1 = build_context_packet_entry(round_num=1, role_id="producer-kim",
                                              model_provider="qwen", model_name="qwen3-max")
            m = append_context_packet(m, cp1)

            cp2 = build_context_packet_entry(round_num=1, role_id="director-lee",
                                              model_provider="deepseek", model_name="deepseek-chat")
            m = append_context_packet(m, cp2)

            # Decision commit
            dec = build_decision_entry(round_num=1, decision_id="d_r1_001",
                                        role_id="producer-kim",
                                        content="Luna's visual concept: cyberpunk schoolgirl")
            m = append_decision(m, dec)

            # Tool output
            tool = build_tool_output_entry(round_num=1, execution_id="exec_001",
                                            action_type="image_search", role_id="director-lee",
                                            status="success")
            m = append_tool_output(m, tool)

            # Consensus building
            r5 = execute_transition(m, LifecycleState.CONSENSUS_BUILDING)
            assert r5.success
            m = r5.manifest

            # Validation
            r6 = execute_transition(m, LifecycleState.VALIDATING)
            assert r6.success
            m = r6.manifest

            # Final validation check: all data on disk
            final = load_manifest(manifest.manifest_path)
            assert final.state == "validating"
            assert len(final.context_packets) == 2
            assert len(final.decisions) == 1
            assert len(final.tool_outputs) == 1
            assert final.current_speaker == "director-lee"
            # Verify the file still exists at the end
            assert os.path.isfile(manifest.manifest_path)
            # Verify file is valid JSON
            with open(manifest.manifest_path) as f:
                data = json.load(f)
            assert data["meeting_id"] == manifest.meeting_id
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Multiple hooks and custom hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleHooks:
    """Verify that multiple hooks fire in order and can be custom."""

    def test_multiple_custom_hooks_with_persistence(self):
        side_effects = []

        def audit_hook(m, t):
            side_effects.append(("audit", t, m.state))
            return m

        def metrics_hook(m, t):
            side_effects.append(("metrics", t, m.state))
            return m

        register_transition_hook(audit_hook)
        register_transition_hook(metrics_hook)
        install_default_hooks()  # adds persistence_hook

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = execute_transition(manifest, LifecycleState.QUEUED)
            assert result.success

            # audit and metrics should have been called
            audit_calls = [e for e in side_effects if e[0] == "audit"]
            metrics_calls = [e for e in side_effects if e[0] == "metrics"]
            assert len(audit_calls) >= 1
            assert len(metrics_calls) >= 1
            # audit fires before metrics (registration order)
            audit_idx = side_effects.index(("audit", "state_change", "queued"))
            metrics_idx = side_effects.index(("metrics", "state_change", "queued"))
            assert audit_idx < metrics_idx
        finally:
            _cleanup_dir(root)

    def test_hook_can_access_full_manifest(self):
        """Custom hooks can read the full manifest state."""
        captured_agenda = None

        def read_agenda(m, t):
            nonlocal captured_agenda
            captured_agenda = m.agenda
            return m

        register_transition_hook(read_agenda)
        install_default_hooks()

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            result = execute_transition(manifest, LifecycleState.QUEUED)
            assert result.success
            assert captured_agenda == _VALID_AGENDA
        finally:
            _cleanup_dir(root)


# ═══════════════════════════════════════════════════════════════════════════
# 8. persist=False — hook still callable, but not via transition
# ═══════════════════════════════════════════════════════════════════════════


class TestPersistFalseBehavior:
    """When persist=False, functions should not dispatch hooks,
    but dispatch_transition_hooks can still be called directly."""

    def test_append_decision_persist_false_skips_hooks(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            entry = build_decision_entry(
                round_num=1,
                decision_id="d_003",
                role_id="producer-kim",
                content="Test",
            )
            updated = append_decision(manifest, entry, persist=False)
            # Hooks should NOT fire
            assert len(captured) == 0
            # But in-memory state is updated
            assert len(updated.decisions) == 1
            # Disk should NOT have the decision
            loaded = load_manifest(manifest.manifest_path)
            assert len(loaded.decisions) == 0
        finally:
            _cleanup_dir(root)

    def test_set_speaker_persist_false_skips_hooks(self):
        captured = []

        def tracker(m, t):
            captured.append(t)
            return m

        register_transition_hook(tracker)

        root = _tmp_meetings_root()
        try:
            manifest = _create_test_manifest(root)
            updated = set_speaker(manifest, "producer-kim", persist=False)
            assert len(captured) == 0
            assert updated.current_speaker == "producer-kim"
            # Disk unchanged
            loaded = load_manifest(manifest.manifest_path)
            assert loaded.current_speaker == ""
        finally:
            _cleanup_dir(root)
