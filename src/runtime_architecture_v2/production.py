"""Phase 17 production health scanning and recovery triage.

This module scans existing MeetingRun artifacts under runtime/meeting_runs/
and produces deterministic health reports and recovery suggestions. It does
not start daemons, push metrics, send alerts, or call live services.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .schemas import MeetingRunState
from .store import MeetingRunStore

_NON_TERMINAL_STATES = frozenset({
    MeetingRunState.CREATED,
    MeetingRunState.CLASSIFIED,
    MeetingRunState.ROUTED,
    MeetingRunState.QUEUED,
    MeetingRunState.ACTIVE,
    MeetingRunState.VALIDATING,
    MeetingRunState.REPORTING,
})
_TERMINAL_STATES = frozenset({
    MeetingRunState.COMPLETED,
    MeetingRunState.FAILED,
    MeetingRunState.CANCELLED,
})

_STUCK_BY_STATE: dict[str, str] = {
    "paused": "resume",
    "active": "reclaim_or_wait",
    "validating": "reclaim_or_wait",
    "reporting": "reclaim_or_wait",
    "created": "manual",
    "classified": "manual",
    "routed": "manual",
    "queued": "manual",
    "failed": "manual",
    "cancelled": "manual",
}


@dataclass(frozen=True)
class RunHealth:
    """Health snapshot for one MeetingRun."""

    meeting_run_id: str
    state: str
    age_hours: float
    worker_task_count: int
    validation_count: int
    checkpoint_count: int
    is_terminal: bool
    is_stuck: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "state": self.state,
            "age_hours": round(self.age_hours, 1),
            "worker_task_count": self.worker_task_count,
            "validation_count": self.validation_count,
            "checkpoint_count": self.checkpoint_count,
            "is_terminal": self.is_terminal,
            "is_stuck": self.is_stuck,
        }


@dataclass(frozen=True)
class HealthReport:
    """Aggregate health report for all MeetingRuns under a root."""

    ok: bool
    total_runs: int
    state_counts: dict[str, int] = field(default_factory=dict)
    stuck_runs: tuple[RunHealth, ...] = ()
    runs: tuple[RunHealth, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "total_runs": self.total_runs,
            "state_counts": self.state_counts,
            "stuck_count": len(self.stuck_runs),
            "stuck_run_ids": [
                run.meeting_run_id for run in self.stuck_runs
            ],
            "runs": [run.to_dict() for run in self.runs],
            "error": self.error,
        }

    def to_summary(self) -> str:
        lines = [f"Total runs: {self.total_runs}"]
        for state, count in sorted(self.state_counts.items()):
            lines.append(f"  {state}: {count}")
        if self.stuck_runs:
            lines.append(
                f"⚠ Stuck (non-terminal): {len(self.stuck_runs)}"
            )
        else:
            lines.append("✅ No stuck runs.")
        return "\n".join(lines)


@dataclass(frozen=True)
class RecoverySuggestion:
    """One actionable recovery suggestion for a stuck/failed run."""

    meeting_run_id: str
    state: str
    action: str
    reason: str
    checkpoint_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "state": self.state,
            "action": self.action,
            "reason": self.reason,
            "checkpoint_count": self.checkpoint_count,
        }


def scan_health(
    *,
    root: str | Path,
    stuck_hours: float = 1.0,
) -> HealthReport:
    """Scan all persisted MeetingRuns and return a deterministic health report."""

    root = Path(root)
    store = MeetingRunStore(root)
    runs_dir = root / "runtime" / "meeting_runs"
    now = datetime.now(UTC)

    if not runs_dir.exists():
        return HealthReport(ok=True, total_runs=0)

    health_runs: list[RunHealth] = []
    state_counts: dict[str, int] = {}
    stuck_runs: list[RunHealth] = []

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        run_json = run_dir / "meeting_run.json"
        if not run_json.exists():
            continue

        try:
            run = store.load_meeting_run(run_dir.name)
        except Exception:
            continue

        state_str = str(run.state)
        age_hours = _age_hours(run_json, now)
        worker_count = len(run.worker_task_ids)
        validation_count = len(run.validation_ids)
        checkpoint_dir = run_dir / "checkpoints"
        checkpoint_count = (
            len(list(checkpoint_dir.glob("*.json")))
            if checkpoint_dir.exists()
            else 0
        )
        is_terminal = run.state in _TERMINAL_STATES
        is_stuck = (
            not is_terminal
            and run.state not in {MeetingRunState.PAUSED}
            and age_hours >= max(stuck_hours, 0)
        )
        if run.state == MeetingRunState.PAUSED and age_hours >= max(stuck_hours, 0):
            is_stuck = True

        health = RunHealth(
            meeting_run_id=run.meeting_run_id,
            state=state_str,
            age_hours=age_hours,
            worker_task_count=worker_count,
            validation_count=validation_count,
            checkpoint_count=checkpoint_count,
            is_terminal=is_terminal,
            is_stuck=is_stuck,
        )
        health_runs.append(health)
        state_counts[state_str] = state_counts.get(state_str, 0) + 1
        if is_stuck:
            stuck_runs.append(health)

    return HealthReport(
        ok=True,
        total_runs=len(health_runs),
        state_counts=state_counts,
        stuck_runs=tuple(stuck_runs),
        runs=tuple(health_runs),
    )


def triage_recovery(report: HealthReport) -> tuple[RecoverySuggestion, ...]:
    """Generate recovery suggestions for stuck or failed runs."""

    suggestions: list[RecoverySuggestion] = []
    for run in report.stuck_runs:
        action = _STUCK_BY_STATE.get(run.state, "manual")
        if action in ("resume",) and run.checkpoint_count == 0:
            action = "manual"
        suggestions.append(
            RecoverySuggestion(
                meeting_run_id=run.meeting_run_id,
                state=run.state,
                action=action,
                reason=_recovery_reason(run.state, run.checkpoint_count, run.age_hours),
                checkpoint_count=run.checkpoint_count,
            )
        )

    for run in report.runs:
        if run.state == "failed" and not run.is_stuck:
            suggestions.append(
                RecoverySuggestion(
                    meeting_run_id=run.meeting_run_id,
                    state=run.state,
                    action="manual",
                    reason="failed terminal — start fresh or inspect logs",
                    checkpoint_count=run.checkpoint_count,
                )
            )

    return tuple(suggestions)


def run_phase17_health_check(
    *,
    root: str | Path,
    mode: Literal["dry-run"] = "dry-run",
    stuck_hours: float = 1.0,
) -> dict[str, Any]:
    """Run the deterministic Phase 17 health check pilot."""

    if mode != "dry-run":
        return {
            "ok": False,
            "pilot_id": "phase17_production_readiness_monitoring_recovery",
            "mode": mode,
            "error": "phase17_only_supports_dry_run",
        }

    root = Path(root)
    report = scan_health(root=root, stuck_hours=stuck_hours)
    suggestions = triage_recovery(report)
    plan_path = _write_health_artifact(root, report, suggestions)

    return {
        "ok": report.ok,
        "pilot_id": "phase17_production_readiness_monitoring_recovery",
        "mode": mode,
        "total_runs": report.total_runs,
        "state_counts": report.state_counts,
        "stuck_count": len(report.stuck_runs),
        "stuck_run_ids": [run.meeting_run_id for run in report.stuck_runs],
        "recovery_suggestions": [s.to_dict() for s in suggestions],
        "health_summary": report.to_summary(),
        "plan_path": str(plan_path),
        "error": report.error,
    }


def _age_hours(path: Path, now: datetime) -> float:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return max(0.0, (now - mtime).total_seconds() / 3600)
    except OSError:
        return 0.0


def _recovery_reason(state: str, checkpoint_count: int, age_hours: float) -> str:
    if state in ("active", "validating", "reporting"):
        return (
            f"stuck in '{state}' for {age_hours:.1f}h; "
            f"{checkpoint_count} checkpoints available"
        )
    if state == "paused":
        if checkpoint_count > 0:
            return f"paused for {age_hours:.1f}h; can resume from checkpoint"
        return f"paused for {age_hours:.1f}h; no checkpoint — manual review needed"
    return f"state '{state}' for {age_hours:.1f}h"


def _write_health_artifact(
    root: Path,
    report: HealthReport,
    suggestions: tuple[RecoverySuggestion, ...],
) -> Path:
    path = root / "runtime" / "phase17-health" / "health_report.json"
    payload = {
        "report": report.to_dict(),
        "recovery_suggestions": [s.to_dict() for s in suggestions],
    }
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
    )
    return path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
