"""Tests for the meeting session factory (Sub-AC 3.2).

Verifies the complete session creation pipeline:
- Valid request → correct MeetingManifest
- Input validation (empty fields, invalid priorities, malformed IDs)
- Business rule enforcement (max agenda length, kebab-case IDs)
- Default application (priority, state, config params)
- Teams/suggested_roles propagation
- SessionFactoryResult invariant enforcement
- Timestamp correctness
- Meeting ID format
- Configuration defaults and overrides
- Thread/guild ID propagation
- Edge cases (unicode, long text at boundary)
"""

from __future__ import annotations

import pytest

from src.meeting_session_factory import (
    SessionFactoryResult,
    create_meeting_session,
)
from src.meeting_trigger import (
    MANIFEST_SCHEMA_VERSION,
    MAX_AGENTS_PER_MEETING,
    MAX_ROUNDS,
    TOKEN_LIMIT_CODEX,
    TOKEN_LIMIT_VALIDATOR,
    TOKEN_LIMIT_WORKER,
    MeetingCommandRequest,
    MeetingConfig,
)

# ── Test constants ──────────────────────────────────────────────────────

_VALID_AGENDA = (
    "신규 캐릭터 '루나'의 비주얼 디자인을 논의하고, "
    "SNS 홍보 전략을 수립하며, 기존 게임엔진 백엔드 API 리팩토링도 함께 검토해주세요."
)
_VALID_USER_ID = "discord_user_12345"
_VALID_CHANNEL_ID = "discord_channel_67890"


def _make_request(**overrides: object) -> MeetingCommandRequest:
    """Build a valid MeetingCommandRequest with optional overrides."""
    defaults: dict[str, object] = {
        "agenda": _VALID_AGENDA,
        "user_id": _VALID_USER_ID,
        "channel_id": _VALID_CHANNEL_ID,
        "priority": "p1",
    }
    defaults.update(overrides)
    return MeetingCommandRequest(**defaults)  # type: ignore[arg-type]


# ── 1. Basic creation ──────────────────────────────────────────────────


class TestBasicCreation:
    """Verify successful session creation with valid inputs."""

    def test_creates_session_with_valid_request(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_test001",
        )
        assert result.success, f"Expected success, got: {result.error}"
        s = result.session
        assert s is not None
        assert s.meeting_id == "meeting_20260611_test001"
        assert s.state == "created"
        assert s.agenda == _VALID_AGENDA
        assert s.user_id == _VALID_USER_ID
        assert s.channel_id == _VALID_CHANNEL_ID
        assert s.priority == "p1"

    def test_session_is_immutable(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_immutable",
        )
        assert result.success
        s = result.session
        assert s is not None
        with pytest.raises(Exception):
            s.state = "queued"  # type: ignore[misc]

    def test_meeting_id_is_deterministic_when_provided(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_deterministic",
        )
        assert result.success
        assert result.session is not None
        assert result.session.meeting_id == "meeting_20260611_deterministic"

    def test_meeting_id_is_auto_generated(self) -> None:
        result = create_meeting_session(_make_request())
        assert result.success
        assert result.session is not None
        mid = result.session.meeting_id
        assert mid.startswith("meeting_")
        parts = mid.split("_")
        assert len(parts) == 3
        # YYYYMMDD part
        assert len(parts[1]) == 8
        assert parts[1].isdigit()
        # Short UUID part (12 hex chars)
        assert len(parts[2]) == 12
        assert all(c in "0123456789abcdef" for c in parts[2])

    def test_each_call_generates_unique_id(self) -> None:
        r1 = create_meeting_session(_make_request())
        r2 = create_meeting_session(_make_request())
        assert r1.session is not None
        assert r2.session is not None
        assert r1.session.meeting_id != r2.session.meeting_id, (
            "Each meeting must have a unique ID"
        )


# ── 2. Required field validation ───────────────────────────────────────


