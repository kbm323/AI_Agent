"""Live runtime adapters for runtime smoke execution.

This module contains the production-facing boundaries for the deterministic
``runtime_smoke_packet`` module:

* Discord REST message posting for thread replies and result-channel cross-posts.
* opencode-go subprocess runner wiring for Qwen/GLM wrappers.
* OpenClaw command execution through a JSON packet file.

All network/process boundaries remain injectable so tests can verify behavior
without making live Discord, opencode-go, or OpenClaw calls.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.opencode_glm_wrapper import get_runner as get_glm_runner
from src.opencode_qwen_wrapper import get_runner as get_qwen_runner
from src.runtime_smoke_packet import RuntimeSmokeDependencies

HttpPost = Callable[
    [str],
    tuple[int, Mapping[str, Any]],
]
OpenClawRunner = Callable[
    [list[str], float, dict[str, str] | None, str | None],
    tuple[int, str, str],
]

_USER_AGENT = "AI_Agent runtime smoke/1.0"
_DEFAULT_DISCORD_API_BASE = "https://discord.com/api/v10"
_DEFAULT_OPENCLAW_PACKET_DIR = ".runtime/openclaw_packets"


@dataclass(frozen=True)
class DiscordLiveConfig:
    """Configuration for Discord REST delivery."""

    bot_token: str
    api_base: str = _DEFAULT_DISCORD_API_BASE
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.bot_token or not self.bot_token.strip():
            raise ValueError("bot_token must be non-empty")
        if not self.api_base or not self.api_base.strip():
            raise ValueError("api_base must be non-empty")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")


@dataclass(frozen=True)
class OpenClawLiveConfig:
    """Configuration for OpenClaw packet-file CLI execution."""

    command: tuple[str, ...] = ("openclaw", "execute", "--packet")
    packet_dir: str = _DEFAULT_OPENCLAW_PACKET_DIR
    timeout_seconds: float = 60.0
    env: dict[str, str] | None = None
    workdir: str | None = None

    def __post_init__(self) -> None:
        if not self.command or any(not part.strip() for part in self.command):
            raise ValueError("command must contain non-empty parts")
        if not self.packet_dir or not self.packet_dir.strip():
            raise ValueError("packet_dir must be non-empty")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")


@dataclass(frozen=True)
class RuntimeLiveAdapterConfig:
    """Combined live adapter configuration."""

    discord: DiscordLiveConfig
    openclaw: OpenClawLiveConfig = field(default_factory=OpenClawLiveConfig)
    workdir: str | None = None
    qwen_model: str = "qwen3.6-plus"
    glm_model: str = "glm-5.1"
    subprocess_env: dict[str, str] = field(default_factory=dict)


# ── Discord REST adapter ────────────────────────────────────────────────

def create_discord_thread_poster(
    config: DiscordLiveConfig,
    *,
    http_post: Callable[..., tuple[int, Mapping[str, Any]]] | None = None,
) -> Callable[[str, str], Mapping[str, Any]]:
    """Create a Discord poster for original thread replies."""

    return _create_discord_message_poster(config, http_post=http_post)


def create_discord_cross_poster(
    config: DiscordLiveConfig,
    *,
    http_post: Callable[..., tuple[int, Mapping[str, Any]]] | None = None,
) -> Callable[[str, str], Mapping[str, Any]]:
    """Create a Discord poster for result-channel cross posts."""

    return _create_discord_message_poster(config, http_post=http_post)


def _create_discord_message_poster(
    config: DiscordLiveConfig,
    *,
    http_post: Callable[..., tuple[int, Mapping[str, Any]]] | None,
) -> Callable[[str, str], Mapping[str, Any]]:
    sender = http_post or _default_http_post

    def post_message(channel_id: str, content: str) -> Mapping[str, Any]:
        if not channel_id or not channel_id.strip():
            raise ValueError("channel_id must be non-empty")
        if not content or not content.strip():
            raise ValueError("content must be non-empty")

        url = _discord_message_url(config.api_base, channel_id)
        headers = {
            "Authorization": f"Bot {config.bot_token}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
        status_code, response = sender(
            url,
            headers=headers,
            body=body,
            timeout=config.timeout_seconds,
        )
        if status_code < 200 or status_code >= 300:
            message = str(response.get("message") or response)
            raise RuntimeError(
                f"Discord message post failed: {status_code} {message}"
            )
        message_id = str(response.get("id") or "")
        if not message_id:
            raise RuntimeError("Discord message post succeeded without message id")
        return {
            "message_id": message_id,
            "channel_id": str(response.get("channel_id") or channel_id),
        }

    return post_message


def _discord_message_url(api_base: str, channel_id: str) -> str:
    base = api_base.rstrip("/")
    return f"{base}/channels/{channel_id.strip()}/messages"


def _default_http_post(
    url: str,
    *,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Mapping[str, Any]]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return response.status, _json_object_or_message(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        return exc.code, _json_object_or_message(payload)


def _json_object_or_message(payload: str) -> Mapping[str, Any]:
    if not payload.strip():
        return {}
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return {"message": payload}
    return decoded if isinstance(decoded, dict) else {"message": decoded}


# ── OpenClaw CLI adapter ────────────────────────────────────────────────

def create_openclaw_executor(
    config: OpenClawLiveConfig,
    *,
    runner: OpenClawRunner | None = None,
) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    """Create an OpenClaw executor using a JSON packet file boundary."""

    active_runner = runner or _default_openclaw_runner

    def execute(action: dict[str, Any]) -> Mapping[str, Any]:
        execution_id = str(action.get("execution_id") or "").strip()
        if not execution_id:
            raise ValueError("execution_id is required for OpenClaw execution")

        packet_path = _write_openclaw_packet(config.packet_dir, execution_id, action)
        command = [*config.command, str(packet_path)]
        exit_code, stdout, stderr = active_runner(
            command,
            config.timeout_seconds,
            config.env,
            config.workdir,
        )
        if exit_code != 0:
            detail = stderr.strip() or stdout.strip() or "no output"
            raise RuntimeError(
                f"OpenClaw command failed with code {exit_code}: {detail[:500]}"
            )
        receipt = _json_object_or_message(stdout)
        return {
            "execution_id": str(receipt.get("execution_id") or execution_id),
            "state": str(receipt.get("state") or "completed"),
            "packet_path": str(packet_path),
            **dict(receipt),
        }

    return execute


def _write_openclaw_packet(
    packet_dir: str,
    execution_id: str,
    action: Mapping[str, Any],
) -> Path:
    safe_name = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in execution_id
    )
    root = Path(packet_dir)
    root.mkdir(parents=True, exist_ok=True)
    packet_path = root / f"{safe_name}.json"
    packet_path.write_text(
        json.dumps(dict(action), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return packet_path


def _default_openclaw_runner(
    command: list[str],
    timeout_seconds: float,
    env: dict[str, str] | None,
    workdir: str | None,
) -> tuple[int, str, str]:
    merged_env = None if env is None else {**os.environ, **env}
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=merged_env,
            cwd=workdir,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = _subprocess_text(exc.stdout)
        stderr = _subprocess_text(exc.stderr) or "timeout"
        return -1, stdout, stderr
    except OSError as exc:
        return -1, "", f"OSError: {exc}"


def _subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


# ── Config/dependency wiring ────────────────────────────────────────────

def load_runtime_live_env(
    env: Mapping[str, str] | None = None,
    *,
    hermes_env_path: str | Path | None = None,
) -> dict[str, str]:
    """Load runtime env, falling back to Hermes' ``~/.hermes/.env``.

    Explicit *env* mappings are used as-is for tests and callers that already
    control their process environment.  When *env* is ``None``, Hermes' secret
    file is read first and the live process environment overrides it.
    """

    if env is not None:
        return dict(env)

    default_path = Path.home() / ".hermes" / ".env"
    source = _read_env_file(
        Path(hermes_env_path) if hermes_env_path is not None else default_path
    )
    source.update(os.environ)
    return source


def load_runtime_live_config(
    env: Mapping[str, str] | None = None,
    *,
    hermes_env_path: str | Path | None = None,
) -> RuntimeLiveAdapterConfig:
    """Load live adapter config from environment-like mapping.

    Supported names:
    - DISCORD_TOKEN or DISCORD_BOT_TOKEN
    - NOTION_API_KEY or NOTION_API_TOKEN
    - DISCORD_API_BASE
    - OPENCLAW_COMMAND, OPENCLAW_PACKET_DIR, OPENCLAW_TIMEOUT_SECONDS
    - AI_AGENT_WORKDIR
    - QWEN_MODEL, GLM_MODEL
    """

    source = load_runtime_live_env(env, hermes_env_path=hermes_env_path)
    token = source.get("DISCORD_TOKEN") or source.get("DISCORD_BOT_TOKEN") or ""
    openclaw_command = tuple(
        shlex.split(source.get("OPENCLAW_COMMAND", "openclaw execute --packet"))
    )
    workdir = source.get("AI_AGENT_WORKDIR") or None
    return RuntimeLiveAdapterConfig(
        discord=DiscordLiveConfig(
            bot_token=token,
            api_base=source.get("DISCORD_API_BASE", _DEFAULT_DISCORD_API_BASE),
            timeout_seconds=float(source.get("DISCORD_TIMEOUT_SECONDS", "10")),
        ),
        openclaw=OpenClawLiveConfig(
            command=openclaw_command,
            packet_dir=source.get("OPENCLAW_PACKET_DIR", _DEFAULT_OPENCLAW_PACKET_DIR),
            timeout_seconds=float(source.get("OPENCLAW_TIMEOUT_SECONDS", "60")),
            env=_runtime_subprocess_env(source),
            workdir=workdir,
        ),
        workdir=workdir,
        qwen_model=source.get("QWEN_MODEL", "qwen3.6-plus"),
        glm_model=source.get("GLM_MODEL", "glm-5.1"),
        subprocess_env=_runtime_subprocess_env(source),
    )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if key:
            values[key] = value
    return values


def _runtime_subprocess_env(source: Mapping[str, str]) -> dict[str, str]:
    keys = [
        "OPENCODE_GO_API_KEY",
        "OPENROUTER_API_KEY",
        "QWEN_API_KEY",
        "GLM_API_KEY",
        "ZAI_API_KEY",
        "NOTION_API_KEY",
        "NOTION_API_TOKEN",
        "NOTION_SECOND_BRAIN_ROOT_PAGE_ID",
        "NOTION_SCHEDULE_DATA_SOURCE_ID",
        "NOTION_IDEA_DATA_SOURCE_ID",
    ]
    values = {key: source[key] for key in keys if source.get(key)}
    if "OPENCODE_API_KEY" not in values and source.get("OPENCODE_GO_API_KEY"):
        values["OPENCODE_API_KEY"] = source["OPENCODE_GO_API_KEY"]
    if "NOTION_API_TOKEN" not in values and source.get("NOTION_API_KEY"):
        values["NOTION_API_TOKEN"] = source["NOTION_API_KEY"]
    if "NOTION_API_KEY" not in values and source.get("NOTION_API_TOKEN"):
        values["NOTION_API_KEY"] = source["NOTION_API_TOKEN"]
    return values


def _with_extra_env(
    runner: OpenClawRunner,
    extra_env: Mapping[str, str],
) -> OpenClawRunner:
    def run(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        merged_env = {**extra_env, **(env or {})}
        return runner(command, timeout_seconds, merged_env or None, workdir)

    return run


def create_runtime_smoke_live_dependencies(
    config: RuntimeLiveAdapterConfig,
    *,
    http_post: Callable[..., tuple[int, Mapping[str, Any]]] | None = None,
    openclaw_runner: OpenClawRunner | None = None,
) -> RuntimeSmokeDependencies:
    """Wire live adapters into ``RuntimeSmokeDependencies``."""

    return RuntimeSmokeDependencies(
        post_thread=create_discord_thread_poster(config.discord, http_post=http_post),
        cross_post=create_discord_cross_poster(config.discord, http_post=http_post),
        qwen_runner=_with_extra_env(get_qwen_runner(), config.subprocess_env),
        glm_runner=_with_extra_env(get_glm_runner(), config.subprocess_env),
        openclaw_executor=create_openclaw_executor(
            config.openclaw,
            runner=openclaw_runner,
        ),
    )


__all__ = [
    "DiscordLiveConfig",
    "OpenClawLiveConfig",
    "RuntimeLiveAdapterConfig",
    "create_discord_cross_poster",
    "create_discord_thread_poster",
    "create_openclaw_executor",
    "create_runtime_smoke_live_dependencies",
    "load_runtime_live_env",
    "load_runtime_live_config",
]
