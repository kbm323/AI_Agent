"""Tests for the trigger router (Sub-AC 2.3).

Verifies that parsed command intent is correctly routed to the appropriate
meeting workflow entry point (initiate, join, cancel, status) with validation.
All tests use ParsedTriggerInput — no integration dependencies.

Test categories:
1. Basic action detection — initiate (default), join, cancel, status
2. Action detection via Korean keywords
3. Action detection via English keywords
4. Meeting ID extraction from topic text
5. Parameter validation per action
6. Edge cases — empty input, conflicting signals, unknown meeting_ids
7. input_from_intent bridge
8. Dataclass immutability
9. trigger_route.to_dict() serialization
"""

from __future__ import annotations

import pytest

from src.trigger_router import (
    TriggerAction,
    TriggerRoute,
    TriggerRoutingResult,
    ParsedTriggerInput,
    route_trigger,
    input_from_intent,
)

# ── Sample valid meeting_id ────────────────────────────────────────────────

_VALID_MEETING_ID = "meeting_20260610_a1b2c3d4e5f6"
_INVALID_MEETING_ID_FORMAT = "meeting_20260610"  # missing hex suffix
_BAD_MEETING_ID = "bad-meeting-id"


# ── Basic action detection ──────────────────────────────────────────────────

class TestBasicActionDetection:
    """Happy-path tests for action type detection."""

    def test_initiate_from_meeting_intent(self):
        """Standard meeting intent → initiate."""
        result = route_trigger(ParsedTriggerInput(
            topic="뮤직비디오 오프닝 아이디어 회의",
            meeting_type="creative_production",
        ))
        assert result.success
        assert result.route is not None
        assert result.route.action == TriggerAction.INITIATE
        assert result.route.topic == "뮤직비디오 오프닝 아이디어 회의"
        assert result.route.meeting_type == "creative_production"
        assert result.route.source == "default"

    def test_initiate_from_initiate_keyword_korean(self):
        """시작 keyword → initiate."""
        result = route_trigger(ParsedTriggerInput(
            topic="신규 프로젝트 회의 시작",
            meeting_type="general_planning",
        ))
        assert result.success
        assert result.route.action == TriggerAction.INITIATE
        assert result.route.source == "keyword"

    def test_initiate_from_initiate_keyword_english(self):
        """'start a meeting' → initiate."""
        result = route_trigger(ParsedTriggerInput(
            topic="start a meeting about API redesign",
            meeting_type="technical_development",
        ))
        assert result.success
        assert result.route.action == TriggerAction.INITIATE

    def test_cancel_with_meeting_id_and_keyword_korean(self):
        """취소 keyword + meeting_id → cancel."""
        result = route_trigger(ParsedTriggerInput(
            topic="회의 취소해줘",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL
        assert result.route.meeting_id == _VALID_MEETING_ID
        assert result.route.source == "keyword"

    def test_cancel_with_meeting_id_and_keyword_english(self):
        """'cancel' keyword + meeting_id → cancel."""
        result = route_trigger(ParsedTriggerInput(
            topic="cancel this meeting please",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL

    def test_cancel_keyword_without_meeting_id(self):
        """취소 keyword without explicit meeting_id → cancel but no meeting_id."""
        result = route_trigger(ParsedTriggerInput(
            topic="회의 취소해줘",
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL
        assert result.route.meeting_id == ""
        assert result.route.confidence == 0.7  # lower confidence without meeting_id

    def test_join_with_meeting_id_and_keyword_korean(self):
        """참여 keyword + meeting_id → join."""
        result = route_trigger(ParsedTriggerInput(
            topic="회의에 참여할게",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.JOIN
        assert result.route.meeting_id == _VALID_MEETING_ID

    def test_join_with_meeting_id_and_keyword_english(self):
        """'join' keyword + meeting_id → join."""
        result = route_trigger(ParsedTriggerInput(
            topic="join the meeting",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.JOIN

    def test_status_with_keyword_korean(self):
        """상태 keyword → status."""
        result = route_trigger(ParsedTriggerInput(
            topic="회의 상태 알려줘",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS
        assert result.route.source == "keyword"

    def test_status_with_keyword_english(self):
        """'status' keyword → status."""
        result = route_trigger(ParsedTriggerInput(
            topic="what's the status of the meeting?",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS

    def test_status_with_meeting_id_in_topic(self):
        """meeting_id embedded in topic → status."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"진행상황 알려줘 {_VALID_MEETING_ID}",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS
        assert result.route.meeting_id == _VALID_MEETING_ID


# ── Korean keyword coverage ─────────────────────────────────────────────────

class TestKoreanKeywordCoverage:
    """Verify all Korean keywords correctly trigger their actions."""

    # ── Cancel keywords ──
    @pytest.mark.parametrize("keyword", [
        "취소", "중단", "멈춰", "그만", "철회", "폐기", "종료",
    ])
    def test_cancel_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 {keyword}해줘",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL, f"keyword '{keyword}' should trigger CANCEL"

    # ── Join keywords ──
    @pytest.mark.parametrize("keyword", [
        "참여", "참가", "들어가", "참석", "합류",
    ])
    def test_join_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의에 {keyword}할게",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.JOIN, f"keyword '{keyword}' should trigger JOIN"

    # ── Status keywords ──
    @pytest.mark.parametrize("keyword", [
        "상태", "현황", "진행", "보고", "목록", "조회", "확인",
    ])
    def test_status_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 {keyword}",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS, f"keyword '{keyword}' should trigger STATUS"

    # ── Initiate keywords ──
    @pytest.mark.parametrize("keyword", [
        "시작", "개최", "열어", "생성", "소집", "소환", "착수",
    ])
    def test_initiate_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 {keyword}",
            meeting_type="general_planning",
        ))
        assert result.success
        assert result.route.action == TriggerAction.INITIATE, f"keyword '{keyword}' should trigger INITIATE"


# ── English keyword coverage ────────────────────────────────────────────────

class TestEnglishKeywordCoverage:
    """Verify all English keywords correctly trigger their actions."""

    @pytest.mark.parametrize("keyword", [
        "cancel", "stop", "abort",
    ])
    def test_cancel_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"please {keyword} the meeting",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL

    @pytest.mark.parametrize("keyword", [
        "join", "enter", "attend",
    ])
    def test_join_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"{keyword} the meeting",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.JOIN

    @pytest.mark.parametrize("keyword", [
        "status", "report", "progress", "list", "query", "check",
    ])
    def test_status_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"meeting {keyword}",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS

    @pytest.mark.parametrize("keyword", [
        "start", "create", "new", "launch", "begin", "kickoff",
    ])
    def test_initiate_keywords(self, keyword):
        result = route_trigger(ParsedTriggerInput(
            topic=f"{keyword} meeting about API design",
            meeting_type="technical_development",
        ))
        assert result.success
        assert result.route.action == TriggerAction.INITIATE


# ── Priority handling ───────────────────────────────────────────────────────

class TestPriorityHandling:
    """Verify priority levels are correctly carried through."""

    def test_p0_carried_through(self):
        result = route_trigger(ParsedTriggerInput(
            topic="긴급 서버 장애 회의",
            meeting_type="risk_assessment",
            priority="p0",
        ))
        assert result.success
        assert result.route.priority == "p0"

    def test_p1_carried_through(self):
        result = route_trigger(ParsedTriggerInput(
            topic="중요 회의",
            meeting_type="general_planning",
            priority="p1",
        ))
        assert result.success
        assert result.route.priority == "p1"

    def test_p2_default(self):
        result = route_trigger(ParsedTriggerInput(
            topic="일반 회의",
            meeting_type="general_planning",
        ))
        assert result.success
        assert result.route.priority == "p2"

    def test_p3_carried_through(self):
        result = route_trigger(ParsedTriggerInput(
            topic="시간 있을 때 회의",
            meeting_type="general_planning",
            priority="p3",
        ))
        assert result.success
        assert result.route.priority == "p3"

    def test_invalid_priority_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의",
            meeting_type="general_planning",
            priority="p5",
        ))
        assert not result.success
        assert "invalid priority" in result.error


# ── Team and role passthrough ───────────────────────────────────────────────

class TestTeamAndRolePassthrough:
    """Verify teams and suggested_roles are carried through to the route."""

    def test_teams_passthrough(self):
        result = route_trigger(ParsedTriggerInput(
            topic="아트팀 회의",
            meeting_type="creative_production",
            teams=("art-director", "tech-director"),
        ))
        assert result.success
        assert result.route.teams == ("art-director", "tech-director")

    def test_suggested_roles_passthrough(self):
        result = route_trigger(ParsedTriggerInput(
            topic="컨셉 아티스트랑 회의",
            meeting_type="creative_production",
            suggested_roles=("concept-artist", "illustrator"),
        ))
        assert result.success
        assert result.route.suggested_roles == ("concept-artist", "illustrator")

    def test_participants_passthrough(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의",
            meeting_type="general_planning",
            participants=("art-director", "backend-dev"),
        ))
        assert result.success
        assert result.route.participants == ("art-director", "backend-dev")


# ── Meeting ID extraction from topic ────────────────────────────────────────

class TestMeetingIdExtraction:
    """Verify meeting_id is extracted from topic text."""

    def test_extract_from_topic_with_cancel(self):
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 취소해줘 {_VALID_MEETING_ID}",
        ))
        assert result.success
        assert result.route.action == TriggerAction.CANCEL
        assert result.route.meeting_id == _VALID_MEETING_ID

    def test_extract_from_topic_with_status(self):
        result = route_trigger(ParsedTriggerInput(
            topic=f"{_VALID_MEETING_ID} 상태 알려줘",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS
        assert result.route.meeting_id == _VALID_MEETING_ID

    def test_extract_from_topic_with_join(self):
        result = route_trigger(ParsedTriggerInput(
            topic=f"{_VALID_MEETING_ID} 참여할게",
        ))
        assert result.success
        assert result.route.action == TriggerAction.JOIN
        assert result.route.meeting_id == _VALID_MEETING_ID

    def test_explicit_meeting_id_overrides_topic(self):
        """Explicit meeting_id parameter takes priority."""
        explicit_id = "meeting_20260610_abcdef123456"
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 취소해줘 {_VALID_MEETING_ID}",
            meeting_id=explicit_id,
        ))
        assert result.success
        assert result.route.meeting_id == explicit_id

    def test_no_meeting_id_in_plain_topic(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의 취소해줘",
        ))
        assert result.success
        assert result.route.meeting_id == ""


# ── Parameter validation ────────────────────────────────────────────────────

class TestParameterValidation:
    """Verify per-action parameter validation."""

    # ── Initiate ──
    def test_initiate_empty_topic_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="",
            meeting_type="general_planning",
        ))
        assert not result.success
        assert "topic" in result.error.lower()

    def test_initiate_invalid_meeting_type_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의",
            meeting_type="invalid_type",
        ))
        assert not result.success
        assert "invalid meeting_type" in result.error

    def test_initiate_all_valid_types_accepted(self):
        valid_types = [
            "creative_production",
            "technical_development",
            "marketing_strategy",
            "risk_assessment",
            "general_planning",
            "project_review",
        ]
        for mt in valid_types:
            result = route_trigger(ParsedTriggerInput(
                topic=f"test meeting for {mt}",
                meeting_type=mt,
            ))
            assert result.success, f"meeting_type '{mt}' should be accepted"

    # ── Join ──
    def test_join_requires_meeting_id(self):
        result = route_trigger(ParsedTriggerInput(
            topic="join the meeting",
        ))
        assert result.success  # still succeeds (routed as join)
        assert result.route.action == TriggerAction.JOIN
        assert result.route.confidence == 0.65  # lower confidence

    def test_join_invalid_meeting_id_format_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="join the meeting",
            meeting_id=_BAD_MEETING_ID,
        ))
        assert not result.success
        assert "invalid meeting_id" in result.error

    # ── Cancel ──
    def test_cancel_invalid_meeting_id_format_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="cancel meeting",
            meeting_id=_BAD_MEETING_ID,
        ))
        assert not result.success
        assert "invalid meeting_id" in result.error

    def test_cancel_with_incomplete_meeting_id_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="cancel meeting",
            meeting_id=_INVALID_MEETING_ID_FORMAT,
        ))
        assert not result.success
        assert "invalid meeting_id" in result.error

    # ── Status ──
    def test_status_with_invalid_meeting_id_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="status check",
            meeting_id=_BAD_MEETING_ID,
        ))
        assert not result.success
        assert "invalid meeting_id" in result.error

    def test_status_without_meeting_id_accepted(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의 상태 알려줘",
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS
        assert result.route.meeting_id == ""


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_whitespace_only_topic_rejected(self):
        result = route_trigger(ParsedTriggerInput(
            topic="   \n\t  ",
        ))
        assert not result.success

    def test_empty_topic_with_valid_meeting_id_defaults_to_status(self):
        """Empty topic + valid meeting_id → status (not failure)."""
        result = route_trigger(ParsedTriggerInput(
            topic="",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS
        assert result.route.meeting_id == _VALID_MEETING_ID

    def test_non_meeting_intent_with_meeting_id(self):
        """When is_meeting=False but meeting_id present → status."""
        result = route_trigger(ParsedTriggerInput(
            topic="오늘 점심 뭐 먹지?",
            is_meeting=False,
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS

    def test_non_meeting_intent_without_meeting_id_rejected(self):
        """Casual chat → rejected."""
        result = route_trigger(ParsedTriggerInput(
            topic="오늘 점심 뭐 먹지?",
            is_meeting=False,
        ))
        assert not result.success

    def test_cancel_priority_over_status(self):
        """Cancel keyword should take priority over status keyword."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 취소 상태 확인 {_VALID_MEETING_ID}",
        ))
        assert result.success
        # Cancel is checked before status in the detection order
        assert result.route.action == TriggerAction.CANCEL

    def test_status_with_multiple_meeting_ids_extracts_first(self):
        """Only the first meeting_id in the topic is extracted."""
        second_id = "meeting_20260610_123456789abc"
        result = route_trigger(ParsedTriggerInput(
            topic=f"{_VALID_MEETING_ID} 상태 and {second_id}",
        ))
        assert result.success
        assert result.route.meeting_id == _VALID_MEETING_ID  # first one

    def test_non_meeting_topic_with_meeting_id_embedded(self):
        """meeting_id in topic but no meeting keywords → status."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"이거 {_VALID_MEETING_ID} 좀 봐줘",
            is_meeting=False,
        ))
        assert result.success
        assert result.route.action == TriggerAction.STATUS


# ── input_from_intent bridge ────────────────────────────────────────────────

class TestInputFromIntentBridge:
    """Verify the convenience bridge from MeetingIntent to ParsedTriggerInput."""

    def test_basic_mapping(self):
        """All MeetingIntent fields map correctly."""
        from src.meeting_intent_parser import MeetingIntent
        intent = MeetingIntent(
            meeting_type="creative_production",
            topic="뮤직비디오 아이디어 회의",
            participants=("art-director", "concept-artist"),
            teams=("art-director",),
            suggested_roles=("concept-artist",),
            urgency="p1",
        )
        parsed = input_from_intent(intent, meeting_id=_VALID_MEETING_ID)
        assert parsed.topic == "뮤직비디오 아이디어 회의"
        assert parsed.meeting_type == "creative_production"
        assert parsed.priority == "p1"
        assert parsed.participants == ("art-director", "concept-artist")
        assert parsed.teams == ("art-director",)
        assert parsed.suggested_roles == ("concept-artist",)
        assert parsed.meeting_id == _VALID_MEETING_ID
        assert parsed.is_meeting is True

    def test_with_no_meeting_intent(self):
        """NoMeetingIntent maps correctly."""
        from src.meeting_intent_parser import NoMeetingIntent
        intent = NoMeetingIntent(is_meeting=False, reason="no_meeting_keyword")
        parsed = input_from_intent(intent)
        assert parsed.is_meeting is False
        assert parsed.topic == ""
        assert parsed.meeting_type == ""

    def test_full_pipeline_initiate(self):
        """End-to-end: MeetingIntent → ParsedTriggerInput → route_trigger."""
        from src.meeting_intent_parser import MeetingIntent
        intent = MeetingIntent(
            meeting_type="technical_development",
            topic="백엔드 API 리팩토링 회의",
            participants=("tech-director", "backend-dev"),
            urgency="p1",
        )
        parsed = input_from_intent(intent)
        result = route_trigger(parsed)
        assert result.success
        assert result.route.action == TriggerAction.INITIATE
        assert result.route.topic == "백엔드 API 리팩토링 회의"
        assert result.route.meeting_type == "technical_development"
        assert result.route.priority == "p1"

    def test_full_pipeline_cancel(self):
        """End-to-end: MeetingIntent with cancel → route_trigger."""
        from src.meeting_intent_parser import MeetingIntent
        intent = MeetingIntent(
            meeting_type="general_planning",
            topic="회의 취소해줘",
        )
        parsed = input_from_intent(intent, meeting_id=_VALID_MEETING_ID)
        result = route_trigger(parsed)
        assert result.success
        assert result.route.action == TriggerAction.CANCEL
        assert result.route.meeting_id == _VALID_MEETING_ID


# ── Dataclass immutability ──────────────────────────────────────────────────

class TestDataclassImmutability:
    """Verify that result dataclasses are frozen."""

    def test_trigger_route_is_frozen(self):
        route = TriggerRoute(action=TriggerAction.INITIATE, topic="test")
        with pytest.raises(Exception):
            route.action = TriggerAction.CANCEL  # type: ignore[misc]

    def test_trigger_routing_result_is_frozen(self):
        result = TriggerRoutingResult(success=True)
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]

    def test_parsed_trigger_input_is_frozen(self):
        parsed = ParsedTriggerInput(topic="test")
        with pytest.raises(Exception):
            parsed.topic = "changed"  # type: ignore[misc]


# ── to_dict serialization ───────────────────────────────────────────────────

class TestSerialization:
    """Verify to_dict() produces JSON-compatible output."""

    def test_to_dict_includes_all_fields(self):
        route = TriggerRoute(
            action=TriggerAction.INITIATE,
            topic="test topic",
            meeting_type="creative_production",
            priority="p1",
            meeting_id=_VALID_MEETING_ID,
            participants=("art-director", "concept-artist"),
            teams=("art-director",),
            suggested_roles=("concept-artist",),
            confidence=0.95,
            reasoning="test reasoning",
            source="keyword",
        )
        d = route.to_dict()
        assert d["action"] == "initiate"
        assert d["topic"] == "test topic"
        assert d["meeting_type"] == "creative_production"
        assert d["priority"] == "p1"
        assert d["meeting_id"] == _VALID_MEETING_ID
        assert d["participants"] == ["art-director", "concept-artist"]
        assert d["teams"] == ["art-director"]
        assert d["suggested_roles"] == ["concept-artist"]
        assert d["confidence"] == 0.95
        assert d["reasoning"] == "test reasoning"
        assert d["source"] == "keyword"

    def test_to_dict_empty_fields(self):
        route = TriggerRoute(action=TriggerAction.STATUS)
        d = route.to_dict()
        assert d["action"] == "status"
        assert d["topic"] == ""
        assert d["participants"] == []
        assert d["teams"] == []


# ── Confidence scoring ──────────────────────────────────────────────────────

class TestConfidenceScoring:
    """Verify confidence scores are sensible per action and conditions."""

    def test_initiate_with_keyword_high_confidence(self):
        result = route_trigger(ParsedTriggerInput(
            topic="새로운 회의 시작",
            meeting_type="general_planning",
        ))
        assert result.route.confidence == 0.95

    def test_initiate_default_medium_confidence(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의해줘",
            meeting_type="general_planning",
        ))
        assert result.route.confidence == 0.85

    def test_cancel_with_meeting_id_high_confidence(self):
        result = route_trigger(ParsedTriggerInput(
            topic="회의 취소",
            meeting_id=_VALID_MEETING_ID,
        ))
        assert result.route.confidence >= 0.85

    def test_status_with_meeting_id_higher_confidence(self):
        with_id = route_trigger(ParsedTriggerInput(
            topic="상태 알려줘",
            meeting_id=_VALID_MEETING_ID,
        ))
        without_id = route_trigger(ParsedTriggerInput(
            topic="상태 알려줘",
        ))
        assert with_id.route.confidence >= without_id.route.confidence

    def test_confidence_in_range(self):
        """All confidence values should be in [0.0, 1.0]."""
        test_cases = [
            ParsedTriggerInput(topic="회의", meeting_type="general_planning"),
            ParsedTriggerInput(topic="회의 취소", meeting_id=_VALID_MEETING_ID),
            ParsedTriggerInput(topic="회의 참여", meeting_id=_VALID_MEETING_ID),
            ParsedTriggerInput(topic="회의 상태"),
        ]
        for tc in test_cases:
            result = route_trigger(tc)
            if result.success:
                assert 0.0 <= result.route.confidence <= 1.0, (
                    f"confidence {result.route.confidence} out of range for topic '{tc.topic}'"
                )


# ── Action priority (which keyword wins) ────────────────────────────────────

class TestActionPriority:
    """Verify the first-match-wins ordering of action detection."""

    def test_cancel_beats_join(self):
        """Cancel + join keywords → cancel wins."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"취소 또는 참여 {_VALID_MEETING_ID}",
        ))
        assert result.route.action == TriggerAction.CANCEL

    def test_cancel_beats_initiate(self):
        """Cancel + initiate keywords → cancel wins."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"회의 취소하고 새로 시작 {_VALID_MEETING_ID}",
        ))
        assert result.route.action == TriggerAction.CANCEL

    def test_join_beats_status(self):
        """Join + status keywords → join wins (when meeting_id present)."""
        result = route_trigger(ParsedTriggerInput(
            topic=f"참여 상태 확인 {_VALID_MEETING_ID}",
        ))
        assert result.route.action == TriggerAction.JOIN
