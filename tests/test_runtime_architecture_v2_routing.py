from __future__ import annotations

from src.runtime_architecture_v2.routing import FakeQwenRouter, RouteType
from src.runtime_architecture_v2.schemas import MeetingRun


def _run(text: str, meeting_run_id: str = "mr_route") -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text=text,
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        guild_id="guild-1",
        hermes_session_id="sess-1",
    )


def test_fake_router_fast_qa_stays_on_hermes_without_workers_or_discord_spam():
    result = FakeQwenRouter().route(_run("간단히 이 용어만 설명해줘"))

    assert result.route_type == RouteType.FAST_QA
    assert result.teams == ("ceo_coordinator",)
    assert result.worker_roles == ()
    assert result.validators == ()
    assert result.execution_required is False
    assert result.projection_policy == "direct_reply"
    assert result.research_owner == ""


def test_fake_router_creative_meeting_selects_domain_research():
    result = FakeQwenRouter().route(_run("버추얼 아이돌 뮤직비디오 콘셉트 회의 열어줘"))

    assert result.route_type == RouteType.CREATIVE_MEETING
    assert result.teams == ("content_lead", "art_lead", "marketing_lead")
    assert "creative_director" in result.worker_roles
    assert result.validators == ("glm_validator",)
    assert result.execution_required is False
    assert result.research_owner == "content_lead"
    assert result.estimated_rounds == 2


def test_fake_router_technical_execution_uses_opencode_roles_and_codex_auditor():
    result = FakeQwenRouter().route(_run("Discord 어댑터 구현하고 테스트까지 실행해줘"))

    assert result.route_type == RouteType.TECHNICAL_EXECUTION
    assert result.teams == ("tech_lead",)
    assert result.worker_roles == ("software_engineer", "test_engineer")
    assert result.validators == ("glm_validator", "codex_auditor")
    assert result.execution_required is True
    assert result.research_owner == "tech_lead"


def test_fake_router_legal_risk_has_no_standalone_research_team():
    result = FakeQwenRouter().route(_run("계약서와 저작권 리스크 검토해줘"))

    assert result.route_type == RouteType.LEGAL_RISK
    assert result.teams == ("business_support_lead", "validation_audit")
    assert result.validators == ("glm_validator", "codex_auditor")
    assert result.research_owner == "business_support_lead"
    assert "research_lead" not in result.teams


def test_fake_router_mixed_request_routes_cross_functionally_with_low_confidence():
    result = FakeQwenRouter().route(
        _run("뮤비 기획하고 홍보 전략 세우고 기술 구현 난이도도 봐줘")
    )

    assert result.route_type == RouteType.MIXED_REQUEST
    assert result.teams == (
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "business_support_lead",
    )
    assert result.validators == ("glm_validator", "codex_auditor")
    assert result.execution_required is True
    assert result.research_owner == "tech_lead"
    assert result.confidence < 0.9
