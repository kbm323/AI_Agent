"""Phase 19 Autonomous Scheduling Daemon.

Periodic tick-based daemon that creates MeetingRuns for recurring company
meetings and dispatches them through the Phase 18 autonomous dispatch loop.
Health-gated and idempotent — skips when the system is unhealthy or a tick
was already processed within the interval.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .dispatch_loop import (
    run_phase18_autonomous_dispatch,
)
from .kanban_ops import _sanitize_text
from .production import HealthReport, scan_health
from .schemas import MeetingRun
from .store import MeetingRunStore

AUTONOMOUS_DAEMON_ID = "phase19_autonomous_scheduling_daemon"

_DEFAULT_SPECS: tuple[dict[str, object], ...] = (
    {
        "spec_id": "spec-daily",
        "name": "Daily Standup",
        "schedule": "every 24h",
        "trigger_text": "일일 스탠드업 — 각 팀 진행상황 보고",
        "priority": "P1",
        "worker_roles": ("content_lead", "tech_lead", "marketing_lead"),
    },
    {
        "spec_id": "spec-weekly",
        "name": "Weekly Review",
        "schedule": "every 7d",
        "trigger_text": "주간 리뷰 — KPI 점검 및 다음 주 계획",
        "priority": "P1",
        "worker_roles": ("content_lead", "art_director", "quality_lead"),
    },
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecurringMeetingSpec:
    """Definition of a recurring company meeting."""

    spec_id: str
    name: str
    schedule: str
    trigger_text: str
    priority: str = "P2"
    worker_roles: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_roles", tuple(self.worker_roles))

    def to_dict(self) -> dict[str, object]:
        return {
            "spec_id": self.spec_id,
            "name": self.name,
            "schedule": self.schedule,
            "trigger_text": self.trigger_text,
            "priority": self.priority,
            "worker_roles": list(self.worker_roles),
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class DaemonTick:
    """Result of one daemon tick cycle."""

    ok: bool
    dry_run: bool
    tick_id: str
    scheduled_meetings: int
    created_runs: int
    skipped_health: int
    skipped_recent: int
    skipped_disabled: int
    dispatch_results: tuple[dict[str, object], ...] = ()
    health_report: HealthReport | None = None
    error: str = ""

    def __post_init__(self) -> None:
        safe_error = _sanitize_text(self.error)
        object.__setattr__(self, "error", safe_error)

    def to_dict(self) -> dict[str, object]:
        hr_dict = self.health_report.to_dict() if self.health_report else None
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "tick_id": self.tick_id,
            "scheduled_meetings": self.scheduled_meetings,
            "created_runs": self.created_runs,
            "skipped_health": self.skipped_health,
            "skipped_recent": self.skipped_recent,
            "skipped_disabled": self.skipped_disabled,
            "dispatch_results": list(self.dispatch_results),
            "health_report": hr_dict,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# AutonomousDaemon
# ---------------------------------------------------------------------------


class AutonomousDaemon:
    """Periodic tick-based daemon for recurring meeting scheduling."""

    def __init__(
        self,
        *,
        root: str | Path,
        dry_run: bool = True,
        max_stuck_threshold: int = 3,
        tick_interval_hours: float = 1.0,
    ) -> None:
        self.root = Path(root)
        self.dry_run = dry_run
        self.max_stuck_threshold = max_stuck_threshold
        self.tick_interval_hours = tick_interval_hours
        self._store = MeetingRunStore(root)
        self._last_tick_at: datetime | None = None
        self._last_health: HealthReport | None = None

    def tick(
        self,
        specs: tuple[RecurringMeetingSpec, ...] = (),
        *,
        health: HealthReport | None = None,
    ) -> DaemonTick:
        """Execute one daemon tick — health check + meeting creation + dispatch."""

        now = datetime.now(UTC)
        tick_id = f"tick-{now.strftime('%Y%m%d-%H%M%S')}"

        # Dedup: skip if recent tick
        if self._last_tick_at is not None:
            elapsed = (now - self._last_tick_at).total_seconds() / 3600
            if elapsed < self.tick_interval_hours:
                return DaemonTick(
                    ok=True,
                    dry_run=self.dry_run,
                    tick_id=tick_id,
                    scheduled_meetings=0,
                    created_runs=0,
                    skipped_health=0,
                    skipped_recent=1,
                    skipped_disabled=0,
                )

        # Health gate
        report = health if health is not None else scan_health(root=self.root)
        self._last_health = report
        stuck_count = len(report.stuck_runs)

        # Count enabled specs
        enabled_specs = [s for s in specs if s.enabled]
        disabled_count = len(specs) - len(enabled_specs)

        if stuck_count > self.max_stuck_threshold:
            self._last_tick_at = now
            return DaemonTick(
                ok=True,
                dry_run=self.dry_run,
                tick_id=tick_id,
                scheduled_meetings=0,
                created_runs=0,
                skipped_health=len(enabled_specs),
                skipped_recent=0,
                skipped_disabled=disabled_count,
                health_report=report,
            )

        # Create MeetingRuns + dispatch
        dispatch_results: list[dict[str, object]] = []
        created = 0

        for spec in enabled_specs:
            meeting_run_id = f"mr-{spec.spec_id}-{tick_id}"
            if self.dry_run:
                dispatch_results.append({
                    "ok": True,
                    "dry_run": True,
                    "meeting_run_id": meeting_run_id,
                    "spec_id": spec.spec_id,
                    "would_create": True,
                })
            else:
                meeting_run = MeetingRun.create(
                    meeting_run_id=meeting_run_id,
                    trigger_text=spec.trigger_text,
                    user_id="daemon",
                    channel_id="ch-daemon",
                    thread_id="th-daemon",
                    priority=spec.priority,
                )
                self._store.save_meeting_run(meeting_run)

                dispatch_result = run_phase18_autonomous_dispatch(
                    root=self.root,
                    mode="live",
                    meeting_run_id=meeting_run_id,
                    max_rounds=3,
                )
                dispatch_results.append(dispatch_result)
                created += 1

        self._last_tick_at = now

        return DaemonTick(
            ok=True,
            dry_run=self.dry_run,
            tick_id=tick_id,
            scheduled_meetings=len(enabled_specs),
            created_runs=created,
            skipped_health=0,
            skipped_recent=0,
            skipped_disabled=disabled_count,
            dispatch_results=tuple(dispatch_results),
            health_report=report,
        )


# ---------------------------------------------------------------------------
# Phase 19 CLI Pilot
# ---------------------------------------------------------------------------


def run_phase19_daemon_tick(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live"] = "dry-run",
    specs: tuple[RecurringMeetingSpec, ...] | None = None,
    max_stuck_threshold: int = 3,
) -> dict[str, Any]:
    """Run one Phase 19 daemon tick."""

    if mode not in ("dry-run", "live"):
        return {
            "ok": False,
            "pilot_id": AUTONOMOUS_DAEMON_ID,
            "mode": mode,
            "error": f"unsupported mode: {mode}",
        }

    root = Path(root)
    if specs is None:
        specs = tuple(
            RecurringMeetingSpec(**s)  # type: ignore[arg-type]
            for s in _DEFAULT_SPECS
        )

    daemon = AutonomousDaemon(
        root=root,
        dry_run=(mode == "dry-run"),
        max_stuck_threshold=max_stuck_threshold,
    )
    tick = daemon.tick(specs=specs)
    artifact_path = _write_tick_artifact(root, tick)

    return {
        "ok": tick.ok,
        "pilot_id": AUTONOMOUS_DAEMON_ID,
        "mode": mode,
        "tick_id": tick.tick_id,
        "scheduled_meetings": tick.scheduled_meetings,
        "created_runs": tick.created_runs,
        "skipped_health": tick.skipped_health,
        "skipped_recent": tick.skipped_recent,
        "skipped_disabled": tick.skipped_disabled,
        "tick": tick.to_dict(),
        "artifact_path": str(artifact_path),
        "error": tick.error,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_tick_artifact(root: Path, tick: DaemonTick) -> Path:
    path = root / "runtime" / "phase19-daemon" / f"{tick.tick_id}.json"
    payload = tick.to_dict()
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
    )
    return path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
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
