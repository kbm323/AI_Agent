"""Persona spec loader for AC23."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PersonaSpec:
    role_id: str
    team: str
    agent_yaml: dict[str, object]
    persona_markdown: str
    git_version: str


def load_persona_spec(role_dir: str | Path, *, git_version: str) -> PersonaSpec:
    path = Path(role_dir)
    agent_path = path / "agent.yaml"
    persona_path = path / "persona.md"
    if not agent_path.exists():
        raise FileNotFoundError(agent_path)
    if not persona_path.exists():
        raise FileNotFoundError(persona_path)
    data = yaml.safe_load(agent_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("agent.yaml must contain a mapping")
    role_id = str(data.get("role_id") or "")
    team = str(data.get("team") or "")
    if not role_id or not team:
        raise ValueError("agent.yaml requires role_id and team")
    return PersonaSpec(
        role_id=role_id,
        team=team,
        agent_yaml=dict(data),
        persona_markdown=persona_path.read_text(encoding="utf-8"),
        git_version=git_version,
    )
