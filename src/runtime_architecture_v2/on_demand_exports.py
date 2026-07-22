"""On-demand exports grounded in durable Runtime v2 meeting evidence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path

from src.runtime_architecture_v2.multi_bot import BOT_PERSONAS, MultiBotSession
from src.runtime_architecture_v2.schemas import MeetingOutcome, MeetingRun
from src.runtime_architecture_v2.store import MeetingRunStore, StoreError


@unique
class OnDemandExportType(StrEnum):
    """Export types the user can request after a meeting."""

    SUMMARY = "summary"
    FINAL_REPORT = "final_report"
    AGREEMENT = "agreement"
    ACTION_ITEMS = "action_items"


@dataclass(frozen=True)
class OnDemandExportResult:
    """Result of an on-demand export request."""

    export_type: str
    meeting_run_id: str
    content: str
    ok: bool = True
    error: str = ""


def find_latest_meeting_run_id(root: str | Path) -> str | None:
    """Return the most recently modified meeting run ID, if one exists."""

    store_root = Path(root) / "runtime" / "meeting_runs"
    if not store_root.exists():
        return None
    dirs = sorted(
        (directory for directory in store_root.iterdir() if directory.is_dir()),
        key=lambda directory: (directory.stat().st_mtime_ns, directory.name),
    )
    return dirs[-1].name if dirs else None


def run_on_demand_export(
    root: str | Path,
    meeting_run_id: str,
    export_type: OnDemandExportType,
) -> OnDemandExportResult:
    """Generate and persist one report from canonical meeting artifacts."""

    store = MeetingRunStore(root)
    run = store.load_meeting_run(meeting_run_id)
    session, outcome, legacy_notes = _load_canonical_artifacts(
        store,
        meeting_run_id,
    )

    if export_type == OnDemandExportType.SUMMARY:
        content = _generate_summary(run, session, outcome, legacy_notes)
    elif export_type == OnDemandExportType.FINAL_REPORT:
        content = _generate_final_report(run, session, outcome, legacy_notes)
    elif export_type == OnDemandExportType.AGREEMENT:
        content = _generate_agreement_document(
            run,
            session,
            outcome,
            legacy_notes,
        )
    elif export_type == OnDemandExportType.ACTION_ITEMS:
        content = _generate_action_items_document(
            run,
            session,
            outcome,
            legacy_notes,
        )
    else:
        return OnDemandExportResult(
            export_type=str(export_type),
            meeting_run_id=meeting_run_id,
            ok=False,
            error=f"unknown export type: {export_type}",
            content="",
        )

    run_dir = store.meeting_run_dir(meeting_run_id)
    report_path = run_dir / "reports" / f"{export_type}.md"
    _atomic_write_text(report_path, content)
    if export_type == OnDemandExportType.FINAL_REPORT:
        _write_legacy_final_report_aliases(
            run_dir=run_dir,
            run=run,
            outcome=outcome,
            content=content,
            legacy_notes=legacy_notes,
        )

    return OnDemandExportResult(
        export_type=str(export_type),
        meeting_run_id=meeting_run_id,
        content=content,
        ok=True,
    )


def _load_canonical_artifacts(
    store: MeetingRunStore,
    meeting_run_id: str,
) -> tuple[MultiBotSession, MeetingOutcome, tuple[str, ...]]:
    legacy_notes: list[str] = []
    try:
        session = store.load_meeting_session(meeting_run_id)
    except StoreError as exc:
        legacy_notes.append(f"회의 발언 기록 없음 ({exc.code})")
        session = MultiBotSession(
            meeting_run_id=meeting_run_id,
            participants=(),
            rounds=(),
            consensus_reached=False,
            escalation_required=True,
        )

    try:
        outcome = store.load_meeting_outcome(meeting_run_id)
    except StoreError as exc:
        legacy_notes.append(f"구조화된 회의 판정 없음 ({exc.code})")
        outcome = MeetingOutcome(
            meeting_run_id=meeting_run_id,
            status="needs_user_decision",
            summary="검증 가능한 합의 없음",
            generation_status="failed",
            error_code="legacy_missing_canonical_evidence",
        )
    return session, outcome, tuple(legacy_notes)


def _generate_summary(
    run: MeetingRun,
    session: MultiBotSession,
    outcome: MeetingOutcome,
    legacy_notes: tuple[str, ...],
) -> str:
    lines = [
        "# 회의 요약",
        "",
        f"안건: {_agenda(run)}",
        f"판정: {outcome.status.value}",
        f"요약: {_outcome_summary(outcome)}",
        f"라운드: {len(session.rounds)}",
        f"참여자: {len(session.participants)}명",
        "",
        "## 주요 근거",
        *_evidence_lines(session, outcome, limit=4),
        *_legacy_lines(legacy_notes),
    ]
    return "\n".join(lines) + "\n"


def _generate_agreement_document(
    run: MeetingRun,
    session: MultiBotSession,
    outcome: MeetingOutcome,
    legacy_notes: tuple[str, ...],
) -> str:
    lines = [
        "# 합의서",
        "",
        f"안건: {_agenda(run)}",
        f"판정: {outcome.status.value}",
        "",
        "## 합의안",
        *_bullet_lines(outcome.agreements, empty="검증 가능한 합의 없음"),
        "",
        "## 미해결 쟁점",
        *_bullet_lines(outcome.disagreements, empty="기록된 미해결 쟁점 없음"),
        "",
        "## 판정 요약",
        _outcome_summary(outcome),
        "",
        "## 근거",
        *_evidence_lines(session, outcome, limit=4),
        *_legacy_lines(legacy_notes),
    ]
    return "\n".join(lines) + "\n"


def _generate_action_items_document(
    run: MeetingRun,
    session: MultiBotSession,
    outcome: MeetingOutcome,
    legacy_notes: tuple[str, ...],
) -> str:
    lines = [
        "# 다음 할 일",
        "",
        f"출처: {_agenda(run)}",
        f"판정 요약: {_outcome_summary(outcome)}",
        "",
        "## 작업 항목",
        *_bullet_lines(outcome.action_items, empty="확정된 작업 항목 없음"),
        "",
        "## 작업 근거",
        *_evidence_lines(session, outcome, limit=2),
        *_legacy_lines(legacy_notes),
    ]
    return "\n".join(lines) + "\n"


def _generate_final_report(
    run: MeetingRun,
    session: MultiBotSession,
    outcome: MeetingOutcome,
    legacy_notes: tuple[str, ...],
) -> str:
    lines = [
        f"# 📋 최종보고서: {_agenda(run)}",
        "",
        "## 🎯 결론",
        _outcome_summary(outcome),
        "",
        "## ✅ 합의안",
        *_bullet_lines(outcome.agreements, empty="검증 가능한 합의 없음"),
        "",
        "## ⚖️ 미해결 쟁점",
        *_bullet_lines(outcome.disagreements, empty="기록된 미해결 쟁점 없음"),
        "",
        "## 🚀 다음 액션",
        *_bullet_lines(outcome.action_items, empty="확정된 작업 항목 없음"),
        "",
        "## 💬 회의 근거",
        *_evidence_lines(session, outcome, limit=6),
        "",
        "## ⚠️ 응답 상태",
        f"- 판정: {outcome.status.value}",
        f"- 판정 생성: {outcome.generation_status}",
        f"- 모델: {outcome.model or '기록 없음'}",
        *_bullet_lines(outcome.validator_notes, empty="검증 메모 없음"),
        *_legacy_lines(legacy_notes),
    ]
    return "\n".join(lines) + "\n"


def _agenda(run: MeetingRun) -> str:
    return str(run.trigger.get("text") or "회의")


def _outcome_summary(outcome: MeetingOutcome) -> str:
    return outcome.summary.strip() or "검증 가능한 합의 없음"


def _bullet_lines(items: tuple[str, ...], *, empty: str) -> list[str]:
    return [f"- {item}" for item in items] if items else [f"- {empty}"]


def _legacy_lines(notes: tuple[str, ...]) -> list[str]:
    if not notes:
        return []
    return ["", "## 레거시 기록 주의", *[f"- {note}" for note in notes]]


def _evidence_lines(
    session: MultiBotSession,
    outcome: MeetingOutcome,
    *,
    limit: int,
) -> list[str]:
    referenced = set(outcome.evidence_refs)
    candidates: list[tuple[str, str]] = []
    for round_data in session.rounds:
        for message in round_data.messages:
            ref = f"round:{message.round}:{message.bot_role}"
            if referenced and ref not in referenced:
                continue
            role = BOT_PERSONAS.get(message.bot_role, message.bot_role)
            status = "" if message.generation_status == "live" else f" [{message.generation_status}]"
            content = " ".join(message.content.split())
            if len(content) > 180:
                content = content[:177].rstrip() + "..."
            candidates.append((ref, f"- [{ref} · {role}]{status} {content}"))

    if not candidates and referenced:
        return [f"- 근거 참조: {ref}" for ref in sorted(referenced)[:limit]]
    if not candidates:
        return ["- 저장된 발언 근거 없음"]
    return [line for _, line in candidates[:limit]]


def _write_legacy_final_report_aliases(
    *,
    run_dir: Path,
    run: MeetingRun,
    outcome: MeetingOutcome,
    content: str,
    legacy_notes: tuple[str, ...],
) -> None:
    """Keep old runtime filenames readable while reports/ is canonical."""

    _atomic_write_text(run_dir / "final_report_v3.md", content)
    payload = {
        "meeting_run_id": run.meeting_run_id,
        "agenda": _agenda(run),
        "outcome": outcome.to_dict(),
        "legacy_evidence_warnings": list(legacy_notes),
        "canonical_report": "reports/final_report.md",
    }
    _atomic_write_text(
        run_dir / "decision_summary.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
