from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import src.runtime_architecture_v2.multi_bot as multi_bot_module
from src.runtime_architecture_v2.multi_bot import (
    BOT_PERSONAS,
    BotMessage,
    MeetingRound,
    MultiBotSession,
    _build_final_report,
    _discord_env_for_profile,
    _phase33_order_roles,
    build_phase14_pilot_request,
    route_bot_projection,
    run_meeting_phase,
    run_phase14_multi_bot_pilot,
)
from src.runtime_architecture_v2.pilot import Phase13PilotModeError
from src.runtime_architecture_v2.projection import (
    DiscordLiveBoundaryPolicy,
    LiveDiscordThreadManager,
    SharedMeetingThreadProjectionPolicy,
)
from src.runtime_architecture_v2.schemas import (
    MeetingRunState,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import OpenCodeGoRunResult

# ── Schema Tests ────────────────────────────────────────────────────────


def test_phase33_order_roles_keeps_chair_first_and_quality_last():
    roles = (
        "content_lead",
        "ceo_coordinator",
        "validation_audit",
        "marketing_lead",
    )

    assert _phase33_order_roles(roles) == (
        "ceo_coordinator",
        "content_lead",
        "marketing_lead",
        "validation_audit",
    )


def test_bot_message_serialization_round_trips():
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="콘텐츠 아이디어 제안입니다.",
        mentions=("marketing_lead",),
        visible_on_discord=True,
    )
    data = msg.to_dict()
    restored = BotMessage.from_dict(data)

    assert restored.bot_role == msg.bot_role
    assert restored.meeting_run_id == msg.meeting_run_id
    assert restored.round == msg.round
    assert restored.msg_type == msg.msg_type
    assert restored.content == msg.content
    assert restored.mentions == msg.mentions
    assert restored.visible_on_discord == msg.visible_on_discord


def test_meeting_round_holds_multiple_bot_messages():
    msgs = tuple(
        BotMessage(
            bot_role=role,
            meeting_run_id="mr_test",
            round=1,
            msg_type="opinion",
            content=f"[{role}] 의견입니다.",
        )
        for role in ("content_lead", "marketing_lead", "quality_lead")
    )
    round_data = MeetingRound(round_number=1, phase="opinions", messages=msgs)

    assert len(round_data.messages) == 3
    assert round_data.messages[0].bot_role == "content_lead"
    assert round_data.messages[2].bot_role == "quality_lead"

    restored = MeetingRound.from_dict(round_data.to_dict())
    assert len(restored.messages) == 3


def test_multi_bot_session_consensus_state_tracking():
    msgs = (
        BotMessage(
            bot_role="content_lead",
            meeting_run_id="mr_test",
            round=1,
            msg_type="opinion",
            content="의견",
        ),
    )
    rounds = (MeetingRound(round_number=1, phase="opinions", messages=msgs),)
    session = MultiBotSession(
        meeting_run_id="mr_test",
        participants=("content_lead", "marketing_lead"),
        rounds=rounds,
        consensus_reached=True,
        escalation_required=False,
        consensus_summary="합의 완료",
    )

    assert session.consensus_reached is True
    assert session.escalation_required is False
    assert "합의 완료" in session.consensus_summary


# ── Meeting Phase Tests ─────────────────────────────────────────────────


def test_meeting_phase_produces_two_rounds(tmp_path: Path):
    from src.runtime_architecture_v2.pilot import (
        build_phase13_pilot_request,
        create_phase13_meeting_run,
    )

    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())
    session = run_meeting_phase(
        run,
        participants=("content_lead", "marketing_lead", "quality_lead"),
        rounds=2,
        live_bot_roles=(),
        fake_bot_roles=("content_lead", "marketing_lead", "quality_lead"),
    )

    assert len(session.rounds) == 2
    assert session.rounds[0].phase == "opinions"
    assert session.rounds[1].phase == "rebuttals"
    assert len(session.rounds[0].messages) == 3
    assert session.consensus_reached is True


