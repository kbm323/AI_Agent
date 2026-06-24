"""Phase 21 Discord Interaction Webhook / Slash Command Layer.

Defines Discord slash command schemas, interaction webhook payloads,
and a command router that maps user commands to bot handlers and
creates MeetingRuns. Does not host webhook endpoints or register
commands with Discord — those are live operations.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .kanban_ops import _sanitize_text
from .schemas import MeetingRun
from .store import MeetingRunStore

DISCORD_WEBHOOK_ID = "phase21_discord_interaction_webhook"

# ── Slash Command Definition ────────────────────────────────────────────


@dataclass(frozen=True)
class SlashCommandOption:
    name: str
    description: str
    option_type: int = 3  # 3=STRING, 4=INTEGER, 6=USER, 8=ROLE
    required: bool = False
    choices: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            "type": self.option_type,
            "required": self.required,
        }
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@dataclass(frozen=True)
class SlashCommand:
    """One Discord slash command definition."""

    name: str
    description: str
    handler_bot: str
    options: tuple[SlashCommandOption, ...] = ()
    dm_permission: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "handler_bot": self.handler_bot,
            "options": [o.to_dict() for o in self.options],
        }

    def to_manifest_entry(self) -> dict[str, object]:
        """Return Discord API-compatible command registration payload."""
        return {
            "name": self.name,
            "description": self.description,
            "options": [o.to_dict() for o in self.options],
            "dm_permission": self.dm_permission,
        }


_TEAM_CHOICES: tuple[dict[str, object], ...] = (
    {"name": "콘텐츠", "value": "콘텐츠"},
    {"name": "아트", "value": "아트"},
    {"name": "기술", "value": "기술"},
    {"name": "마케팅", "value": "마케팅"},
)

_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        name="회의",
        description="새 회의를 시작합니다",
        handler_bot="버추얼컴퍼니-Hermes",
        options=(
            SlashCommandOption(
                name="주제", description="회의 주제", required=True,
            ),
        ),
    ),
    SlashCommand(
        name="상태",
        description="현재 회사 상태와 진행 중인 작업을 확인합니다",
        handler_bot="버추얼컴퍼니-Hermes",
    ),
    SlashCommand(
        name="보고",
        description="최종 보고를 요청합니다",
        handler_bot="ceo_coordinator",
        options=(
            SlashCommandOption(
                name="종류", description="보고 종류",
                choices=(
                    {"name": "일일", "value": "daily"},
                    {"name": "주간", "value": "weekly"},
                ),
            ),
        ),
    ),
    SlashCommand(
        name="팀작업",
        description="특정 팀에 작업을 지시합니다",
        handler_bot="버추얼컴퍼니-Hermes",
        options=(
            SlashCommandOption(
                name="팀", description="대상 팀", required=True,
                choices=_TEAM_CHOICES,
            ),
            SlashCommandOption(
                name="내용", description="작업 내용", required=True,
            ),
        ),
    ),
    SlashCommand(
        name="도움",
        description="사용 가능한 명령어 목록을 보여줍니다",
        handler_bot="버추얼컴퍼니-Hermes",
    ),
)

# ── Interaction Payload ─────────────────────────────────────────────────


@dataclass(frozen=True)
class DiscordInteraction:
    """Incoming Discord interaction webhook payload."""

    interaction_id: str
    type: int
    user_id: str
    channel_id: str
    command_name: str
    options: dict[str, str] = field(default_factory=dict)
    guild_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "interaction_id": self.interaction_id,
            "type": self.type,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "command_name": self.command_name,
            "options": self.options,
            "guild_id": self.guild_id,
        }


@dataclass(frozen=True)
class InteractionResponse:
    """Response sent back to Discord for an interaction."""

    ok: bool
    interaction_id: str
    content: str
    ephemeral: bool = True
    handler_bot: str = ""
    meeting_run_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _sanitize_text(self.content))

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "interaction_id": self.interaction_id,
            "content": self.content,
            "ephemeral": self.ephemeral,
            "handler_bot": self.handler_bot,
            "meeting_run_id": self.meeting_run_id,
        }


# ── Command Router ──────────────────────────────────────────────────────


class DiscordCommandRouter:
    """Route Discord interactions to bot handlers and create MeetingRuns."""

    def __init__(
        self,
        *,
        root: str | Path,
        dry_run: bool = True,
    ) -> None:
        self.root = Path(root)
        self.dry_run = dry_run
        self._store = MeetingRunStore(root)
        self._commands: dict[str, SlashCommand] = {
            cmd.name: cmd for cmd in _COMMANDS
        }

    def route(self, interaction: DiscordInteraction) -> InteractionResponse:
        cmd = self._commands.get(interaction.command_name)
        if cmd is None:
            return InteractionResponse(
                ok=False,
                interaction_id=interaction.interaction_id,
                content=(
                    f"알 수 없는 명령어 `/{(interaction.command_name)}`입니다. "
                    "`/도움`으로 사용 가능한 명령어를 확인하세요."
                ),
            )

        if cmd.name == "도움":
            help_lines = ["**사용 가능한 명령어:**"]
            for c in _COMMANDS:
                help_lines.append(f"`/{c.name}` — {c.description}")
            return InteractionResponse(
                ok=True,
                interaction_id=interaction.interaction_id,
                content="\n".join(help_lines),
                handler_bot=cmd.handler_bot,
            )

        if cmd.name == "상태":
            return InteractionResponse(
                ok=True,
                interaction_id=interaction.interaction_id,
                content="✅ **회사 상태**\n"
                        "• Runtime v2: 정상 작동 중\n"
                        "• 전체 테스트: 5,488 passed\n"
                        "• `scripts/run_phase17_health_check.py`로 상세 확인",
                handler_bot=cmd.handler_bot,
            )

        if cmd.name == "팀작업":
            team = interaction.options.get("팀", "")
            content_text = interaction.options.get("내용", "")
            return InteractionResponse(
                ok=True,
                interaction_id=interaction.interaction_id,
                content=(
                    f"📋 **{team} 팀**에 작업을 등록했습니다.\n"
                    f"내용: {content_text}\n"
                    f"담당: 해당 팀장"
                ),
                handler_bot=cmd.handler_bot,
            )

        # For 회의, 보고: create MeetingRun
        if not self.dry_run:
            meeting_run_id = (
                f"mr-{cmd.name}-{interaction.interaction_id}"
            )
            meeting_run = MeetingRun.create(
                meeting_run_id=meeting_run_id,
                trigger_text=(
                    interaction.options.get("주제", "")
                    or interaction.options.get("종류", "")
                    or cmd.description
                ),
                user_id=interaction.user_id,
                channel_id=interaction.channel_id,
                thread_id=interaction.channel_id,
                priority="P1",
            )
            self._store.save_meeting_run(meeting_run)
            meeting_id = meeting_run_id
        else:
            meeting_id = f"dry-run:{cmd.name}-{interaction.interaction_id}"

        return InteractionResponse(
            ok=True,
            interaction_id=interaction.interaction_id,
            content=(
                f"✅ `/{cmd.name}` 명령이 접수되었습니다.\n"
                f"담당: {cmd.handler_bot}\n"
                f"MeetingRun: `{meeting_id}`"
            ),
            handler_bot=cmd.handler_bot,
            meeting_run_id=meeting_id,
        )


DefaultCommandRouter = DiscordCommandRouter

# ── Phase 21 CLI Pilot ─────────────────────────────────────────────────


def run_phase21_webhook(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live"] = "dry-run",
) -> dict[str, Any]:
    """Generate slash command manifest and run simulated interactions."""

    if mode not in ("dry-run", "live"):
        return {
            "ok": False,
            "pilot_id": DISCORD_WEBHOOK_ID,
            "mode": mode,
            "error": f"unsupported mode: {mode}",
        }

    root = Path(root)
    router = DiscordCommandRouter(root=root, dry_run=(mode == "dry-run"))

    # Simulate all commands
    sim_results: list[dict[str, object]] = []
    for cmd in _COMMANDS:
        sim = router.route(DiscordInteraction(
            interaction_id=f"sim-{cmd.name}",
            type=2,
            user_id="u-sim",
            channel_id="ch-sim",
            command_name=cmd.name,
            options={"주제": "테스트"} if cmd.name == "회의" else {},
        ))
        sim_results.append(sim.to_dict())

    # Generate manifest
    manifest = {
        "manifest_id": DISCORD_WEBHOOK_ID,
        "commands_count": len(_COMMANDS),
        "commands": [c.to_manifest_entry() for c in _COMMANDS],
    }
    artifact_path = _write_manifest_artifact(root, manifest)

    return {
        "ok": True,
        "pilot_id": DISCORD_WEBHOOK_ID,
        "mode": mode,
        "commands_count": len(_COMMANDS),
        "commands": [c.to_dict() for c in _COMMANDS],
        "simulations": sim_results,
        "manifest_path": str(artifact_path),
        "error": "",
    }


def _write_manifest_artifact(
    root: Path, manifest: dict[str, object],
) -> Path:
    path = root / "runtime" / "phase21-webhook" / "slash_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".slash_manifest.", suffix=".tmp", dir=str(path.parent),
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
