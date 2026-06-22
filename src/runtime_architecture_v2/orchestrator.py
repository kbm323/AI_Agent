"""Deterministic Runtime Architecture v2 MeetingRun orchestrator.

Phase 7 wires the existing domain policies together for a full fake/simulation
MeetingRun flow. It deliberately avoids live Discord, live model execution, and
custom queue storage; those remain behind explicit later-phase boundaries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .projection import (
    DiscordProjectionFormatter,
    FakeDiscordProjectionSink,
    ProjectionPublishResult,
)
from .routing import FakeQwenRouter, RoutingAdapter
from .scheduling_policy import SchedulingDecision, SchedulingPolicy, SchedulingRequest
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
from .workers import FakeWorkerRunner, WorkerRunner


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
    ) -> None:
        self.store = MeetingRunStore(root)
        self.router = router or FakeQwenRouter()
        self.scheduling_policy = scheduling_policy or SchedulingPolicy()
        self.worker_runner = worker_runner or FakeWorkerRunner()
        self.validation_policy = validation_policy or ValidationPolicy()
        self.projection_formatter = projection_formatter or DiscordProjectionFormatter()
        self.projection_sink = projection_sink or FakeDiscordProjectionSink()

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
                collected = replace(
                    task,
                    state=WorkerTaskState.FAILED,
                    error=str(exc),
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