def test_meeting_phase_with_one_live_bot(tmp_path: Path):
    from src.runtime_architecture_v2.pilot import (
        build_phase13_pilot_request,
        create_phase13_meeting_run,
    )

    calls: list[list[str]] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(command)
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout="라이브 콘텐츠 팀장 의견입니다.",
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())
    session = run_meeting_phase(
        run,
        participants=("content_lead", "marketing_lead", "quality_lead"),
        rounds=2,
        live_bot_roles=("content_lead",),
        fake_bot_roles=("marketing_lead", "quality_lead"),
        command_runner=command_runner,
        workdir=str(tmp_path),
    )

    assert len(session.rounds) == 2
    assert len(calls) >= 2  # opinion + rebuttal for live bot


def test_meeting_phase_round2_prompt_uses_round1_transcript_and_quality_language(tmp_path: Path):
    from src.runtime_architecture_v2.pilot import (
        build_phase13_pilot_request,
        create_phase13_meeting_run,
    )

    prompts: list[str] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        prompt = command[command.index("--prompt") + 1]
        prompts.append(prompt)
        if "2라운드" in prompt and "품질관리 팀장" in prompt:
            content = "콘텐츠 팀장 의견에 동의하되, 법무 답변이 비어 보이면 실패 상태로 표시해야 합니다."
        elif "2라운드" in prompt:
            content = "1라운드 의견을 바탕으로 보완 조건을 제안합니다."
        else:
            content = "1라운드 초기 의견입니다."
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps({"content": content}, ensure_ascii=False),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())
    session = run_meeting_phase(
        run,
        participants=("content_lead", "quality_lead"),
        rounds=2,
        live_bot_roles=("content_lead", "quality_lead"),
        fake_bot_roles=(),
        command_runner=command_runner,
        workdir=str(tmp_path),
    )

    round2_prompts = [prompt for prompt in prompts if "2라운드" in prompt]
    assert round2_prompts
    assert all("1라운드 회의록" in prompt for prompt in round2_prompts)
    assert all("반복하지 마세요" in prompt for prompt in round2_prompts)
    assert all("동의하는 다른 팀장 의견" in prompt for prompt in round2_prompts)
    assert all("보완/반박할 다른 팀장 의견" in prompt for prompt in round2_prompts)
    assert all("최종 합의에 넣을 조건" in prompt for prompt in round2_prompts)
    assert any("콘텐츠 팀장:" in prompt for prompt in round2_prompts)
    assert any("품질관리 팀장:" in prompt for prompt in round2_prompts)

    quality_round2_prompt = next(
        prompt for prompt in round2_prompts if "당신은 AI 가상 엔터테인먼트 회사의 '품질관리 팀장" in prompt
    )
    assert "사용자가 이해할 수 있는 품질 기준" in quality_round2_prompt
    assert "worker_execution_failed → 실패 상태로 표시" in quality_round2_prompt
    assert "placeholder output → 임시/빈 응답" in quality_round2_prompt
    assert "회귀 테스트 → 재발 방지 검증" in quality_round2_prompt
    assert "evidence artifact → 검증 기록" in quality_round2_prompt

    quality_round1 = next(
        msg.content for msg in session.rounds[0].messages if msg.bot_role == "quality_lead"
    )
    quality_round2 = next(
        msg.content for msg in session.rounds[1].messages if msg.bot_role == "quality_lead"
    )
    assert quality_round1 != quality_round2


def test_meeting_phase_live_bot_uses_trigger_text_in_hermes_prompt(
    tmp_path: Path,
    monkeypatch,
):
    from src.runtime_architecture_v2.pilot import create_phase13_meeting_run

    prompts: list[str] = []

    class CapturingRunner:
        def __init__(self, **kwargs):
            pass

        def dispatch(self, task):
            prompts.append(task.hermes_refs["prompt"])
            return task.__class__.from_dict({**task.to_dict(), "state": "running"})

        def collect(self, task):
            Path(task.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(task.output_path).write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "content": "정보 쇼츠 안건을 반영한 콘텐츠 의견입니다.",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return task.__class__.from_dict(
                {**task.to_dict(), "state": WorkerTaskState.SUCCEEDED}
            )

    monkeypatch.setattr(multi_bot_module, "OpenCodeGoWorkerRunner", CapturingRunner)
    run = create_phase13_meeting_run(
        tmp_path,
        {
            "pilot_id": "phase14_test",
            "trigger_text": "정보 쇼츠 유튜브 콘텐츠 회의",
            "user_id": "u",
            "channel_id": "c",
            "thread_id": "",
        },
    )

    session = run_meeting_phase(
        run,
        participants=("content_lead",),
        rounds=1,
        live_bot_roles=("content_lead",),
        fake_bot_roles=(),
        command_runner=None,
        workdir=str(tmp_path),
    )

    assert "정보 쇼츠 유튜브 콘텐츠 회의" in prompts[0]
    assert session.rounds[0].messages[0].content == "정보 쇼츠 안건을 반영한 콘텐츠 의견입니다."


def test_meeting_phase_rejects_no_participants(tmp_path: Path):
    from src.runtime_architecture_v2.pilot import (
        build_phase13_pilot_request,
        create_phase13_meeting_run,
    )

    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())
    try:
        run_meeting_phase(run, participants=(), rounds=1)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("meeting phase must reject empty participants")


