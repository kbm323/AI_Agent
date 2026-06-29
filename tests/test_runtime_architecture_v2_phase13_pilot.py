from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.pilot import (
    Phase13PilotModeError,
    build_phase13_pilot_request,
    build_phase13_route,
    build_phase13_worker_tasks,
    create_phase13_meeting_run,
    run_phase13_pilot,
)
from src.runtime_architecture_v2.schemas import (
    MeetingRunState,
    WorkerTaskRunner,
    WorkerTaskState,
)
from src.runtime_architecture_v2.workers import OpenCodeGoRunResult


def test_phase13_pilot_request_is_stable_and_one_live_worker_by_default():
    request = build_phase13_pilot_request()

    assert request["pilot_id"] == "phase13_live_company_workflow_pilot"
    assert request["trigger_text"]
    assert request["live_worker_roles"] == ["content_lead"]
    assert request["fake_support_roles"] == ["marketing_lead", "quality_lead"]
    assert "openclaw" not in json.dumps(request, ensure_ascii=False).lower()


def test_create_phase13_meeting_run_writes_runtime_artifact(tmp_path: Path):
    request = build_phase13_pilot_request()

    run = create_phase13_meeting_run(tmp_path, request)

    assert run.meeting_run_id.startswith("phase13_live_company_workflow_pilot")
    assert run.state == MeetingRunState.CREATED
    assert run.trigger["text"] == request["trigger_text"]
    meeting_run_path = (
        tmp_path / "runtime" / "meeting_runs" / run.meeting_run_id / "meeting_run.json"
    )
    assert meeting_run_path.exists()
    payload = json.loads(meeting_run_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["pilot_id"] == "phase13_live_company_workflow_pilot"


def test_phase13_route_is_pilot_only_company_role_mapping(tmp_path: Path):
    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())

    route = build_phase13_route(run)

    assert route.primary_role == "content_lead"
    assert route.supporting_roles == ("marketing_lead", "quality_lead")
    assert route.live_worker_roles == ("content_lead",)
    assert route.fake_worker_roles == ("marketing_lead", "quality_lead")
    assert "ceo_coordinator" not in route.live_worker_roles
    assert "personal_assistant" not in route.live_worker_roles


def test_phase13_worker_tasks_include_one_opencode_go_task_by_default(tmp_path: Path):
    run = create_phase13_meeting_run(tmp_path, build_phase13_pilot_request())
    route = build_phase13_route(run)

    tasks = build_phase13_worker_tasks(run, route, tmp_path, live_worker_count=1)

    live_tasks = [task for task in tasks if task.runner == WorkerTaskRunner.OPENCODE_GO]
    fake_tasks = [
        task for task in tasks if task.runner == WorkerTaskRunner.HERMES_WRAPPER
    ]
    assert len(live_tasks) == 1
    assert live_tasks[0].role == "content_lead"
    assert live_tasks[0].model_policy["role_id"] == "content-director"
    assert live_tasks[0].model_policy["provider"] == "opencode-go"
    assert live_tasks[0].model_policy["preferred"] == "qwen3.7-plus"
    assert live_tasks[0].model_policy["projection_profile"] == "aicompanycontent"
    assert len(fake_tasks) == 2
    fake_by_role = {task.role: task.model_policy for task in fake_tasks}
    assert fake_by_role["marketing_lead"]["role_id"] == "marketing-lead"
    assert fake_by_role["quality_lead"]["role_id"] == "validator"
    assert fake_by_role["quality_lead"]["preferred"] == "glm-5.1"
    for task in tasks:
        assert str(tmp_path / "runtime" / "meeting_runs") in task.packet_path
        assert task.worker_task_id.startswith(f"wt_{run.meeting_run_id}_")


def test_phase13_dry_run_never_uses_live_worker_and_writes_report(tmp_path: Path):
    result = run_phase13_pilot(root=tmp_path, mode="dry-run")

    assert result.ok is True
    assert result.mode == "dry-run"
    assert result.live_worker_count == 0
    assert result.fake_worker_count == 3
    assert result.meeting_run.state == MeetingRunState.COMPLETED
    assert Path(result.report_path).exists()
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "# Phase 13 Pilot Report" in report
    assert "raw_worker_outputs" not in report
    assert "@everyone" not in report


def test_phase13_dry_run_never_calls_injected_command_runner(tmp_path: Path):
    calls: list[list[str]] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(command)
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"should_not":"run"}',
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="dry-run",
        command_runner=command_runner,
    )

    assert result.ok is True
    assert calls == []


