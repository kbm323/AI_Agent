"""Phase 20: 29-Bot Discord Registry and Deployment Manifest.

Defines the complete 29-role bot topology for the AI Virtual Entertainment
Company. Each bot is mention-gated by default and registered with department,
display name, and permission profile.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

BOT_REGISTRY_ID = "phase20_29_bot_discord_registry"

_DEFAULT_PERMISSIONS = (
    "read_message_history",
    "send_messages",
    "send_messages_in_threads",
    "embed_links",
    "attach_files",
    "use_slash_commands",
)


@dataclass(frozen=True)
class BotProfile:
    """One Discord bot profile in the virtual company."""

    role_id: str
    display_name: str
    department: str
    permissions: tuple[str, ...] = _DEFAULT_PERMISSIONS
    mention_gated: bool = True
    priority: str = "P2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "permissions", tuple(self.permissions))

    def to_dict(self) -> dict[str, object]:
        return {
            "role_id": self.role_id,
            "display_name": self.display_name,
            "department": self.department,
            "permissions": list(self.permissions),
            "mention_gated": self.mention_gated,
            "priority": self.priority,
        }


# ── 29-Bot Registry ────────────────────────────────────────────────────

_EXECUTIVE: tuple[BotProfile, ...] = (
    BotProfile("ceo_coordinator", "대표", "Executive", priority="P0"),
    BotProfile("coo", "운영총괄", "Executive", priority="P1"),
    BotProfile("cfo", "재무총괄", "Executive"),
)

_CONTENT: tuple[BotProfile, ...] = (
    BotProfile("content_lead", "콘텐츠 팀장", "Content", priority="P1"),
    BotProfile("producer", "프로듀서", "Content"),
    BotProfile("writer", "작가", "Content"),
    BotProfile("editor", "편집자", "Content"),
    BotProfile("script_director", "대본감독", "Content"),
    BotProfile("storyboard_artist", "스토리보드 아티스트", "Content"),
)

_ART: tuple[BotProfile, ...] = (
    BotProfile("art_lead", "아트 팀장", "Art", priority="P1"),
    BotProfile("character_designer", "캐릭터 디자이너", "Art"),
    BotProfile("background_artist", "배경 아티스트", "Art"),
    BotProfile("animator", "애니메이터", "Art"),
    BotProfile("vfx_artist", "VFX 아티스트", "Art"),
)

_TECH: tuple[BotProfile, ...] = (
    BotProfile("tech_lead", "기술 팀장", "Technology", priority="P1"),
    BotProfile("engine_developer", "엔진 개발자", "Technology"),
    BotProfile("backend_developer", "백엔드 개발자", "Technology"),
    BotProfile("ai_engineer", "AI 엔지니어", "Technology"),
    BotProfile("devops_engineer", "데브옵스 엔지니어", "Technology"),
)

_MARKETING: tuple[BotProfile, ...] = (
    BotProfile("marketing_lead", "마케팅 팀장", "Marketing", priority="P1"),
    BotProfile("sns_manager", "SNS 매니저", "Marketing"),
    BotProfile("community_manager", "커뮤니티 매니저", "Marketing"),
    BotProfile("business_support_lead", "사업지원 팀장", "Marketing"),
    BotProfile("partnership_manager", "파트너십 매니저", "Marketing"),
)

_QUALITY: tuple[BotProfile, ...] = (
    BotProfile("validation_audit", "검증 팀장", "Quality", priority="P1"),
    BotProfile("quality_lead", "QA 리드", "Quality"),
    BotProfile("legal_compliance", "법무/컴플라이언스", "Quality"),
)

_SUPPORT: tuple[BotProfile, ...] = (
    BotProfile("project_manager", "프로젝트 매니저", "Support"),
    BotProfile("hr_lead", "인사/문화", "Support"),
)

_ALL_PROFILES: tuple[BotProfile, ...] = (
    _EXECUTIVE + _CONTENT + _ART + _TECH + _MARKETING + _QUALITY + _SUPPORT
)

# ── Registry ───────────────────────────────────────────────────────────


class BotRegistry:
    """Immutable registry of all 29 bot profiles."""

    def __init__(self, profiles: tuple[BotProfile, ...] = _ALL_PROFILES) -> None:
        self.profiles = profiles
        self._by_id: dict[str, BotProfile] = {p.role_id: p for p in profiles}
        self._by_dept: dict[str, tuple[BotProfile, ...]] = {}
        for p in profiles:
            self._by_dept.setdefault(p.department, ())
            self._by_dept[p.department] = self._by_dept[p.department] + (p,)

    def get(self, role_id: str) -> BotProfile | None:
        return self._by_id.get(role_id)

    def by_department(self, department: str) -> tuple[BotProfile, ...]:
        return self._by_dept.get(department, ())

    def to_manifest(self) -> dict[str, object]:
        return {
            "registry_id": BOT_REGISTRY_ID,
            "total_bots": len(self.profiles),
            "departments": sorted(self._by_dept.keys()),
            "bots": [p.to_dict() for p in self.profiles],
        }


DEFAULT_REGISTRY = BotRegistry()

# ── Bot Persona Display Names (for multi_bot.py compatibility) ─────────

BOT_PERSONA_NAMES: dict[str, str] = {
    p.role_id: p.display_name for p in _ALL_PROFILES
}

# ── Phase 20 CLI Pilot ─────────────────────────────────────────────────


def run_phase20_bot_registry(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live"] = "dry-run",
) -> dict[str, Any]:
    """Generate the 29-bot deployment manifest."""

    if mode not in ("dry-run", "live"):
        return {
            "ok": False,
            "pilot_id": BOT_REGISTRY_ID,
            "mode": mode,
            "error": f"unsupported mode: {mode}",
        }

    root = Path(root)
    registry = DEFAULT_REGISTRY
    manifest = registry.to_manifest()
    artifact_path = _write_manifest_artifact(root, manifest)

    dept_counts = {
        dept: len(registry.by_department(dept))
        for dept in sorted(registry._by_dept.keys())
    }

    return {
        "ok": True,
        "pilot_id": BOT_REGISTRY_ID,
        "mode": mode,
        "total_bots": len(registry.profiles),
        "departments": dept_counts,
        "manifest_path": str(artifact_path),
        "error": "",
    }


def _write_manifest_artifact(root: Path, manifest: dict[str, object]) -> Path:
    path = root / "runtime" / "phase20-bots" / "bot_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".bot_manifest.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path