# ── Projection Tests ────────────────────────────────────────────────────


def test_bot_persona_covers_all_roles():
    expected_roles = {
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "business_support_lead",
        "validation_audit",
        "quality_lead",
    }
    assert set(BOT_PERSONAS.keys()) >= expected_roles


def test_route_bot_projection_selects_correct_persona():
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="콘텐츠 제안입니다.",
    )
    result = route_bot_projection(msg)

    assert result.status == "published"
    assert "fake-discord-message" in result.discord_message_id


def test_route_bot_projection_sanitizes_secrets():
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="api_key=LEAK123456 and @everyone should be safe",
    )
    result = route_bot_projection(msg)

    assert result.status == "published"
    assert "LEAK123456" not in result.discord_message_id


def test_route_bot_projection_truncates_without_breaking_code_block():
    content = "# 보고\n```text\n" + "x" * 2100

    truncated = multi_bot_module._truncate_discord_projection_content(content)

    assert len(truncated) <= 1900
    assert truncated.endswith("\n```")
    assert truncated.count("```") == 2


def test_route_bot_projection_respects_visible_flag():
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="내부 전용 메시지",
        visible_on_discord=False,
    )
    # visible_on_discord=False messages are filtered before route_bot_projection
    # This test just verifies the flag is preserved
    assert msg.visible_on_discord is False


def test_route_bot_projection_live_discord_without_token(tmp_path: Path):
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="테스트 메시지",
    )
    result = route_bot_projection(
        msg, live_discord=True, target_channel_id="test-channel", env={}
    )
    assert result.status == "blocked"


def test_phase14_profile_env_loader_reads_only_discord_token(
    tmp_path: Path, monkeypatch
):
    fake_home = tmp_path / "home"
    profile_dir = fake_home / ".hermes" / "profiles" / "aicompanycontent"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "DISCORD_BOT_TOKEN=bot-token-123\nOPENCODE_GO_API_KEY=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.runtime_architecture_v2.multi_bot.Path.home", lambda: fake_home
    )

    env = _discord_env_for_profile("aicompanycontent")

    assert env == {"DISCORD_BOT_TOKEN": "bot-token-123"}


def test_route_bot_projection_live_discord_unknown_channel_blocks_before_http():
    calls = []
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="테스트 메시지",
    )

    result = route_bot_projection(
        msg,
        live_discord=True,
        target_channel_id="unknown-channel",
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        discord_http_post=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result.status == "blocked"
    assert result.error == "channel_not_allowed"
    assert calls == []


def test_route_bot_projection_profile_home_routes_to_verified_channel():
    calls = []
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="테스트 메시지",
    )

    result = route_bot_projection(
        msg,
        live_discord=True,
        target_channel_id="profile-home",
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        discord_http_post=lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or {"status_code": 200, "json": {"id": "posted-1"}}
        ),
    )

    assert result.status == "published"
    assert result.discord_message_id == "posted-1"
    assert calls
    args, _kwargs = calls[0]
    assert args[0].endswith("/channels/1505927982722580500/messages")


