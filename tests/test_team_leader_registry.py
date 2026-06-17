"""Tests for team leader bot registry (AC24)."""

from __future__ import annotations

from src.team_leader_registry import build_team_leader_registry


def test_registry_keeps_only_persistent_team_leaders_as_bots() -> None:
    roles = (
        {"role_id": "creative-lead", "team": "creative", "kind": "team_leader"},
        {"role_id": "tech-lead", "team": "tech", "kind": "team_leader"},
        {"role_id": "artist", "team": "creative", "kind": "specialist"},
    )

    registry = build_team_leader_registry(roles)

    assert registry.bot_role_ids == ("creative-lead", "tech-lead")
    assert registry.worker_only_role_ids == ("artist",)
