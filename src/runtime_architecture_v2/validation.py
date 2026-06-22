"""Validation policy and validator role boundaries for Runtime Architecture v2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .policies import PolicyDecision, QuotaPolicy
from .schemas import (
    ValidationVerdict,
    ValidationVerdictValue,
    WorkerTask,
    WorkerTaskRunner,
)


class CorrectionActionKind(StrEnum):
    CONTINUE = "continue"
    REVISE = "revise"
    STOP = "stop"
    ASK_USER = "ask_user"


@dataclass(frozen=True)
class ValidationDecision:
    """Deterministic correction-loop decision derived from validator verdicts."""

    meeting_run_id: str
    kind: CorrectionActionKind
    next_state: str
    blocking_validation_ids: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    requires_user: bool = False
    follow_up_worker_required: bool = False
    rationale: str = ""


@dataclass(frozen=True)
class ValidatorExecutionPlan:
    """Quota-gated validator worker task plan."""

    status: str
    quota_decision: PolicyDecision
    worker_tasks: tuple[WorkerTask, ...] = ()
    degraded_verdicts: tuple[ValidationVerdict, ...] = ()


@dataclass(frozen=True)
class ValidatorRolePolicy:
    """Execution role definition for model-backed validation tasks."""

    role: str
    preferred_model: str
    execution_role: str
    model_family: str
    fallback_runner: str

    @classmethod
    def glm_validator(cls) -> ValidatorRolePolicy:
        return cls(
            role="glm_validator",
            preferred_model="glm-5.1",
            execution_role="validator",
            model_family="glm",
            fallback_runner="none",
        )

    @classmethod
    def codex_auditor(cls) -> ValidatorRolePolicy:
        return cls(
            role="codex_auditor",
            preferred_model="codex",
            execution_role="auditor",
            model_family="codex",
            fallback_runner="codex_cli_only_if_opencode_go_unavailable",
        )

    def build_worker_task(
        self,
        *,
        meeting_run_id: str,
        validation_id: str,
        packet_path: str | Path,
        output_path: str | Path,
    ) -> WorkerTask:
        return WorkerTask(
            worker_task_id=validation_id,
            meeting_run_id=meeting_run_id,
            role=self.role,
            runner=WorkerTaskRunner.OPENCODE_GO,
            packet_path=str(packet_path),
            output_path=str(output_path),
            model_policy={
                "preferred": self.preferred_model,
                "execution_role": self.execution_role,
                "model_family": self.model_family,
                "fallback_runner": self.fallback_runner,
            },
        )


class ValidatorExecutionPlanner:
    """Build opencode-go-first validator tasks with a quota guard."""

    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root)

    def plan(
        self,
        *,
        meeting_run_id: str,
        validators: tuple[str, ...],
        quota_policy: QuotaPolicy,
        active_provider: str,
    ) -> ValidatorExecutionPlan:
        quota_decision = quota_policy.evaluate(active_provider=active_provider)
        validator_roles = validators or ("glm_validator",)
        if not quota_decision.allowed:
            return ValidatorExecutionPlan(
                status="quota_blocked",
                quota_decision=quota_decision,
                degraded_verdicts=tuple(
                    build_degraded_verdict(
                        validation_id=f"val_{meeting_run_id}_{role}_degraded",
                        meeting_run_id=meeting_run_id,
                        validator_role=role,
                        validator_model=_model_for_role(role),
                        reason=f"quota blocked: {quota_decision.reason}",
                    )
                    for role in validator_roles
                ),
            )
        return ValidatorExecutionPlan(
            status="ready",
            quota_decision=quota_decision,
            worker_tasks=tuple(
                _role_policy_for(role).build_worker_task(
                    meeting_run_id=meeting_run_id,
                    validation_id=f"val_{meeting_run_id}_{index}",
                    packet_path=(
                        self.root
                        / "runtime"
                        / "meeting_runs"
                        / meeting_run_id
                        / "validator_packets"
                        / f"val_{meeting_run_id}_{index}.json"
                    ),
                    output_path=(
                        self.root
                        / "runtime"
                        / "meeting_runs"
                        / meeting_run_id
                        / "validator_outputs"
                        / f"val_{meeting_run_id}_{index}.json"
                    ),
                )
                for index, role in enumerate(validator_roles, start=1)
            ),
        )


class ValidationPolicy:
    """Collapse GLM/Codex verdicts into one MeetingRun correction action."""

    def decide(
        self,
        *,
        meeting_run_id: str,
        verdicts: tuple[ValidationVerdict, ...] | list[ValidationVerdict],
    ) -> ValidationDecision:
        verdict_tuple = tuple(verdicts)
        if not verdict_tuple:
            return ValidationDecision(
                meeting_run_id=meeting_run_id,
                kind=CorrectionActionKind.ASK_USER,
                next_state="paused",
                requires_user=True,
                rationale="missing validation evidence",
            )

        mismatched_ids = tuple(
            verdict.validation_id
            for verdict in verdict_tuple
            if verdict.meeting_run_id != meeting_run_id
        )
        if mismatched_ids:
            return ValidationDecision(
                meeting_run_id=meeting_run_id,
                kind=CorrectionActionKind.ASK_USER,
                next_state="paused",
                blocking_validation_ids=mismatched_ids,
                requires_user=True,
                rationale="validation verdict meeting_run_id mismatch",
            )

        required_actions = tuple(
            action for verdict in verdict_tuple for action in verdict.required_actions
        )

        rejected = self._ids_for(
            verdict_tuple, {ValidationVerdictValue.REJECT, ValidationVerdictValue.FAIL}
        )
        if rejected:
            return ValidationDecision(
                meeting_run_id=meeting_run_id,
                kind=CorrectionActionKind.STOP,
                next_state="failed",
                blocking_validation_ids=rejected,
                required_actions=required_actions,
                rationale="validator rejected the result",
            )

        escalated = self._ids_for(
            verdict_tuple,
            {ValidationVerdictValue.ESCALATE, ValidationVerdictValue.DEGRADED},
        )
        if escalated:
            return ValidationDecision(
                meeting_run_id=meeting_run_id,
                kind=CorrectionActionKind.ASK_USER,
                next_state="paused",
                blocking_validation_ids=escalated,
                required_actions=required_actions,
                requires_user=True,
                rationale="validator requires user decision or degraded fallback",
            )

        revised = self._ids_for(verdict_tuple, {ValidationVerdictValue.REVISE})
        if revised:
            return ValidationDecision(
                meeting_run_id=meeting_run_id,
                kind=CorrectionActionKind.REVISE,
                next_state="active",
                blocking_validation_ids=revised,
                required_actions=required_actions,
                follow_up_worker_required=True,
                rationale="validator requested correction loop",
            )

        return ValidationDecision(
            meeting_run_id=meeting_run_id,
            kind=CorrectionActionKind.CONTINUE,
            next_state="reporting",
            required_actions=required_actions,
            rationale="validation passed with no blocking verdicts",
        )

    @staticmethod
    def _ids_for(
        verdicts: tuple[ValidationVerdict, ...],
        values: set[ValidationVerdictValue],
    ) -> tuple[str, ...]:
        return tuple(
            verdict.validation_id for verdict in verdicts if verdict.verdict in values
        )


def _role_policy_for(role: str) -> ValidatorRolePolicy:
    if role == "codex_auditor":
        return ValidatorRolePolicy.codex_auditor()
    if role == "glm_validator":
        return ValidatorRolePolicy.glm_validator()
    return ValidatorRolePolicy(
        role=role,
        preferred_model=role,
        execution_role="validator",
        model_family=role,
        fallback_runner="none",
    )


def _model_for_role(role: str) -> str:
    return _role_policy_for(role).preferred_model


def build_degraded_verdict(
    *,
    validation_id: str,
    meeting_run_id: str,
    validator_role: str,
    validator_model: str,
    reason: str,
) -> ValidationVerdict:
    """Create an explicit degraded verdict for unavailable validators."""

    return ValidationVerdict(
        validation_id=validation_id,
        meeting_run_id=meeting_run_id,
        validator_role=validator_role,
        validator_model=validator_model,
        verdict=ValidationVerdictValue.DEGRADED,
        confidence=0.0,
        findings=("validator unavailable",),
        required_actions=("request user decision or retry validation",),
        degraded_reason=reason,
    )


__all__ = [
    "CorrectionActionKind",
    "ValidationDecision",
    "ValidationPolicy",
    "ValidatorExecutionPlan",
    "ValidatorExecutionPlanner",
    "ValidatorRolePolicy",
    "build_degraded_verdict",
]
