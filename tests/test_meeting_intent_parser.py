"""Tests for the meeting intent parser (Sub-AC 2b).

Verifies that cleaned message text is correctly parsed into structured
meeting intent (meeting_type, topic, participants, urgency) or that
non-meeting messages return the NoMeetingIntent sentinel.

Test categories:
1. Basic meeting intent detection — happy path (Korean + English)
2. No meeting / non-command messages
3. Meeting type classification — all six types
4. Urgency detection — P0 through P3
5. Participant extraction from role/team mentions
6. Topic extraction
7. Edge cases — empty input, only keyword, mixed languages
8. Confidence and reasoning
9. Dataclass immutability
"""

from __future__ import annotations

import pytest

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
    NoMeetingIntent,
    parse_meeting_intent,
)


# ── Basic meeting intent detection ────────────────────────────────────────


class TestBasicMeetingIntentDetection:
    """Happy-path tests for meeting intent detection in Korean and English."""

    # Korean meeting requests
    def test_korean_meeting_request_회의(self):
        result = parse_meeting_intent("신규 캐릭터 디자인 회의해줘")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)
        assert "캐릭터" in result.topic.lower()

    def test_forced_slash_command_accepts_natural_topic_without_meeting_keyword(self):
        result = parse_meeting_intent(
            "버류얼 유튜버 2d기반이걸 3d로 만들어 볼려고해. 추천작업 알려줘",
            force_meeting=True,
        )

        assert result.is_meeting
        assert isinstance(result, MeetingIntent)
        assert result.topic == "버류얼 유튜버 2d기반이걸 3d로 만들어 볼려고해. 추천작업 알려줘"
        assert result.meeting_type == "creative_production"

    def test_korean_meeting_request_논의(self):
        result = parse_meeting_intent("백엔드 API 리팩토링 논의하자")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    def test_korean_meeting_request_검토(self):
        result = parse_meeting_intent("SNS 홍보 전략 검토 부탁해요")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    def test_korean_meeting_request_상의(self):
        result = parse_meeting_intent("보안 취약점 대응 방안 상의합시다")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    def test_korean_meeting_request_토론(self):
        result = parse_meeting_intent("신규 IP 방향성 토론")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    # English meeting requests
    def test_english_meeting_request_meeting(self):
        result = parse_meeting_intent("Let's have a meeting about the new game engine architecture")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    def test_english_meeting_request_discuss(self):
        result = parse_meeting_intent("We need to discuss the character design direction")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)

    def test_english_meeting_request_review(self):
        result = parse_meeting_intent("Please review the latest build and give feedback")
        assert result.is_meeting
        assert isinstance(result, MeetingIntent)


# ── No meeting intent (non-command messages) ──────────────────────────────


class TestNoMeetingIntent:
    """Verify non-meeting messages correctly return NoMeetingIntent."""

    def test_casual_conversation(self):
        result = parse_meeting_intent("오늘 점심 뭐 먹을까요?")
        assert not result.is_meeting
        assert isinstance(result, NoMeetingIntent)
        assert result.reason == "no_meeting_keyword"

    def test_greeting(self):
        result = parse_meeting_intent("안녕하세요!")
        assert not result.is_meeting
        assert isinstance(result, NoMeetingIntent)

    def test_question_not_meeting(self):
        result = parse_meeting_intent("캐릭터 디자인 파일 어디에 저장되어 있나요?")
        assert not result.is_meeting
        assert isinstance(result, NoMeetingIntent)

    def test_status_check(self):
        result = parse_meeting_intent("서버 상태 좀 확인해줘")
        assert not result.is_meeting
        assert isinstance(result, NoMeetingIntent)

    def test_empty_string(self):
        result = parse_meeting_intent("")
        assert not result.is_meeting
        assert result.reason == "empty_input"

    def test_whitespace_only(self):
        result = parse_meeting_intent("   \n\t  ")
        assert not result.is_meeting
        assert result.reason == "empty_input"

    def test_english_non_meeting(self):
        result = parse_meeting_intent("What time is lunch?")
        assert not result.is_meeting
        assert isinstance(result, NoMeetingIntent)


