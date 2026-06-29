"""Phase 13 live company workflow pilot.

This module connects one bounded company request into the existing Runtime
Architecture v2 boundaries. It is intentionally a pilot layer, not a new queue,
router, gateway, or production autonomy loop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .model_policy import worker_model_policy_for_role
from .policies import redact_sensitive_text
from .projection import (
    DiscordLiveBoundaryPolicy,
    DiscordProjectionFormatter,
    FakeDiscordProjectionSink,
    LiveDiscordProjectionSink,
    ProjectionPublishResult,
)
from .scheduling_policy import SchedulingPolicy, SchedulingRequest
from .schemas import (
    MeetingRun,
    MeetingRunState,
    RecoveryCheckpoint,
    RoutingResult,
    ValidationVerdict,
    ValidationVerdictValue,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from .store import MeetingRunStore
from .validation import ValidationPolicy
from .workers import (
    FakeWorkerRunner,
    OpenCodeGoCommandRunner,
    OpenCodeGoWorkerRunner,
    WorkerRunner,
)

Phase13Mode = Literal["dry-run", "live-worker"]

PILOT_ID = "phase13_live_company_workflow_pilot"
PILOT_TRIGGER_TEXT = (
    "AI virtual entertainment company의 다음 콘텐츠 아이디어 하나를 회의하고, "
    "실행 가능성/마케팅 포인트/검증 리스크를 한 페이지로 정리해줘."
)


@dataclass(frozen=True)
class Phase13PilotModeError(Exception):
    """Fail-closed pilot mode validation error."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class Phase13Route:
    """Pilot-only role route that also exposes a Runtime v2 RoutingResult."""

    primary_role: str
    supporting_roles: tuple[str, ...]
    live_worker_roles: tuple[str, ...]
    fake_worker_roles: tuple[str, ...]
    routing_result: RoutingResult


@dataclass(frozen=True)
class Phase13PilotResult:
    """Structured result returned by the CLI and tests."""

    pilot_id: str
    mode: str
    ok: bool
    meeting_run: MeetingRun
    route: Phase13Route
    worker_tasks: tuple[WorkerTask, ...]
    validation_verdicts: tuple[ValidationVerdict, ...]
    report_path: str
    projection_publish_result: ProjectionPublishResult
    live_worker_count: int
    fake_worker_count: int
    error: str = ""

    def to_cli_dict(self) -> dict[str, object]:
        return {
            "pilot_id": self.pilot_id,
            "mode": self.mode,
            "meeting_run_id": self.meeting_run.meeting_run_id,
            "top_level_state": str(self.meeting_run.state),
            "live_worker_count": self.live_worker_count,
            "fake_worker_count": self.fake_worker_count,
            "worker_task_ids": [task.worker_task_id for task in self.worker_tasks],
            "validation_ids": [
                verdict.validation_id for verdict in self.validation_verdicts
            ],
            "report_path": self.report_path,
            "projection_status": self.projection_publish_result.status,
            "projection_message_id": self.projection_publish_result.discord_message_id,
            "error": self.error,
            "ok": self.ok,
        }


def build_phase13_pilot_request() -> dict[str, object]:
    """Return the canonical deterministic Phase 13 pilot request."""

    return {
        "pilot_id": PILOT_ID,
        "trigger_text": PILOT_TRIGGER_TEXT,
        "user_id": "phase13-user",
        "channel_id": "phase13-channel",
        "thread_id": "",
        "guild_id": "",
        "priority": "P2",
        "live_worker_roles": ["content_lead"],
        "fake_support_roles": ["marketing_lead", "quality_lead"],
    }


def create_phase13_meeting_run(
    root: str | Path,
    request: Mapping[str, object] | None = None,
) -> MeetingRun:
    """Create and persist the initial pilot MeetingRun under runtime/."""

    request = dict(request or build_phase13_pilot_request())
    meeting_run_id = _new_phase13_meeting_run_id(
        str(request.get("pilot_id") or PILOT_ID)
    )
    run = MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text=str(request["trigger_text"]),
        user_id=str(request.get("user_id") or "phase13-user"),
        channel_id=str(request.get("channel_id") or "phase13-channel"),
        thread_id=str(request.get("thread_id") or ""),
        guild_id=str(request.get("guild_id") or ""),
        priority=str(request.get("priority") or "P2"),
    )
    run = replace(
        run,
        metadata={
            "pilot_id": str(request.get("pilot_id") or PILOT_ID),
            "phase": "13",
            "scope": "live_company_workflow_pilot",
            "production_claim": False,
        },
    )
    store = MeetingRunStore(root)
    store.save_meeting_run(run)
    store.append_decision_event(
        run.meeting_run_id,
        {"event": "phase13_pilot_created", "pilot_id": PILOT_ID},
    )
    return run


