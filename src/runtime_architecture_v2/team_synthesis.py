"""Phase 30 team synthesis: group specialist results by projection profile.

This module bridges the 29 internal specialist roles to the 7 Discord-facing
Hermes profile bots. After worker tasks complete, their outputs are grouped
by projection profile (aicompanycontent, aicompanyart, aicompanytech,
aicompanymarketing, aicompanyquality, aicompanyceo) and delivered through the
corresponding bot.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .model_policy import (
    RoleModelPolicy,
    load_role_model_policies,
    normalize_role_id,
    projection_profile_for_role,
)
from .schemas import WorkerTask

_HERMES_PROJECTION_PROFILES: tuple[str, ...] = (
    "aicompanycontent",
    "aicompanyart",
    "aicompanytech",
    "aicompanymarketing",
    "aicompanyquality",
    "aicompanyceo",
)

_DISPLAY_PROFILES: dict[str, str] = {
    "aicompanycontent": "🎬 콘텐츠/제작팀",
    "aicompanyart": "🎨 아트/디자인팀",
    "aicompanytech": "⚙️ 기술/엔지니어링팀",
    "aicompanymarketing": "📈 마케팅팀",
    "aicompanyquality": "🛡️ 검증팀",
    "aicompanyceo": "🛠️ CEO/실행",
}


@dataclass(frozen=True)
class SpecialistResult:
    """Sanitized output from one internal specialist worker task."""

    role_id: str
    team: str
    role_type: str
    projection_profile: str
    output_summary: str
    worker_task_id: str
    exit_code: int


@dataclass(frozen=True)
class TeamSynthesis:
    """Aggregated results grouped by projection profile.

    Each profile entry contains the specialist results that will be delivered
    through the corresponding Discord-facing Hermes profile bot. The synthesis
    preserves the internal specialist role identity while sanitizing output
    for public (Discord) projection.
    """

    meeting_run_id: str
    groups: dict[str, tuple[SpecialistResult, ...]] = field(default_factory=dict)
    uncovered_roles: tuple[str, ...] = ()  # roles that ran but had no profile

    @property
    def profile_count(self) -> int:
        return len(self.groups)

    @property
    def total_specialists(self) -> int:
        return sum(len(results) for results in self.groups.values())

    def as_projectable_summary(self) -> dict[str, str]:
        """Return a one-line summary per profile suitable for Discord output."""
        result: dict[str, str] = {}
        for profile_id, results in self.groups.items():
            display = _DISPLAY_PROFILES.get(profile_id, profile_id)
            specialists = ", ".join(r.role_id.replace("-", " ") for r in results)
            result[profile_id] = (
                f"{display}: {len(results)}명 참여 ({specialists})"
            )
        return result


def synthesize_worker_results(
    meeting_run_id: str,
    completed_tasks: tuple[WorkerTask, ...],
) -> TeamSynthesis:
    """Group completed worker tasks by projection profile.

    This is the boundary between 29 internal specialist roles and the 7
    Discord-facing Hermes profiles. Output summaries are truncated to 500
    characters to prevent token leakage through projection channels.
    """
    policies = load_role_model_policies()
    groups: dict[str, list[SpecialistResult]] = defaultdict(list)
    uncovered: list[str] = []

    for task in completed_tasks:
        role_id = str(task.role)
        try:
            profile = projection_profile_for_role(role_id)
        except KeyError:
            uncovered.append(role_id)
            continue

        canonical = normalize_role_id(role_id)
        summary = _extract_summary(task, max_length=500)
        policy: RoleModelPolicy | None = policies.get(canonical)
        specialist = SpecialistResult(
            role_id=canonical,
            team=policy.team if policy else "unknown",
            role_type=policy.role_type if policy else "worker",
            projection_profile=profile,
            output_summary=summary,
            worker_task_id=task.worker_task_id,
            exit_code=0,
        )
        groups[profile].append(specialist)

    return TeamSynthesis(
        meeting_run_id=meeting_run_id,
        groups={p: tuple(r) for p, r in groups.items()},
        uncovered_roles=tuple(uncovered),
    )


def _extract_summary(task: WorkerTask, *, max_length: int = 500) -> str:
    """Extract a safe summary from a completed worker task output.

    Reads the output_path JSON file if it exists; falls back to
    a short role-based placeholder.
    """
    import json
    from pathlib import Path

    output_path = Path(task.output_path)
    if not output_path.exists():
        return f"[{task.role}] 작업 완료 (출력 없음)"

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return f"[{task.role}] 작업 결과 파싱 실패"

    text = str(
        payload.get("summary")
        or payload.get("output")
        or payload.get("text")
        or payload.get("message")
        or ""
    )
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return text


__all__ = [
    "SpecialistResult",
    "TeamSynthesis",
    "synthesize_worker_results",
    "projection_profile_for_role",
    "_HERMES_PROJECTION_PROFILES",
]