class TestRequiredFieldValidation:
    """Verify that missing or empty required fields are rejected."""

    def test_empty_agenda_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(agenda=""),
        )
        assert not result.success
        assert "agenda must not be empty" in result.error  # type: ignore[operator]

    def test_whitespace_only_agenda_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(agenda="   "),
        )
        assert not result.success
        assert "agenda must not be empty" in result.error  # type: ignore[operator]

    def test_empty_user_id_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(user_id=""),
        )
        assert not result.success
        assert "user_id" in result.error.lower()  # type: ignore[operator]

    def test_whitespace_only_user_id_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(user_id="   "),
        )
        assert not result.success
        assert "user_id" in result.error.lower()  # type: ignore[operator]

    def test_empty_channel_id_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(channel_id=""),
        )
        assert not result.success
        assert "channel_id" in result.error.lower()  # type: ignore[operator]

    def test_whitespace_only_channel_id_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(channel_id="   "),
        )
        assert not result.success
        assert "channel_id" in result.error.lower()  # type: ignore[operator]

    def test_all_empty_fields_rejected(self) -> None:
        result = create_meeting_session(
            MeetingCommandRequest(agenda="", user_id="", channel_id=""),
        )
        assert not result.success
        # First validation failure should mention agenda
        assert result.error is not None
        assert "agenda" in result.error.lower()


# ── 3. Priority validation ──────────────────────────────────────────────


class TestPriorityValidation:
    """Verify priority level validation and defaults."""

    def test_all_valid_priorities_accepted(
        self,
    ) -> None:
        for p in ("p0", "p1", "p2", "p3"):
            result = create_meeting_session(
                _make_request(priority=p),
                meeting_id=f"meeting_20260611_{p}test",
            )
            assert result.success, f"Expected priority {p} to be valid"
            assert result.session is not None
            assert result.session.priority == p

    def test_invalid_priority_rejected(self) -> None:
        for p in ("p4", "p5", "urgent", "high", "low", "P1", ""):
            result = create_meeting_session(
                _make_request(priority=p),
            )
            assert not result.success, (
                f"Expected priority '{p}' to be rejected"
            )
            assert result.error is not None
            assert "invalid priority" in result.error

    def test_default_priority_is_p2(self) -> None:
        """Request without explicit priority defaults to p2."""
        req = MeetingCommandRequest(
            agenda="test",
            user_id="u1",
            channel_id="c1",
        )
        result = create_meeting_session(
            req,
            meeting_id="meeting_20260611_p2default",
        )
        assert result.success
        assert result.session is not None
        assert result.session.priority == "p2"


# ── 4. Business rule enforcement ────────────────────────────────────────


class TestBusinessRules:
    """Verify business rule enforcement beyond basic field validation."""

    def test_agenda_max_length_enforced(self) -> None:
        """Agenda exceeding 10k characters is rejected."""
        result = create_meeting_session(
            _make_request(agenda="x" * 10_001),
        )
        assert not result.success
        assert result.error is not None
        assert "10,000" in result.error

    def test_agenda_at_boundary_10k_accepted(self) -> None:
        """Agenda at exactly 10k characters is accepted."""
        result = create_meeting_session(
            _make_request(agenda="x" * 10_000),
            meeting_id="meeting_20260611_10kboundary",
        )
        assert result.success, f"Expected 10k char agenda to be valid, got: {result.error}"
        assert result.session is not None
        assert len(result.session.agenda) == 10_000

    def test_team_role_id_with_spaces_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(teams=("content pd",)),
        )
        assert not result.success
        assert result.error is not None
        assert "space" in result.error.lower()

    def test_team_role_id_empty_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(teams=("",)),
        )
        assert not result.success
        assert result.error is not None
        assert "empty" in result.error.lower()

    def test_team_role_id_whitespace_only_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(teams=("   ",)),
        )
        assert not result.success
        assert result.error is not None
        assert "empty" in result.error.lower()

    def test_suggested_role_with_spaces_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(suggested_roles=("concept artist",)),
        )
        assert not result.success
        assert result.error is not None
        assert "space" in result.error.lower()

    def test_suggested_role_empty_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(suggested_roles=("",)),
        )
        assert not result.success
        assert result.error is not None
        assert "empty" in result.error.lower()

    def test_valid_kebab_case_teams_accepted(self) -> None:
        result = create_meeting_session(
            _make_request(
                teams=("content-pd", "art-director", "tech-director"),
            ),
            meeting_id="meeting_20260611_kebabteams",
        )
        assert result.success, f"Expected kebab-case teams to be valid: {result.error}"
        assert result.session is not None
        assert result.session.required_roles == (
            "content-pd", "art-director", "tech-director",
        )

    def test_valid_kebab_case_roles_accepted(self) -> None:
        result = create_meeting_session(
            _make_request(
                suggested_roles=(
                    "concept-artist",
                    "backend-dev",
                    "sns-strategist",
                    "data-engineer",
                ),
            ),
            meeting_id="meeting_20260611_kebabroles",
        )
        assert result.success, f"Expected kebab-case roles to be valid: {result.error}"
        assert result.session is not None
        assert result.session.optional_roles == (
            "concept-artist", "backend-dev", "sns-strategist", "data-engineer",
        )