def build_phase13_route(run: MeetingRun) -> Phase13Route:
    """Build the pilot-only company route without adding a new router engine."""

    routing_result = RoutingResult(
        meeting_run_id=run.meeting_run_id,
        route_type="creative_meeting",
        teams=("content_lead", "marketing_lead", "quality_lead"),
        worker_roles=("content_lead", "marketing_lead", "quality_lead"),
        validators=("quality_lead",),
        research_owner="content_lead",
        execution_required=True,
        estimated_rounds=1,
        projection_policy="summary_only",
        confidence=0.98,
        rationale=(
            "Phase 13 deterministic pilot route: one live content worker with "
            "fake marketing and quality support roles."
        ),
    )
    return Phase13Route(
        primary_role="content_lead",
        supporting_roles=("marketing_lead", "quality_lead"),
        live_worker_roles=("content_lead",),
        fake_worker_roles=("marketing_lead", "quality_lead"),
        routing_result=routing_result,
    )


def build_phase13_worker_tasks(
    run: MeetingRun,
    route: Phase13Route,
    root: str | Path,
    *,
    live_worker_count: int = 1,
) -> tuple[WorkerTask, ...]:
    """Create one content live task plus fake support task references."""

    if live_worker_count < 0 or live_worker_count > 1:
        raise Phase13PilotModeError(
            code="invalid_live_worker_count",
            message="Phase 13 permits at most one live worker",
        )
    root = Path(root)
    roles = (*route.live_worker_roles, *route.fake_worker_roles)
    live_roles = set(route.live_worker_roles[:live_worker_count])
    tasks = []
    for index, role in enumerate(roles, start=1):
        runner = (
            WorkerTaskRunner.OPENCODE_GO
            if role in live_roles
            else WorkerTaskRunner.HERMES_WRAPPER
        )
        task_id = f"wt_{run.meeting_run_id}_{index}_{role}"
        run_dir = root / "runtime" / "meeting_runs" / run.meeting_run_id
        tasks.append(
            WorkerTask(
                worker_task_id=task_id,
                meeting_run_id=run.meeting_run_id,
                role=role,
                runner=runner,
                packet_path=str(run_dir / "packets" / f"{task_id}.json"),
                output_path=str(run_dir / "worker_outputs" / f"{task_id}.json"),
                model_policy=_model_policy_for_role(role, runner),
            )
        )
    return tuple(tasks)