# ── Meeting type classification ──────────────────────────────────────────


class TestMeetingTypeClassification:
    """Verify meeting type classification for all six types."""

    def test_creative_production__character(self):
        result = parse_meeting_intent("신규 캐릭터 루나 비주얼 디자인 회의")
        assert result.meeting_type == MEETING_TYPE_CREATIVE  # type: ignore[union-attr]

    def test_creative_production__music_video(self):
        result = parse_meeting_intent("뮤직비디오 오프닝 아이디어 회의해줘")
        assert result.meeting_type == MEETING_TYPE_CREATIVE  # type: ignore[union-attr]

    def test_creative_production__script(self):
        result = parse_meeting_intent("신규 애니메이션 대본 검토해주세요")
        assert result.meeting_type == MEETING_TYPE_CREATIVE  # type: ignore[union-attr]

    def test_creative_production__vfx(self):
        result = parse_meeting_intent("VFX 파티클 시스템 개선 논의")
        assert result.meeting_type == MEETING_TYPE_CREATIVE  # type: ignore[union-attr]

    def test_technical_development__backend(self):
        result = parse_meeting_intent("백엔드 API 리팩토링 회의")
        assert result.meeting_type == MEETING_TYPE_TECHNICAL  # type: ignore[union-attr]

    def test_technical_development__architecture(self):
        result = parse_meeting_intent("게임엔진 아키텍처 개선 논의")
        assert result.meeting_type == MEETING_TYPE_TECHNICAL  # type: ignore[union-attr]

    def test_technical_development__deploy(self):
        result = parse_meeting_intent("CI/CD 배포 파이프라인 검토")
        assert result.meeting_type == MEETING_TYPE_TECHNICAL  # type: ignore[union-attr]

    def test_marketing_strategy__sns(self):
        result = parse_meeting_intent("SNS 홍보 전략 수립 회의")
        assert result.meeting_type == MEETING_TYPE_MARKETING  # type: ignore[union-attr]

    def test_marketing_strategy__campaign(self):
        result = parse_meeting_intent("신작 출시 마케팅 캠페인 논의")
        assert result.meeting_type == MEETING_TYPE_MARKETING  # type: ignore[union-attr]

    def test_marketing_strategy__brand(self):
        result = parse_meeting_intent("브랜드 이미지 리뉴얼 검토 회의")
        assert result.meeting_type == MEETING_TYPE_MARKETING  # type: ignore[union-attr]

    def test_risk_assessment__security(self):
        result = parse_meeting_intent("보안 취약점 발견, 대응 방안 긴급 회의")
        assert result.meeting_type == MEETING_TYPE_RISK  # type: ignore[union-attr]

    def test_risk_assessment__legal(self):
        result = parse_meeting_intent("저작권 법률 검토 회의")
        assert result.meeting_type == MEETING_TYPE_RISK  # type: ignore[union-attr]

    def test_risk_assessment__budget(self):
        result = parse_meeting_intent("프로젝트 예산 재검토 논의")
        assert result.meeting_type == MEETING_TYPE_RISK  # type: ignore[union-attr]

    def test_project_review__milestone(self):
        result = parse_meeting_intent("Q3 마일스톤 중간점검 회의")
        assert result.meeting_type == MEETING_TYPE_REVIEW  # type: ignore[union-attr]

    def test_project_review__retrospective(self):
        result = parse_meeting_intent("프로젝트 회고 레트로 스펙티브")
        assert result.meeting_type == MEETING_TYPE_REVIEW  # type: ignore[union-attr]

    def test_general_planning__default(self):
        """When no domain keywords match, defaults to general_planning."""
        result = parse_meeting_intent("다음 분기 전략 회의")
        assert result.meeting_type == MEETING_TYPE_PLANNING  # type: ignore[union-attr]

    def test_general_planning__brainstorm(self):
        result = parse_meeting_intent("새로운 IP 아이디어 브레인스토밍")
        assert result.meeting_type == MEETING_TYPE_PLANNING  # type: ignore[union-attr]


# ── Urgency / priority detection ──────────────────────────────────────────


