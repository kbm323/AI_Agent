"""Deterministic Runtime Architecture v2 MeetingRun orchestrator.

Phase 7 wires the existing domain policies together for a full fake/simulation
MeetingRun flow. It deliberately avoids live Discord, live model execution, and
custom queue storage; those remain behind explicit later-phase boundaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .policies import (
    ObservabilityPolicy,
    PolicyDecision,
    QuotaPolicy,
    SecurityPolicy,
)
from .projection import (
    DiscordProjectionFormatter,
    FakeDiscordProjectionSink,
    ProjectionPublishResult,
)
from .routing import FakeQwenRouter, RoutingAdapter
from .scheduling_policy import (
    SchedulingDecision,
    SchedulingPolicy,
    SchedulingRequest,
)
from .schemas import (
    DiscordProjectionEvent,
    MeetingRun,
    MeetingRunState,
    RecoveryCheckpoint,
    RoutingResult,
    ValidationVerdict,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from .store import MeetingRunStore
from .validation import ValidationDecision, ValidationPolicy
from .workers import FakeWorkerRunner, WorkerRunError, WorkerRunner


@dataclass(frozen=True)
class RuntimeOrchestratorResult:
    """Complete deterministic result of one orchestrated MeetingRun."""

    meeting_run: MeetingRun
    routing_result: RoutingResult
    scheduling_decision: SchedulingDecision
    worker_tasks: tuple[WorkerTask, ...]
    validation_verdicts: tuple[ValidationVerdict, ...]
    validation_decision: ValidationDecision
    projection_event: DiscordProjectionEvent
    projection_publish_result: ProjectionPublishResult
    checkpoint: RecoveryCheckpoint
    security_decision: PolicyDecision
    quota_decision: PolicyDecision


class RuntimeOrchestrator:
    """Run the deterministic MeetingRun flow from trigger to projection."""

    def __init__(
        self,
        *,
        root: str | Path,
        router: RoutingAdapter | None = None,
        scheduling_policy: SchedulingPolicy | None = None,
        worker_runner: WorkerRunner | None = None,
        validation_policy: ValidationPolicy | None = None,
        projection_formatter: DiscordProjectionFormatter | None = None,
        projection_sink: FakeDiscordProjectionSink | None = None,
        security_policy: SecurityPolicy | None = None,
        quota_policy: QuotaPolicy | None = None,
        observability_policy: ObservabilityPolicy | None = None,
        active_provider: str = "opencode-go",
    ) -> None:
        self.store = MeetingRunStore(root)
        self.router = router or FakeQwenRouter()
        self.scheduling_policy = scheduling_policy or SchedulingPolicy()
        self.worker_runner = worker_runner or FakeWorkerRunner()
        self.validation_policy = validation_policy or ValidationPolicy()
        self.projection_formatter = projection_formatter or DiscordProjectionFormatter()
        self.projection_sink = projection_sink or FakeDiscordProjectionSink()
        self.security_policy = security_policy or SecurityPolicy()
        self.quota_policy = quota_policy or QuotaPolicy()
        self.observability_policy = observability_policy or ObservabilityPolicy()
        self.active_provider = active_provider

    def run(
        self,
        *,
        meeting_run_id: str,
        trigger_text: str,
        user_id: str,
        channel_id: str,
        thread_id: str,
        guild_id: str = "",
        hermes_session_id: str = "",
        priority: str = "P2",
        simulation: bool = True,
    ) -> RuntimeOrchestratorResult:
        meeting_run = MeetingRun.create(
            meeting_run_id=meeting_run_id,
            trigger_text=trigger_text,
            user_id=user_id,
            channel_id=channel_id,
            thread_id=thread_id,
            guild_id=guild_id,
            hermes_session_id=hermes_session_id,
            priority=priority,
        )
        try:
            security_decision = self.security_policy.evaluate(meeting_run)
        except Exception:
            security_decision = PolicyDecision(
                allowed=False,
                reason="security_policy_exception",
                safe_summary="security policy failed closed; input redacted",
                next_state="paused",
                severity="warning",
            )
        self._append_observability_event(
            meeting_run,
            stage="security_gate",
            outcome=security_decision.reason,
            severity=security_decision.severity,
            detail=security_decision.safe_summary,
        )
        if not security_decision.allowed:
            quota_decision = PolicyDecision(
                allowed=True,
                reason="quota_not_evaluated_after_security_block",
                safe_summary="quota gate skipped after security block",
            )
            return self._policy_pause_result(
                meeting_run=meeting_run,
                channel_id=channel_id,
                thread_id=thread_id,
                reason=security_decision.reason,
                safe_summary=security_decision.safe_summary,
                gate="security_gate",
                security_decision=security_decision,
                quota_decision=quota_decision,
                simulation=simulation,
            )

        self.store.save_meeting_run(meeting_run)
        self.store.append_decision_event(
            meeting_run_id,
            {"event": "meeting_run_created", "state": str(meeting_run.state)},
        )

        routing_result = self.router.route(meeting_run)
        meeting_run = replace(
            meeting_run,
            state=MeetingRunState.ROUTED,
            routing_result=routing_result.to_dict(),
        )
        self.store.save_meeting_run(meeting_run)
        self.store.append_decision_event(
            meeting_run_id,
            {
                "event": "meeting_run_routed",
                "route_type": str(routing_result.route_type),
                "teams": list(routing_result.teams),
            },
        )

        scheduling_decision = self.scheduling_policy.decide(
            SchedulingRequest(
                meeting_run_id=meeting_run_id,
                route_type=str(routing_result.route_type),
                long_running=routing_result.execution_required,
                simulation=simulation,
            )
        )
        self.store.append_decision_event(
            meeting_run_id,
            {"event": "meeting_run_scheduled", **scheduling_decision.to_dict()},
        )

        try:
            quota_decision = self.quota_policy.evaluate(
                active_provider=self.active_provider
            )
        except Exception:
            quota_decision = PolicyDecision(
                allowed=False,
                reason="quota_policy_exception",
                safe_summary="quota policy failed closed before worker dispatch",
                next_state="paused",
                severity="warning",
            )
        self._append_observability_event(
            meeting_run,
            stage="quota_gate",
            outcome=quota_decision.reason,
            severity=quota_decision.severity,
            detail=quota_decision.safe_summary,
        )
        if not quota_decision.allowed:
            return self._policy_pause_result(
                meeting_run=meeting_run,
                channel_id=channel_id,
                thread_id=thread_id,
                reason=quota_decision.reason,
                safe_summary=quota_decision.safe_summary,
                gate="quota_gate",
                security_decision=security_decision,
                quota_decision=quota_decision,
                simulation=simulation,
                routing_result=routing_result,
                scheduling_decision=scheduling_decision,
            )

        worker_tasks = self._run_workers(meeting_run_id, routing_result)
        validation_verdicts = self._build_validation_verdicts(
            meeting_run_id=meeting_run_id,
            validators=routing_result.validators,
            worker_tasks=worker_tasks,
        )
        validation_decision = self.validation_policy.decide(
            meeting_run_id=meeting_run_id,
            verdicts=validation_verdicts,
        )
        self.store.append_audit_event(
            meeting_run_id,
            {
                "event": "validation_decided",
                "kind": str(validation_decision.kind),
                "next_state": validation_decision.next_state,
                "validation_ids": [
                    verdict.validation_id for verdict in validation_verdicts
                ],
            },
        )
        final_state = MeetingRunState(validation_decision.next_state)
        if final_state == MeetingRunState.REPORTING:
            final_state = MeetingRunState.COMPLETED

        projection_event = self._build_projection_event(
            meeting_run=meeting_run,
            final_state=final_state,
            routing_result=routing_result,
            validation_verdicts=validation_verdicts,
            validation_decision=validation_decision,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        projection_publish_result = self.projection_sink.publish(projection_event)
        self._save_projection_event(projection_event)

        checkpoint = RecoveryCheckpoint(
            checkpoint_id=f"chk_{meeting_run_id}_final",
            meeting_run_id=meeting_run_id,
            state=final_state,
            completed_worker_task_ids=tuple(
                task.worker_task_id
                for task in worker_tasks
                if task.state == WorkerTaskState.SUCCEEDED
            ),
            pending_worker_task_ids=tuple(
                task.worker_task_id
                for task in worker_tasks
                if task.state != WorkerTaskState.SUCCEEDED
            ),
            idempotency_key=f"{meeting_run_id}:{final_state.value}",
            note=validation_decision.rationale,
        )
        self.store.save_checkpoint(checkpoint)

        meeting_run = replace(
            meeting_run,
            state=final_state,
            worker_task_ids=tuple(task.worker_task_id for task in worker_tasks),
            validation_ids=tuple(
                verdict.validation_id for verdict in validation_verdicts
            ),
            projection_event_ids=(projection_event.event_id,),
            checkpoint_ids=(checkpoint.checkpoint_id,),
        )
        self.store.save_meeting_run(meeting_run)
        self.store.append_decision_event(
            meeting_run_id,
            {
                "event": "meeting_run_completed",
                "state": final_state.value,
                "projection_event_id": projection_event.event_id,
            },
        )

        return RuntimeOrchestratorResult(
            meeting_run=meeting_run,
            routing_result=routing_result,
            scheduling_decision=scheduling_decision,
            worker_tasks=worker_tasks,
            validation_verdicts=validation_verdicts,
            validation_decision=validation_decision,
            projection_event=projection_event,
            projection_publish_result=projection_publish_result,
            checkpoint=checkpoint,
            security_decision=security_decision,
            quota_decision=quota_decision,
        )

    def _policy_pause_result(
        self,
        *,
        meeting_run: MeetingRun,
        channel_id: str,
        thread_id: str,
        reason: str,
        safe_summary: str,
        gate: str,
        security_decision: PolicyDecision,
        quota_decision: PolicyDecision,
        simulation: bool,
        routing_result: RoutingResult | None = None,
        scheduling_decision: SchedulingDecision | None = None,
    ) -> RuntimeOrchestratorResult:
        routing_result = routing_result or RoutingResult(
            meeting_run_id=meeting_run.meeting_run_id,
            route_type="policy_blocked",
            validators=("glm_validator",),
            execution_required=False,
            projection_policy="validation_audit_only",
            confidence=1.0,
            rationale=reason,
        )
        scheduling_decision = scheduling_decision or self.scheduling_policy.decide(
            SchedulingRequest(
                meeting_run_id=meeting_run.meeting_run_id,
                route_type="policy_blocked",
                long_running=False,
                simulation=simulation,
            )
        )
        safe_trigger = dict(meeting_run.trigger)
        safe_trigger["text"] = safe_summary
        paused_run = replace(
            meeting_run,
            state=MeetingRunState.PAUSED,
            trigger=safe_trigger,
            routing_result=routing_result.to_dict(),
        )
        verdict = ValidationVerdict(
            validation_id=f"val_{meeting_run.meeting_run_id}_{gate}",
            meeting_run_id=meeting_run.meeting_run_id,
            validator_role="runtime_policy",
            validator_model="deterministic_policy",
            verdict="degraded",
            confidence=1.0,
            findings=(f"{gate}: {safe_summary}",),
            required_actions=(reason,),
            degraded_reason=reason,
        )
        validation_verdicts = (verdict,)
        validation_decision = self.validation_policy.decide(
            meeting_run_id=meeting_run.meeting_run_id,
            verdicts=validation_verdicts,
        )
        projection_event = self.projection_formatter.build_validation_event(
            event_id=f"proj_{meeting_run.meeting_run_id}",
            verdict=verdict,
            target_channel_id=channel_id,
            target_thread_id=thread_id,
        )
        projection_publish_result = self.projection_sink.publish(projection_event)
        self._save_projection_event(projection_event)
        checkpoint = RecoveryCheckpoint(
            checkpoint_id=f"chk_{meeting_run.meeting_run_id}_paused",
            meeting_run_id=meeting_run.meeting_run_id,
            state=MeetingRunState.PAUSED,
            idempotency_key=f"{meeting_run.meeting_run_id}:paused:{gate}",
            note=validation_decision.rationale,
        )
        self.store.save_checkpoint(checkpoint)
        paused_run = replace(
            paused_run,
            validation_ids=(verdict.validation_id,),
            projection_event_ids=(projection_event.event_id,),
            checkpoint_ids=(checkpoint.checkpoint_id,),
        )
        self.store.save_meeting_run(paused_run)
        self.store.append_audit_event(
            meeting_run.meeting_run_id,
            {
                "event": "policy_gate_paused",
                "gate": gate,
                "reason": reason,
                "safe_summary": safe_summary,
            },
        )
        self.store.append_audit_event(
            meeting_run.meeting_run_id,
            {
                "event": "validation_decided",
                "kind": str(validation_decision.kind),
                "next_state": validation_decision.next_state,
                "validation_ids": [verdict.validation_id],
            },
        )
        self.store.append_decision_event(
            meeting_run.meeting_run_id,
            {
                "event": "meeting_run_paused",
                "state": MeetingRunState.PAUSED.value,
                "gate": gate,
                "projection_event_id": projection_event.event_id,
            },
        )
        return RuntimeOrchestratorResult(
            meeting_run=paused_run,
            routing_result=routing_result,
            scheduling_decision=scheduling_decision,
            worker_tasks=(),
            validation_verdicts=validation_verdicts,
            validation_decision=validation_decision,
            projection_event=projection_event,
            projection_publish_result=projection_publish_result,
            checkpoint=checkpoint,
            security_decision=security_decision,
            quota_decision=quota_decision,
        )

    def _append_observability_event(
        self,
        run: MeetingRun,
        *,
        stage: str,
        outcome: str,
        severity: str,
        detail: str,
    ) -> None:
        self.store.append_audit_event(
            run.meeting_run_id,
            self.observability_policy.event(
                run,
                stage=stage,
                outcome=outcome,
                severity=severity,
                detail=detail,
            ),
        )

    def _run_workers(
        self,
        meeting_run_id: str,
        routing_result: RoutingResult,
    ) -> tuple[WorkerTask, ...]:
        tasks: list[WorkerTask] = []
        for index, role in enumerate(routing_result.worker_roles, start=1):
            task_id = f"wt_{meeting_run_id}_{index}"
            task = WorkerTask(
                worker_task_id=task_id,
                meeting_run_id=meeting_run_id,
                role=role,
                runner=WorkerTaskRunner.OPENCODE_GO,
                packet_path=str(
                    self.store.meeting_run_dir(meeting_run_id)
                    / "packets"
                    / f"{task_id}.json"
                ),
                output_path=str(
                    self.store.meeting_run_dir(meeting_run_id)
                    / "worker_outputs"
                    / f"{task_id}.json"
                ),
            )
            try:
                running = self.worker_runner.dispatch(task)
                collected = self.worker_runner.collect(running)
            except Exception as exc:
                error_code = (
                    exc.code
                    if isinstance(exc, WorkerRunError) and exc.code
                    else "worker_runner_exception"
                )
                collected = replace(
                    task,
                    state=WorkerTaskState.FAILED,
                    error=error_code,
                )
            if collected.state != WorkerTaskState.SUCCEEDED:
                self.store.append_audit_event(
                    meeting_run_id,
                    {
                        "event": "worker_failed",
                        "worker_task_id": collected.worker_task_id,
                        "role": collected.role,
                        "state": str(collected.state),
                        "error": collected.error,
                    },
                )
            tasks.append(collected)
        return tuple(tasks)

    @staticmethod
    def _build_validation_verdicts(
        *,
        meeting_run_id: str,
        validators: tuple[str, ...],
        worker_tasks: tuple[WorkerTask, ...],
    ) -> tuple[ValidationVerdict, ...]:
        validator_roles = validators or ("glm_validator",)
        failed_tasks = tuple(
            task for task in worker_tasks if task.state != WorkerTaskState.SUCCEEDED
        )
        verdicts: list[ValidationVerdict] = []
        for index, validator_role in enumerate(validator_roles, start=1):
            validation_id = f"val_{meeting_run_id}_{index}"
            if failed_tasks:
                findings = tuple(
                    task.error or f"worker {task.worker_task_id} did not succeed"
                    for task in failed_tasks
                )
                verdicts.append(
                    ValidationVerdict(
                        validation_id=validation_id,
                        meeting_run_id=meeting_run_id,
                        validator_role=validator_role,
                        validator_model=_model_for_validator(validator_role),
                        verdict="reject",
                        confidence=0.0,
                        findings=findings,
                        required_actions=("inspect failed worker task",),
                    )
                )
                continue
            verdicts.append(
                ValidationVerdict(
                    validation_id=validation_id,
                    meeting_run_id=meeting_run_id,
                    validator_role=validator_role,
                    validator_model=_model_for_validator(validator_role),
                    verdict="pass",
                    confidence=1.0,
                    findings=("deterministic fake flow completed",),
                )
            )
        return tuple(verdicts)

    def _build_projection_event(
        self,
        *,
        meeting_run: MeetingRun,
        final_state: MeetingRunState,
        routing_result: RoutingResult,
        validation_verdicts: tuple[ValidationVerdict, ...],
        validation_decision: ValidationDecision,
        channel_id: str,
        thread_id: str,
    ) -> DiscordProjectionEvent:
        if validation_decision.next_state == MeetingRunState.FAILED.value:
            return self.projection_formatter.build_validation_event(
                event_id=f"proj_{meeting_run.meeting_run_id}",
                verdict=validation_verdicts[0],
                target_channel_id=channel_id,
                target_thread_id=thread_id,
            )
        return self.projection_formatter.build_summary_event(
            event_id=f"proj_{meeting_run.meeting_run_id}",
            run=meeting_run,
            state=final_state,
            routing=routing_result,
            verdicts=validation_verdicts,
            target_channel_id=channel_id,
            target_thread_id=thread_id,
            raw_worker_outputs=(),
        )

    def _save_projection_event(self, event: DiscordProjectionEvent) -> Path:
        path = (
            self.store.meeting_run_dir(event.meeting_run_id)
            / "discord_projection"
            / f"{event.event_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(event.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return path


def _model_for_validator(validator_role: str) -> str:
    if validator_role == "codex_auditor":
        return "codex"
    if validator_role == "glm_validator":
        return "glm-5.1"
    return validator_role


__all__ = ["RuntimeOrchestrator", "RuntimeOrchestratorResult"]