def run_phase13_pilot(
    *,
    root: str | Path,
    mode: Phase13Mode = "dry-run",
    max_live_workers: int = 0,
    command_runner: OpenCodeGoCommandRunner | None = None,
    live_discord: bool = False,
    env: Mapping[str, str] | None = None,
    target_channel_id: str = "phase13-channel",
    discord_http_post: Callable[..., Mapping[str, object]] | None = None,
) -> Phase13PilotResult:
    """Run the bounded Phase 13 workflow pilot."""

    if mode not in {"dry-run", "live-worker"}:
        raise Phase13PilotModeError("invalid_mode", f"unsupported mode: {mode}")
    if mode == "dry-run" and max_live_workers != 0:
        raise Phase13PilotModeError(
            "invalid_live_worker_count",
            "dry-run may only use zero live workers",
        )
    if mode == "dry-run" and live_discord:
        raise Phase13PilotModeError(
            "invalid_live_discord_mode",
            "dry-run cannot publish through live Discord projection",
        )
    if mode == "live-worker" and max_live_workers != 1:
        raise Phase13PilotModeError(
            "invalid_live_worker_count",
            "live-worker mode requires --max-live-workers 1",
        )

    root = Path(root)
    request = build_phase13_pilot_request()
    run = create_phase13_meeting_run(root, request)
    store = MeetingRunStore(root)
    route = build_phase13_route(run)
    live_count = 0 if mode == "dry-run" else max_live_workers
    worker_tasks = build_phase13_worker_tasks(
        run,
        route,
        root,
        live_worker_count=live_count,
    )
    run = replace(
        run,
        state=MeetingRunState.ROUTED,
        routing_result=route.routing_result.to_dict(),
        worker_task_ids=tuple(task.worker_task_id for task in worker_tasks),
    )
    store.save_meeting_run(run)
    scheduling_decision = SchedulingPolicy().decide(
        SchedulingRequest(
            meeting_run_id=run.meeting_run_id,
            route_type=route.routing_result.route_type,
            long_running=mode == "live-worker",
            simulation=mode == "dry-run",
        )
    )
    store.append_decision_event(
        run.meeting_run_id,
        {"event": "phase13_pilot_scheduled", **scheduling_decision.to_dict()},
    )
    store.append_decision_event(
        run.meeting_run_id,
        {
            "event": "phase13_pilot_routed",
            "live_worker_count": live_count,
            "fake_worker_count": len(worker_tasks) - live_count,
        },
    )

    completed_tasks = _execute_phase13_tasks(
        worker_tasks,
        mode=mode,
        command_runner=command_runner,
        workdir=str(root),
    )
    ok_workers = all(
        task.state == WorkerTaskState.SUCCEEDED for task in completed_tasks
    )
    validation_verdicts = _build_phase13_validation_verdicts(run, completed_tasks)
    validation_decision = ValidationPolicy().decide(
        meeting_run_id=run.meeting_run_id,
        verdicts=validation_verdicts,
    )
    final_state = MeetingRunState.COMPLETED
    error = ""
    if not ok_workers:
        final_state = MeetingRunState.FAILED
        error = "worker_execution_failed"
    elif validation_decision.next_state not in {"reporting", "completed"}:
        final_state = MeetingRunState(validation_decision.next_state)
        error = validation_decision.rationale

    run = replace(
        run,
        state=final_state,
        validation_ids=tuple(verdict.validation_id for verdict in validation_verdicts),
    )
    report_path = _write_phase13_report(
        root=root,
        run=run,
        route=route,
        worker_tasks=completed_tasks,
        validation_verdicts=validation_verdicts,
        mode=mode,
        live_discord=live_discord,
    )
    projection_result = _publish_or_record_projection(
        root=root,
        run=run,
        route=route,
        validation_verdicts=validation_verdicts,
        target_channel_id=target_channel_id,
        live_discord=live_discord,
        env=env,
        discord_http_post=discord_http_post,
    )
    if live_discord and projection_result.status != "published":
        final_state = MeetingRunState.FAILED
        error = "live_discord_publish_blocked"
        run = replace(run, state=final_state)
    checkpoint = RecoveryCheckpoint(
        checkpoint_id=f"chk_{run.meeting_run_id}_phase13_final",
        meeting_run_id=run.meeting_run_id,
        state=final_state,
        completed_worker_task_ids=tuple(
            task.worker_task_id
            for task in completed_tasks
            if task.state == WorkerTaskState.SUCCEEDED
        ),
        pending_worker_task_ids=tuple(
            task.worker_task_id
            for task in completed_tasks
            if task.state != WorkerTaskState.SUCCEEDED
        ),
        idempotency_key=f"{run.meeting_run_id}:phase13:{mode}",
        note="Phase 13 pilot completed within bounded workflow surface.",
    )
    store.save_checkpoint(checkpoint)
    run = replace(
        run,
        projection_event_ids=(f"proj_{run.meeting_run_id}_phase13_summary",),
        checkpoint_ids=(checkpoint.checkpoint_id,),
    )
    store.save_meeting_run(run)
    store.append_decision_event(
        run.meeting_run_id,
        {
            "event": "phase13_pilot_completed",
            "state": final_state.value,
            "report_path": report_path,
            "live_discord": live_discord,
        },
    )
    return Phase13PilotResult(
        pilot_id=PILOT_ID,
        mode=mode,
        ok=(final_state == MeetingRunState.COMPLETED and not error),
        meeting_run=run,
        route=route,
        worker_tasks=completed_tasks,
        validation_verdicts=validation_verdicts,
        report_path=report_path,
        projection_publish_result=projection_result,
        live_worker_count=live_count,
        fake_worker_count=len(completed_tasks) - live_count,
        error=error,
    )