class TestUrgencyDetection:
    """Verify urgency signals map to correct priority levels."""

    def test_p0_긴급(self):
        result = parse_meeting_intent("긴급: 서버 장애 발생, 즉시 대응 회의")
        assert result.urgency == PRIORITY_P0  # type: ignore[union-attr]

    def test_p0_emergency(self):
        result = parse_meeting_intent("EMERGENCY meeting about production outage")
        assert result.urgency == PRIORITY_P0  # type: ignore[union-attr]

    def test_p0_asap(self):
        result = parse_meeting_intent("We need to meet ASAP about the security incident")
        assert result.urgency == PRIORITY_P0  # type: ignore[union-attr]

    def test_p1_중요(self):
        result = parse_meeting_intent("중요: 신규 캐릭터 최종 디자인 검토 회의")
        assert result.urgency == PRIORITY_P1  # type: ignore[union-attr]

    def test_p1_urgent(self):
        result = parse_meeting_intent("Urgent discussion about the marketing campaign launch")
        assert result.urgency == PRIORITY_P1  # type: ignore[union-attr]

    def test_p1_critical(self):
        result = parse_meeting_intent("Critical design review meeting")
        assert result.urgency == PRIORITY_P1  # type: ignore[union-attr]

    def test_p2_default(self):
        """No urgency signal → default P2."""
        result = parse_meeting_intent("뮤직비디오 아이디어 회의")
        assert result.urgency == PRIORITY_P2  # type: ignore[union-attr]

    def test_p2_english_default(self):
        result = parse_meeting_intent("Let's have a meeting about the new update")
        assert result.urgency == PRIORITY_P2  # type: ignore[union-attr]

    def test_p3_시간_있을_때(self):
        result = parse_meeting_intent("시간 있을 때 다음 프로젝트 방향성 논의해요")
        assert result.urgency == PRIORITY_P3  # type: ignore[union-attr]

    def test_p3_low_priority(self):
        result = parse_meeting_intent("Low priority: brainstorm session when available")
        assert result.urgency == PRIORITY_P3  # type: ignore[union-attr]

    def test_p0_overrides_others(self):
        """P0 keywords should take priority over other signals."""
        result = parse_meeting_intent("중요하진 않지만 긴급한 서버 장애 회의")
        assert result.urgency == PRIORITY_P0  # type: ignore[union-attr]


# ── Participant extraction ────────────────────────────────────────────────


class TestParticipantExtraction:
    """Verify role/team mentions are extracted as participants."""

    def test_art_director_mentioned(self):
        result = parse_meeting_intent("아트 디렉터와 캐릭터 디자인 회의")
        assert "art-director" in result.participants  # type: ignore[union-attr]

    def test_multiple_participants(self):
        result = parse_meeting_intent(
            "마케팅 리드랑 아트 디렉터, 테크 디렉터 다 같이 모여서 "
            "신규 IP 출시 전략 논의하자"
        )
        assert "art-director" in result.participants  # type: ignore[union-attr]
        assert "marketing-lead" in result.participants  # type: ignore[union-attr]
        assert "tech-director" in result.participants  # type: ignore[union-attr]

    def test_team_name_mentioned(self):
        result = parse_meeting_intent("아트팀 전체 회의 소집")
        assert "art-director" in result.participants  # type: ignore[union-attr]

    def test_english_role_mentioned(self):
        result = parse_meeting_intent("Meeting with the concept-artist and animator")
        assert "concept-artist" in result.participants  # type: ignore[union-attr]
        assert "animator" in result.participants  # type: ignore[union-attr]

    def test_no_participants_mentioned(self):
        result = parse_meeting_intent("뮤직비디오 아이디어 회의")
        assert result.participants == ()  # type: ignore[union-attr]

    def test_participants_deduplicated(self):
        result = parse_meeting_intent(
            "아트 디렉터랑 아트 디렉터한테 물어볼 것도 있어서 회의하자"
        )
        count = sum(1 for p in result.participants if p == "art-director")  # type: ignore[union-attr]
        assert count == 1


# ── Topic extraction ──────────────────────────────────────────────────────


