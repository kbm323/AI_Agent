"""Phase 22 Always-on Autonomous Company — TDD tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import runtime_architecture_v2.autonomous_company as autonomous_company_module
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
            active_bots=29, error="api_key: sk-leaked-secret-999",
        )
        raw = json.dumps(r.to_dict())
        assert "sk-leaked" not in raw
        assert "api_key" not in raw


    def test_result_alias_normalizes_active_bots_to_registered_roles(self) -> None:
        r = CompanyCycleResult(
            ok=True, dry_run=True, cycle_id="c1",
            health_ok=True, stuck_runs=0,
            daemon_scheduled=0, daemon_created=0,
            dispatch_total=0, knowledge_updated=True,
            commands_simulated=5, total_meeting_runs=4,
            active_bots=7, registered_roles=29, error="",
        )
        assert r.registered_roles == 29
        assert r.active_bots == 29


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

    def test_registered_roles_replaces_or_supplements_active_bots(
        self, tmp_path: Path,
    ) -> None:
        company = AutonomousCompany(root=tmp_path, dry_run=True)
        result = company.run()
        assert result.registered_roles == 29
        assert result.active_bots == result.registered_roles
        result_dict = result.to_dict()
        assert result_dict["registered_roles"] == 29
        assert result_dict["active_bots"] == 29

    def test_live_mode_fails_closed_when_dispatch_results_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FailingDaemon:
            def __init__(self, **_: object) -> None:
                pass

            def tick(self, **_: object) -> SimpleNamespace:
                return SimpleNamespace(
                    scheduled_meetings=1,
                    created_runs=1,
                    dispatch_results=(
                        {"ok": False, "error": "dispatch failed token=sk-secret"},
                    ),
                )

        monkeypatch.setattr(
            autonomous_company_module, "AutonomousDaemon", FailingDaemon,
        )
        company = AutonomousCompany(root=tmp_path, dry_run=False)
        result = company.run()
        assert result.ok is False
        assert result.dispatch_total == 0
        assert result.subphase_status["daemon_dispatch"] == "failed"
        raw = json.dumps(result.to_dict())
        assert "daemon_dispatch_failed" in raw
        assert "sk-secret" not in raw

    def test_live_mode_fails_closed_when_dispatch_results_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class MissingDispatchDaemon:
            def __init__(self, **_: object) -> None:
                pass

            def tick(self, **_: object) -> SimpleNamespace:
                return SimpleNamespace(
                    scheduled_meetings=1,
                    created_runs=1,
                    dispatch_results=(),
                )

        monkeypatch.setattr(
            autonomous_company_module, "AutonomousDaemon", MissingDispatchDaemon,
        )
        company = AutonomousCompany(root=tmp_path, dry_run=False)
        result = company.run()
        assert result.ok is False
        assert result.daemon_created == 1
        assert result.dispatch_total == 0
        assert result.subphase_status["daemon_dispatch"] == "failed"
        assert "live_dispatch_dependency_missing" in result.error

    def test_knowledge_exception_is_recorded_as_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def raise_knowledge_error(**_: object) -> object:
            raise RuntimeError("knowledge backend exploded token=sk-secret")

        monkeypatch.setattr(
            autonomous_company_module,
            "retrieve_knowledge_context",
            raise_knowledge_error,
        )
        company = AutonomousCompany(root=tmp_path, dry_run=True)
        result = company.run()
        assert result.knowledge_updated is False
        assert result.subphase_status["knowledge"] == "warning"
        raw = json.dumps(result.to_dict())
        assert "knowledge_update_failed" in raw
        assert "sk-secret" not in raw

    def test_command_route_exception_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FailingRouter:
            def __init__(self, **_: object) -> None:
                pass

            def route(self, _: object) -> object:
                raise RuntimeError("command route failed token=sk-secret")

        monkeypatch.setattr(
            autonomous_company_module, "DiscordCommandRouter", FailingRouter,
        )
        company = AutonomousCompany(root=tmp_path, dry_run=True)
        result = company.run()
        assert result.commands_simulated == 0
        assert result.subphase_status["commands"] == "warning"
        raw = json.dumps(result.to_dict())
        assert "command_route_failed" in raw
        assert "sk-secret" not in raw


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
