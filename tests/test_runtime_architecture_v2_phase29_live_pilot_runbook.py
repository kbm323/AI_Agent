"""Phase 29 — 24h Live Pilot & Production Runbook tests.

The Phase 29 layer proves bounded operations readiness without actually running
for 24 hours. It aggregates Gate 5-9 status, runbook completeness, pilot
constraints, recovery evidence, and quota/cost evidence into a production
readiness verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime_architecture_v2.live_pilot_runbook import (
    BoundedOpsEvidence,
    GateStatus,
    LivePilotStatus,
    ProductionReadinessVerdict,
    ProductionRunbook,
    QuotaCostEvidence,
    RecoveryEvidence,
    TwentyFourHourLivePilotPolicy,
)


def _all_gates_pass() -> tuple[GateStatus, ...]:
    return tuple(
        GateStatus(name=f"gate_{i}", status="pass", reason="")
        for i in range(5, 10)
    )


def _valid_recovery_evidence() -> RecoveryEvidence:
    return RecoveryEvidence(
        checkpoint_interval_seconds=300,
        rollback_command="hermes --profile aicompanyceo --stop && git checkout stable",
        incident_channel="home:aicompanytech:#기술-메인",
        manual_override_contact="operator@example.com",
    )


def _valid_quota_cost_evidence() -> QuotaCostEvidence:
    return QuotaCostEvidence(
        budget_cap_usd=100.0,
        hourly_spend_max=10.0,
        model_quota_thresholds={"opencode-go": 0.8, "glm": 0.8, "codex": 0.8},
        alert_channel="home:aicompanyquality:#전체-리뷰",
    )


def _valid_bounded_ops_evidence() -> BoundedOpsEvidence:
    return BoundedOpsEvidence(
        max_runs_per_hour=10,
        allowed_window_hours=("09:00", "23:00"),
        allowed_channels=(
            "home:aicompanyassistant:#일일-브리핑",
            "home:aicompanyceo:#전략-회의실",
        ),
        mention_gated=True,
    )


# AC1: bounded defaults


class TestTwentyFourHourLivePilotPolicy:
    def test_current_verified_passes_with_valid_evidence(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        decision = policy.evaluate(
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.PASS
        assert decision.reason == ""

    def test_fails_when_max_runs_per_hour_exceeded(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=100,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "max_runs_per_hour" in decision.reason

    def test_fails_when_mention_gate_disabled(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=5,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=False,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "mention_gate" in decision.reason

    def test_fails_when_window_outside_bounds(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=5,
            allowed_window_hours=("00:00", "23:59"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "allowed_window_hours" in decision.reason

    def test_fails_when_allowed_channels_empty(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=5,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=(),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "allowed_channels" in decision.reason

    def test_fails_when_recovery_checkpoint_missing(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        recovery = RecoveryEvidence(
            checkpoint_interval_seconds=0,
            rollback_command="git checkout stable",
            incident_channel="home:aicompanytech:#기술-메인",
            manual_override_contact="operator@example.com",
        )
        decision = policy.evaluate(
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=recovery,
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "checkpoint_interval" in decision.reason

    def test_fails_when_max_runs_per_hour_negative(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=-1,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "max_runs_per_hour" in decision.reason

    def test_fails_when_max_runs_per_hour_zero(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=0,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "max_runs_per_hour" in decision.reason

    def test_fails_when_budget_cap_not_positive(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        quota = QuotaCostEvidence(
            budget_cap_usd=0.0,
            hourly_spend_max=5.0,
            model_quota_thresholds={"opencode-go": 0.8},
            alert_channel="home:aicompanyquality:#전체-리뷰",
        )
        decision = policy.evaluate(
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=quota,
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "budget_cap" in decision.reason

    def test_fails_when_hourly_spend_exceeds_daily_budget(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        quota = QuotaCostEvidence(
            budget_cap_usd=5.0,
            hourly_spend_max=10.0,
            model_quota_thresholds={"opencode-go": 0.8},
            alert_channel="home:aicompanyquality:#전체-리뷰",
        )
        decision = policy.evaluate(
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=quota,
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "hourly_spend_exceeds_budget_cap" in decision.reason

    def test_fails_when_allowed_window_hours_are_invalid(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        ops = BoundedOpsEvidence(
            max_runs_per_hour=5,
            allowed_window_hours=("23:00", "09:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        decision = policy.evaluate(
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "allowed_window_hours" in decision.reason

    def test_fails_when_incident_channel_not_profile_local(self):
        policy = TwentyFourHourLivePilotPolicy.current_verified()
        recovery = RecoveryEvidence(
            checkpoint_interval_seconds=300,
            rollback_command="git checkout stable",
            incident_channel="#기술-메인",
            manual_override_contact="operator@example.com",
        )
        decision = policy.evaluate(
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=recovery,
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert decision.status == LivePilotStatus.FAIL
        assert "incident_channel_prefix" in decision.reason

    def test_simulate_not_ready_when_gate_missing(self):
        result = ProductionReadinessVerdict.simulate_24h_pilot(
            gate_statuses=(GateStatus(name="gate_5", status="pass"),),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert result["verdict"] == "NOT_READY"
        assert any("gate_statuses_incomplete" in b for b in result["blockers"])


# AC2: Runbook completeness


class TestProductionRunbook:
    def test_complete_runbook_passes(self):
        runbook = ProductionRunbook.current_verified()
        assert runbook.is_complete()

    def test_incomplete_runbook_fails(self):
        runbook = ProductionRunbook(
            pre_flight_sections={
                "team_contacts": "ok",
                "rollback_plan": "",
            }
        )
        assert not runbook.is_complete()

    def test_current_verified_has_required_sections(self):
        runbook = ProductionRunbook.current_verified()
        required = {
            "team_contacts",
            "rollback_plan",
            "quota_budget",
            "incident_response",
            "observability",
            "discord_channels",
        }
        assert required <= set(runbook.pre_flight_sections.keys())


# ── AC3: ProductionReadinessVerdict aggregates gates ──────────────────────────────────


class TestProductionReadinessVerdict:
    def test_ready_when_all_gates_and_evidence_pass(self):
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert verdict.status == "READY"
        assert verdict.blockers == ()

    def test_not_ready_when_any_gate_fails(self):
        gates = list(_all_gates_pass())
        gates[2] = GateStatus(name="gate_7", status="fail", reason="quota_gate_open")
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=tuple(gates),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert verdict.status == "NOT_READY"
        assert any("gate_7" in b for b in verdict.blockers)

    def test_not_ready_when_runbook_incomplete(self):
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook(pre_flight_sections={"team_contacts": ""}),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert verdict.status == "NOT_READY"
        assert any("runbook" in b.lower() for b in verdict.blockers)

    def test_not_ready_when_pilot_policy_fails(self):
        ops = BoundedOpsEvidence(
            max_runs_per_hour=1000,
            allowed_window_hours=("09:00", "23:00"),
            allowed_channels=("home:aicompanyceo:#전략-회의실",),
            mention_gated=True,
        )
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=ops,
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert verdict.status == "NOT_READY"
        assert any("pilot" in b.lower() for b in verdict.blockers)


# ── AC4: simulate bounded 24h pilot returns verdict without sleeping ─────────────────


class TestBoundedPilotSimulation:
    def test_simulate_returns_ready_verdict(self):
        result = ProductionReadinessVerdict.simulate_24h_pilot(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        assert result["verdict"] == "READY"
        assert "observations" in result
        assert len(result["observations"]) > 0

    def test_simulate_does_not_actually_sleep_24h(self):
        import time
        start = time.monotonic()
        ProductionReadinessVerdict.simulate_24h_pilot(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0

    def test_simulate_observations_are_valid(self):
        result = ProductionReadinessVerdict.simulate_24h_pilot(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        for obs in result["observations"]:
            assert obs["trace_id"]
            assert obs["status"] in {"pass", "fail"}


# AC5: artifact persistence


class TestArtifactPersistence:
    def test_verdict_serializes_to_json(self, tmp_path: Path):
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        artifact = verdict.to_artifact()
        path = tmp_path / "verdict.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["status"] == "READY"
        assert loaded["phase"] == "phase29"

    def test_artifact_contains_phase_status_and_blockers(self):
        verdict = ProductionReadinessVerdict.evaluate(
            gate_statuses=_all_gates_pass(),
            runbook=ProductionRunbook.current_verified(),
            pilot_policy=TwentyFourHourLivePilotPolicy.current_verified(),
            bounded_ops=_valid_bounded_ops_evidence(),
            recovery=_valid_recovery_evidence(),
            quota_cost=_valid_quota_cost_evidence(),
        )
        artifact = verdict.to_artifact()
        assert artifact["phase"] == "phase29"
        assert artifact["status"] == "READY"
        assert artifact["blockers"] == []
        # Recovery evidence is provided as input but is not duplicated in the
        # artifact; secrets must be loaded from profile-local env, never
        # committed.