class TestTopicExtraction:
    """Verify the core topic is correctly extracted from meeting messages."""

    def test_topic_strips_회의_keyword(self):
        result = parse_meeting_intent("신규 캐릭터 디자인 회의")
        topic = result.topic  # type: ignore[union-attr]
        assert "회의" not in topic.lower()

    def test_topic_strips_해줘_suffix(self):
        result = parse_meeting_intent("뮤직비디오 아이디어 회의해줘")
        topic = result.topic  # type: ignore[union-attr]
        assert "해줘" not in topic.lower()

    def test_topic_strips_부탁해요_suffix(self):
        result = parse_meeting_intent("SNS 전략 검토 부탁해요")
        topic = result.topic  # type: ignore[union-attr]
        assert "부탁해요" not in topic.lower()

    def test_topic_preserves_core_content(self):
        result = parse_meeting_intent("신규 캐릭터 루나의 비주얼 디자인 회의해줘")
        topic = result.topic  # type: ignore[union-attr]
        assert "루나" in topic
        assert "비주얼" in topic.lower()

    def test_topic_english(self):
        result = parse_meeting_intent("Let's discuss the new character design")
        topic = result.topic  # type: ignore[union-attr]
        assert "discuss" not in topic.lower()
        assert "character" in topic.lower()
        assert "design" in topic.lower()

    def test_minimal_meeting_message(self):
        """Just '회의' — topic should be preserved."""
        result = parse_meeting_intent("회의")
        assert result.is_meeting  # type: ignore[union-attr]
        assert result.meeting_type == MEETING_TYPE_PLANNING  # type: ignore[union-attr]

    def test_topic_not_empty_after_strip(self):
        """Topic should never be empty even after aggressive stripping."""
        result = parse_meeting_intent("회의해줘")
        assert result.is_meeting  # type: ignore[union-attr]
        assert len(result.topic) > 0  # type: ignore[union-attr]


# ── Mixed language / code-switching ───────────────────────────────────────


class TestMixedLanguage:
    """Verify parsing works with Korean-English code-switching."""

    def test_korean_with_english_term(self):
        result = parse_meeting_intent("Game Engine 아키텍처 리팩토링 회의")
        assert result.meeting_type == MEETING_TYPE_TECHNICAL  # type: ignore[union-attr]

    def test_english_with_korean_meeting_word(self):
        result = parse_meeting_intent("Let's have a 회의 about the new API design")
        assert result.is_meeting  # type: ignore[union-attr]

    def test_english_meeting_with_korean_topic(self):
        result = parse_meeting_intent("Meeting about 신규 캐릭터 디자인")
        assert result.meeting_type == MEETING_TYPE_CREATIVE  # type: ignore[union-attr]


# ── Confidence and reasoning ──────────────────────────────────────────────


class TestConfidenceAndReasoning:
    """Verify confidence scores and reasoning strings are populated."""

    def test_confidence_in_range(self):
        result = parse_meeting_intent("뮤직비디오 아이디어 회의")
        m = result  # type: ignore[union-attr]
        assert 0.0 <= m.confidence <= 1.0
        assert m.reasoning != ""

    def test_strong_signal_higher_confidence(self):
        """Multiple domain keywords → higher confidence."""
        result_weak = parse_meeting_intent("회의")
        result_strong = parse_meeting_intent("캐릭터 디자인 VFX 애니메이션 회의")
        cw = result_weak.confidence  # type: ignore[union-attr]
        cs = result_strong.confidence  # type: ignore[union-attr]
        assert cs >= cw

    def test_default_has_reasoning(self):
        result = parse_meeting_intent("주간 업무 회의")
        m = result  # type: ignore[union-attr]
        assert len(m.reasoning) > 0


# ── Dataclass immutability ────────────────────────────────────────────────


class TestDataclassImmutability:
    """Verify MeetingIntent and NoMeetingIntent are frozen."""

    def test_meeting_intent_is_frozen(self):
        result = parse_meeting_intent("뮤직비디오 회의")
        assert isinstance(result, MeetingIntent)
        with pytest.raises(Exception):
            result.meeting_type = "changed"  # type: ignore[misc]

    def test_no_meeting_intent_is_frozen(self):
        result = parse_meeting_intent("안녕하세요")
        assert isinstance(result, NoMeetingIntent)
        with pytest.raises(Exception):
            result.is_meeting = True  # type: ignore[misc]


