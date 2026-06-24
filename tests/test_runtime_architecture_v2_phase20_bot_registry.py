"""Phase 20 29-role Org Chart Registry — TDD tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_architecture_v2.bot_registry import (
    BOT_REGISTRY_ID,
    DEFAULT_REGISTRY,
    BotProfile,
    run_phase20_bot_registry,
)


class TestBotProfile:
    def test_profile_fields(self) -> None:
        p = BotProfile(
            role_id="content_lead",
            display_name="콘텐츠 팀장",
            department="Content",
            permissions=("read_history", "send_messages"),
        )
        assert p.role_id == "content_lead"
        assert p.mention_gated is True
        assert p.priority == "P2"

    def test_profile_to_dict(self) -> None:
        p = BotProfile("tech_lead", "기술 팀장", "Technology", ("read", "write"))
        d = p.to_dict()
        assert d["role_id"] == "tech_lead"
        assert d["mention_gated"] is True


class TestBotRegistry:
    def test_default_registry_has_29_roles(self) -> None:
        assert len(DEFAULT_REGISTRY.profiles) == 29

    def test_no_duplicate_role_ids(self) -> None:
        ids = [p.role_id for p in DEFAULT_REGISTRY.profiles]
        assert len(ids) == len(set(ids))

    def test_get_by_role_id(self) -> None:
        p = DEFAULT_REGISTRY.get("ceo_coordinator")
        assert p is not None
        assert p.display_name == "대표"

    def test_get_missing_returns_none(self) -> None:
        assert DEFAULT_REGISTRY.get("nonexistent") is None

    def test_by_department(self) -> None:
        execs = DEFAULT_REGISTRY.by_department("Executive")
        assert len(execs) == 3
        ids = {p.role_id for p in execs}
        assert "ceo_coordinator" in ids

    def test_all_mention_gated(self) -> None:
        for p in DEFAULT_REGISTRY.profiles:
            assert p.mention_gated is True, f"{p.role_id} not mention-gated"

    def test_ceo_is_p0(self) -> None:
        ceo = DEFAULT_REGISTRY.get("ceo_coordinator")
        assert ceo.priority == "P0"

    def test_to_manifest(self) -> None:
        m = DEFAULT_REGISTRY.to_manifest()
        assert m["registry_id"] == BOT_REGISTRY_ID
        assert m["total_bots"] == 29
        assert len(m["bots"]) == 29
        assert all("role_id" in b for b in m["bots"])

    def test_manifest_json_serializable(self) -> None:
        m = DEFAULT_REGISTRY.to_manifest()
        raw = json.dumps(m, ensure_ascii=False)
        back = json.loads(raw)
        assert back["total_bots"] == 29


class TestPhase20CLI:
    def test_dry_run(self, tmp_path: Path) -> None:
        r = run_phase20_bot_registry(root=tmp_path, mode="dry-run")
        assert r["ok"] is True
        assert r["total_bots"] == 29

    def test_manifest_written(self, tmp_path: Path) -> None:
        _ = run_phase20_bot_registry(root=tmp_path, mode="dry-run")
        path = tmp_path / "runtime" / "phase20-bots" / "bot_manifest.json"
        assert path.exists()

    def test_invalid_mode(self, tmp_path: Path) -> None:
        r = run_phase20_bot_registry(root=tmp_path, mode="chaos")
        assert r["ok"] is False


class TestMultiBotPersonasUpdated:
    def test_bot_personas_has_29_entries(self) -> None:
        from runtime_architecture_v2.multi_bot import BOT_PERSONAS
        assert len(BOT_PERSONAS) == 29

    def test_legacy_roles_preserved(self) -> None:
        from runtime_architecture_v2.multi_bot import BOT_PERSONAS
        assert "ceo_coordinator" in BOT_PERSONAS
        assert "content_lead" in BOT_PERSONAS
        assert "tech_lead" in BOT_PERSONAS
