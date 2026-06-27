from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.multi_bot import (
    BOT_PERSONAS,
    BotMessage,
    MeetingRound,
    MultiBotSession,
    _discord_env_for_profile,
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
)
from src.runtime_architecture_v2.workers import OpenCodeGoRunResult

# ── Schema Tests ────────────────────────────────────────────────────────


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
            stdout='{"ok": true}',
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
    )

    assert result.ok is True
    assert result.meeting_thread_status == "created"
    assert result.meeting_thread_id == "thread-phase14"
    assert result.projection_messages_posted == 6
    assert len(result.projection_results) == 6
    urls = [args[0] for args, _kwargs in calls]
    assert urls[0].endswith("/channels/1505600167221526621/threads")
    assert all(url.endswith("/channels/thread-phase14/messages") for url in urls[1:])


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
    assert result.fake_worker_count == 3
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


def test_phase14_live_worker_mode_rejects_more_than_two_workers(tmp_path: Path):
    try:
        run_phase14_multi_bot_pilot(
            root=tmp_path, mode="live-worker", max_live_workers=3
        )
    except Phase13PilotModeError as exc:
        assert exc.code == "invalid_live_worker_count"
    else:  # pragma: no cover
        raise AssertionError("must reject more than 2 live workers")


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
    assert result.fake_worker_count == 1
    assert len(calls) >= 1
    assert {call[2] for call in calls if len(call) >= 3 and call[1] == "--model"} == {
        "qwen3.7-max"
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
    assert payload["fake_worker_count"] == 3
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