# ── Team selection (Sub-AC 2.2) ───────────────────────────────────────────


class TestTeamSelection:
    """Verify team-level selection is extracted from meeting messages.

    Teams are detected via team name patterns (e.g. '아트팀', '기술팀')
    and mapped to their team-leader role-IDs.
    """

    def test_single_team_korean(self):
        result = parse_meeting_intent("아트팀 전체 회의 소집")
        assert "art-director" in result.teams  # type: ignore[union-attr]

    def test_single_team_english(self):
        result = parse_meeting_intent("Marketing team meeting about Q4 campaign")
        assert "marketing-lead" in result.teams  # type: ignore[union-attr]

    def test_multiple_teams_korean(self):
        result = parse_meeting_intent(
            "아트팀이랑 기술팀, 마케팅팀 다 같이 모여서 신규 IP 출시 전략 논의"
        )
        teams = result.teams  # type: ignore[union-attr]
        assert "art-director" in teams
        assert "tech-director" in teams
        assert "marketing-lead" in teams

    def test_team_with_space_korean(self):
        result = parse_meeting_intent("콘텐츠 팀이랑 마케팅 팀 회의")
        teams = result.teams  # type: ignore[union-attr]
        assert "content-pd" in teams
        assert "marketing-lead" in teams

    def test_design_team_maps_to_art(self):
        """디자인팀 → art-director."""
        result = parse_meeting_intent("디자인팀 회의 소집")
        assert "art-director" in result.teams  # type: ignore[union-attr]

    def test_dev_team_maps_to_tech(self):
        """개발팀 → tech-director."""
        result = parse_meeting_intent("개발팀 긴급 회의")
        assert "tech-director" in result.teams  # type: ignore[union-attr]

    def test_no_teams_mentioned(self):
        result = parse_meeting_intent("뮤직비디오 아이디어 회의")
        assert result.teams == ()  # type: ignore[union-attr]

    def test_teams_deduplicated(self):
        result = parse_meeting_intent("아트팀이랑 아트팀 디자인 검토 회의")
        count = sum(1 for t in result.teams if t == "art-director")  # type: ignore[union-attr]
        assert count == 1

    def test_teams_sorted(self):
        result = parse_meeting_intent("기술팀이랑 아트팀이랑 콘텐츠팀 회의")
        teams = result.teams  # type: ignore[union-attr]
        assert teams == tuple(sorted(teams)), f"teams not sorted: {teams}"


# ── Suggested roles (Sub-AC 2.2) ──────────────────────────────────────────


class TestSuggestedRoles:
    """Verify role-level participant constraints are extracted separately.

    Suggested roles are specific individual roles mentioned by the user
    (e.g. 'concept-artist', 'backend-dev'), distinct from team selection.
    """

    def test_single_role_korean(self):
        result = parse_meeting_intent("컨셉 아티스트랑 캐릭터 디자인 회의")
        assert "concept-artist" in result.suggested_roles  # type: ignore[union-attr]

    def test_single_role_english(self):
        result = parse_meeting_intent("Meeting with the backend-dev about API design")
        assert "backend-dev" in result.suggested_roles  # type: ignore[union-attr]

    def test_multiple_roles(self):
        result = parse_meeting_intent(
            "일러스트레이터랑 애니메이터, 백엔드 개발자랑 다 같이 회의"
        )
        roles = result.suggested_roles  # type: ignore[union-attr]
        assert "illustrator" in roles
        assert "animator" in roles
        assert "backend-dev" in roles

    def test_leader_by_name_not_team(self):
        """Leader mentioned by title, not team name → goes to suggested_roles."""
        result = parse_meeting_intent("아트 디렉터님, UI 디자이너랑 같이 UI 개편 회의")
        roles = result.suggested_roles  # type: ignore[union-attr]
        assert "art-director" in roles
        assert "ui-designer" in roles

    def test_no_roles_mentioned(self):
        result = parse_meeting_intent("신규 프로젝트 방향성 회의")
        assert result.suggested_roles == ()  # type: ignore[union-attr]

    def test_roles_deduplicated(self):
        result = parse_meeting_intent("컨셉 아티스트랑 컨셉아티스트도 같이 회의")
        count = sum(1 for r in result.suggested_roles if r == "concept-artist")  # type: ignore[union-attr]
        assert count == 1

    def test_roles_sorted(self):
        result = parse_meeting_intent("백엔드 개발자랑 애니메이터랑 회의")
        roles = result.suggested_roles  # type: ignore[union-attr]
        assert roles == tuple(sorted(roles)), f"roles not sorted: {roles}"

    def test_role_with_no_space_variant(self):
        """No-space Korean variants (e.g. '컨셉아티스트') are matched."""
        result = parse_meeting_intent("컨셉아티스트랑 vfx아티스트 의견 듣고 싶어서 회의")
        roles = result.suggested_roles  # type: ignore[union-attr]
        assert "concept-artist" in roles
        assert "vfx-artist" in roles

    def test_executor_role_detected(self):
        result = parse_meeting_intent("코드 실행자 포함해서 회의")
        assert "code-executor" in result.suggested_roles  # type: ignore[union-attr]


