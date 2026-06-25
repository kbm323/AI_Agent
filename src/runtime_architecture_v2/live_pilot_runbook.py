"""Phase 29 — 24h Live Pilot & Production Runbook.

This module provides the final bounded-operations proof and production readiness
verdict. It does not run a real 24-hour pilot; it verifies that all live gates,
the runbook, recovery/quota/cost evidence, and pilot constraints are satisfied
before any long-running live operation is declared safe to start.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any


@unique
class LivePilotStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class BoundedOpsEvidence:
    """Evidence that live operations are bounded."""

    max_runs_per_hour: int
    allowed_window_hours: tuple[str, str]
    allowed_channels: tuple[str, ...]
    mention_gated: bool


@dataclass(frozen=True)
class RecoveryEvidence:
    """Evidence that rollback and recovery are prepared."""

    checkpoint_interval_seconds: int
    rollback_command: str
    incident_channel: str
    manual_override_contact: str


@dataclass(frozen=True)
class QuotaCostEvidence:
    """Evidence that quota/cost controls are in place."""

    budget_cap_usd: float
    hourly_spend_max: float
    model_quota_thresholds: dict[str, float]
    alert_channel: str


@dataclass(frozen=True)
class LivePilotObservation:
    """Synthetic observation from a bounded 24h pilot simulation."""

    timestamp: str
    event_type: str
    trace_id: str
    status: str


@dataclass(frozen=True)
class LivePilotDecision:
    """Decision from TwentyFourHourLivePilotPolicy.evaluate."""

    status: LivePilotStatus
    reason: str = ""


@dataclass(frozen=True)
class GateStatus:
    """Status of a single live-production gate."""

    name: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class TwentyFourHourLivePilotPolicy:
    """Bounded constraints for a 24-hour live pilot."""

    max_runs_per_hour: int = 10
    max_cost_usd: float = 100.0
    allowed_window_hours: tuple[str, str] = ("09:00", "23:00")
    allowed_channel_prefixes: tuple[str, ...] = ("home:",)
    require_mention_gated: bool = True
    require_rollback_plan: bool = True
    require_quota_alert_channel: bool = True
    min_checkpoint_interval_seconds: int = 60

    @classmethod
    def current_verified(cls) -> TwentyFourHourLivePilotPolicy:
        """Return the verified 24h pilot policy."""

        return cls()

    def evaluate(
        self,
        *,
        bounded_ops: BoundedOpsEvidence,
        recovery: RecoveryEvidence,
        quota_cost: QuotaCostEvidence,
    ) -> LivePilotDecision:
        """Fail-closed evaluation of bounded pilot readiness."""

        if bounded_ops.max_runs_per_hour <= 0:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                f"max_runs_per_hour_invalid: {bounded_ops.max_runs_per_hour}",
            )
        if bounded_ops.max_runs_per_hour > self.max_runs_per_hour:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                (
                    f"max_runs_per_hour_exceeded: "
                    f"{bounded_ops.max_runs_per_hour} > {self.max_runs_per_hour}"
                ),
            )
        start, end = bounded_ops.allowed_window_hours
        if len(bounded_ops.allowed_window_hours) != 2 or start >= end:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                f"allowed_window_hours_invalid: {bounded_ops.allowed_window_hours}",
            )
        if bounded_ops.allowed_window_hours != self.allowed_window_hours:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                f"allowed_window_hours_mismatch: {bounded_ops.allowed_window_hours}",
            )
        if not bounded_ops.allowed_channels:
            return LivePilotDecision(
                LivePilotStatus.FAIL, "allowed_channels_empty"
            )
        for channel in bounded_ops.allowed_channels:
            if not any(
                channel.startswith(prefix)
                for prefix in self.allowed_channel_prefixes
            ):
                return LivePilotDecision(
                    LivePilotStatus.FAIL,
                    f"allowed_channel_prefix_not_allowed: {channel}",
                )
        if self.require_mention_gated and not bounded_ops.mention_gated:
            return LivePilotDecision(
                LivePilotStatus.FAIL, "mention_gate_required"
            )

        if (
            recovery.checkpoint_interval_seconds
            < self.min_checkpoint_interval_seconds
        ):
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                (
                    f"checkpoint_interval_too_small: "
                    f"{recovery.checkpoint_interval_seconds} < "
                    f"{self.min_checkpoint_interval_seconds}"
                ),
            )
        if not recovery.incident_channel.startswith("home:"):
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                (
                    f"incident_channel_prefix_not_allowed: "
                    f"{recovery.incident_channel}"
                ),
            )
        if self.require_rollback_plan and not recovery.rollback_command.strip():
            return LivePilotDecision(
                LivePilotStatus.FAIL, "rollback_command_missing"
            )
        if not recovery.incident_channel.strip():
            return LivePilotDecision(
                LivePilotStatus.FAIL, "incident_channel_missing"
            )
        if not recovery.manual_override_contact.strip():
            return LivePilotDecision(
                LivePilotStatus.FAIL, "manual_override_contact_missing"
            )

        if quota_cost.budget_cap_usd <= 0:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                f"budget_cap_invalid: {quota_cost.budget_cap_usd}",
            )
        if quota_cost.budget_cap_usd > self.max_cost_usd:
            return LivePilotDecision(
                LivePilotStatus.FAIL,
                (
                    f"budget_cap_exceeded: "
                    f"{quota_cost.budget_cap_usd} > {self.max_cost_usd}"
                ),
            )
        if quota_cost.hourly_spend_max <= 0:
            return LivePilotDecision(
                LivePilotStatus.FAIL, "hourly_spend_max_invalid"
            )
        if quota_cost.hourly_spend_max > quota_cost.budget_cap_usd:
            return LivePilotDecision(
                LivePilotStatus.FAIL, "hourly_spend_exceeds_budget_cap"
            )
        if not quota_cost.model_quota_thresholds:
            return LivePilotDecision(
                LivePilotStatus.FAIL, "model_quota_thresholds_empty"
            )
        for model, threshold in quota_cost.model_quota_thresholds.items():
            if not 0.0 < threshold <= 1.0:
                return LivePilotDecision(
                    LivePilotStatus.FAIL,
                    f"model_quota_threshold_invalid: {model}={threshold}",
                )
        if self.require_quota_alert_channel and not quota_cost.alert_channel.strip():
            return LivePilotDecision(
                LivePilotStatus.FAIL, "quota_alert_channel_missing"
            )

        return LivePilotDecision(LivePilotStatus.PASS, "")


@dataclass(frozen=True)
class ProductionRunbook:
    """Pre-flight checklist for live production readiness."""

    pre_flight_sections: dict[str, str] = field(default_factory=dict)

    _REQUIRED_SECTIONS: tuple[str, ...] = (
        "team_contacts",
        "rollback_plan",
        "quota_budget",
        "incident_response",
        "observability",
        "discord_channels",
    )

    @classmethod
    def current_verified(cls) -> ProductionRunbook:
        """Return the verified production runbook."""

        return cls(
            pre_flight_sections={
                "team_contacts": (
                    "7 Hermes profile owners + human operator escalation"
                ),
                "rollback_plan": (
                    "hermes --profile <name> --stop; "
                    "git checkout stable; restart from checkpoint"
                ),
                "quota_budget": (
                    "$100 cap, $10/hour max, alert at 80% model quota"
                ),
                "incident_response": (
                    "#기술-메인 for ops, "
                    "#전체-리뷰 for validation escalations"
                ),
                "observability": (
                    "Hermes logs under runtime/logs/<profile>, "
                    "rotation 50MB/5 files"
                ),
                "discord_channels": (
                    "7 allowed home channels, mention-gated, no free-response"
                ),
            }
        )

    def is_complete(self) -> bool:
        """Fail closed if any required section is missing or empty."""

        return all(
            self.pre_flight_sections.get(section, "").strip()
            for section in self._REQUIRED_SECTIONS
        )


@dataclass(frozen=True)
class ProductionReadinessVerdict:
    """Final production readiness verdict for Phase 29."""

    status: str
    phase: str = "phase29"
    blockers: tuple[str, ...] = ()
    gate_statuses: tuple[GateStatus, ...] = ()
    observations: tuple[LivePilotObservation, ...] = ()

    @classmethod
    def evaluate(
        cls,
        *,
        gate_statuses: tuple[GateStatus, ...],
        runbook: ProductionRunbook,
        pilot_policy: TwentyFourHourLivePilotPolicy,
        bounded_ops: BoundedOpsEvidence,
        recovery: RecoveryEvidence,
        quota_cost: QuotaCostEvidence,
    ) -> ProductionReadinessVerdict:
        """Evaluate full production readiness. Fail-closed."""

        blockers: list[str] = []

        expected_gates = {f"gate_{i}" for i in range(5, 10)}
        actual_gates = {g.name for g in gate_statuses}
        if actual_gates != expected_gates:
            blockers.append(
                f"gate_statuses_incomplete: expected {sorted(expected_gates)}, "
                f"got {sorted(actual_gates)}"
            )

        for gate in gate_statuses:
            if gate.status != "pass":
                blockers.append(f"{gate.name}_failed: {gate.reason}")

        if not runbook.is_complete():
            missing = [
                section
                for section in ProductionRunbook._REQUIRED_SECTIONS
                if not runbook.pre_flight_sections.get(section, "").strip()
            ]
            blockers.append(f"runbook_incomplete: {missing}")

        pilot_decision = pilot_policy.evaluate(
            bounded_ops=bounded_ops,
            recovery=recovery,
            quota_cost=quota_cost,
        )
        if pilot_decision.status == LivePilotStatus.FAIL:
            blockers.append(f"pilot_policy_failed: {pilot_decision.reason}")

        if blockers:
            return ProductionReadinessVerdict(
                status="NOT_READY",
                blockers=tuple(blockers),
                gate_statuses=gate_statuses,
            )

        return ProductionReadinessVerdict(
            status="READY",
            blockers=(),
            gate_statuses=gate_statuses,
        )

    @classmethod
    def simulate_24h_pilot(
        cls,
        *,
        gate_statuses: tuple[GateStatus, ...],
        runbook: ProductionRunbook,
        pilot_policy: TwentyFourHourLivePilotPolicy,
        bounded_ops: BoundedOpsEvidence,
        recovery: RecoveryEvidence,
        quota_cost: QuotaCostEvidence,
    ) -> dict[str, Any]:
        """Produce a bounded 24h pilot simulation report without sleeping."""

        verdict = cls.evaluate(
            gate_statuses=gate_statuses,
            runbook=runbook,
            pilot_policy=pilot_policy,
            bounded_ops=bounded_ops,
            recovery=recovery,
            quota_cost=quota_cost,
        )

        now = datetime.now(UTC).isoformat()
        observations = (
            LivePilotObservation(
                timestamp=now,
                event_type="pilot_start",
                trace_id="phase29-simulated-start",
                status="pass" if verdict.status == "READY" else "fail",
            ),
            LivePilotObservation(
                timestamp=now,
                event_type="checkpoint",
                trace_id="phase29-checkpoint-1",
                status="pass" if recovery.checkpoint_interval_seconds > 0 else "fail",
            ),
            LivePilotObservation(
                timestamp=now,
                event_type="quota_check",
                trace_id="phase29-quota-check",
                status="pass" if quota_cost.budget_cap_usd > 0 else "fail",
            ),
            LivePilotObservation(
                timestamp=now,
                event_type="pilot_end",
                trace_id="phase29-simulated-end",
                status="pass" if verdict.status == "READY" else "fail",
            ),
        )

        verdict_with_obs = ProductionReadinessVerdict(
            status=verdict.status,
            blockers=verdict.blockers,
            gate_statuses=verdict.gate_statuses,
            observations=observations,
        )

        return {
            "phase": verdict_with_obs.phase,
            "verdict": verdict_with_obs.status,
            "blockers": list(verdict_with_obs.blockers),
            "gate_statuses": [
                {"name": g.name, "status": g.status, "reason": g.reason}
                for g in verdict_with_obs.gate_statuses
            ],
            "observations": [asdict(o) for o in observations],
        }

    def to_artifact(self) -> dict[str, Any]:
        """Serialize the verdict as a persisted artifact."""

        return {
            "phase": self.phase,
            "status": self.status,
            "blockers": list(self.blockers),
            "gate_statuses": [
                {"name": g.name, "status": g.status, "reason": g.reason}
                for g in self.gate_statuses
            ],
            "observations": [asdict(o) for o in self.observations],
        }
