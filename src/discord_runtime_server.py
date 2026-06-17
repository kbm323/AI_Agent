"""Small stdlib HTTP server for Discord runtime interactions."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.discord_runtime_interaction import handle_runtime_interaction_request
from src.discord_runtime_smoke_runner import (
    run_runtime_smoke_for_interaction,
    send_interaction_completion_followup,
)
from src.runtime_live_adapters import load_runtime_live_env

SmokeStarter = Callable[[dict[str, Any]], Any]


def make_runtime_request_handler(
    *,
    public_key: str,
    start_smoke: SmokeStarter,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to a Discord public key and starter."""

    class RuntimeInteractionHandler(BaseHTTPRequestHandler):
        server_version = "AI_AgentDiscordRuntime/0.1"

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path not in {"/discord/interactions", "/interactions"}:
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length)
            status, body = handle_runtime_interaction_request(
                raw_body=raw_body,
                headers={key: value for key, value in self.headers.items()},
                public_key=public_key,
                start_smoke=start_smoke,
            )
            self._send_json(status, body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/healthz":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def log_message(self, format: str, *args: Any) -> None:
            print(
                f"[discord-runtime] {self.address_string()} {format % args}",
                file=sys.stderr,
            )

        def _send_json(self, status: int, body: Mapping[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return RuntimeInteractionHandler


def create_background_smoke_starter(
    *,
    env: Mapping[str, str] | None = None,
) -> SmokeStarter:
    """Create a starter that runs live smoke in a daemon thread."""

    def start(payload: dict[str, Any]) -> None:
        thread = threading.Thread(
            target=_run_and_log_smoke,
            args=(payload, dict(env) if env is not None else None),
            daemon=True,
        )
        thread.start()

    return start


def _run_and_log_smoke(payload: dict[str, Any], env: dict[str, str] | None) -> None:
    try:
        report = run_runtime_smoke_for_interaction(payload, env=env)
    except Exception as exc:  # pragma: no cover - defensive boundary
        report = {"ok": False, "stage": "exception", "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False), file=sys.stderr, flush=True)
    followup_report = send_interaction_completion_followup(payload, report)
    print(
        json.dumps({"interaction_followup": followup_report}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )


def resolve_discord_public_key(env: Mapping[str, str]) -> str:
    """Resolve Discord application public key from env or Bot API."""

    if env.get("DISCORD_PUBLIC_KEY"):
        return str(env["DISCORD_PUBLIC_KEY"])
    token = env.get("DISCORD_BOT_TOKEN") or env.get("DISCORD_TOKEN")
    if not token:
        return ""
    request = urllib.request.Request(
        "https://discord.com/api/v10/oauth2/applications/@me",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "AI_Agent discord runtime server/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError:
        return ""
    return str(data.get("verify_key") or "")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Discord runtime interaction server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--public-key", default="")
    args = parser.parse_args(argv)

    env = load_runtime_live_env()
    public_key = args.public_key or resolve_discord_public_key(env)
    if not public_key:
        print("DISCORD_PUBLIC_KEY or Discord bot token application info is required", file=sys.stderr)
        return 2

    handler_cls = make_runtime_request_handler(
        public_key=public_key,
        start_smoke=create_background_smoke_starter(env=env),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)
    print(
        f"Discord runtime server listening on http://{args.host}:{args.port}/discord/interactions",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "create_background_smoke_starter",
    "make_runtime_request_handler",
    "main",
    "resolve_discord_public_key",
]
