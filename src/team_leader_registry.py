"""Team leader Discord bot registry for AC24."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class TeamLeaderRegistry:
    bot_role_ids: tuple[str, ...]
    worker_only_role_ids: tuple[str, ...]


def build_team_leader_registry(
    roles: Sequence[Mapping[str, object]],
) -> TeamLeaderRegistry:
    bots: list[str] = []
    workers: list[str] = []
    for role in roles:
        role_id = str(role.get("role_id") or "")
        if not role_id:
            continue
        if role.get("kind") == "team_leader":
            bots.append(role_id)
        else:
            workers.append(role_id)
    return TeamLeaderRegistry(
        bot_role_ids=tuple(bots),
        worker_only_role_ids=tuple(workers),
    )
