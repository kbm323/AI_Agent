from __future__ import annotations

import os

import pytest

from src.runtime_architecture_v2.projection import (
    DiscordProjectionFormatter,
    FakeDiscordProjectionSink,
    HermesCommandSurfacePolicy,
    LiveDiscordProjectionSink,
    _default_discord_http_post,
    default_team_bot_topology,
)
from src.runtime_architecture_v2.schemas import (
    DiscordProjectionEvent,
    MeetingRun,
    MeetingRunState,
    RoutingResult,
    ValidationVerdict,
)


def test_default_team_bot_topology_keeps_stable_company_org_chart():
    topology = default_team_bot_topology()

    assert tuple(topology.roles) == (
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "business_support_lead",
        "validation_audit",
    )
    assert topology.role_for_team("tech_lead") == "tech_lead"
    assert topology.role_for_team("validation_audit") == "validation_audit"
    assert topology.role_for_team("unknown_team") == "ceo_coordinator"
    assert "research_lead" not in topology.roles
    assert "personal_assistant" not in topology.roles
    assert "openclaw" not in topology.to_dict()


def test_team_bot_topology_rejects_forbidden_role_variants():
    forbidden_variants = (
        "Research Lead",
        "research-lead",
        "PERSONAL_ASSISTANT",
        "OpenClaw",
        "openclaw_bot",
    )

    for role in forbidden_variants:
        with pytest.raises(ValueError, match="forbidden bot roles"):
            default_team_bot_topology().__class__(roles=(role,))


def test_team_bot_topology_rejects_mapping_and_fallback_escape_hatches():
    topology_type = default_team_bot_topology().__class__

    with pytest.raises(ValueError, match="forbidden bot roles"):
        topology_type(
            roles=("ceo_coordinator",),
            team_to_role={"x": "research_lead"},
        )

    with pytest.raises(ValueError, match="forbidden bot roles"):
        topology_type(roles=("ceo_coordinator",), fallback_role="personal_assistant")

    with pytest.raises(ValueError, match="unknown mapped bot role"):
        topology_type(
            roles=("ceo_coordinator",),
            team_to_role={"x": "shadow_bot"},
        )


def test_formatter_builds_discord_safe_summary_without_raw_worker_output():
    run = MeetingRun.create(
        meeting_run_id="mr_006",
        trigger_text="@everyone 기술/마케팅 합동 회의 열어줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        guild_id="guild-1",
        priority="P1",
    )
    routing = RoutingResult(
        meeting_run_id="mr_006",
        route_type="meeting",
        teams=("tech_lead", "marketing_lead"),
        worker_roles=("pipeline_rd", "newsletter_strategy"),
        validators=("glm_validator", "codex_auditor"),
        research_owner="tech_lead",
        projection_policy="summary_only",
        rationale="Need cross-functional decision.",
    )
    verdict = ValidationVerdict(
        validation_id="val_006",
        meeting_run_id="mr_006",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        verdict="conditional_pass",
        confidence=0.88,
        findings=("summary is consistent",),
        required_actions=("collect visual reference",),
    )

    event = DiscordProjectionFormatter().build_summary_event(
        event_id="proj_006",
        run=run,
        state=MeetingRunState.REPORTING,
        routing=routing,
        verdicts=(verdict,),
        target_channel_id="results-1",
        target_thread_id="thread-1",
        raw_worker_outputs=("raw transcript should never leak",),
    )

    assert event.bot_role == "ceo_coordinator"
    assert event.target_channel_id == "results-1"
    assert event.target_thread_id == "thread-1"
    assert event.source == "meeting_run"
    assert event.source_id == "mr_006"
    assert "@everyone" not in event.content
    assert "@everyone" in event.content
    assert "raw transcript should never leak" not in event.content
    assert "conditional_pass" in event.content
    assert len(event.content) <= 2000


def test_formatter_redacts_secret_like_values_before_projection():
    run = MeetingRun.create(
        meeting_run_id="mr_secret",
        trigger_text="검토해줘 api_key=SECRET_VALUE @here",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )
    routing = RoutingResult(
        meeting_run_id="mr_secret",
        route_type="meeting",
        teams=("tech_lead",),
        research_owner="tech_lead",
        rationale="temporary password=hunter2 should not leak",
    )
    verdict = ValidationVerdict(
        validation_id="val_secret",
        meeting_run_id="mr_secret",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        verdict="pass",
        confidence=0.91,
        findings=("token: SECRET_VALUE",),
        required_actions=("rotate credential=SECRET_VALUE",),
    )

    summary = DiscordProjectionFormatter().build_summary_event(
        event_id="proj_secret",
        run=run,
        state=MeetingRunState.REPORTING,
        routing=routing,
        verdicts=(verdict,),
        target_channel_id="results-1",
    )
    validation = DiscordProjectionFormatter().build_validation_event(
        event_id="proj_secret_validation",
        verdict=verdict,
        target_channel_id="audit-1",
    )

    combined = summary.content + validation.content
    assert "SECRET_VALUE" not in combined
    assert "hunter2" not in combined
    assert "[redacted]" in combined
    assert "@here" not in combined