def test_route_bot_projection_profile_home_uses_default_live_http_client(monkeypatch):
    calls = []

    def fake_default_http_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status_code": 200, "json": {"id": "posted-default"}}

    monkeypatch.setattr(
        "src.runtime_architecture_v2.multi_bot._default_discord_http_post",
        fake_default_http_post,
    )
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="테스트 메시지",
    )

    result = route_bot_projection(
        msg,
        live_discord=True,
        target_channel_id="profile-home",
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
    )

    assert result.status == "published"
    assert result.discord_message_id == "posted-default"
    assert calls


def test_route_bot_projection_env_none_loads_only_profile_discord_token(
    tmp_path: Path,
    monkeypatch,
):
    fake_home = tmp_path / "home"
    profile_dir = fake_home / ".hermes" / "profiles" / "aicompanycontent"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "DISCORD_BOT_TOKEN=bot-token-123\nOPENCODE_GO_API_KEY=must-not-leak\n",
        encoding="utf-8",
    )
    calls = []

    def fake_default_http_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status_code": 200, "json": {"id": "posted-profile-env"}}

    monkeypatch.setattr(
        "src.runtime_architecture_v2.multi_bot.Path.home", lambda: fake_home
    )
    monkeypatch.setattr(
        "src.runtime_architecture_v2.multi_bot._default_discord_http_post",
        fake_default_http_post,
    )
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_test",
        round=1,
        msg_type="opinion",
        content="테스트 메시지",
    )

    result = route_bot_projection(
        msg,
        live_discord=True,
        target_channel_id="profile-home",
        env=None,
    )

    assert result.status == "published"
    assert result.discord_message_id == "posted-profile-env"
    assert calls
    _args, kwargs = calls[0]
    assert kwargs["headers"]["Authorization"] == "Bot bot-token-123"
    assert "must-not-leak" not in str(kwargs)


def test_live_discord_thread_manager_creates_public_thread_without_ping_parse():
    calls = []

    def fake_http_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status_code": 201, "json": {"id": "thread-123"}}

    manager = LiveDiscordThreadManager(
        env={"DISCORD_BOT_TOKEN": "ceo-token"},
        http_post=fake_http_post,
    )

    result = manager.create_meeting_thread(
        parent_channel_id="1505600167221526621",
        name="LG 트윈스 갤러리 회의",
    )

    assert result.status == "created"
    assert result.thread_id == "thread-123"
    assert calls
    args, kwargs = calls[0]
    assert args[0].endswith("/channels/1505600167221526621/threads")
    assert kwargs["json_body"]["name"] == "LG 트윈스 갤러리 회의"
    assert kwargs["json_body"]["type"] == 11
    assert kwargs["json_body"]["invitable"] is False
    assert kwargs["headers"]["Authorization"] == "Bot ceo-token"


def test_route_bot_projection_to_shared_meeting_thread_uses_role_profile_token():
    calls = []
    policy = SharedMeetingThreadProjectionPolicy(
        boundary_policy=DiscordLiveBoundaryPolicy.current_verified(),
        parent_channel_id="1505600167221526621",
        thread_id="thread-123",
    )
    msg = BotMessage(
        bot_role="content_lead",
        meeting_run_id="mr_shared_thread",
        round=1,
        msg_type="opinion",
        content="콘텐츠팀장 의견입니다.",
    )

    result = route_bot_projection(
        msg,
        live_discord=True,
        target_channel_id="1505600167221526621",
        target_thread_id="thread-123",
        env={"DISCORD_BOT_TOKEN": "content-token"},
        shared_thread_policy=policy,
        discord_http_post=lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or {"status_code": 200, "json": {"id": "message-1"}}
        ),
    )

    assert result.status == "published"
    assert result.discord_message_id == "message-1"
    assert calls
    args, kwargs = calls[0]
    assert args[0].endswith("/channels/thread-123/messages")
    assert kwargs["headers"]["Authorization"] == "Bot content-token"
    assert "[콘텐츠 팀장]" in kwargs["json_body"]["content"]


