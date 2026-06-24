"""Phase 22 Always-on Autonomous Company Runtime.

Unified orchestrator that runs one full autonomous company cycle:
health scan → daemon tick → dispatch → knowledge update → command simulation.
This is the final integration layer tying together all Phase 13-21 modules.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "error", _sanitize_text(self.error))

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
            "active_bots": self.active_bots,
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
        knowledge_updated = False

        # ── Phase 17: Health Scan ───────────────────────────────────
        try:
            health = scan_health(root=self.root)
        except Exception:
            health = HealthReport(ok=False, total_runs=0)
            error_parts.append("health_scan_failed")

        # ── Phase 20: Active bots ───────────────────────────────────
        active_bots = len(DEFAULT_REGISTRY.profiles)

        # ── Phase 19: Daemon ────────────────────────────────────────
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

        # ── Phase 15: Knowledge ─────────────────────────────────────
        try:
            _ = retrieve_knowledge_context(
                root=self.root,
                query="회사 상태 요약",
                limit=1,
            )
            knowledge_updated = True
        except Exception:
            pass

        # ── Phase 21: Commands ──────────────────────────────────────
        router = DiscordCommandRouter(root=self.root, dry_run=self.dry_run)
        simulated = 0
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
                pass

        # ── Phase 17: Total meeting runs ────────────────────────────
        runs_dir = self.root / "runtime" / "meeting_runs"
        total_runs = len(list(runs_dir.iterdir())) if runs_dir.exists() else 0

        return CompanyCycleResult(
            ok=len(error_parts) == 0,
            dry_run=self.dry_run,
            cycle_id=cycle_id,
            health_ok=health.ok,
            stuck_runs=len(health.stuck_runs),
            daemon_scheduled=daemon_tick.scheduled_meetings,
            daemon_created=daemon_tick.created_runs,
            dispatch_total=sum(
                1 for dr in daemon_tick.dispatch_results if dr.get("ok")
            ),
            knowledge_updated=knowledge_updated,
            commands_simulated=simulated,
            total_meeting_runs=total_runs,
            active_bots=active_bots,
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