# ── Team vs role separation (Sub-AC 2.2) ──────────────────────────────────


class TestTeamAndRoleSeparation:
    """Verify teams and suggested_roles are correctly separated.

    Team name mentions (e.g. '아트팀') go to ``teams``.
    Individual role mentions (e.g. '컨셉 아티스트') go to ``suggested_roles``.
    ``participants`` is the backward-compatible union of both.
    """

    def test_teams_and_roles_separated(self):
        result = parse_meeting_intent(
            "아트팀이랑 기술팀 참여하고, 컨셉 아티스트랑 백엔드 개발자도 같이 회의"
        )
        teams = result.teams  # type: ignore[union-attr]
        roles = result.suggested_roles  # type: ignore[union-attr]
        assert "art-director" in teams
        assert "tech-director" in teams
        assert "concept-artist" in roles
        assert "backend-dev" in roles

    def test_participants_is_union(self):
        """``participants`` must contain all teams and suggested_roles."""
        result = parse_meeting_intent(
            "아트팀이랑 컨셉 아티스트랑 회의"
        )
        participants = result.participants  # type: ignore[union-attr]
        assert "art-director" in participants
        assert "concept-artist" in participants

    def test_team_in_participants_not_in_roles(self):
        """Team mentioned by team name → in teams + participants, not in roles."""
        result = parse_meeting_intent("아트팀 회의")
        assert "art-director" in result.teams  # type: ignore[union-attr]
        assert "art-director" in result.participants  # type: ignore[union-attr]
        assert "art-director" not in result.suggested_roles  # type: ignore[union-attr]  # team name → not a role

    def test_leader_name_in_roles_not_in_teams(self):
        """Leader mentioned by name (not team) → in roles + participants, not in teams."""
        result = parse_meeting_intent("아트 디렉터랑 회의")
        assert "art-director" in result.suggested_roles  # type: ignore[union-attr]
        assert "art-director" in result.participants  # type: ignore[union-attr]
        # Team name was not mentioned, so teams stays empty
        # (Only team-name patterns like '아트팀' set teams)

    def test_both_team_and_leader_name(self):
        """Mentioning both team name and leader name."""
        result = parse_meeting_intent("아트팀 전체랑 아트 디렉터님 회의")
        assert "art-director" in result.teams  # type: ignore[union-attr]  # from '아트팀'
        assert "art-director" in result.suggested_roles  # type: ignore[union-attr]  # from '아트 디렉터'
        # participants deduplicates: art-director appears once
        count = sum(1 for p in result.participants if p == "art-director")  # type: ignore[union-attr]
        assert count == 1

    def test_reasoning_includes_teams(self):
        result = parse_meeting_intent("아트팀이랑 기술팀 회의")
        reasoning = result.reasoning  # type: ignore[union-attr]
        assert "teams=" in reasoning

    def test_reasoning_includes_suggested_roles(self):
        result = parse_meeting_intent("컨셉 아티스트랑 백엔드 개발자 회의")
        reasoning = result.reasoning  # type: ignore[union-attr]
        assert "suggested_roles=" in reasoning

    def test_combined_reasoning(self):
        """When both teams and roles present, both appear in reasoning."""
        result = parse_meeting_intent("아트팀이랑 컨셉 아티스트 회의")
        reasoning = result.reasoning  # type: ignore[union-attr]
        assert "teams=" in reasoning
        assert "suggested_roles=" in reasoning