# ── 5. Defaults and configuration ──────────────────────────────────────


class TestDefaultsAndConfig:
    """Verify default application and configuration overrides."""

    def test_default_state_is_created(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_state_default",
        )
        assert result.success
        assert result.session is not None
        assert result.session.state == "created"

    def test_default_round_count_is_zero(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_roundcount",
        )
        assert result.success
        assert result.session is not None
        assert result.session.round_count == 0

    def test_default_validation_score_is_zero(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_val_score",
        )
        assert result.success
        assert result.session is not None
        assert result.session.validation_score == 0.0

    def test_default_validation_verdict_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_val_verdict",
        )
        assert result.success
        assert result.session is not None
        assert result.session.validation_verdict == ""

    def test_default_consensus_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_consensus",
        )
        assert result.success
        assert result.session is not None
        assert result.session.consensus == ""

    def test_default_tags_and_risk_tags_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_tags",
        )
        assert result.success
        assert result.session is not None
        assert result.session.tags == ()
        assert result.session.risk_tags == ()

    def test_default_agenda_type_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_agenda_type",
        )
        assert result.success
        assert result.session is not None
        assert result.session.agenda_type == ""

    def test_validator_required_default_true(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_validator",
        )
        assert result.success
        assert result.session is not None
        assert result.session.validator_required is True

    def test_codex_required_default_false(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_codex",
        )
        assert result.success
        assert result.session is not None
        assert result.session.codex_required is False

    def test_default_error_log_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_error_log",
        )
        assert result.success
        assert result.session is not None
        assert result.session.error_log == ()

    def test_default_speaker_state_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_speaker",
        )
        assert result.success
        assert result.session is not None
        assert result.session.current_speaker == ""
        assert result.session.speaker_queue == ()
        assert result.session.completed_step == ""

    def test_default_context_packets_empty(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_cp",
        )
        assert result.success
        assert result.session is not None
        assert result.session.context_packets == ()
        assert result.session.decisions == ()
        assert result.session.tool_outputs == ()

    def test_system_config_defaults_applied(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_syscfg",
        )
        assert result.success
        s = result.session
        assert s is not None
        assert s.max_rounds == MAX_ROUNDS
        assert s.max_agents_per_meeting == MAX_AGENTS_PER_MEETING
        assert s.token_limit_worker == TOKEN_LIMIT_WORKER
        assert s.token_limit_validator == TOKEN_LIMIT_VALIDATOR
        assert s.token_limit_codex == TOKEN_LIMIT_CODEX
        assert s.schema_version == MANIFEST_SCHEMA_VERSION

    def test_custom_config_overrides(self) -> None:
        custom = MeetingConfig(
            max_rounds=5,
            max_agents_per_meeting=10,
            token_limit_worker=8000,
            token_limit_validator=15000,
            token_limit_codex=25000,
            primary_validator_model="custom-model",
            conditional_validator_model="custom-codex",
            manifest_schema_version="custom-schema.v1",
            meetings_root="custom_meetings",
        )
        result = create_meeting_session(
            _make_request(),
            config=custom,
            meeting_id="meeting_20260611_customcfg",
        )
        assert result.success
        s = result.session
        assert s is not None
        assert s.max_rounds == 5
        assert s.max_agents_per_meeting == 10
        assert s.token_limit_worker == 8000
        assert s.token_limit_validator == 15000
        assert s.token_limit_codex == 25000
        assert s.primary_validator_model == "custom-model"
        assert s.conditional_validator_model == "custom-codex"
        assert s.schema_version == "custom-schema.v1"
        assert s.meetings_root == "custom_meetings"

    def test_partial_config_override(self) -> None:
        """Only specified config fields are overridden; rest use defaults."""
        custom = MeetingConfig(max_rounds=4)
        result = create_meeting_session(
            _make_request(),
            config=custom,
            meeting_id="meeting_20260611_partialcfg",
        )
        assert result.success
        s = result.session
        assert s is not None
        assert s.max_rounds == 4
        # Other fields should remain at system defaults
        assert s.max_agents_per_meeting == MAX_AGENTS_PER_MEETING
        assert s.token_limit_worker == TOKEN_LIMIT_WORKER


