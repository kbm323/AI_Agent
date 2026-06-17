"""Tests for persona spec loading (AC23)."""

from __future__ import annotations

from pathlib import Path

from src.persona_spec_loader import load_persona_spec


def test_loads_agent_yaml_and_persona_md_with_git_version(tmp_path: Path) -> None:
    role_dir = tmp_path / "creative" / "producer"
    role_dir.mkdir(parents=True)
    (role_dir / "agent.yaml").write_text("role_id: producer\nteam: creative\n", encoding="utf-8")
    (role_dir / "persona.md").write_text("# Producer\nMakes decisions.\n", encoding="utf-8")

    spec = load_persona_spec(role_dir, git_version="abc123")

    assert spec.role_id == "producer"
    assert spec.team == "creative"
    assert "Makes decisions" in spec.persona_markdown
    assert spec.git_version == "abc123"
