"""Phase 22 Always-on Autonomous Company — TDD tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_architecture_v2.autonomous_company import (
    AutonomousCompany,
    CompanyCycleResult,
    run_phase22_company_cycle,
)


class TestCompanyCycleResult:
    def test_result_ok_dry_run(self) -> None:
        r = CompanyCycleResult(
            ok=True, dry_run=True, cycle_id="c1",
            health_ok=True, stuck_runs=0,
            daemon_scheduled=2, daemon_created=0,
            dispatch_total=0, knowledge_updated=False,
            commands_simulated=5, total_meeting_runs=4,
            active_bots=29, error="",
        )
        assert r.ok is True
        assert r.dry_run is True
        assert r.active_bots == 29

    def test_result_to_dict(self) -> None:
        r = CompanyCycleResult(
            ok=True, dry_run=True, cycle_id="c1",
            health_ok=True, stuck_runs=0,
            daemon_scheduled=0, daemon_created=0,
            dispatch_total=0, knowledge_updated=True,
            commands_simulated=5, total_meeting_runs=4,
            active_bots=29, error="",
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["knowledge_updated"] is True

    def test_result_error_sanitized(self) -> None:
        r = CompanyCycleResult(
            ok=False, dry_run=True, cycle_id="c1",
            health_ok=True, stuck_runs=0,
            daemon_scheduled=0, daemon_created=0,
            dispatch_total=0, knowledge_updated=False,
            commands_simulated=0, total_meeting_runs=0,
            active_bots=29, error="api_key=sk-leaked-secret-999",
        )
        raw = json.dumps(r.to_dict())
        assert "sk-leaked" not in raw
        assert "api_key" not in raw


class TestAutonomousCompany:
    def test_run_dry_run(self, tmp_path: Path) -> None:
        company = AutonomousCompany(root=tmp_path, dry_run=True)
        result = company.run()
        assert result.ok is True
        assert result.dry_run is True
        assert result.active_bots == 29  # full org chart
        assert result.commands_simulated == 5

    def test_health_gate_blocks_when_stuck(self, tmp_path: Path) -> None:
        company = AutonomousCompany(
            root=tmp_path, dry_run=True, max_stuck_threshold=0,
        )
        result = company.run()
        assert result.stuck_runs == 0  # no runs yet
        assert result.daemon_scheduled == 2  # default specs count


class TestPhase22CLI:
    def test_dry_run(self, tmp_path: Path) -> None:
        r = run_phase22_company_cycle(root=tmp_path, mode="dry-run")
        assert r["ok"] is True
        assert r["mode"] == "dry-run"

    def test_artifact_written(self, tmp_path: Path) -> None:
        _ = run_phase22_company_cycle(root=tmp_path, mode="dry-run")
        path = tmp_path / "runtime" / "phase22-company"
        assert path.exists()
        artifacts = list(path.glob("*.json"))
        assert len(artifacts) >= 1
