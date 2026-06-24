"""Phase 22 Always-on Autonomous Company Runtime.

Unified orchestrator that runs one full autonomous company cycle:
health scan → daemon tick → dispatch → knowledge update → command simulation.
This is the final integration layer tying together all Phase 13-21 modules.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .bot_registry import DEFAULT_REGISTRY
from .daemon import (
    _DEFAULT_SPECS as DAEMON_SPECS,
)
from .daemon import (
    AutonomousDaemon,
    RecurringMeetingSpec,
)
from .discord_webhook import DiscordCommandRouter, DiscordInteraction
from .kanban_ops import _sanitize_text
from .knowledge import retrieve_knowledge_context
from .production import HealthReport, scan_health

AUTONOMOUS_COMPANY_ID = "phase22_always_on_autonomous_company"


@dataclass(frozen=True)
class CompanyCycleResult:
    """Result of one full autonomous company cycle."""

    ok: bool
    dry_run: bool
    cycle_id: str
    health_ok: bool
    stuck_runs: int
    daemon_scheduled: int
    daemon_created: int
    dispatch_total: int
    knowledge_updated: bool
    commands_simulated: int
    total_meeting_runs: int
    active_bots: int
    error: str = ""
    warnings: tuple[str, ...] = ()
    subphase_status: dict[str, str] = field(default_factory=dict)
    registered_roles: int = 0

    def __post_init__(self) -> None:
        sanitized_warnings = tuple(_sanitize_text(w) for w in self.warnings)
        sanitized_status = {
            _sanitize_text(str(k)): _sanitize_text(str(v))
            for k, v in self.subphase_status.items()
        }
        registered_roles = self.registered_roles or self.active_bots
        active_bots = registered_roles
        object.__setattr__(self, "error", _sanitize_text(self.error))
        object.__setattr__(self, "warnings", sanitized_warnings)
        object.__setattr__(self, "subphase_status", sanitized_status)
        object.__setattr__(self, "registered_roles", registered_roles)
        object.__setattr__(self, "active_bots", active_bots)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "cycle_id": self.cycle_id,
            "health_ok": self.health_ok,
            "stuck_runs": self.stuck_runs,
            "daemon_scheduled": self.daemon_scheduled,
            "daemon_created": self.daemon_created,
            "dispatch_total": self.dispatch_total,
            "knowledge_updated": self.knowledge_updated,
            "commands_simulated": self.commands_simulated,
            "total_meeting_runs": self.total_meeting_runs,
            "registered_roles": self.registered_roles,
            "active_bots": self.active_bots,
            "warnings": self.warnings,
            "subphase_status": self.subphase_status,
            "error": self.error,
        }


class AutonomousCompany:
    """Unified always-on autonomous company runtime."""

    def __init__(
        self,
        *,
        root: str | Path,
        dry_run: bool = True,
        max_stuck_threshold: int = 3,
    ) -> None:
        self.root = Path(root)
        self.dry_run = dry_run
        self.max_stuck_threshold = max_stuck_threshold

    def run(self) -> CompanyCycleResult:
        now = datetime.now(UTC)
        cycle_id = f"cycle-{now.strftime('%Y%m%d-%H%M%S')}"
        error_parts: list[str] = []
        warnings: list[str] = []
        subphase_status: dict[str, str] = {}
        knowledge_updated = False
        daemon_scheduled = 0
        daemon_created = 0
        dispatch_total = 0
        dispatch_results: tuple[dict[str, object], ...] = ()

        # ── Phase 17: Health Scan ───────────────────────────────────
        try:
            health = scan_health(root=self.root)
            subphase_status["health"] = "ok" if health.ok else "failed"
            if not health.ok:
                error_parts.append("health_scan_failed")
        except Exception:
            health = HealthReport(ok=False, total_runs=0)
            subphase_status["health"] = "failed"
            error_parts.append("health_scan_failed")

        # ── Phase 20: Registered roles ───────────────────────────────
        registered_roles = len(DEFAULT_REGISTRY.profiles)

        # ── Phase 19: Daemon ────────────────────────────────────────
        try:
            daemon_specs = tuple(
                RecurringMeetingSpec(**s)  # type: ignore[arg-type]
                for s in DAEMON_SPECS
            )
            daemon = AutonomousDaemon(
                root=self.root,
                dry_run=self.dry_run,
                max_stuck_threshold=self.max_stuck_threshold,
            )
            daemon_tick = daemon.tick(specs=daemon_specs, health=health)
            daemon_scheduled = daemon_tick.scheduled_meetings
            daemon_created = daemon_tick.created_runs
            dispatch_results = tuple(daemon_tick.dispatch_results)
            dispatch_total = sum(1 for dr in dispatch_results if dr.get("ok"))
            failed_dispatches = tuple(
                dr for dr in dispatch_results if not dr.get("ok")
            )
            if failed_dispatches:
                subphase_status["daemon_dispatch"] = "failed"
                message = "daemon_dispatch_failed"
                if self.dry_run:
                    warnings.append(message)
                else:
                    error_parts.append(message)
            elif not self.dry_run and daemon_created and not dispatch_results:
                subphase_status["daemon"] = "ok"
                subphase_status["daemon_dispatch"] = "failed"
                error_parts.append("live_dispatch_dependency_missing")
            else:
                subphase_status["daemon"] = "ok"
                subphase_status["daemon_dispatch"] = "ok"
        except Exception:
            subphase_status["daemon"] = "failed"
            error_parts.append("daemon_tick_failed")

        # ── Phase 15: Knowledge ─────────────────────────────────────
        try:
            _ = retrieve_knowledge_context(
                root=self.root,
                query="회사 상태 요약",
                limit=1,
            )
            knowledge_updated = True
            subphase_status["knowledge"] = "ok"
        except Exception:
            subphase_status["knowledge"] = "warning"
            warnings.append("knowledge_update_failed")

        # ── Phase 21: Commands ──────────────────────────────────────
        router = DiscordCommandRouter(root=self.root, dry_run=self.dry_run)
        simulated = 0
        command_failures = 0
        from .discord_webhook import _COMMANDS
        for cmd in _COMMANDS:
            try:
                router.route(DiscordInteraction(
                    interaction_id=f"auto-{cmd.name}",
                    type=2, user_id="company-daemon",
                    channel_id="ch-auto", command_name=cmd.name,
                    options={},
                ))
                simulated += 1
            except Exception:
                command_failures += 1
        if command_failures:
            subphase_status["commands"] = "warning"
            warnings.append("command_route_failed")
        else:
            subphase_status["commands"] = "ok"

        # ── Phase 17: Total meeting runs ────────────────────────────
        runs_dir = self.root / "runtime" / "meeting_runs"
        total_runs = len(list(runs_dir.iterdir())) if runs_dir.exists() else 0

        return CompanyCycleResult(
            ok=len(error_parts) == 0,
            dry_run=self.dry_run,
            cycle_id=cycle_id,
            health_ok=health.ok,
            stuck_runs=len(health.stuck_runs),
            daemon_scheduled=daemon_scheduled,
            daemon_created=daemon_created,
            dispatch_total=dispatch_total,
            knowledge_updated=knowledge_updated,
            commands_simulated=simulated,
            total_meeting_runs=total_runs,
            active_bots=registered_roles,
            registered_roles=registered_roles,
            warnings=tuple(warnings),
            subphase_status=subphase_status,
            error="; ".join(error_parts) if error_parts else "",
        )


# ── Phase 22 CLI ────────────────────────────────────────────────────


def run_phase22_company_cycle(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live"] = "dry-run",
) -> dict[str, Any]:
    """Run one full autonomous company cycle."""

    if mode not in ("dry-run", "live"):
        return {
            "ok": False,
            "pilot_id": AUTONOMOUS_COMPANY_ID,
            "mode": mode,
            "error": f"unsupported mode: {mode}",
        }

    root = Path(root)
    company = AutonomousCompany(root=root, dry_run=(mode == "dry-run"))
    result = company.run()
    artifact_path = _write_cycle_artifact(root, result)

    return {
        "ok": result.ok,
        "pilot_id": AUTONOMOUS_COMPANY_ID,
        "mode": mode,
        "cycle_id": result.cycle_id,
        "cycle": result.to_dict(),
        "artifact_path": str(artifact_path),
        "error": result.error,
    }


def _write_cycle_artifact(
    root: Path, result: CompanyCycleResult,
) -> Path:
    path = root / "runtime" / "phase22-company" / f"{result.cycle_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".cycle.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(
                result.to_dict(), handle,
                ensure_ascii=False, indent=2, sort_keys=True,
            )
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path