# ── Diverse natural-language inputs ───────────────────────────────────────


class TestDiverseInputs:
    """Test with a variety of natural-language inputs as specified in the AC."""

    DIVERSE_INPUTS: tuple[tuple[str, bool, str | None, str], ...] = (
        # (input_text, should_be_meeting, expected_type, description)
        (
            "뮤직비디오 오프닝 아이디어 회의해줘",
            True,
            MEETING_TYPE_CREATIVE,
            "standard Korean music video request",
        ),
        (
            "신규 캐릭터 '루나'의 비주얼 디자인을 논의하고, SNS 홍보 전략을 "
            "수립하며, 기존 게임엔진 백엔드 API 리팩토링도 함께 검토해주세요.",
            True,
            MEETING_TYPE_CREATIVE,  # strongest signal: 캐릭터 + 디자인
            "complex multi-domain Korean request",
        ),
        (
            "긴급: 서버 다운, 전체 장애 발생. 즉시 대응팀 소집.",
            True,
            MEETING_TYPE_RISK,
            "emergency incident Korean",
        ),
        (
            "Q3 마일스톤 진행상황 점검 회의",
            True,
            MEETING_TYPE_REVIEW,
            "project review Korean",
        ),
        (
            "신작 게임 글로벌 출시 마케팅 캠페인 전략 수립 회의",
            True,
            MEETING_TYPE_MARKETING,
            "marketing strategy Korean",
        ),
        (
            "마이크로서비스 아키텍처로의 전환 논의",
            True,
            MEETING_TYPE_TECHNICAL,
            "technical architecture Korean",
        ),
        (
            "Budget review meeting for Q4",
            True,
            MEETING_TYPE_RISK,
            "budget review English",
        ),
        (
            "Let's discuss the new character design and animation pipeline",
            True,
            MEETING_TYPE_CREATIVE,
            "creative discussion English",
        ),
        (
            "Urgent: security vulnerability found in production — meeting needed",
            True,
            MEETING_TYPE_RISK,
            "security incident English",
        ),
        (
            "Sprint retrospective and milestone review",
            True,
            MEETING_TYPE_REVIEW,
            "retrospective English",
        ),
        (
            "오늘 점심 메뉴 추천 좀 해주세요",
            False,
            None,
            "non-meeting casual Korean",
        ),
        (
            "What's the weather like today?",
            False,
            None,
            "non-meeting casual English",
        ),
        (
            "아트 디렉터님, UI 디자이너랑 같이 UI 개편 회의해요",
            True,
            MEETING_TYPE_CREATIVE,  # UI design → creative
            "with role mentions Korean",
        ),
        (
            "시간 있을 때 다음 프로젝트 방향성 가볍게 논의해요",
            True,
            MEETING_TYPE_PLANNING,
            "low-priority planning Korean",
        ),
        (
            "ASAP meeting about the brand campaign launch with the marketing lead",
            True,
            MEETING_TYPE_MARKETING,
            "urgent marketing English",
        ),
    )

    def test_diverse_inputs(self):
        """Parametrised test covering diverse natural-language inputs."""
        for text, should_be_meeting, expected_type, desc in self.DIVERSE_INPUTS:
            result = parse_meeting_intent(text)
            assert result.is_meeting == should_be_meeting, (
                f"[{desc}] Expected is_meeting={should_be_meeting}, "
                f"got {result.is_meeting} for: {text!r}"
            )
            if should_be_meeting and expected_type is not None:
                assert result.meeting_type == expected_type, (  # type: ignore[union-attr]
                    f"[{desc}] Expected meeting_type={expected_type}, "
                    f"got {result.meeting_type} for: {text!r}"
                )
