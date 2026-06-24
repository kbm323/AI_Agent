from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.schemas import MeetingRun, MeetingRunState
from src.runtime_architecture_v2.store import MeetingRunStore


def _seed_run(root: Path, meeting_run_id: str, state: MeetingRunState) -> MeetingRun:
    run = MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text="Phase 17 health check test",
        user_id="u1",
        channel_id="c1",
        thread_id="t1",
    )
    run = run.__class__(
        meeting_run_id=run.meeting_run_id,
        state=state,
        trigger=run.trigger,
        priority=run.priority,
        hermes_refs=run.hermes_refs,
    )
    MeetingRunStore(root).save_meeting_run(run)
    return run


def test_phase17_scans_meeting_runs_and_counts_by_state(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health

    _seed_run(tmp_path, "mr_completed_1", MeetingRunState.COMPLETED)
    _seed_run(tmp_path, "mr_completed_2", MeetingRunState.COMPLETED)
    _seed_run(tmp_path, "mr_active_1", MeetingRunState.ACTIVE)
    _seed_run(tmp_path, "mr_failed_1", MeetingRunState.FAILED)

    report = scan_health(root=tmp_path)

    assert report.ok is True
    assert report.total_runs == 4
    assert report.state_counts["completed"] == 2
    assert report.state_counts["active"] == 1
    assert report.state_counts["failed"] == 1
    assert report.state_counts.get("created", 0) == 0
    assert all(run.state in report.state_counts for run in report.runs)


def test_phase17_flags_stuck_runs_older_than_threshold(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health

    _seed_run(tmp_path, "mr_stuck_1", MeetingRunState.ACTIVE)
    _seed_run(tmp_path, "mr_stuck_2", MeetingRunState.ROUTED)
    _seed_run(tmp_path, "mr_ok_1", MeetingRunState.COMPLETED)

    report = scan_health(root=tmp_path, stuck_hours=0)

    assert len(report.stuck_runs) == 2
    stuck_ids = {run.meeting_run_id for run in report.stuck_runs}
    assert "mr_stuck_1" in stuck_ids
    assert "mr_stuck_2" in stuck_ids
    assert "mr_ok_1" not in stuck_ids


def test_phase17_terminal_runs_are_not_stuck(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health

    _seed_run(tmp_path, "mr_completed", MeetingRunState.COMPLETED)
    _seed_run(tmp_path, "mr_failed", MeetingRunState.FAILED)
    _seed_run(tmp_path, "mr_cancelled", MeetingRunState.CANCELLED)

    report = scan_health(root=tmp_path, stuck_hours=0)

    assert len(report.stuck_runs) == 0


def test_phase17_recovery_triage_suggests_resume_for_paused_runs(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health, triage_recovery

    _seed_run(tmp_path, "mr_paused", MeetingRunState.PAUSED)
    report = scan_health(root=tmp_path, stuck_hours=0)
    suggestions = triage_recovery(report)

    assert len(suggestions) >= 1
    paused_suggestions = [s for s in suggestions if s.meeting_run_id == "mr_paused"]
    assert len(paused_suggestions) == 1
    assert paused_suggestions[0].action in ("resume", "manual")


def test_phase17_recovery_triage_suggests_manual_for_failed_runs(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health, triage_recovery

    _seed_run(tmp_path, "mr_failed", MeetingRunState.FAILED)
    report = scan_health(root=tmp_path, stuck_hours=0)
    suggestions = triage_recovery(report)

    failed_suggestions = [s for s in suggestions if s.meeting_run_id == "mr_failed"]
    assert len(failed_suggestions) == 1
    assert failed_suggestions[0].action == "manual"


def test_phase17_health_report_summary_does_not_leak_run_ids(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health

    _seed_run(tmp_path, "mr_secret", MeetingRunState.FAILED)
    report = scan_health(root=tmp_path)

    summary = report.to_summary()
    assert "mr_secret" not in summary


def test_phase17_empty_workspace_returns_clean_health_report(tmp_path: Path):
    from src.runtime_architecture_v2.production import scan_health

    report = scan_health(root=tmp_path)

    assert report.ok is True
    assert report.total_runs == 0
    assert report.stuck_runs == ()
    assert report.state_counts == {}


def test_phase17_cli_dry_run_outputs_machine_readable_json(tmp_path: Path):
    _seed_run(tmp_path, "mr_cli_test", MeetingRunState.COMPLETED)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase17_health_check.py",
            "--mode",
            "dry-run",
            "--root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "dry-run"
    assert payload["total_runs"] >= 1
    assert completed.stderr == ""


def test_phase17_cli_includes_stuck_count_and_recovery_suggestions(tmp_path: Path):
    _seed_run(tmp_path, "mr_active", MeetingRunState.ACTIVE)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase17_health_check.py",
            "--mode",
            "dry-run",
            "--root",
            str(tmp_path),
            "--stuck-hours",
            "0",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["stuck_count"] >= 1
    assert len(payload["recovery_suggestions"]) >= 1
    assert all("action" in s for s in payload["recovery_suggestions"])