# ── 6. Propagated fields ───────────────────────────────────────────────


class TestFieldPropagation:
    """Verify that request fields are correctly propagated to the session."""

    def test_agenda_stripped(self) -> None:
        result = create_meeting_session(
            _make_request(agenda="  회의 주제  "),
            meeting_id="meeting_20260611_strip",
        )
        assert result.success
        assert result.session is not None
        assert result.session.agenda == "회의 주제"

    def test_user_id_stripped(self) -> None:
        result = create_meeting_session(
            _make_request(user_id="  u1  "),
            meeting_id="meeting_20260611_userstrip",
        )
        assert result.success
        assert result.session is not None
        assert result.session.user_id == "u1"

    def test_channel_id_stripped(self) -> None:
        result = create_meeting_session(
            _make_request(channel_id="  c1  "),
            meeting_id="meeting_20260611_chanstrip",
        )
        assert result.success
        assert result.session is not None
        assert result.session.channel_id == "c1"

    def test_thread_id_propagated(self) -> None:
        result = create_meeting_session(
            _make_request(thread_id="thread_abc123"),
            meeting_id="meeting_20260611_thread",
        )
        assert result.success
        assert result.session is not None
        assert result.session.thread_id == "thread_abc123"

    def test_guild_id_propagated(self) -> None:
        result = create_meeting_session(
            _make_request(guild_id="guild_xyz789"),
            meeting_id="meeting_20260611_guild",
        )
        assert result.success
        assert result.session is not None
        assert result.session.guild_id == "guild_xyz789"

    def test_teams_propagated_to_required_roles(self) -> None:
        result = create_meeting_session(
            _make_request(teams=("content-pd", "art-director")),
            meeting_id="meeting_20260611_teams",
        )
        assert result.success
        assert result.session is not None
        assert result.session.required_roles == ("content-pd", "art-director")

    def test_suggested_roles_propagated_to_optional_roles(self) -> None:
        result = create_meeting_session(
            _make_request(suggested_roles=("backend-dev", "concept-artist")),
            meeting_id="meeting_20260611_sugg_roles",
        )
        assert result.success
        assert result.session is not None
        assert result.session.optional_roles == ("backend-dev", "concept-artist")

    def test_empty_teams_defaults_to_empty_tuple(self) -> None:
        result = create_meeting_session(
            _make_request(teams=()),
            meeting_id="meeting_20260611_noteams",
        )
        assert result.success
        assert result.session is not None
        assert result.session.required_roles == ()

    def test_empty_suggested_roles_defaults_to_empty_tuple(self) -> None:
        result = create_meeting_session(
            _make_request(suggested_roles=()),
            meeting_id="meeting_20260611_noroles",
        )
        assert result.success
        assert result.session is not None
        assert result.session.optional_roles == ()


# ── 7. SessionFactoryResult invariants ─────────────────────────────────