def test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages(
    tmp_path: Path,
):
    calls = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "status": "succeeded",
                    "content": "사용자 질문 스타일에 맞춘 정상 한국어 회의 발언입니다.",
                    "attempted_models": ["test-model"],
                },
                ensure_ascii=True,
            ),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    def fake_http_post(*args, **kwargs):
        calls.append((args, kwargs))
        url = args[0]
        if url.endswith("/channels/1505600167221526621/threads"):
            return {"status_code": 201, "json": {"id": "thread-phase14"}}
        if url.endswith("/channels/thread-phase14/messages"):
            return {"status_code": 200, "json": {"id": f"msg-{len(calls)}"}}
        return {"status_code": 404, "json": {}}

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={"DISCORD_BOT_TOKEN": "test-token"},
        target_channel_id="1505600167221526621",
        thread_name="테스트 팀장 회의",
        discord_http_post=fake_http_post,
        live_bot_roles_override=("content_lead",),
        fake_bot_roles_override=(
            "ceo_coordinator",
            "art_lead",
            "tech_lead",
            "marketing_lead",
            "validation_audit",
        ),
    )

    assert result.ok is True
    assert result.meeting_thread_status == "created"
    assert result.meeting_thread_id == "thread-phase14"
    assert result.projection_messages_posted == 12
    assert len(result.projection_results) == 12
    urls = [args[0] for args, _kwargs in calls]
    assert urls[0].endswith("/channels/1505600167221526621/threads")
    assert all(url.endswith("/channels/thread-phase14/messages") for url in urls[1:])
    message_bodies = [kwargs["json_body"]["content"] for _args, kwargs in calls[1:]]
    last_body = message_bodies[-1]
    assert len(message_bodies) == 12
    assert "[검증 팀장]" in last_body
    assert all("# 📋" not in body for body in message_bodies)
    assert all("## 🎯 결론" not in body for body in message_bodies)
    assert all("## ✅ 합의안" not in body for body in message_bodies)
    assert all("## 🚀 다음 액션" not in body for body in message_bodies)
    assert all("회의 체크포인트" not in body for body in message_bodies)
    assert any("사용자 질문 스타일에 맞춘 정상 한국어 회의 발언" in body for body in message_bodies)
    assert all('"status": "succeeded"' not in body for body in message_bodies)
    assert all("test-model" not in body for body in message_bodies)
    assert all("\\u" not in body for body in message_bodies)


def test_phase33_live_projection_order_is_chair_led_even_when_ceo_is_fake(
    tmp_path: Path,
):
    calls = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps(
                {"content": "Phase33 안건을 반영한 콘텐츠팀장 live 발언입니다."},
                ensure_ascii=False,
            ),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    def fake_http_post(*args, **kwargs):
        calls.append((args, kwargs))
        url = args[0]
        if url.endswith("/channels/1505600167221526621/threads"):
            return {"status_code": 201, "json": {"id": "thread-phase33"}}
        if url.endswith("/channels/thread-phase33/messages"):
            return {"status_code": 200, "json": {"id": f"msg-{len(calls)}"}}
        return {"status_code": 404, "json": {}}

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={"DISCORD_BOT_TOKEN": "test-token"},
        target_channel_id="1505600167221526621",
        thread_name="Phase33 회의 진행 품질 검증",
        trigger_text="Phase33 회의 진행 품질 검증 회의",
        discord_http_post=fake_http_post,
        live_bot_roles_override=("content_lead",),
        fake_bot_roles_override=(
            "ceo_coordinator",
            "art_lead",
            "tech_lead",
            "marketing_lead",
            "validation_audit",
        ),
    )

    assert result.ok is True
    message_bodies = [kwargs["json_body"]["content"] for _args, kwargs in calls[1:]]
    assert len(message_bodies) == 12
    assert "[대표]" in message_bodies[0]
    assert "[콘텐츠 팀장]" in message_bodies[1]
    assert "[대표]" in message_bodies[6]
    assert "[검증 팀장]" in message_bodies[11]
    assert "신규 버추얼 아이돌 그룹의 데뷔 컨셉" not in "\n".join(message_bodies)


def test_phase14_live_discord_thread_creation_failure_fails_closed(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"ok": true}',
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={},
        target_channel_id="1505600167221526621",
        discord_http_post=lambda *args, **kwargs: {
            "status_code": 201,
            "json": {"id": "should-not-call"},
        },
    )

    assert result.ok is False
    assert result.error == "live_discord_thread_blocked"
    assert result.meeting_thread_status == "blocked"
    assert result.meeting_thread_error == "missing_discord_bot_token"
    assert result.projection_results == ()


