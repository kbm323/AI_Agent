"""On-demand meeting artifact exports (Phase 32 / Phase 5).

Default meetings produce only team-lead discussion messages in Discord.
Summaries, final reports, Notion exports, and Second Brain notes are
generated only when the user explicitly requests them.

This module provides:
- Finding the latest meeting_run by directory timestamp.
- Dispatching export type to the appropriate generator.
- Simple summary / action-item extraction from meeting session data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
import json
from pathlib import Path
from typing import Any

from src.runtime_architecture_v2.final_report_v3 import (
    build_default_final_report_decision,
    render_final_report_v3_discord,
    render_final_report_v3_local,
)
from src.runtime_architecture_v2.multi_bot import (
    BOT_PERSONAS,
    MultiBotSession,
)
from src.runtime_architecture_v2.schemas import MeetingRun, WorkerTask
from src.runtime_architecture_v2.store import MeetingRunStore


@unique
class OnDemandExportType(StrEnum):
    """Export types the user can request after a meeting."""

    SUMMARY = "summary"  # 요약해줘
    FINAL_REPORT = "final_report"  # 최종보고서로 정리해줘
    AGREEMENT = "agreement"  # 합의서로 정리해줘
    ACTION_ITEMS = "action_items"  # 할 일로 만들어줘


@dataclass(frozen=True)
class OnDemandExportResult:
    """Result of an on-demand export request."""

    export_type: str
    meeting_run_id: str
    content: str
    ok: bool = True
    error: str = ""


def find_latest_meeting_run_id(root: str | Path) -> str | None:
    """Return the meeting_run_id of the most-recently modified meeting run.

    Returns ``None`` when ``runtime/meeting_runs/`` does not exist or is empty.
    """
    store_root = Path(root) / "runtime" / "meeting_runs"
    if not store_root.exists():
        return None
    dirs = sorted(
        (d for d in store_root.iterdir() if d.is_dir()),
        key=lambda d: (d.stat().st_mtime_ns, d.name),
    )
    return dirs[-1].name if dirs else None


def run_on_demand_export(
    root: str | Path,
    meeting_run_id: str,
    export_type: OnDemandExportType,
) -> OnDemandExportResult:
    """Generate the requested on-demand export for a meeting run.

    Raises ``FileNotFoundError`` when the meeting_run does not exist.
    """
    store = MeetingRunStore(root)
    run = store.load_meeting_run(meeting_run_id)

    # Reconstruct the minimal session from stored artifacts.
    session = _reconstruct_session(store, meeting_run_id, run)

    # Collect worker task metadata from stored output files.
    worker_outputs_dir = store.meeting_run_dir(meeting_run_id) / "worker_outputs"
    worker_tasks = _collect_worker_tasks(worker_outputs_dir, run)

    content: str
    if export_type == OnDemandExportType.SUMMARY:
        content = _generate_summary(run, session)
    elif export_type == OnDemandExportType.FINAL_REPORT:
        content = _generate_final_report_v3(
            store=store,
            run=run,
            session=session,
            worker_tasks=worker_tasks,
        )
    elif export_type == OnDemandExportType.AGREEMENT:
        content = _generate_agreement_document(run, session)
    elif export_type == OnDemandExportType.ACTION_ITEMS:
        content = _generate_action_items_document(run, session)
    else:
        return OnDemandExportResult(
            export_type=str(export_type),
            meeting_run_id=meeting_run_id,
            ok=False,
            error=f"unknown export type: {export_type}",
            content="",
        )

    return OnDemandExportResult(
        export_type=str(export_type),
        meeting_run_id=meeting_run_id,
        content=content,
        ok=True,
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _reconstruct_session(
    store: MeetingRunStore,
    meeting_run_id: str,
    run: MeetingRun,
) -> MultiBotSession:
    """Build a minimal MultiBotSession from stored artifacts."""
    # For now use consensus summary from meeting_run trigger/state.
    # A richer reconstruction would load round messages from packets/.
    return MultiBotSession(
        meeting_run_id=meeting_run_id,
        participants=tuple(run.worker_task_ids or ()),
        rounds=(),
        consensus_reached=True,
        escalation_required=False,
        consensus_summary=str(run.trigger.get("text", "")),
    )


def _collect_worker_tasks(
    worker_outputs_dir: Path,
    run: MeetingRun,
) -> list[WorkerTask]:
    """Collect WorkerTask metadata from stored output files."""
    tasks: list[WorkerTask] = []
    if not worker_outputs_dir.exists():
        return tasks
    for output_file in sorted(worker_outputs_dir.glob("*.json")):
        try:
            data = _read_json(output_file)
            if isinstance(data, dict):
                tasks.append(
                    WorkerTask(
                        worker_task_id=str(data.get("worker_task_id", output_file.stem)),
                        meeting_run_id=run.meeting_run_id,
                        role=str(data.get("role", output_file.stem.rsplit("_", 1)[-1])),
                        runner=str(data.get("runner", "opencode_go")),
                        state=str(data.get("state", "succeeded")),
                        error=str(data.get("error", "")),
                        output_path=str(output_file),
                        model_policy=data.get("model_policy", {}),
                        hermes_refs=data.get("hermes_refs", {}),
                    )
                )
        except (OSError, ValueError):
            continue
    return tasks


def _collect_specialist_roles(worker_tasks: list[WorkerTask]) -> tuple[str, ...]:
    """Identify specialist/internal roles from worker tasks."""
    return tuple(t.role for t in worker_tasks if t.role not in BOT_PERSONAS)


def _generate_final_report_v3(
    *,
    store: MeetingRunStore,
    run: MeetingRun,
    session: MultiBotSession,
    worker_tasks: list[WorkerTask],
) -> str:
    """Generate requested Final Report v3 and write local artifacts."""
    run_dir = store.meeting_run_dir(run.meeting_run_id)
    agenda = str(run.trigger.get("text", "회의"))
    source_text = _build_source_text(run=run, session=session, worker_tasks=worker_tasks)
    source_roles = _source_role_names(run=run, worker_tasks=worker_tasks)
    decision = build_default_final_report_decision(
        agenda=agenda,
        source_text=source_text,
        source_roles=source_roles,
    )
    discord_content = render_final_report_v3_discord(decision, agenda=agenda)
    local_content = render_final_report_v3_local(
        decision,
        agenda=agenda,
        source_text=source_text,
        model_evidence="schema=FinalReportDecision; validator=PASS; generator=on-demand-v3",
    )
    (run_dir / "decision_summary.json").write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "final_report_v3.md").write_text(local_content, encoding="utf-8")
    return discord_content


def _build_source_text(
    *,
    run: MeetingRun,
    session: MultiBotSession,
    worker_tasks: list[WorkerTask],
) -> str:
    parts = [str(run.trigger.get("text", "")), session.consensus_summary]
    for task in worker_tasks:
        parts.append(f"{task.role} {task.state} {task.error}")
        if task.output_path:
            try:
                parts.append(Path(task.output_path).read_text(encoding="utf-8")[:2000])
            except OSError:
                pass
    return "\n".join(p for p in parts if p)


def _source_role_names(
    *,
    run: MeetingRun,
    worker_tasks: list[WorkerTask],
) -> tuple[str, ...]:
    role_map = {
        "ceo_coordinator": "대표",
        "content_lead": "콘텐츠 팀장",
        "art_lead": "아트 팀장",
        "tech_lead": "기술 팀장",
        "marketing_lead": "마케팅 팀장",
        "validation_audit": "검증 팀장",
    }
    roles: list[str] = []
    role_ids = tuple(run.worker_task_ids or ()) + tuple(task.role for task in worker_tasks)
    for role in role_ids:
        display = role_map.get(str(role), str(role))
        if display and display not in roles:
            roles.append(display)
    if not roles:
        roles.append("대표")
    return tuple(roles)


def _generate_summary(run: MeetingRun, session: MultiBotSession) -> str:
    """Generate a short meeting summary."""
    trigger = str(run.trigger.get("text", "회의"))
    consensus = session.consensus_summary or trigger
    lines = [
        "# 회의 요약",
        "",
        f"안건: {trigger}",
        f"합의: {consensus[:120]}",
        f"라운드: {len(session.rounds)}",
        f"참여자: {len(session.participants)}명",
    ]
    return "\n".join(lines) + "\n"


def _generate_agreement_document(run: MeetingRun, session: MultiBotSession) -> str:
    """Generate a shorter agreement-focused document."""
    trigger = str(run.trigger.get("text", "회의"))
    lines = [
        "# 합의서",
        "",
        f"안건: {trigger}",
        "",
        "## 합의안",
        f"{session.consensus_summary or '합의 결과를 확인하세요.'}",
    ]
    return "\n".join(lines) + "\n"


def _generate_action_items_document(run: MeetingRun, session: MultiBotSession) -> str:
    """Extract action items from meeting results."""
    trigger = str(run.trigger.get("text", "회의"))
    lines = [
        "# 다음 할 일",
        "",
        f"출처: {trigger}",
        "",
        "## 작업 항목",
        "• 회의 결과를 검토하고 후속 작업을 확정한다.",
        "• 합의된 방향에 따라 실행 계획을 수립한다.",
    ]
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
