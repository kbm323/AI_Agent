"""Command-line runner for the runtime smoke packet live adapters."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.runtime_live_adapters import (
    RuntimeLiveAdapterConfig,
    create_runtime_smoke_live_dependencies,
    load_runtime_live_config,
    load_runtime_live_env,
)
from src.runtime_smoke_packet import RuntimeSmokeConfig, run_runtime_smoke_packet

ExecutableResolver = Callable[[str], str | None]
SmokeRunner = Callable[..., Any]
DependencyFactory = Callable[[RuntimeLiveAdapterConfig], Any]


def build_default_payload(
    *,
    guild_id: str,
    channel_id: str,
    thread_id: str,
    user_id: str,
    topic: str,
    result_channel_id: str,
) -> dict[str, Any]:
    """Build a minimal Discord-like slash command payload for smoke tests."""

    return {
        "id": "0",
        "guild_id": guild_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "member": {"user": {"id": user_id}},
        "data": {
            "name": "meeting",
            "options": [
                {"name": "topic", "value": topic},
                {"name": "result_channel_id", "value": result_channel_id},
            ],
        },
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    smoke_runner: SmokeRunner = run_runtime_smoke_packet,
    dependency_factory: DependencyFactory = create_runtime_smoke_live_dependencies,
    executable_resolver: ExecutableResolver = shutil.which,
) -> int:
    """Run live runtime smoke or dry-run prerequisite checks."""

    args = _parse_args(argv)
    source_env = load_runtime_live_env(env)
    missing = _missing_prerequisites(source_env, executable_resolver)

    if args.dry_run:
        _print_json(
            {
                "ok": not missing,
                "mode": "dry-run",
                "missing": missing,
            }
        )
        return 0 if not missing else 2

    if missing:
        _print_json(
            {
                "ok": False,
                "mode": "live",
                "stage": "prerequisites",
                "missing": missing,
            }
        )
        return 2

    live_config = load_runtime_live_config(source_env)
    dependencies = dependency_factory(live_config)
    payload = build_default_payload(
        guild_id=args.guild_id,
        channel_id=args.channel_id,
        thread_id=args.thread_id,
        user_id=args.user_id,
        topic=args.topic,
        result_channel_id=args.result_channel_id,
    )
    result = smoke_runner(
        payload=payload,
        config=RuntimeSmokeConfig(
            meetings_root=args.meetings_root,
            workdir=args.workdir,
            qwen_model=live_config.qwen_model,
            glm_model=live_config.glm_model,
            openclaw_action_type=args.openclaw_action_type,
            openclaw_risk_level=args.openclaw_risk_level,
            openclaw_target=args.openclaw_target,
            openclaw_expected_duration_seconds=args.openclaw_expected_duration,
        ),
        dependencies=dependencies,
    )
    report = _result_to_report(result)
    _print_json(report)
    return 0 if report.get("ok") else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run AI_Agent runtime smoke through live adapters.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--meetings-root", default="meetings")
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--topic", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--result-channel-id", required=True)
    parser.add_argument("--guild-id", default="")
    parser.add_argument("--user-id", default="runtime-smoke")
    parser.add_argument("--openclaw-action-type", default="diagnostic_read")
    parser.add_argument("--openclaw-risk-level", default="high")
    parser.add_argument("--openclaw-target", default="runtime-smoke")
    parser.add_argument("--openclaw-expected-duration", type=float, default=5.0)
    return parser.parse_args(argv)


def _missing_prerequisites(
    env: Mapping[str, str],
    executable_resolver: ExecutableResolver,
) -> list[str]:
    missing: list[str] = []
    if not (env.get("DISCORD_TOKEN") or env.get("DISCORD_BOT_TOKEN")):
        missing.append("DISCORD_TOKEN or DISCORD_BOT_TOKEN")
    if executable_resolver("opencode-go") is None:
        missing.append("opencode-go")
    openclaw_command = env.get("OPENCLAW_COMMAND", "openclaw")
    parts = openclaw_command.split()
    openclaw_bin = parts[0] if parts else "openclaw"
    if executable_resolver(openclaw_bin) is None:
        missing.append(openclaw_bin)
    return missing


def _result_to_report(result: Any) -> dict[str, Any]:
    data = {
        key: getattr(result, key)
        for key in [
            "success",
            "stage",
            "error",
            "meeting_id",
            "context_packet_path",
            "discord_thread_message_id",
            "discord_cross_post_message_id",
            "qwen_success",
            "glm_success",
            "openclaw_state",
            "openclaw_error",
        ]
        if hasattr(result, key)
    }
    data["ok"] = bool(data.pop("success", False))
    return data


def _print_json(data: Mapping[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_default_payload", "main"]