# ── Pilot Dry-run Tests ─────────────────────────────────────────────────


def test_phase14_dry_run_produces_multi_bot_output(tmp_path: Path):
    result = run_phase14_multi_bot_pilot(root=tmp_path, mode="dry-run")

    assert result.ok is True
    assert result.mode == "dry-run"
    assert result.live_worker_count == 0
    assert result.fake_worker_count == 3 + len(result.internal_specialist_roles)
    assert result.meeting_run.state == MeetingRunState.COMPLETED
    assert len(result.bot_participants) == 3
    assert "content_lead" in result.bot_participants
    assert result.rounds_completed == 2
    assert result.projection_messages_posted >= 4  # 3 opinions + at least 1 rebuttal


def test_phase14_dry_run_never_calls_injected_command_runner(tmp_path: Path):
    calls: list[list[str]] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(command)
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"should_not":"run"}',
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        command_runner=command_runner,
    )

    assert result.ok is True
    assert calls == []


def test_phase14_dry_run_rejects_live_discord_before_sink(tmp_path: Path):
    try:
        run_phase14_multi_bot_pilot(
            root=tmp_path,
            mode="dry-run",
            live_discord=True,
            env={"DISCORD_BOT_TOKEN": "would-not-be-used"},
        )
    except Phase13PilotModeError as exc:
        assert exc.code == "invalid_live_discord_mode"
    else:  # pragma: no cover
        raise AssertionError("dry-run must reject live Discord projection")


def test_phase14_live_worker_mode_rejects_more_than_configured_workers(tmp_path: Path):
    try:
        run_phase14_multi_bot_pilot(
            root=tmp_path, mode="live-worker", max_live_workers=4
        )
    except Phase13PilotModeError as exc:
        assert exc.code == "invalid_live_worker_count"
    else:  # pragma: no cover
        raise AssertionError("must reject more live workers than configured roles")


def test_phase14_live_worker_mode_with_injected_runner(tmp_path: Path):
    calls: list[list[str]] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(command)
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"idea":"multi-bot virtual idol debut concept"}',
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=2,
        command_runner=command_runner,
    )

    assert result.ok is True
    assert result.live_worker_count == 2
    assert result.fake_worker_count == 1 + len(result.internal_specialist_roles)
    assert len(calls) >= 1
    assert {call[2] for call in calls if len(call) >= 3 and call[1] == "--model"} == {
        "qwen3.7-plus"
    }
    policies = {task.role: task.model_policy for task in result.worker_tasks}
    assert policies["content_lead"]["role_id"] == "content-director"
    assert policies["content_lead"]["projection_profile"] == "aicompanycontent"
    assert policies["marketing_lead"]["role_id"] == "marketing-lead"
    assert policies["marketing_lead"]["projection_profile"] == "aicompanymarketing"
    assert policies["quality_lead"]["role_id"] == "validator"
    assert policies["quality_lead"]["preferred"] == "glm-5.1"
    assert policies["quality_lead"]["projection_profile"] == "aicompanyquality"
    assert result.meeting_run.state == MeetingRunState.COMPLETED


def test_phase14_live_worker_failure_returns_structured_result(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        raise RuntimeError("simulated worker crash")

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=2,
        command_runner=command_runner,
    )

    assert result.ok is False
    assert result.meeting_run.state == MeetingRunState.FAILED


def test_phase14_live_discord_blocked_marks_result_not_ok(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"ok": true}',
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={},
    )

    assert result.ok is False
    assert result.error == "live_discord_thread_blocked"
    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.meeting_thread_error == "channel_not_allowed"
    assert result.projection_results == ()


# ── Pilot Request Fixture Tests ─────────────────────────────────────────


def test_phase14_pilot_request_is_stable():
    request = build_phase14_pilot_request()

    assert request["pilot_id"] == "phase14_multi_bot_operational_pilot"
    assert request["trigger_text"]
    assert request["live_bot_roles"] == ["content_lead", "marketing_lead"]
    assert request["fake_bot_roles"] == ["quality_lead"]
    assert "openclaw" not in json.dumps(request, ensure_ascii=False).lower()