def test_formatter_redacts_common_structured_secret_formats():
    run = MeetingRun.create(
        meeting_run_id="mr_structured_secret",
        trigger_text='JSON {"token": "SECRET_JSON"} bearer SECRET_BEARER',
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )
    routing = RoutingResult(
        meeting_run_id="mr_structured_secret",
        route_type="meeting",
        teams=("tech_lead",),
        research_owner="tech_lead",
        rationale="yaml credential: SECRET_YAML quoted password='SECRET_QUOTED'",
    )

    event = DiscordProjectionFormatter().build_summary_event(
        event_id="proj_structured_secret",
        run=run,
        state=MeetingRunState.REPORTING,
        routing=routing,
        target_channel_id="results-1",
    )

    assert "SECRET_JSON" not in event.content
    assert "SECRET_BEARER" not in event.content
    assert "SECRET_YAML" not in event.content
    assert "SECRET_QUOTED" not in event.content
    assert event.content.count("[redacted]") >= 4
    assert "MeetingRun mr_structured_secret projection" in event.content


def test_formatter_redacts_assignment_wrapped_bearer_secret():
    run = MeetingRun.create(
        meeting_run_id="mr_bearer",
        trigger_text="token: Bearer SECRET_LEAK",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )

    event = DiscordProjectionFormatter().build_summary_event(
        event_id="proj_bearer",
        run=run,
        state=MeetingRunState.REPORTING,
        routing=None,
        target_channel_id="results-1",
    )

    assert "SECRET_LEAK" not in event.content
    assert "[redacted]" in event.content


def test_formatter_truncates_long_unicode_content_without_losing_traceability():
    long_text = "긴본문" * 900
    run = MeetingRun.create(
        meeting_run_id="mr_long",
        trigger_text=long_text,
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )

    event = DiscordProjectionFormatter().build_summary_event(
        event_id="proj_long",
        run=run,
        state=MeetingRunState.REPORTING,
        routing=None,
        target_channel_id="results-1",
    )

    assert len(event.content) == 2000
    assert event.source == "meeting_run"
    assert event.source_id == "mr_long"
    assert event.meeting_run_id == "mr_long"
    assert event.content.startswith("MeetingRun mr_long projection")


def test_formatter_routes_validation_projection_to_validation_audit_bot():
    verdict = ValidationVerdict(
        validation_id="val_007",
        meeting_run_id="mr_007",
        validator_role="codex_auditor",
        validator_model="codex",
        verdict="reject",
        confidence=0.64,
        findings=("missing Discord projection idempotency",),
        required_actions=("add fake sink test",),
    )

    event = DiscordProjectionFormatter().build_validation_event(
        event_id="proj_val_007",
        verdict=verdict,
        target_channel_id="audit-1",
    )

    assert event == DiscordProjectionEvent.from_dict(event.to_dict())
    assert event.bot_role == "validation_audit"
    assert event.meeting_run_id == "mr_007"
    assert event.source == "validation_verdict"
    assert event.source_id == "val_007"
    assert "reject" in event.content


def test_fake_projection_sink_is_idempotent_and_rejects_empty_content():
    sink = FakeDiscordProjectionSink()
    event = DiscordProjectionEvent(
        event_id="proj_008",
        meeting_run_id="mr_008",
        bot_role="tech_lead",
        target_channel_id="results-1",
        target_thread_id="thread-1",
        content="기술 리드 업데이트",
        source="worker_task",
        source_id="wt_008",
    )

    first = sink.publish(event)
    second = sink.publish(event)

    assert first.status == "published"
    assert second.status == "duplicate"
    assert first.discord_message_id == second.discord_message_id
    assert sink.events == (event,)

    rejected = sink.publish(
        DiscordProjectionEvent(
            event_id="proj_empty",
            meeting_run_id="mr_008",
            bot_role="tech_lead",
            target_channel_id="results-1",
            content="",
            source="worker_task",
            source_id="wt_empty",
        )
    )
    assert rejected.status == "rejected"
    assert "content" in rejected.error


def test_hermes_command_surface_policy_prefers_native_gateway():
    policy = HermesCommandSurfacePolicy.default()

    assert policy.command_mode == "hermes_native"
    assert policy.accepts_mention_trigger is True
    assert policy.accepts_slash_command is False
    assert policy.requires_custom_interaction_endpoint is False
    assert policy.requires_custom_queue_db is False
    assert "Hermes Gateway" in policy.describe()
    assert "queue.db" not in policy.describe()