def _execute_phase13_tasks(
    tasks: tuple[WorkerTask, ...],
    *,
    mode: str,
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: str,
) -> tuple[WorkerTask, ...]:
    completed = []
    for task in tasks:
        runner: WorkerRunner
        if task.runner == WorkerTaskRunner.OPENCODE_GO and mode == "live-worker":
            runner = OpenCodeGoWorkerRunner(
                command_runner=command_runner,
                timeout_seconds=600,
                workdir=workdir,
            )
        else:
            runner = FakeWorkerRunner(output=_fake_output_for_role(task.role))
        dispatched = runner.dispatch(task)
        completed.append(runner.collect(dispatched))
    return tuple(completed)


def _build_phase13_validation_verdicts(
    run: MeetingRun,
    worker_tasks: tuple[WorkerTask, ...],
) -> tuple[ValidationVerdict, ...]:
    failed = tuple(
        task for task in worker_tasks if task.state != WorkerTaskState.SUCCEEDED
    )
    if failed:
        return (
            ValidationVerdict(
                validation_id=f"val_{run.meeting_run_id}_quality_lead",
                meeting_run_id=run.meeting_run_id,
                validator_role="quality_lead",
                validator_model="policy_fake",
                verdict=ValidationVerdictValue.REJECT,
                confidence=1.0,
                findings=("one or more worker tasks failed",),
                required_actions=("inspect structured worker output",),
            ),
        )
    return (
        ValidationVerdict(
            validation_id=f"val_{run.meeting_run_id}_quality_lead",
            meeting_run_id=run.meeting_run_id,
            validator_role="quality_lead",
            validator_model="policy_fake",
            verdict=ValidationVerdictValue.PASS,
            confidence=0.92,
            findings=(
                "pilot output contains content, marketing, and validation sections",
                "live surface stayed within Phase 13 max-one-worker guardrail",
            ),
            required_actions=("document remaining unproven production surfaces",),
        ),
    )


def _write_phase13_report(
    *,
    root: Path,
    run: MeetingRun,
    route: Phase13Route,
    worker_tasks: tuple[WorkerTask, ...],
    validation_verdicts: tuple[ValidationVerdict, ...],
    mode: str,
    live_discord: bool,
) -> str:
    report_path = (
        root / "runtime" / "meeting_runs" / run.meeting_run_id / "final_report.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 13 Pilot Report",
        "",
        "## Request",
        str(run.trigger.get("text") or ""),
        "",
        "## Route",
        (
            f"- Content Lead: {route.primary_role} "
            f"({'live' if mode == 'live-worker' else 'fake'})"
        ),
        "- Marketing Lead: marketing_lead (fake support)",
        "- Quality Lead: quality_lead (policy/fake validation)",
        "",
        "## Output",
        *_worker_report_lines(worker_tasks),
        "",
        "## Validation",
        *(
            f"- {verdict.validator_role}: {verdict.verdict} "
            f"({'; '.join(verdict.findings)})"
            for verdict in validation_verdicts
        ),
        "",
        "## Boundaries",
        f"- mode: {mode}",
        f"- live Discord projection attempted: {live_discord}",
        "- max live workers: 1",
        "- long-running autonomy: not attempted",
        "- full Discord app interaction e2e: not claimed",
    ]
    report_path.write_text(_sanitize_report("\n".join(lines) + "\n"), encoding="utf-8")
    return str(report_path)