def test_phase14_gateway_trigger_text_overrides_static_pilot_request(tmp_path: Path):
    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        trigger_text="정보 쇼츠 유튜브 콘텐츠 회의",
    )

    assert result.meeting_run.trigger["text"] == "정보 쇼츠 유튜브 콘텐츠 회의"


def test_phase14_selects_internal_specialists_without_discord_participation(tmp_path: Path):
    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        trigger_text="야구 정보 쇼츠 자동화 파이프라인과 유튜브 성과 분석 회의",
        live_bot_roles_override=(
            "ceo_coordinator",
            "content_lead",
            "art_lead",
            "tech_lead",
            "marketing_lead",
            "validation_audit",
        ),
        fake_bot_roles_override=(),
    )

    assert result.bot_participants == (
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "validation_audit",
    )
    assert set(result.internal_specialist_roles) >= {
        "data-analyst",
        "backend-engineer",
        "video-editor",
    }
    assert set(result.internal_specialist_roles).isdisjoint(result.bot_participants)
    assert set(result.internal_specialist_roles).issubset(
        {task.role for task in result.worker_tasks}
    )


def test_phase14_final_report_summarizes_evidence_and_fallbacks(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        model = command[command.index("--model") + 1]
        prompt = command[command.index("--prompt") + 1] if "--prompt" in command else ""
        if model == "qwen3.7-plus":
            return OpenCodeGoRunResult(
                exit_code=1,
                stdout="",
                stderr="model temporarily unavailable",
                timeout_occurred=False,
            )
        if "data-analyst" in prompt:
            content = "데이터 분석가는 시청 지속률과 전환 지표를 기준으로 성과 판단을 제안합니다."
        elif "backend-engineer" in prompt:
            content = "백엔드 엔지니어는 수집 API와 큐 기반 자동화 경계를 우선 확정해야 한다고 봅니다."
        elif "quality-assurance" in prompt:
            content = "품질 담당자는 최종 보고의 섹션 순서와 evidence 분리를 회귀 테스트로 고정해야 한다고 봅니다."
        elif "ui-ux-designer" in prompt:
            content = "UI/UX 디자이너는 Discord에서는 bullet, 로컬 artifact에서는 표를 유지해야 한다고 제안합니다."
        else:
            content = f"{model} team output"
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps({"content": content}),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        trigger_text="야구 데이터 자동화 품질 UI UX 회의",
        live_bot_roles_override=("content_lead",),
        fake_bot_roles_override=("quality_lead",),
    )

    assert result.ok is True
    assert result.fallback_events
    assert result.final_report == ""  # no longer auto-generated

    report = _build_final_report(
        run=result.meeting_run,
        session=result.session,
        worker_tasks=result.worker_tasks,
        validation_verdicts=(),
        internal_specialist_roles=result.internal_specialist_roles,
        fallback_events=result.fallback_events,
    )
    assert report.index("## 🎯 결론") < report.index("## ✅ 합의안")
    assert report.index("## ✅ 합의안") < report.index("## 🚀 다음 액션")
    assert report.index("## 🚀 다음 액션") < report.index("## 👥 팀장 핵심 의견")
    assert report.index("## 👥 팀장 핵심 의견") < report.index("## 🧑‍💻 Specialist 투입")
    assert report.index("## 🧑‍💻 Specialist 투입") < report.index("## 🔍 검증 상세 / 모델 Evidence")
    assert "**상태:** ✅ 완료" in report
    assert "| 팀장 | 핵심 포인트 |" in report
    assert "| specialist | 결과 한줄 요약 |" in report
    assert "data-analyst" in report
    assert "데이터 분석가는" in report
    assert "백엔드 엔지니어는" in report
    assert "quality-assurance" in report
    assert "품질 담당자는" in report
    assert "ui-ux-designer" in report
    assert "UI/UX 디자이너는" in report
    assert "## 🔍 검증 상세 / 모델 Evidence" in report
    assert "fallback_used=true" in report
    assert "qwen3.7-plus -> deepseek-v4-pro" in report
    assert "검증 팀장 관점에서" not in report
    assert report.index("| 팀장 | 핵심 포인트 |") < report.index("| specialist | 결과 한줄 요약 |")
    agreement_section = report.split("## ✅ 합의안", 1)[1].split("## 🚀 다음 액션", 1)[0]
    assert "기준으로 확정한다" not in agreement_section
    assert agreement_section.count("안건은") <= 1
    action_section = report.split("## 🚀 다음 액션", 1)[1].split("## ⚠️", 1)[0]
    assert "회귀 테스트" in action_section
    assert "specialist 고유 output" in action_section
    assert "evidence 분리" in action_section
    assert "최종 보고 마지막 메시지의 결론/합의안/다음 액션을 우선 확인" not in action_section