def test_live_discord_projection_sink_uses_env_token_and_injected_http_client():
    calls = []

    def http_post(url, *, headers, json_body, timeout_seconds):  # noqa: ANN001
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"status_code": 200, "json": {"id": "discord-msg-1"}}

    sink = LiveDiscordProjectionSink(
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        http_post=http_post,
        api_base_url="https://discord.test/api/v10",
        timeout_seconds=9,
    )
    event = DiscordProjectionEvent(
        event_id="proj_live",
        meeting_run_id="mr_live",
        bot_role="tech_lead",
        target_channel_id="channel-1",
        target_thread_id="thread-1",
        content="hello @everyone token=SECRET_VALUE",
        source="meeting_run",
        source_id="mr_live",
    )

    result = sink.publish(event)

    assert result.status == "published"
    assert result.discord_message_id == "discord-msg-1"
    assert calls == [
        {
            "url": "https://discord.test/api/v10/channels/thread-1/messages",
            "headers": {
                "Authorization": "Bot token-from-env",
                "Content-Type": "application/json",
            },
            "json_body": {
                "content": "hello @\u000beveryone token=[redacted]",
                "allowed_mentions": {"parse": []},
            },
            "timeout_seconds": 9,
        }
    ]


def test_default_discord_http_post_sends_discord_compatible_user_agent(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def read(self):
            return b'{"id":"discord-msg-ua"}'

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "src.runtime_architecture_v2.projection.urllib.request.urlopen",
        fake_urlopen,
    )

    response = _default_discord_http_post(
        "https://discord.com/api/v10/channels/channel-1/messages",
        headers={
            "Authorization": "Bot token-from-env",
            "Content-Type": "application/json",
        },
        json_body={"content": "hello"},
        timeout_seconds=7,
    )

    assert response["status_code"] == 200
    assert captured["timeout"] == 7
    assert captured["headers"]["User-agent"].startswith("DiscordBot ")
    assert "AI_Agent" in captured["headers"]["User-agent"]


def test_live_discord_projection_sink_fails_closed_without_token_or_on_http_error():
    event = DiscordProjectionEvent(
        event_id="proj_live_fail",
        meeting_run_id="mr_live_fail",
        bot_role="tech_lead",
        target_channel_id="channel-1",
        content="hello",
        source="meeting_run",
        source_id="mr_live_fail",
    )

    missing = LiveDiscordProjectionSink(env={}, http_post=lambda *args, **kwargs: None)
    missing_result = missing.publish(event)

    assert missing_result.status == "blocked"
    assert missing_result.error == "missing_discord_bot_token"

    failed = LiveDiscordProjectionSink(
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        http_post=lambda *args, **kwargs: {"status_code": 429, "text": "rate limited"},
    ).publish(event)

    assert failed.status == "failed"
    assert failed.error == "discord_http_429"
    assert failed.discord_message_id == ""


def test_live_discord_projection_sink_fails_closed_on_http_exception_or_bad_status():
    event = DiscordProjectionEvent(
        event_id="proj_live_exception",
        meeting_run_id="mr_live_exception",
        bot_role="tech_lead",
        target_channel_id="channel-1",
        content="hello",
        source="meeting_run",
        source_id="mr_live_exception",
    )

    def raising_http_post(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("network exploded with token-from-env")

    raised = LiveDiscordProjectionSink(
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        http_post=raising_http_post,
    ).publish(event)

    assert raised.status == "failed"
    assert raised.error == "discord_http_exception"
    assert "token" not in raised.error

    malformed = LiveDiscordProjectionSink(
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        http_post=lambda *args, **kwargs: {"status_code": "not-an-int"},
    ).publish(event)

    assert malformed.status == "failed"
    assert malformed.error == "discord_http_invalid_status"


def test_live_discord_projection_sink_respects_explicit_empty_env(monkeypatch):
    monkeypatch.setitem(os.environ, "DISCORD_BOT_TOKEN", "real-process-token")
    calls = []
    event = DiscordProjectionEvent(
        event_id="proj_env_guard",
        meeting_run_id="mr_env_guard",
        bot_role="tech_lead",
        target_channel_id="channel-1",
        content="hello",
        source="meeting_run",
        source_id="mr_env_guard",
    )

    result = LiveDiscordProjectionSink(
        env={},
        http_post=lambda *args, **kwargs: calls.append((args, kwargs)),
    ).publish(event)

    assert result.status == "blocked"
    assert result.error == "missing_discord_bot_token"
    assert calls == []


def test_live_discord_projection_sink_fails_closed_on_malformed_http_response():
    event = DiscordProjectionEvent(
        event_id="proj_malformed_response",
        meeting_run_id="mr_malformed_response",
        bot_role="tech_lead",
        target_channel_id="channel-1",
        content="hello",
        source="meeting_run",
        source_id="mr_malformed_response",
    )

    result = LiveDiscordProjectionSink(
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        http_post=lambda *args, **kwargs: None,
    ).publish(event)

    assert result.status == "failed"
    assert result.error == "discord_http_invalid_response"
