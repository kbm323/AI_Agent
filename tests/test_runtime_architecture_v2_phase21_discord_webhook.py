"""Phase 21 Discord Interaction Webhook — TDD tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_architecture_v2.discord_webhook import (
    DefaultCommandRouter,
    DiscordCommandRouter,
    DiscordInteraction,
    InteractionResponse,
    SlashCommand,
    run_phase21_webhook,
)


def _slash_cmd(name: str = "test") -> DiscordInteraction:
    return DiscordInteraction(
        interaction_id="int-001",
        type=2,
        user_id="u-test",
        channel_id="ch-test",
        command_name=name,
        options={},
    )


class TestSlashCommand:
    def test_command_fields(self) -> None:
        cmd = SlashCommand(
            name="test",
            description="test command",
            handler_bot="ceo_coordinator",
        )
        assert cmd.name == "test"
        assert cmd.handler_bot == "ceo_coordinator"

    def test_command_to_dict(self) -> None:
        cmd = SlashCommand("test", "desc", "bot_x")
        d = cmd.to_dict()
        assert d["name"] == "test"


class TestDiscordInteraction:
    def test_interaction_fields(self) -> None:
        di = _slash_cmd("회의")
        assert di.command_name == "회의"
        assert di.type == 2

    def test_interaction_to_dict(self) -> None:
        di = _slash_cmd("회의")
        d = di.to_dict()
        assert d["command_name"] == "회의"


class TestInteractionResponse:
    def test_response_ok(self) -> None:
        resp = InteractionResponse(
            ok=True, interaction_id="int-1", content="완료",
            handler_bot="비서", meeting_run_id="mr-1",
        )
        assert resp.ok is True

    def test_response_no_secret_leak(self) -> None:
        resp = InteractionResponse(
            ok=False, interaction_id="int-1",
            content="api_key=sk-secret-123 leaked",
        )
        d = resp.to_dict()
        raw = json.dumps(d)
        assert "sk-secret" not in raw
        assert "api_key" not in raw


class TestCommandRouter:
    def test_route_known_command(self, tmp_path: Path) -> None:
        router = DiscordCommandRouter(root=tmp_path, dry_run=True)
        di = _slash_cmd("회의")
        resp = router.route(di)
        assert resp.ok is True
        assert resp.handler_bot == "버추얼컴퍼니-Hermes"

    def test_route_unknown_command(self, tmp_path: Path) -> None:
        router = DiscordCommandRouter(root=tmp_path, dry_run=True)
        di = _slash_cmd("없는명령어")
        resp = router.route(di)
        assert resp.ok is False
        assert "알 수 없는" in resp.content

    def test_route_status_command(self, tmp_path: Path) -> None:
        router = DiscordCommandRouter(root=tmp_path, dry_run=True)
        di = _slash_cmd("상태")
        resp = router.route(di)
        assert resp.ok is True
        assert resp.handler_bot == "버추얼컴퍼니-Hermes"

    def test_route_team_work(self, tmp_path: Path) -> None:
        router = DiscordCommandRouter(root=tmp_path, dry_run=True)
        di = DiscordInteraction(
            interaction_id="int-t", type=2, user_id="u1",
            channel_id="ch1", command_name="팀작업",
            options={"팀": "콘텐츠", "내용": "테스트"},
        )
        resp = router.route(di)
        assert resp.ok is True
        assert "콘텐츠" in resp.content

    def test_default_router_has_5_commands(self) -> None:
        cmds = DefaultCommandRouter(root=Path("/tmp"))._commands
        assert len(cmds) == 5

    def test_5_commands_have_valid_handlers(self) -> None:
        from runtime_architecture_v2.bot_registry import DEFAULT_REGISTRY
        valid_ids = {p.role_id for p in DEFAULT_REGISTRY.profiles}
        valid_ids.add("버추얼컴퍼니-Hermes")
        for cmd in DefaultCommandRouter(root=Path("/tmp"))._commands.values():
            assert cmd.handler_bot in valid_ids, f"{cmd.name}: {cmd.handler_bot}"


class TestPhase21CLI:
    def test_dry_run(self, tmp_path: Path) -> None:
        r = run_phase21_webhook(root=tmp_path, mode="dry-run")
        assert r["ok"] is True
        assert r["commands_count"] == 5

    def test_manifest_written(self, tmp_path: Path) -> None:
        _ = run_phase21_webhook(root=tmp_path, mode="dry-run")
        path = tmp_path / "runtime" / "phase21-webhook" / "slash_manifest.json"
        assert path.exists()


class TestManifestFormat:
    def test_manifest_discord_api_format(self) -> None:
        # Discord API: {name, description, options: [{name, description, type}]}
        from runtime_architecture_v2.discord_webhook import _COMMANDS
        for cmd in _COMMANDS:
            d = cmd.to_manifest_entry()
            assert "name" in d
            assert "description" in d
            assert isinstance(d.get("options"), list)