def test_phase13_live_worker_mode_requires_exactly_one_live_worker(tmp_path: Path):
    try:
        run_phase13_pilot(root=tmp_path, mode="live-worker", max_live_workers=2)
    except Phase13PilotModeError as exc:
        assert exc.code == "invalid_live_worker_count"
    else:  # pragma: no cover
        raise AssertionError("live-worker mode must reject more than one live worker")


def test_phase13_live_worker_mode_uses_injected_runner(tmp_path: Path):
    calls: list[list[str]] = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        calls.append(command)
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"idea":"virtual idol behind-the-scenes short"}',
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
    )

    assert result.ok is True
    assert result.live_worker_count == 1
    assert result.fake_worker_count == 2
    assert len(calls) == 1
    assert calls[0][:5] == [
        "hermes-provider",
        "--provider",
        "opencode-go",
        "--model",
        "qwen3.7-plus",
    ]
    assert result.worker_tasks[0].state == WorkerTaskState.SUCCEEDED


def test_phase13_report_redacts_secret_like_stdout_and_mentions(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout="api_key=LEAK123456\n@everyone raw line\nBearer SHOULD_NOT_LEAK\n",
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
    )

    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "LEAK123456" not in report
    assert "SHOULD_NOT_LEAK" not in report
    assert "@everyone" not in report
    assert "raw line" not in report



def test_phase13_dry_run_rejects_live_discord_before_sink(tmp_path: Path):
    try:
        run_phase13_pilot(
            root=tmp_path,
            mode="dry-run",
            live_discord=True,
            env={"DISCORD_BOT_TOKEN": "would-not-be-used"},
        )
    except Phase13PilotModeError as exc:
        assert exc.code == "invalid_live_discord_mode"
    else:  # pragma: no cover
        raise AssertionError("dry-run must reject live Discord projection")


def test_phase13_live_discord_blocked_without_token_marks_result_not_ok(
    tmp_path: Path,
):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"ok": true}',
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={},
    )

    assert result.ok is False
    assert result.error == "live_discord_publish_blocked"
    assert result.projection_publish_result.status == "blocked"



def test_phase13_live_discord_boundary_blocks_unknown_channel_before_http(
    tmp_path: Path,
):
    calls = []

    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout='{"ok": true}',
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
        live_discord=True,
        env={"DISCORD_BOT_TOKEN": "token-from-env"},
        target_channel_id="not-allowed-channel",
        discord_http_post=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result.ok is False
    assert result.error == "live_discord_publish_blocked"
    assert result.projection_publish_result.status == "blocked"
    assert result.projection_publish_result.error == "channel_not_allowed"
    assert calls == []



def test_phase13_report_summarizes_opencode_event_stream_without_raw_dump(
    tmp_path: Path,
):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=(
                '{"type":"step_start","sessionID":"ses_test"}\n'
                + json.dumps(
                    {
                        "type": "text",
                        "text": "핵심 콘텐츠 아이디어: 팬 투표형 비하인드 쇼츠",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ),
            stderr="",
            timeout_occurred=False,
        )

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
    )

    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "핵심 콘텐츠 아이디어" in report
    assert "step_start" not in report
    assert "sessionID" not in report



def test_phase13_live_worker_failure_returns_structured_result(tmp_path: Path):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        raise RuntimeError("raw provider token should not leak")

    result = run_phase13_pilot(
        root=tmp_path,
        mode="live-worker",
        max_live_workers=1,
        command_runner=command_runner,
    )

    assert result.ok is False
    assert result.meeting_run.state == MeetingRunState.FAILED
    assert result.worker_tasks[0].state == WorkerTaskState.FAILED
    output = json.loads(
        Path(result.worker_tasks[0].output_path).read_text(encoding="utf-8")
    )
    assert output["error"].startswith("legacy_runner_adapter_error")
    assert "raw provider token" not in json.dumps(output)


def test_phase13_cli_dry_run_outputs_machine_readable_json(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase13_company_workflow_pilot.py",
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
    assert payload["pilot_id"] == "phase13_live_company_workflow_pilot"
    assert payload["mode"] == "dry-run"
    assert payload["live_worker_count"] == 0
    assert payload["fake_worker_count"] == 3
    assert payload["ok"] is True
    assert Path(payload["report_path"]).exists()