class TestSessionFactoryResult:
    """Verify SessionFactoryResult dataclass invariants."""

    def test_success_result_has_session(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_success",
        )
        assert result.success
        assert result.session is not None
        assert result.error is None

    def test_failure_result_has_error(self) -> None:
        result = create_meeting_session(
            _make_request(agenda=""),
        )
        assert not result.success
        assert result.session is None
        assert result.error is not None

    def test_success_without_session_raises(self) -> None:
        with pytest.raises(ValueError, match="success=True requires session"):
            SessionFactoryResult(success=True, session=None, error=None)

    def test_failure_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="success=False requires error"):
            SessionFactoryResult(success=False, session=None, error=None)

    def test_failure_with_session_and_error(self) -> None:
        """A failure result may carry a session and error (for degraded cases)."""
        s = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_degraded",
        ).session
        result = SessionFactoryResult(
            success=False,
            session=s,
            error="degraded but partial session available",
        )
        assert not result.success
        assert result.session is s
        assert result.error is not None


# ── 8. Timestamps ──────────────────────────────────────────────────────


class TestTimestamps:
    """Verify timestamp behaviour."""

    def test_created_at_and_updated_at_are_set(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_timestamps",
        )
        assert result.success
        assert result.session is not None
        assert result.session.created_at
        assert result.session.updated_at

    def test_created_at_equals_updated_at_on_creation(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_ts_equal",
        )
        assert result.success
        assert result.session is not None
        assert result.session.created_at == result.session.updated_at

    def test_timestamps_are_iso8601_format(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_ts_iso",
        )
        assert result.success
        assert result.session is not None
        ts = result.session.created_at
        # ISO 8601 includes 'T' separator and timezone info
        assert "T" in ts
        assert "+" in ts or "Z" in ts


# ── 9. Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Verify edge case behaviour."""

    def test_unicode_agenda_accepted(self) -> None:
        result = create_meeting_session(
            _make_request(agenda="🎮 캐릭터 '루나'의 ビジュアル 디자인 — 第3弾 ✨"),
            meeting_id="meeting_20260611_unicode",
        )
        assert result.success, f"Expected unicode agenda to be valid: {result.error}"
        assert result.session is not None
        assert "루나" in result.session.agenda

    def test_long_but_valid_agenda_accepted(self) -> None:
        long_topic = "뮤직비디오 기획 회의. " * 100  # ~1,400 chars
        result = create_meeting_session(
            _make_request(agenda=long_topic),
            meeting_id="meeting_20260611_longtopic",
        )
        assert result.success, f"Expected long agenda to be valid: {result.error}"

    def test_agenda_exactly_10000_chars_accepted(self) -> None:
        result = create_meeting_session(
            _make_request(agenda="가" * 10_000),
            meeting_id="meeting_20260611_10k",
        )
        assert result.success, f"Expected 10k agenda to be valid: {result.error}"

    def test_agenda_10001_chars_rejected(self) -> None:
        result = create_meeting_session(
            _make_request(agenda="가" * 10_001),
        )
        assert not result.success
        assert result.error is not None
        assert "10,000" in result.error

    def test_empty_thread_id_allowed(self) -> None:
        result = create_meeting_session(
            _make_request(thread_id=""),
            meeting_id="meeting_20260611_nothread",
        )
        assert result.success
        assert result.session is not None
        assert result.session.thread_id == ""

    def test_empty_guild_id_allowed(self) -> None:
        result = create_meeting_session(
            _make_request(guild_id=""),
            meeting_id="meeting_20260611_noguild",
        )
        assert result.success
        assert result.session is not None
        assert result.session.guild_id == ""

    def test_agenda_with_newlines_accepted(self) -> None:
        multi_line = "첫 번째 안건\n두 번째 안건\n세 번째 안건"
        result = create_meeting_session(
            _make_request(agenda=multi_line),
            meeting_id="meeting_20260611_multiline",
        )
        assert result.success, f"Expected multiline agenda to be valid: {result.error}"
        assert result.session is not None
        assert "\n" in result.session.agenda


# ── 10. JSON serialization compatibility ───────────────────────────────