def test_phase14_final_report_marks_placeholder_specialist_output_failed(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        prompt = command[command.index("--prompt") + 1] if "--prompt" in command else ""
        if "legal-reviewer" in prompt:
            content = "legal-reviewer specialist output"
        else:
            content = "회의 발언입니다."
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps({"content": content, "attempted_models": ["glm-5.1"]}),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        trigger_text="법무 계약 검토 회의",
        live_bot_roles_override=("content_lead",),
        fake_bot_roles_override=("quality_lead",),
    )

    assert result.ok is True
    assert result.final_report == ""  # no longer auto-generated

    report = _build_final_report(
        run=result.meeting_run,
        session=result.session,
        worker_tasks=result.worker_tasks,
        validation_verdicts=(),
        internal_specialist_roles=result.internal_specialist_roles,
        fallback_events=result.fallback_events,
    )
    assert "legal-reviewer" in report
    assert "legal-reviewer specialist output" not in report
    assert "worker_execution_failed" in report
    agreement_section = report.split("## ✅ 합의안", 1)[1].split("## 🚀 다음 액션", 1)[0]
    conclusion_section = report.split("## 🎯 결론", 1)[1].split("## ✅ 합의안", 1)[0].strip()
    assert "최종 합의는 `" not in agreement_section
    assert conclusion_section not in agreement_section
    assert "legal-reviewer placeholder" in agreement_section
    assert "worker_execution_failed" in agreement_section
    action_section = report.split("## 🚀 다음 액션", 1)[1].split("## ⚠️", 1)[0]
    assert "legal-reviewer placeholder" in action_section
    assert "worker_execution_failed" in action_section
    assert "evidence 분리와 specialist 고유 output을 회귀 테스트로 고정한다" not in action_section
    legal_line = next(
        line for line in report.splitlines() if line.startswith("legal-reviewer")
    )
    assert "⚠️" in legal_line


# ── CLI Tests ───────────────────────────────────────────────────────────


def test_phase14_cli_dry_run_outputs_machine_readable_json(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase14_multi_bot_pilot.py",
            "--mode",
            "dry-run",
            "--root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["pilot_id"] == "phase14_multi_bot_operational_pilot"
    assert payload["mode"] == "dry-run"
    assert payload["live_worker_count"] == 0
    assert payload["fake_worker_count"] == 3 + len(payload["internal_specialist_roles"])
    assert payload["ok"] is True
    assert payload["rounds_completed"] == 2
    assert len(payload["bot_participants"]) == 3


def test_phase14_cli_rejects_invalid_mode(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase14_multi_bot_pilot.py",
            "--mode",
            "live-worker",
            "--max-live-workers",
            "5",
            "--root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_live_worker_count"


# ── Phase 32 Stage 2 Tests ──────────────────────────────────────────────


def test_phase32_default_meeting_does_not_auto_generate_final_report_v2(
    tmp_path: Path,
):
    result = run_phase14_multi_bot_pilot(root=tmp_path, mode="dry-run")
    assert result.ok is True
    assert result.final_report == ""

    run_dir = (
        Path(tmp_path)
        / "runtime"
        / "meeting_runs"
        / result.meeting_run.meeting_run_id
    )
    report_path = run_dir / "final_report_v2.md"
    assert not report_path.exists(), "final_report_v2.md must not be auto-generated"

    meeting_run_path = run_dir / "meeting_run.json"
    assert meeting_run_path.exists()

    final_report_md = run_dir / "final_report.md"
    assert final_report_md.exists()
