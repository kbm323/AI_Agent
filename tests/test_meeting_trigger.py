"""Tests for the meeting creation trigger module (Sub-AC 1c).

Verifies the complete meeting creation pipeline:
- Valid command request → correct MeetingContext
- Input validation (empty fields raise ValueError)
- Directory structure creation
- Manifest content completeness
- JSON serialization roundtrip
- State transitions (using 15-state lifecycle)
- Error logging
- Uniqueness guarantees
- Configuration defaults and overrides
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.meeting_trigger import (
    MANIFEST_SCHEMA_VERSION,
    MAX_AGENTS_PER_MEETING,
    MAX_ROUNDS,
    TOKEN_LIMIT_CODEX,
    TOKEN_LIMIT_VALIDATOR,
    TOKEN_LIMIT_WORKER,
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    create_meeting,
    load_manifest,
    update_manifest,
)
from src.shared.lifecycle import (
    LifecycleState,
    validate_transition,
)

# ── Test fixtures ───────────────────────────────────────────────────────

_VALID_AGENDA = (
    "신규 캐릭터 '루나'의 비주얼 디자인을 논의하고, "
    "SNS 홍보 전략을 수립하며, 기존 게임엔진 백엔드 API 리팩토링도 함께 검토해주세요."
)

_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


def _make_request(**overrides: str) -> MeetingCommandRequest:
    """Build a valid MeetingCommandRequest with optional overrides."""
    defaults = {
        "agenda": _VALID_AGENDA,
        "user_id": _VALID_USER_ID,
        "channel_id": _VALID_CHANNEL_ID,
        "priority": "p1",
    }
    defaults.update(overrides)
    return MeetingCommandRequest(**defaults)


def _tmp_meetings_root() -> str:
    """Create a temporary directory for meeting storage."""
    return tempfile.mkdtemp(prefix="ai_agent_test_meetings_")


# ── 1. Basic creation ──────────────────────────────────────────────────

class TestCreateMeeting:
    """Verify successful meeting creation with valid inputs."""

    def test_creates_meeting_with_valid_request(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert isinstance(ctx, MeetingContext)
            assert ctx.meeting_id.startswith("meeting_")
            assert ctx.manifest.state == "created"
            assert ctx.manifest.agenda == _VALID_AGENDA
            assert ctx.manifest.user_id == _VALID_USER_ID
            assert ctx.manifest.channel_id == _VALID_CHANNEL_ID
        finally:
            _cleanup_dir(root)

    def test_meeting_id_is_unique_per_call(self):
        root = _tmp_meetings_root()
        try:
            ctx1 = create_meeting(_make_request(), meetings_root=root)
            ctx2 = create_meeting(_make_request(), meetings_root=root)
            assert ctx1.meeting_id != ctx2.meeting_id, (
                "Each meeting must have a unique ID"
            )
        finally:
            _cleanup_dir(root)

    def test_meeting_id_format(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            parts = ctx.meeting_id.split("_")
            # Format: meeting_YYYYMMDD_<12-hex-chars>
            assert len(parts) == 3, (
                f"Expected 'meeting_DATE_UUID', got: {ctx.meeting_id}"
            )
            assert parts[0] == "meeting"
            assert len(parts[1]) == 8  # YYYYMMDD
            assert len(parts[2]) == 12  # short UUID hex
            assert all(c in "0123456789abcdef" for c in parts[2])
        finally:
            _cleanup_dir(root)

    def test_manifest_json_is_written(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert os.path.isfile(ctx.manifest_path), (
                f"manifest.json not found at {ctx.manifest_path}"
            )
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["meeting_id"] == ctx.meeting_id
            assert data["state"] == "created"
            assert data["agenda"] == _VALID_AGENDA
        finally:
            _cleanup_dir(root)


# ── 2. Input validation ────────────────────────────────────────────────

class TestInputValidation:
    """Verify that invalid inputs raise appropriate errors."""

    def test_empty_agenda_raises_value_error(self):
        root = _tmp_meetings_root()
        try:
            with pytest.raises(ValueError, match="agenda must not be empty"):
                create_meeting(
                    _make_request(agenda=""), meetings_root=root
                )
        finally:
            _cleanup_dir(root)

    def test_whitespace_only_agenda_raises_value_error(self):
        root = _tmp_meetings_root()
        try:
            with pytest.raises(ValueError, match="agenda must not be empty"):
                create_meeting(
                    _make_request(agenda="   \n\t  "),
                    meetings_root=root,
                )
        finally:
            _cleanup_dir(root)

    def test_empty_user_id_raises_value_error(self):
        root = _tmp_meetings_root()
        try:
            with pytest.raises(ValueError, match="user_id must not be empty"):
                create_meeting(
                    _make_request(user_id=""), meetings_root=root
                )
        finally:
            _cleanup_dir(root)

    def test_empty_channel_id_raises_value_error(self):
        root = _tmp_meetings_root()
        try:
            with pytest.raises(ValueError, match="channel_id must not be empty"):
                create_meeting(
                    _make_request(channel_id=""), meetings_root=root
                )
        finally:
            _cleanup_dir(root)


# ── 3. Directory structure ─────────────────────────────────────────────

class TestDirectoryStructure:
    """Verify the correct directory hierarchy is created."""

    def test_all_subdirectories_created(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            expected_dirs = [
                ctx.meeting_dir,
                ctx.rounds_dir,
                ctx.raw_outputs_dir,
                ctx.decisions_dir,
                ctx.knowledge_dir,
            ]
            for d in expected_dirs:
                assert os.path.isdir(d), f"Directory not created: {d}"
        finally:
            _cleanup_dir(root)

    def test_meeting_dir_is_under_meetings_root(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            resolved_root = str(Path(root).resolve())
            assert ctx.meeting_dir.startswith(resolved_root), (
                f"Meeting dir {ctx.meeting_dir} not under root {root}"
            )
        finally:
            _cleanup_dir(root)

    def test_meeting_dir_isolation(self):
        """Two meetings must have completely separate directories."""
        root = _tmp_meetings_root()
        try:
            ctx1 = create_meeting(_make_request(), meetings_root=root)
            ctx2 = create_meeting(_make_request(), meetings_root=root)
            assert ctx1.meeting_dir != ctx2.meeting_dir
            assert not ctx1.meeting_dir.startswith(ctx2.meeting_dir)
            assert not ctx2.meeting_dir.startswith(ctx1.meeting_dir)
        finally:
            _cleanup_dir(root)

    def test_manifest_in_meeting_dir(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest_path.startswith(ctx.meeting_dir)
            assert ctx.manifest_path.endswith("manifest.json")
        finally:
            _cleanup_dir(root)


# ── 4. Manifest content ────────────────────────────────────────────────

class TestManifestContent:
    """Verify all required Seed ontology fields are present in the manifest."""

    _REQUIRED_STRING_FIELDS = (
        "meeting_id", "state", "priority", "agenda", "agenda_type",
        "validation_verdict", "consensus", "user_id", "channel_id",
        "thread_id", "manifest_path", "meetings_root",
        "primary_validator_model", "conditional_validator_model",
        "schema_version",
    )

    _REQUIRED_LIST_FIELDS = (
        "tags", "risk_tags", "required_roles", "optional_roles",
        "error_log",
    )

    _REQUIRED_NUMERIC_FIELDS = (
        "round_count", "validation_score",
        "max_rounds", "max_agents_per_meeting",
        "token_limit_worker", "token_limit_validator",
        "token_limit_codex",
    )

    _REQUIRED_BOOL_FIELDS = (
        "validator_required", "codex_required",
    )

    _REQUIRED_TIMESTAMP_FIELDS = (
        "created_at", "updated_at",
    )

    def test_all_required_string_fields_present(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            for field in self._REQUIRED_STRING_FIELDS:
                assert field in data, (
                    f"Required string field '{field}' missing from manifest"
                )
                assert isinstance(data[field], str), (
                    f"Field '{field}' must be a string, got "
                    f"{type(data[field]).__name__}"
                )
        finally:
            _cleanup_dir(root)

    def test_all_required_list_fields_present(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            for field in self._REQUIRED_LIST_FIELDS:
                assert field in data, (
                    f"Required list field '{field}' missing from manifest"
                )
                assert isinstance(data[field], list), (
                    f"Field '{field}' must be a list, got "
                    f"{type(data[field]).__name__}"
                )
        finally:
            _cleanup_dir(root)

    def test_all_required_numeric_fields_present(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            for field in self._REQUIRED_NUMERIC_FIELDS:
                assert field in data, (
                    f"Required numeric field '{field}' missing from manifest"
                )
                assert isinstance(data[field], (int, float)), (
                    f"Field '{field}' must be numeric, got "
                    f"{type(data[field]).__name__}"
                )
        finally:
            _cleanup_dir(root)

    def test_all_required_bool_fields_present(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            for field in self._REQUIRED_BOOL_FIELDS:
                assert field in data, (
                    f"Required bool field '{field}' missing from manifest"
                )
                assert isinstance(data[field], bool), (
                    f"Field '{field}' must be a bool, got "
                    f"{type(data[field]).__name__}"
                )
        finally:
            _cleanup_dir(root)

    def test_timestamps_are_iso_8601(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            for field in self._REQUIRED_TIMESTAMP_FIELDS:
                assert field in data
                assert "T" in data[field], (
                    f"Timestamp '{field}' is not ISO 8601: {data[field]}"
                )
        finally:
            _cleanup_dir(root)

    def test_initial_state_is_created(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest.state == "created"
            assert ctx.manifest.state == str(LifecycleState.CREATED)
        finally:
            _cleanup_dir(root)

    def test_classification_fields_start_empty(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest.agenda_type == ""
            assert ctx.manifest.tags == ()
            assert ctx.manifest.risk_tags == ()
            assert ctx.manifest.required_roles == ()
            assert ctx.manifest.optional_roles == ()
            assert ctx.manifest.round_count == 0
            assert ctx.manifest.validation_score == 0.0
        finally:
            _cleanup_dir(root)

    def test_config_fields_match_defaults(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest.max_rounds == MAX_ROUNDS
            assert ctx.manifest.max_agents_per_meeting == MAX_AGENTS_PER_MEETING
            assert ctx.manifest.token_limit_worker == TOKEN_LIMIT_WORKER
            assert ctx.manifest.token_limit_validator == TOKEN_LIMIT_VALIDATOR
            assert ctx.manifest.token_limit_codex == TOKEN_LIMIT_CODEX
            assert ctx.manifest.schema_version == MANIFEST_SCHEMA_VERSION
        finally:
            _cleanup_dir(root)


# ── 5. JSON roundtrip ──────────────────────────────────────────────────

class TestManifestRoundtrip:
    """Verify manifest serialization and deserialization."""

    def test_to_dict_produces_json_serializable(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            d = manifest.to_dict()
            # Must not raise
            json.dumps(d, ensure_ascii=False)
        finally:
            _cleanup_dir(root)

    def test_to_json_roundtrip(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            json_str = manifest.to_json()
            data = json.loads(json_str)
            assert data["meeting_id"] == manifest.meeting_id
            assert data["agenda"] == manifest.agenda
            assert data["state"] == manifest.state
        finally:
            _cleanup_dir(root)

    def test_load_manifest_roundtrip(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            loaded = load_manifest(ctx.manifest_path)
            assert loaded.meeting_id == ctx.manifest.meeting_id
            assert loaded.state == ctx.manifest.state
            assert loaded.agenda == ctx.manifest.agenda
            assert loaded.user_id == ctx.manifest.user_id
            assert loaded.channel_id == ctx.manifest.channel_id
            assert loaded.priority == ctx.manifest.priority
            assert loaded.created_at == ctx.manifest.created_at
        finally:
            _cleanup_dir(root)

    def test_load_manifest_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_manifest("/nonexistent/path/manifest.json")


# ── 6. State transitions (15-state lifecycle) ──────────────────────────

class TestStateTransitions:
    """Verify manifest state transitions via with_state() using the
    15-state lifecycle from LifecycleState."""

    def test_transition_created_to_queued(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            original = ctx.manifest
            assert original.state == "created"
            assert validate_transition(LifecycleState.CREATED, LifecycleState.QUEUED)

            queued = original.with_state(LifecycleState.QUEUED)
            assert queued.state == "queued"
            assert original.state == "created", "Original must not be mutated"
            assert queued.meeting_id == original.meeting_id
        finally:
            _cleanup_dir(root)

    def test_transition_queued_to_routing(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            queued = ctx.manifest.with_state(LifecycleState.QUEUED)
            routing = queued.with_state(LifecycleState.ROUTING)
            assert routing.state == "routing"
            assert queued.state == "queued"
            assert validate_transition(LifecycleState.QUEUED, LifecycleState.ROUTING)
        finally:
            _cleanup_dir(root)

    def test_full_normal_flow(self):
        """Verify the normal happy-path flow through all states."""
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)

            m = ctx.manifest
            m = m.with_state(LifecycleState.QUEUED)
            assert m.state == "queued"
            m = m.with_state(LifecycleState.ROUTING)
            assert m.state == "routing"
            m = m.with_state(LifecycleState.CONTEXT_RETRIEVAL)
            assert m.state == "context_retrieval"
            m = m.with_state(LifecycleState.IN_MEETING)
            assert m.state == "in_meeting"
            m = m.with_state(LifecycleState.CONSENSUS_BUILDING)
            assert m.state == "consensus_building"
            m = m.with_state(LifecycleState.VALIDATING)
            assert m.state == "validating"
            m = m.with_state(LifecycleState.FINALIZING)
            assert m.state == "finalizing"
            m = m.with_state(LifecycleState.COMPLETED)
            assert m.state == "completed"
            assert str(LifecycleState.COMPLETED) == "completed"
        finally:
            _cleanup_dir(root)

    def test_transition_to_failed(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert validate_transition(LifecycleState.CREATED, LifecycleState.FAILED)
            failed = ctx.manifest.with_state(LifecycleState.FAILED)
            assert failed.state == "failed"
        finally:
            _cleanup_dir(root)

    def test_transition_to_cancelled(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert validate_transition(LifecycleState.CREATED, LifecycleState.CANCELLED)
            cancelled = ctx.manifest.with_state(LifecycleState.CANCELLED)
            assert cancelled.state == "cancelled"
        finally:
            _cleanup_dir(root)

    def test_completed_is_terminal(self):
        """completed cannot transition to anything else."""
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            m = ctx.manifest.with_state(LifecycleState.QUEUED)
            m = m.with_state(LifecycleState.ROUTING)
            m = m.with_state(LifecycleState.CONTEXT_RETRIEVAL)
            m = m.with_state(LifecycleState.IN_MEETING)
            m = m.with_state(LifecycleState.CONSENSUS_BUILDING)
            m = m.with_state(LifecycleState.VALIDATING)
            m = m.with_state(LifecycleState.FINALIZING)
            m = m.with_state(LifecycleState.COMPLETED)
            # Can still set it (with_state doesn't validate transitions)
            # but validate_transition should reject any outgoing
            assert not validate_transition(
                LifecycleState.COMPLETED, LifecycleState.QUEUED
            )
            assert not validate_transition(
                LifecycleState.COMPLETED, LifecycleState.ROUTING
            )
        finally:
            _cleanup_dir(root)

    def test_transition_updates_timestamp(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            import time
            time.sleep(0.01)  # ensure timestamp differs
            updated = manifest.with_state(LifecycleState.QUEUED)
            assert updated.updated_at != manifest.updated_at, (
                "updated_at must change on state transition"
            )
        finally:
            _cleanup_dir(root)


# ── 7. Error logging ──────────────────────────────────────────────────

class TestErrorLogging:
    """Verify error_log append behaviour."""

    def test_initial_error_log_empty(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest.error_log == ()
        finally:
            _cleanup_dir(root)

    def test_with_error_appends_entry(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            error_entry = {
                "timestamp": "2026-06-10T12:00:00Z",
                "error_type": "qwen_timeout",
                "message": "Qwen API timed out after 30s",
                "severity": "warning",
                "recovery": "falling back to static routing",
            }
            updated = manifest.with_error(error_entry)
            assert len(updated.error_log) == 1
            assert updated.error_log[0] == error_entry
            assert manifest.error_log == (), (
                "Original manifest error_log must not be mutated"
            )
        finally:
            _cleanup_dir(root)

    def test_multiple_errors_accumulate(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            e1 = {"timestamp": "ts1", "error_type": "e1", "message": "m1"}
            e2 = {"timestamp": "ts2", "error_type": "e2", "message": "m2"}
            e3 = {"timestamp": "ts3", "error_type": "e3", "message": "m3"}

            result = manifest.with_error(e1).with_error(e2).with_error(e3)
            assert len(result.error_log) == 3
            assert result.error_log[0] == e1
            assert result.error_log[1] == e2
            assert result.error_log[2] == e3
        finally:
            _cleanup_dir(root)


# ── 8. Priority handling ───────────────────────────────────────────────

class TestPriorityHandling:
    """Verify priority levels are correctly passed through."""

    def test_p0_priority(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(
                _make_request(priority="p0"), meetings_root=root
            )
            assert ctx.manifest.priority == "p0"
        finally:
            _cleanup_dir(root)

    def test_p1_priority(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(
                _make_request(priority="p1"), meetings_root=root
            )
            assert ctx.manifest.priority == "p1"
        finally:
            _cleanup_dir(root)

    def test_p2_default_priority(self):
        root = _tmp_meetings_root()
        try:
            req = MeetingCommandRequest(
                agenda="Test agenda",
                user_id="user123",
                channel_id="ch456",
            )
            ctx = create_meeting(req, meetings_root=root)
            assert ctx.manifest.priority == "p2"
        finally:
            _cleanup_dir(root)

    def test_p3_priority(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(
                _make_request(priority="p3"), meetings_root=root
            )
            assert ctx.manifest.priority == "p3"
        finally:
            _cleanup_dir(root)


# ── 9. Config overrides ────────────────────────────────────────────────

class TestConfigOverrides:
    """Verify custom MeetingConfig values propagate to the manifest."""

    def test_custom_meetings_root_in_manifest(self):
        root = _tmp_meetings_root()
        try:
            config = MeetingConfig(meetings_root="/custom/meetings/path")
            ctx = create_meeting(_make_request(), meetings_root=root, config=config)
            assert ctx.manifest.meetings_root == "/custom/meetings/path"
        finally:
            _cleanup_dir(root)

    def test_custom_token_limits(self):
        root = _tmp_meetings_root()
        try:
            config = MeetingConfig(
                token_limit_worker=8000,
                token_limit_validator=16000,
                token_limit_codex=24000,
            )
            ctx = create_meeting(_make_request(), meetings_root=root, config=config)
            assert ctx.manifest.token_limit_worker == 8000
            assert ctx.manifest.token_limit_validator == 16000
            assert ctx.manifest.token_limit_codex == 24000
        finally:
            _cleanup_dir(root)

    def test_custom_max_rounds(self):
        root = _tmp_meetings_root()
        try:
            config = MeetingConfig(max_rounds=5)
            ctx = create_meeting(_make_request(), meetings_root=root, config=config)
            assert ctx.manifest.max_rounds == 5
        finally:
            _cleanup_dir(root)

    def test_custom_validator_models(self):
        root = _tmp_meetings_root()
        try:
            config = MeetingConfig(
                primary_validator_model="custom-validator-v2",
                conditional_validator_model="custom-codex-v2",
            )
            ctx = create_meeting(_make_request(), meetings_root=root, config=config)
            assert ctx.manifest.primary_validator_model == "custom-validator-v2"
            assert ctx.manifest.conditional_validator_model == "custom-codex-v2"
        finally:
            _cleanup_dir(root)


# ── 10. Manifest immutability ──────────────────────────────────────────

class TestManifestImmutability:
    """Verify MeetingManifest is truly frozen/immutable."""

    def test_manifest_is_frozen(self):
        root = _tmp_meetings_root()
        try:
            manifest = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            with pytest.raises((TypeError, AttributeError)):
                manifest.state = "changed"  # type: ignore[misc]
        finally:
            _cleanup_dir(root)

    def test_with_state_returns_new_instance(self):
        root = _tmp_meetings_root()
        try:
            original = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            updated = original.with_state(LifecycleState.QUEUED)
            assert original is not updated
            assert id(original) != id(updated)
        finally:
            _cleanup_dir(root)

    def test_with_error_returns_new_instance(self):
        root = _tmp_meetings_root()
        try:
            original = create_meeting(
                _make_request(), meetings_root=root
            ).manifest
            updated = original.with_error(
                {"timestamp": "t", "error_type": "e", "message": "m"}
            )
            assert original is not updated
            assert id(original) != id(updated)
        finally:
            _cleanup_dir(root)


# ── 11. Atomic write ──────────────────────────────────────────────────

class TestAtomicWrite:
    """Verify manifest writing is atomic (no .tmp residue on success)."""

    def test_no_tmp_file_left_after_write(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            # Only manifest.json should exist, not .tmp
            meeting_dir = Path(ctx.meeting_dir)
            files = list(meeting_dir.glob("*"))
            tmp_files = [f for f in files if f.name.endswith(".tmp")]
            assert len(tmp_files) == 0, (
                f"Temporary file left behind: {tmp_files}"
            )
            assert any(f.name == "manifest.json" for f in files)
        finally:
            _cleanup_dir(root)

    def test_update_manifest_no_tmp_left(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            updated_manifest = ctx.manifest.with_state(LifecycleState.QUEUED)
            result = update_manifest(updated_manifest)
            assert result.state == "queued"

            meeting_dir = Path(ctx.meeting_dir)
            tmp_files = [f for f in meeting_dir.glob("*") if f.name.endswith(".tmp")]
            assert len(tmp_files) == 0, (
                f"Temporary file left after update: {tmp_files}"
            )
        finally:
            _cleanup_dir(root)


# ── 12. Korean content support ─────────────────────────────────────────

class TestKoreanContent:
    """Verify Korean meeting topics are handled correctly."""

    def test_korean_agenda_stored(self):
        root = _tmp_meetings_root()
        try:
            korean_topic = "뮤직비디오 오프닝 아이디어 회의"
            ctx = create_meeting(
                _make_request(agenda=korean_topic),
                meetings_root=root,
            )
            assert ctx.manifest.agenda == korean_topic
            with open(ctx.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["agenda"] == korean_topic
        finally:
            _cleanup_dir(root)

    def test_korean_with_emoji(self):
        root = _tmp_meetings_root()
        try:
            topic = "신규 캐릭터 '루나' 🎨 디자인 및 SNS 📱 전략"
            ctx = create_meeting(
                _make_request(agenda=topic),
                meetings_root=root,
            )
            assert ctx.manifest.agenda == topic
            # Roundtrip through JSON
            loaded = load_manifest(ctx.manifest_path)
            assert loaded.agenda == topic
        finally:
            _cleanup_dir(root)


# ── 13. Thread ID and guild ID passthrough ─────────────────────────────

class TestDiscordFields:
    """Verify Discord-specific fields are passed through correctly."""

    def test_thread_id_stored(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(
                _make_request(thread_id="thread_abc_123"),
                meetings_root=root,
            )
            assert ctx.manifest.thread_id == "thread_abc_123"
        finally:
            _cleanup_dir(root)

    def test_guild_id_stored(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(
                _make_request(guild_id="guild_xyz_789"),
                meetings_root=root,
            )
            assert ctx.manifest.guild_id == "guild_xyz_789"
        finally:
            _cleanup_dir(root)

    def test_thread_and_guild_default_empty(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            assert ctx.manifest.thread_id == ""
            assert ctx.manifest.guild_id == ""
        finally:
            _cleanup_dir(root)


# ── 14. update_manifest persistence ────────────────────────────────────

class TestUpdateManifest:
    """Verify update_manifest persists changes to disk."""

    def test_update_manifest_writes_to_disk(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            queued = ctx.manifest.with_state(LifecycleState.QUEUED)
            result = update_manifest(queued)

            # Reload from disk
            loaded = load_manifest(ctx.manifest_path)
            assert loaded.state == "queued"
            assert result.state == "queued"
        finally:
            _cleanup_dir(root)

    def test_update_manifest_refreshes_timestamp(self):
        root = _tmp_meetings_root()
        try:
            ctx = create_meeting(_make_request(), meetings_root=root)
            import time
            time.sleep(0.01)
            updated = update_manifest(ctx.manifest)
            assert updated.updated_at != ctx.manifest.updated_at
        finally:
            _cleanup_dir(root)


# ── 15. validate_transition integration ────────────────────────────────

class TestValidateTransitionIntegration:
    """Verify manifest state changes respect the transition rules."""

    def test_valid_transitions_pass(self):
        """All forward-flow transitions should validate as True."""
        valid_pairs = [
            (LifecycleState.CREATED, LifecycleState.QUEUED),
            (LifecycleState.QUEUED, LifecycleState.ROUTING),
            (LifecycleState.ROUTING, LifecycleState.CONTEXT_RETRIEVAL),
            (LifecycleState.CONTEXT_RETRIEVAL, LifecycleState.IN_MEETING),
            (LifecycleState.IN_MEETING, LifecycleState.CONSENSUS_BUILDING),
            (LifecycleState.CONSENSUS_BUILDING, LifecycleState.VALIDATING),
            (LifecycleState.VALIDATING, LifecycleState.FINALIZING),
            (LifecycleState.FINALIZING, LifecycleState.COMPLETED),
        ]
        for from_s, to_s in valid_pairs:
            assert validate_transition(from_s, to_s), (
                f"Transition {from_s.value} → {to_s.value} should be valid"
            )

    def test_invalid_transitions_rejected(self):
        """Backward transitions and terminal→anything should be False."""
        invalid_pairs = [
            (LifecycleState.COMPLETED, LifecycleState.QUEUED),
            (LifecycleState.CANCELLED, LifecycleState.QUEUED),
            (LifecycleState.FAILED, LifecycleState.CREATED),
            (LifecycleState.STALE, LifecycleState.CREATED),
            (LifecycleState.IN_MEETING, LifecycleState.CREATED),
            (LifecycleState.VALIDATING, LifecycleState.ROUTING),
        ]
        for from_s, to_s in invalid_pairs:
            assert not validate_transition(from_s, to_s), (
                f"Transition {from_s.value} → {to_s.value} should be invalid"
            )

    def test_created_to_cancelled_allowed(self):
        """User cancellation from any active state is permitted."""
        active_states = [
            LifecycleState.CREATED,
            LifecycleState.QUEUED,
            LifecycleState.ROUTING,
            LifecycleState.IN_MEETING,
            LifecycleState.VALIDATING,
            LifecycleState.EXECUTING,
        ]
        for state in active_states:
            assert validate_transition(state, LifecycleState.CANCELLED), (
                f"Transition {state.value} → cancelled should be valid"
            )

    def test_validate_transition_with_strings(self):
        """validate_transition accepts string state names."""
        assert validate_transition("created", "queued")
        assert not validate_transition("completed", "created")
        assert validate_transition("created", "cancelled")


# ── Helpers ─────────────────────────────────────────────────────────────

def _cleanup_dir(path: str) -> None:
    """Recursively remove a test directory."""
    import shutil

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
