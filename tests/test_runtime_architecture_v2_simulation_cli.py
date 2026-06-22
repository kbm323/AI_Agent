from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.simulation_cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_simulation_cli_runs_full_fake_e2e_and_prints_json_report(
    tmp_path,
    capsys,
):
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "--meeting-run-id",
            "mr_cli_success",
            "--trigger-text",
            "콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘",
            "--user-id",
            "user-1",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
            "--guild-id",
            "guild-1",
            "--hermes-session-id",
            "sess-1",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["mode"] == "simulation"
    assert report["meeting_run_id"] == "mr_cli_success"
    assert report["state"] == "completed"
    assert report["route_type"] == "mixed_request"
    assert report["worker_task_count"] == 3
    assert report["validation_verdicts"] == ["pass", "pass"]
    assert report["projection_status"] == "published"
    assert report["used_live_adapters"] is False
    assert report["requires_custom_queue_store"] is False

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_cli_success"
    assert (run_dir / "meeting_run.json").exists()
    assert (run_dir / "decision_log.jsonl").exists()
    assert (run_dir / "audit_log.jsonl").exists()
    assert (run_dir / "discord_projection" / "proj_mr_cli_success.json").exists()


def test_simulation_cli_security_pause_returns_nonzero_without_secret_leak(
    tmp_path,
    capsys,
):
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "--meeting-run-id",
            "mr_cli_security_pause",
            "--trigger-text",
            "API_TOKEN=example-secret-value 포함 회의",
            "--user-id",
            "user-1",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
        ]
    )

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "example-secret-value" not in output
    report = json.loads(output)
    assert report["ok"] is False
    assert report["state"] == "paused"
    assert report["security_reason"] == "secret_like_input_detected"
    assert report["worker_task_count"] == 0
    assert report["used_live_adapters"] is False

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_cli_security_pause"
    persisted = (run_dir / "meeting_run.json").read_text(encoding="utf-8")
    projection = (
        run_dir / "discord_projection" / "proj_mr_cli_security_pause.json"
    ).read_text(encoding="utf-8")
    assert "example-secret-value" not in persisted
    assert "example-secret-value" not in projection


def test_simulation_cli_quota_snapshot_can_pause_before_workers(tmp_path, capsys):
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "--meeting-run-id",
            "mr_cli_quota_pause",
            "--trigger-text",
            "코드 구현해줘",
            "--user-id",
            "user-1",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
            "--simulation",
            "false",
            "--active-provider",
            "codex",
            "--quota-provider",
            "codex",
            "--quota-monthly-percent",
            "0",
            "--quota-weekly-percent",
            "99",
            "--quota-hourly-percent",
            "12",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["state"] == "paused"
    assert report["quota_reason"] == "quota_weekly_critical"
    assert report["worker_task_count"] == 0
    assert report["scheduling_kind"] == "hermes_background_process"
    assert report["used_live_adapters"] is False


def test_simulation_cli_module_entrypoint_runs_as_real_subprocess(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.runtime_architecture_v2.simulation_cli",
            "--root",
            str(tmp_path),
            "--meeting-run-id",
            "mr_cli_subprocess",
            "--trigger-text",
            "콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘",
            "--user-id",
            "user-1",
            "--channel-id",
            "channel-1",
            "--thread-id",
            "thread-1",
        ],
        check=False,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert set(report) == {
        "checkpoint_id",
        "meeting_run_id",
        "mode",
        "ok",
        "projection_event_id",
        "projection_status",
        "quota_reason",
        "requires_custom_queue_store",
        "route_type",
        "scheduling_kind",
        "scheduling_primitive",
        "security_reason",
        "state",
        "used_live_adapters",
        "validation_decision",
        "validation_verdicts",
        "worker_task_count",
    }
    assert report["meeting_run_id"] == "mr_cli_subprocess"
    assert report["ok"] is True
    assert report["used_live_adapters"] is False
    assert (tmp_path / "runtime" / "meeting_runs" / "mr_cli_subprocess").exists()
