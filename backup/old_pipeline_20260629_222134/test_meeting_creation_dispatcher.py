"""Tests for the meeting creation dispatcher (Sub-AC 2c).

Verifies that ``dispatch_meeting()``:
- Accepts valid MeetingIntent and dispatches to the orchestrator
- Rejects invalid intents with descriptive errors (empty topic,
  invalid meeting_type, invalid priority)
- Correctly normalises intent fields into a MeetingCommandRequest
- Supports mock orchestrator injection for testing
- Handles orchestrator exceptions gracefully
- Passes through optional fields (thread_id, guild_id, meetings_root)

Test categories:
1. Valid intents — happy path, all meeting types
2. Invalid intents — empty topic, bad type, bad priority
3. Normalisation — field mapping correctness
4. Mock orchestrator — injection, call verification, error handling
5. Edge cases — whitespace-only topic, mixed-case priority, long topics
6. Dataclass immutability
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

import pytest

from src.meeting_creation_dispatcher import (
    DispatchResult,
    OrchestratorCallable,
    dispatch_meeting,
)
from src.meeting_intent_parser import (
    MEETING_TYPE_CREATIVE,
    MEETING_TYPE_MARKETING,
    MEETING_TYPE_PLANNING,
    MEETING_TYPE_REVIEW,
    MEETING_TYPE_RISK,
    MEETING_TYPE_TECHNICAL,
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    MeetingIntent,
)
from src.meeting_trigger import (
    MeetingCommandRequest,
    MeetingConfig,
    MeetingContext,
    create_meeting,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_intent(**overrides: object) -> MeetingIntent:
    """Build a valid MeetingIntent with optional overrides."""
    defaults: dict[str, object] = {
        "meeting_type": MEETING_TYPE_CREATIVE,
        "topic": "뮤직비디오 오프닝 아이디어 회의",
        "participants": (),
        "urgency": PRIORITY_P2,
        "confidence": 1.0,
        "reasoning": "test intent",
    }
    defaults.update(overrides)
    return MeetingIntent(**defaults)  # type: ignore[arg-type]


def _tmp_root() -> str:
    """Create a temporary directory for meeting storage."""
    return tempfile.mkdtemp(prefix="ai_agent_test_dispatcher_")


def _cleanup(path: str) -> None:
    """Remove a directory tree, ignoring errors."""
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ── 1. Valid intents — happy path ─────────────────────────────────────────


class TestValidIntents:
    """Verify that valid MeetingIntent payloads are dispatched successfully."""

    def test_valid_intent_creates_meeting(self):
        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(),
                user_id="u1",
                channel_id="c1",
                meetings_root=root,
            )
            assert result.success
            assert result.context is not None
            assert result.error is None
            assert result.context.meeting_id.startswith("meeting_")
            assert result.context.manifest.state == "created"
            assert result.context.manifest.agenda == (
                "뮤직비디오 오프닝 아이디어 회의"
            )
        finally:
            _cleanup(root)

    def test_intent_preserved_in_result(self):
        root = _tmp_root()
        try:
            intent = _make_intent(
                topic="캐릭터 디자인 검토",
                urgency=PRIORITY_P1,
            )
            result = dispatch_meeting(
                intent, user_id="u2", channel_id="c2", meetings_root=root
            )
            assert result.success
            assert result.intent is intent, (
                "Original intent must be preserved in DispatchResult"
            )
        finally:
            _cleanup(root)

    def test_all_six_meeting_types_accepted(self):
        """Every recognised meeting_type must pass validation."""
        root = _tmp_root()
        try:
            for mt in [
                MEETING_TYPE_CREATIVE,
                MEETING_TYPE_TECHNICAL,
                MEETING_TYPE_MARKETING,
                MEETING_TYPE_RISK,
                MEETING_TYPE_PLANNING,
                MEETING_TYPE_REVIEW,
            ]:
                intent = _make_intent(meeting_type=mt)
                result = dispatch_meeting(
                    intent,
                    user_id="u1",
                    channel_id="c1",
                    meetings_root=root,
                )
                assert result.success, (
                    f"meeting_type '{mt}' should be accepted, "
                    f"got error: {result.error}"
                )
        finally:
            _cleanup(root)

    def test_all_four_priorities_accepted(self):
        """Every recognised priority (p0–p3) must pass validation."""
        root = _tmp_root()
        try:
            for pri in [PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3]:
                intent = _make_intent(urgency=pri)
                result = dispatch_meeting(
                    intent,
                    user_id="u1",
                    channel_id="c1",
                    meetings_root=root,
                )
                assert result.success, (
                    f"priority '{pri}' should be accepted, "
                    f"got error: {result.error}"
                )
        finally:
            _cleanup(root)

    def test_meeting_dir_isolation(self):
        """Two dispatched meetings must have separate directories."""
        root = _tmp_root()
        try:
            r1 = dispatch_meeting(
                _make_intent(topic="회의 A"),
                user_id="u1", channel_id="c1", meetings_root=root,
            )
            r2 = dispatch_meeting(
                _make_intent(topic="회의 B"),
                user_id="u1", channel_id="c1", meetings_root=root,
            )
            assert r1.success and r2.success
            assert r1.context is not None and r2.context is not None
            assert r1.context.meeting_dir != r2.context.meeting_dir
        finally:
            _cleanup(root)

    def test_meeting_config_passed_through(self):
        """Custom MeetingConfig should reach the orchestrator."""
        root = _tmp_root()
        try:
            custom_config = MeetingConfig(
                max_rounds=5,
                max_agents_per_meeting=10,
            )
            result = dispatch_meeting(
                _make_intent(),
                user_id="u1",
                channel_id="c1",
                meetings_root=root,
                config=custom_config,
            )
            assert result.success
            assert result.context is not None
            # Custom config values should be reflected in the manifest
            assert result.context.manifest.max_rounds == 5
            assert result.context.manifest.max_agents_per_meeting == 10
        finally:
            _cleanup(root)


# ── 2. Invalid intents — validation rejection ─────────────────────────────


class TestInvalidIntents:
    """Verify that invalid MeetingIntent payloads are rejected with errors."""

    def test_empty_topic_rejected(self):
        result = dispatch_meeting(
            _make_intent(topic=""),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert result.context is None
        assert result.error is not None
        assert "topic must not be empty" in result.error

    def test_whitespace_only_topic_rejected(self):
        result = dispatch_meeting(
            _make_intent(topic="   \n\t  "),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert "topic must not be empty" in (result.error or "")

    def test_invalid_meeting_type_rejected(self):
        result = dispatch_meeting(
            _make_intent(meeting_type="invalid_type_xyz"),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert result.error is not None
        assert "invalid meeting_type" in result.error
        assert "invalid_type_xyz" in result.error

    def test_empty_meeting_type_rejected(self):
        result = dispatch_meeting(
            _make_intent(meeting_type=""),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert result.error is not None
        assert "invalid meeting_type" in result.error

    def test_invalid_priority_rejected(self):
        result = dispatch_meeting(
            _make_intent(urgency="p99"),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert result.error is not None
        assert "invalid priority" in result.error
        assert "p99" in result.error

    def test_uppercase_priority_rejected(self):
        """Urgency must be lowercase p0–p3; uppercase is not valid."""
        result = dispatch_meeting(
            _make_intent(urgency="P1"),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert result.error is not None
        assert "invalid priority" in result.error

    def test_intent_preserved_on_failure(self):
        """On validation failure, the original intent should still be returned."""
        intent = _make_intent(topic="")
        result = dispatch_meeting(
            intent, user_id="u1", channel_id="c1"
        )
        assert not result.success
        assert result.intent is intent

    def test_topic_is_first_checked(self):
        """Topic validation runs before meeting_type validation."""
        # Both topic and meeting_type are invalid — topic error should
        # be reported first (fail-fast, one error at a time).
        result = dispatch_meeting(
            _make_intent(topic="", meeting_type="bad"),
            user_id="u1",
            channel_id="c1",
        )
        assert not result.success
        assert "topic" in (result.error or ""), (
            f"Expected topic error first, got: {result.error}"
        )


# ── 3. Normalisation — field mapping correctness ──────────────────────────


class TestNormalisation:
    """Verify that MeetingIntent fields are correctly mapped to
    MeetingCommandRequest fields."""

    def _capture_request(self) -> tuple[
        OrchestratorCallable, list[MeetingCommandRequest]
    ]:
        """Return a mock orchestrator that captures the request it receives."""
        captured: list[MeetingCommandRequest] = []

        def mock_orchestrator(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            captured.append(request)
            # Delegate to real create_meeting for a valid response
            return create_meeting(
                request, meetings_root=meetings_root, config=config
            )

        return mock_orchestrator, captured

    def test_topic_mapped_to_agenda(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(topic="신규 캐릭터 디자인 회의"),
                user_id="u1", channel_id="c1",
                meetings_root=root,
                orchestrator=mock,
            )
            assert result.success
            assert len(captured) == 1
            assert captured[0].agenda == "신규 캐릭터 디자인 회의"
        finally:
            _cleanup(root)

    def test_user_id_passed_through(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(),
                user_id="discord_user_999",
                channel_id="c1",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].user_id == "discord_user_999"
        finally:
            _cleanup(root)

    def test_channel_id_passed_through(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(),
                user_id="u1",
                channel_id="discord_channel_777",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].channel_id == "discord_channel_777"
        finally:
            _cleanup(root)

    def test_thread_id_passed_through(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(),
                user_id="u1",
                channel_id="c1",
                thread_id="thread_123",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].thread_id == "thread_123"
        finally:
            _cleanup(root)

    def test_guild_id_passed_through(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(),
                user_id="u1",
                channel_id="c1",
                guild_id="guild_456",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].guild_id == "guild_456"
        finally:
            _cleanup(root)

    def test_priority_mapped_from_urgency(self):
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(urgency=PRIORITY_P1),
                user_id="u1", channel_id="c1",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].priority == "p1"
        finally:
            _cleanup(root)

    def test_topic_stripped_of_whitespace(self):
        """Leading/trailing whitespace on topic should be stripped."""
        mock, captured = self._capture_request()
        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(topic="   회의 주제   "),
                user_id="u1", channel_id="c1",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(captured) == 1
            assert captured[0].agenda == "회의 주제"
        finally:
            _cleanup(root)


# ── 4. Mock orchestrator ──────────────────────────────────────────────────


class TestMockOrchestrator:
    """Verify the mock orchestrator injection works correctly for testing."""

    def test_mock_orchestrator_called_once(self):
        call_count = 0

        def mock(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            nonlocal call_count
            call_count += 1
            return create_meeting(
                request, meetings_root=meetings_root, config=config
            )

        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(),
                user_id="u1", channel_id="c1",
                meetings_root=root,
                orchestrator=mock,
            )
            assert result.success
            assert call_count == 1, (
                f"Mock orchestrator should be called exactly once, "
                f"was called {call_count} times"
            )
        finally:
            _cleanup(root)

    def test_mock_orchestrator_receives_meeting_command_request(self):
        received: list[MeetingCommandRequest] = []

        def mock(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            received.append(request)
            return create_meeting(
                request, meetings_root=meetings_root, config=config
            )

        root = _tmp_root()
        try:
            dispatch_meeting(
                _make_intent(
                    topic="테스트 회의",
                    urgency=PRIORITY_P0,
                ),
                user_id="user_x", channel_id="chan_y",
                meetings_root=root,
                orchestrator=mock,
            )
            assert len(received) == 1
            req = received[0]
            assert isinstance(req, MeetingCommandRequest)
            assert req.agenda == "테스트 회의"
            assert req.user_id == "user_x"
            assert req.channel_id == "chan_y"
            assert req.priority == "p0"
        finally:
            _cleanup(root)

    def test_orchestrator_exception_caught(self):
        """When the orchestrator raises, dispatch_meeting must catch it
        and return a structured error — never let the exception propagate."""

        def failing_mock(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            raise RuntimeError("simulated orchestrator crash")

        result = dispatch_meeting(
            _make_intent(),
            user_id="u1",
            channel_id="c1",
            orchestrator=failing_mock,
        )
        assert not result.success
        assert result.context is None
        assert result.error is not None
        assert "RuntimeError" in result.error
        assert "simulated orchestrator crash" in result.error

    def test_orchestrator_value_error_caught(self):
        """create_meeting raises ValueError for empty fields.  The
        dispatcher must catch this and return a DispatchResult rather
        than propagating the exception."""

        def raising_mock(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            raise ValueError("agenda must not be empty")

        # We need a valid intent (so validation passes), but the
        # orchestrator might still reject for its own reasons.
        result = dispatch_meeting(
            _make_intent(topic="유효한 주제"),
            user_id="u1",
            channel_id="c1",
            orchestrator=raising_mock,
        )
        assert not result.success
        assert "ValueError" in (result.error or "")
        assert "agenda must not be empty" in (result.error or "")


# ── 5. Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Verify edge-case behaviour."""

    def test_long_topic_accepted(self):
        root = _tmp_root()
        try:
            long_topic = "A" * 1000
            result = dispatch_meeting(
                _make_intent(topic=long_topic),
                user_id="u1", channel_id="c1",
                meetings_root=root,
            )
            assert result.success
            assert result.context is not None
            assert result.context.manifest.agenda == long_topic
        finally:
            _cleanup(root)

    def test_default_priority_is_p2(self):
        """When urgency is p2 (default), it should be accepted."""
        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(urgency=PRIORITY_P2),
                user_id="u1", channel_id="c1",
                meetings_root=root,
            )
            assert result.success
        finally:
            _cleanup(root)

    def test_korean_topic_with_special_chars(self):
        root = _tmp_root()
        try:
            topic = "신규 IP '루나'의 비주얼 콘셉트 — 2차 디자인 검토 (feat. 마케팅팀)"
            result = dispatch_meeting(
                _make_intent(topic=topic),
                user_id="u1", channel_id="c1",
                meetings_root=root,
            )
            assert result.success
            assert result.context is not None
            assert result.context.manifest.agenda == topic
        finally:
            _cleanup(root)

    def test_meetings_root_defaults_to_none(self):
        """When meetings_root is None, the orchestrator should receive None
        and use its own default."""
        captured_root: list[Optional[str]] = []

        def mock(
            request: MeetingCommandRequest,
            *,
            meetings_root: Optional[str] = None,
            config: Optional[MeetingConfig] = None,
        ) -> MeetingContext:
            captured_root.append(meetings_root)
            return create_meeting(
                request, meetings_root=meetings_root, config=config
            )

        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(),
                user_id="u1", channel_id="c1",
                # meetings_root omitted → None
                orchestrator=mock,
            )
            assert result.success
            assert len(captured_root) == 1
            assert captured_root[0] is None, (
                f"Expected None, got {captured_root[0]}"
            )
        finally:
            _cleanup(root)


# ── 6. Dataclass immutability ─────────────────────────────────────────────


class TestDispatchResultImmutability:
    """Verify that DispatchResult is immutable (frozen dataclass)."""

    def test_dispatch_result_is_frozen(self):
        result = dispatch_meeting(
            _make_intent(),
            user_id="u1", channel_id="c1",
            meetings_root=_tmp_root(),
        )
        assert result.success or not result.success  # just access fields
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]

    def test_success_result_has_no_error(self):
        root = _tmp_root()
        try:
            result = dispatch_meeting(
                _make_intent(),
                user_id="u1", channel_id="c1",
                meetings_root=root,
            )
            assert result.success
            assert result.error is None
            assert result.context is not None
        finally:
            _cleanup(root)

    def test_failure_result_has_no_context(self):
        result = dispatch_meeting(
            _make_intent(topic=""),
            user_id="u1", channel_id="c1",
        )
        assert not result.success
        assert result.context is None
        assert result.error is not None