def _publish_or_record_projection(
    *,
    root: Path,
    run: MeetingRun,
    route: Phase13Route,
    validation_verdicts: tuple[ValidationVerdict, ...],
    target_channel_id: str,
    live_discord: bool,
    env: Mapping[str, str] | None,
    discord_http_post: Callable[..., Mapping[str, object]] | None = None,
) -> ProjectionPublishResult:
    formatter = DiscordProjectionFormatter()
    event = formatter.build_summary_event(
        event_id=f"proj_{run.meeting_run_id}_phase13_summary",
        run=run,
        state=run.state,
        routing=route.routing_result,
        verdicts=validation_verdicts,
        target_channel_id=target_channel_id,
    )
    if live_discord:
        boundary_policy = DiscordLiveBoundaryPolicy.current_verified()
        sink = LiveDiscordProjectionSink(
            env=env,
            http_post=discord_http_post,
            boundary_policy=boundary_policy,
            profile="aicompanycontent",
            guild_id=boundary_policy.guild_id,
        )
    else:
        sink = FakeDiscordProjectionSink()
    result = sink.publish(event)
    projection_dir = (
        root / "runtime" / "meeting_runs" / run.meeting_run_id / "discord_projection"
    )
    projection_dir.mkdir(parents=True, exist_ok=True)
    (projection_dir / f"{event.event_id}.json").write_text(
        json.dumps(
            {
                "event": event.to_dict(),
                "publish_result": {
                    "event_id": result.event_id,
                    "status": result.status,
                    "discord_message_id": result.discord_message_id,
                    "error": result.error,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def _worker_report_lines(worker_tasks: tuple[WorkerTask, ...]) -> list[str]:
    lines = []
    for task in worker_tasks:
        status = str(task.state)
        output_summary = _output_summary(task)
        lines.append(f"- {task.role}: {status} — {output_summary}")
    return lines


def _output_summary(task: WorkerTask) -> str:
    path = Path(task.output_path)
    if not path.exists():
        return "no output file"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "invalid structured output"
    if payload.get("status") == "succeeded":
        result = payload.get("result")
        if isinstance(result, dict):
            return str(
                result.get("summary")
                or result.get("answer")
                or "structured output written"
            )
        stdout_summary = _summarize_opencode_stdout(str(payload.get("stdout") or ""))
        if stdout_summary:
            return stdout_summary
        content_summary = _summarize_opencode_stdout(str(payload.get("content") or ""))
        if content_summary:
            return content_summary
        content = str(payload.get("content") or "").strip()
        if content and "\n" not in content:
            return _sanitize_report(content)[:240]
        return "structured output written"
    return str(payload.get("error") or payload.get("status") or "failed")


def _summarize_opencode_stdout(stdout: str) -> str:
    """Extract human text from opencode-go JSONL events without raw event dumps."""

    text_parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for key in ("text", "content", "message"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(_sanitize_report(value.strip()))
        part = event.get("part")
        if isinstance(part, dict):
            value = part.get("text") or part.get("content")
            if isinstance(value, str) and value.strip():
                text_parts.append(_sanitize_report(value.strip()))
    summary = text_parts[-1] if text_parts else ""
    content_match = re.search(
        r"(?is)\*\*Content Idea Proposed:\*\*\s*(.+?)(?:\n\n|$)",
        summary,
    )
    if content_match:
        summary = content_match.group(1).strip()
    return summary[:240]


def _fake_output_for_role(role: str) -> dict[str, object]:
    if role == "content_lead":
        return {
            "summary": "가상 아이돌의 제작 비하인드 숏폼 시리즈 아이디어를 제안했다.",
            "content_idea": "팬이 다음 에피소드 소품을 투표로 고르는 인터랙티브 쇼츠.",
        }
    if role == "marketing_lead":
        return {
            "summary": "팬 참여형 투표와 제작 로그를 마케팅 포인트로 정리했다.",
            "channels": ["Discord", "YouTube Shorts", "X"],
        }
    return {
        "summary": "저작권, 과장 광고, 팬 개인정보 입력을 검증 리스크로 표시했다.",
        "risk_level": "bounded_pilot",
    }


def _model_policy_for_role(
    role: str,
    runner: WorkerTaskRunner,
) -> dict[str, object]:
    policy = worker_model_policy_for_role(role)
    policy["runner"] = str(runner.value if hasattr(runner, "value") else runner)
    if runner == WorkerTaskRunner.OPENCODE_GO:
        policy["phase13_live_worker"] = role == "content_lead"
        return policy
    policy["phase13_fake"] = True
    return policy


def _new_phase13_meeting_run_id(pilot_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{_safe_meeting_run_id(pilot_id)}_{timestamp}"


def _safe_meeting_run_id(pilot_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", pilot_id.strip())
    return normalized or PILOT_ID


def _sanitize_report(content: str) -> str:
    redacted = redact_sensitive_text(content)
    redacted = re.sub(r"(?i)api[_-]?key\s*[:=]\s*\S+", "api_key=[REDACTED]", redacted)
    redacted = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", redacted)
    return (
        redacted.replace("@everyone", "@ everyone")
        .replace("@here", "@ here")
        .replace("raw_worker_outputs", "worker output dumps")
        .replace("sessionID", "session_id")
    )


__all__ = [
    "PILOT_ID",
    "Phase13PilotModeError",
    "Phase13PilotResult",
    "Phase13Route",
    "build_phase13_pilot_request",
    "build_phase13_route",
    "build_phase13_worker_tasks",
    "create_phase13_meeting_run",
    "run_phase13_pilot",
]