class TestJsonSerialization:
    """Verify the session can be serialized to JSON (manifest.json compatibility)."""

    def test_session_to_dict_is_json_serializable(self) -> None:
        import json

        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_serialize",
        )
        assert result.success
        assert result.session is not None
        d = result.session.to_dict()
        # Must not raise
        json_str = json.dumps(d, ensure_ascii=False)
        assert json_str
        # Verify roundtrip
        parsed = json.loads(json_str)
        assert parsed["meeting_id"] == "meeting_20260611_serialize"
        assert parsed["state"] == "created"
        assert parsed["priority"] == "p1"

    def test_session_to_json_produces_pretty_output(self) -> None:
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_pretty",
        )
        assert result.success
        assert result.session is not None
        json_str = result.session.to_json()
        assert "meeting_id" in json_str
        assert "\n" in json_str  # pretty-printed


# ── 11. Contract verification (Sub-AC 3.2) ──────────────────────────────


class TestSubAc32Contract:
    """Verify the module fulfills the Sub-AC 3.2 contract:

    - Create a validated MeetingSession record from a MeetingRequest
    - Enforce required fields
    - Apply defaults
    - Enforce business rules
    """

    def test_valid_request_produces_session_record(self) -> None:
        """Contract: MeetingRequest → MeetingSession."""
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_contract",
        )
        assert result.success
        s = result.session
        assert s is not None
        # All required ontology concepts must be present
        assert s.meeting_id
        assert s.state
        assert s.priority
        assert s.agenda
        assert s.user_id
        assert s.channel_id

    def test_required_fields_enforced(self) -> None:
        """Contract: required fields are enforced."""
        # agenda is required
        assert not create_meeting_session(
            _make_request(agenda=""),
        ).success
        # user_id is required
        assert not create_meeting_session(
            _make_request(user_id=""),
        ).success
        # channel_id is required
        assert not create_meeting_session(
            _make_request(channel_id=""),
        ).success

    def test_defaults_applied(self) -> None:
        """Contract: system defaults are applied to every session."""
        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_defaults_contract",
        )
        assert result.success
        s = result.session
        assert s is not None
        assert s.state == "created"
        assert s.round_count == 0
        assert s.validation_score == 0.0
        assert s.validator_required is True
        assert s.codex_required is False
        assert s.max_rounds == MAX_ROUNDS

    def test_business_rules_enforced(self) -> None:
        """Contract: business rules are enforced."""
        # Max agenda length
        assert not create_meeting_session(
            _make_request(agenda="x" * 10_001),
        ).success
        # Valid priority range
        assert not create_meeting_session(
            _make_request(priority="p5"),
        ).success
        # Team IDs must be kebab-case
        assert not create_meeting_session(
            _make_request(teams=("bad id",)),
        ).success

    def test_session_factory_is_pure(self) -> None:
        """Contract: the factory is pure — no filesystem I/O."""
        import tempfile
        import os

        # Create a session — must not write any files
        tmpdir = tempfile.mkdtemp()
        try:
            result = create_meeting_session(
                _make_request(),
                meeting_id="meeting_20260611_pure",
            )
            assert result.success
            # No files should have been created in tmpdir
            created = os.listdir(tmpdir)
            assert len(created) == 0, (
                f"Factory should be pure, but files were created: {created}"
            )
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_all_15_states_reachable_from_created(self) -> None:
        """The 'created' state produced by the factory must support all
        documented terminal and exception state transitions."""
        from src.shared.lifecycle import LifecycleState, MEETING_TRANSITIONS

        result = create_meeting_session(
            _make_request(),
            meeting_id="meeting_20260611_15states",
        )
        assert result.success
        s = result.session
        assert s is not None
        assert s.state == str(LifecycleState.CREATED)

        legal_from_created = MEETING_TRANSITIONS[LifecycleState.CREATED]
        assert LifecycleState.QUEUED in legal_from_created
        assert LifecycleState.CANCELLED in legal_from_created
        assert LifecycleState.FAILED in legal_from_created
        assert LifecycleState.STALE in legal_from_created
        assert LifecycleState.TIMED_OUT in legal_from_created
