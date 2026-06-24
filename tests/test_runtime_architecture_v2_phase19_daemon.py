"""Phase 19 Autonomous Scheduling Daemon — TDD tests."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_architecture_v2.daemon import (
    AUTONOMOUS_DAEMON_ID,
    AutonomousDaemon,
    DaemonTick,
    RecurringMeetingSpec,
    run_phase19_daemon_tick,
)
from runtime_architecture_v2.production import HealthReport


def _specs() -> tuple[RecurringMeetingSpec, ...]:
    return (
        RecurringMeetingSpec(
            spec_id="spec-daily",
            name="Daily Standup",
            schedule="every 24h",
            trigger_text="일일 스탠드업 — 각 팀 진행상황 보고",
            priority="P1",
            worker_roles=("content_lead", "tech_lead", "marketing_lead"),
        ),
        RecurringMeetingSpec(
            spec_id="spec-weekly",
            name="Weekly Review",
            schedule="every 7d",
            trigger_text="주간 리뷰 — KPI 점검 및 다음 주 계획",
            priority="P1",
            worker_roles=("content_lead", "art_director", "quality_lead"),
        ),
    )


def _disabled_spec() -> RecurringMeetingSpec:
    return RecurringMeetingSpec(
        spec_id="spec-disabled",
        name="Disabled Meeting",
        schedule="every 1h",
        trigger_text="비활성 회의",
        priority="P3",
        worker_roles=("tech_lead",),
        enabled=False,
    )


# ---------------------------------------------------------------------------
# RecurringMeetingSpec
# ---------------------------------------------------------------------------

class TestRecurringMeetingSpecSchema:
    def test_spec_fields(self) -> None:
        spec = RecurringMeetingSpec(
            spec_id="spec-1",
            name="Test",
            schedule="every 24h",
            trigger_text="trigger",
            priority="P1",
            worker_roles=("bot_a", "bot_b"),
        )
        assert spec.spec_id == "spec-1"
        assert spec.enabled is True
        assert len(spec.worker_roles) == 2

    def test_spec_to_dict(self) -> None:
        spec = RecurringMeetingSpec(
            spec_id="spec-1",
            name="Test",
            schedule="every 24h",
            trigger_text="hello",
            priority="P2",
            worker_roles=("bot_a",),
        )
        d = spec.to_dict()
        assert d["spec_id"] == "spec-1"
        assert d["enabled"] is True


# ---------------------------------------------------------------------------
# DaemonTick
# ---------------------------------------------------------------------------

class TestDaemonTickSchema:
    def test_tick_fields(self) -> None:
        tick = DaemonTick(
            ok=True,
            dry_run=True,
            tick_id="tick-001",
            scheduled_meetings=2,
            created_runs=0,
            skipped_health=0,
            skipped_recent=0,
            skipped_disabled=0,
            dispatch_results=(),
            health_report=None,
            error="",
        )
        assert tick.ok is True
        assert tick.created_runs == 0

    def test_tick_to_dict(self) -> None:
        tick = DaemonTick(
            ok=True,
            dry_run=True,
            tick_id="tick-001",
            scheduled_meetings=1,
            created_runs=0,
            skipped_health=0,
            skipped_recent=0,
            skipped_disabled=0,
            dispatch_results=(),
            health_report=None,
            error="",
        )
        d = tick.to_dict()
        assert d["ok"] is True
        assert d["tick_id"] == "tick-001"


# ---------------------------------------------------------------------------
# AutonomousDaemon — dry-run
# ---------------------------------------------------------------------------

class TestAutonomousDaemonDryRun:
    def test_dry_run_no_meeting_runs_created(self, tmp_path: Path) -> None:
        daemon = AutonomousDaemon(
            root=tmp_path,
            dry_run=True,
            max_stuck_threshold=3,
            tick_interval_hours=24.0,
        )
        tick = daemon.tick(specs=_specs())
        assert tick.ok is True
        assert tick.dry_run is True
        assert tick.created_runs == 0
        assert tick.scheduled_meetings == 2

    def test_disabled_spec_skipped(self, tmp_path: Path) -> None:
        daemon = AutonomousDaemon(root=tmp_path, dry_run=True)
        specs = (_disabled_spec(),)
        tick = daemon.tick(specs=specs)
        assert tick.skipped_disabled == 1
        assert tick.scheduled_meetings == 0

    def test_health_gate_skips_when_stuck(self, tmp_path: Path) -> None:
        daemon = AutonomousDaemon(
            root=tmp_path,
            dry_run=True,
            max_stuck_threshold=0,
        )
        from runtime_architecture_v2.production import RunHealth
        stuck_health = HealthReport(
            ok=True, total_runs=5, state_counts={},
            stuck_runs=(
                RunHealth("mr-stuck", "active", 5.0, 3, 0, 0, False, True),
            ),
        )
        tick = daemon.tick(specs=_specs(), health=stuck_health)
        assert tick.skipped_health >= 1
        assert tick.created_runs == 0

    def test_dedup_within_tick_interval(self, tmp_path: Path) -> None:
        daemon = AutonomousDaemon(
            root=tmp_path,
            dry_run=True,
            tick_interval_hours=24.0,
        )
        daemon._last_tick_at = datetime.now(UTC)
        tick = daemon.tick(specs=_specs())
        assert tick.skipped_recent >= 1


# ---------------------------------------------------------------------------
# CLI pilot
# ---------------------------------------------------------------------------

class TestPhase19CLIPilot:
    def test_dry_run_mode(self, tmp_path: Path) -> None:
        result = run_phase19_daemon_tick(
            root=tmp_path,
            mode="dry-run",
        )
        assert result["ok"] is True
        assert result["mode"] == "dry-run"
        assert result["pilot_id"] == AUTONOMOUS_DAEMON_ID

    def test_live_mode(self, tmp_path: Path) -> None:
        result = run_phase19_daemon_tick(
            root=tmp_path,
            mode="live",
        )
        assert result["ok"] is True
        assert result["mode"] == "live"

    def test_invalid_mode(self, tmp_path: Path) -> None:
        result = run_phase19_daemon_tick(
            root=tmp_path,
            mode="chaos",
        )
        assert result["ok"] is False
        assert "unsupported" in result.get("error", "").lower()

    def test_artifact_written(self, tmp_path: Path) -> None:
        _result = run_phase19_daemon_tick(
            root=tmp_path,
            mode="dry-run",
        )
        artifact_dir = tmp_path / "runtime" / "phase19-daemon"
        assert artifact_dir.exists()
        artifacts = list(artifact_dir.glob("*.json"))
        assert len(artifacts) >= 1


# ---------------------------------------------------------------------------
# Boundary safety
# ---------------------------------------------------------------------------

class TestPhase19BoundarySafety:
    def test_no_secret_in_tick(self, tmp_path: Path) -> None:
        tick = DaemonTick(
            ok=True, dry_run=True, tick_id="tick-sec",
            scheduled_meetings=1, created_runs=0,
            skipped_health=0, skipped_recent=0, skipped_disabled=0,
            dispatch_results=(), health_report=None,
            error="api_key=leaked-secret-123",
        )
        d = tick.to_dict()
        raw = json.dumps(d)
        assert "leaked-secret" not in raw
        assert "api_key" not in raw
