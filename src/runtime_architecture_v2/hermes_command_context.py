from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

GetEnv = Callable[[str, str], str]
_SNOWFLAKE_RE = re.compile(r"^[0-9]{1,24}$")
_SNOWFLAKE_TIMESTAMP_MASK = ~((1 << 22) - 1)


def discord_timestamp_floor_snowflake(value: object) -> str:
    candidate = str(value or "")
    if not _SNOWFLAKE_RE.fullmatch(candidate):
        return ""
    return str(int(candidate) & _SNOWFLAKE_TIMESTAMP_MASK)


@dataclass(frozen=True)
class HermesCommandContext:
    platform: str = ""
    chat_id: str = ""
    chat_name: str = ""
    thread_id: str = ""
    user_id: str = ""
    user_name: str = ""
    session_id: str = ""
    invocation_message_id: str = ""
    invocation_boundary_kind: str = ""
    session_start_message_id: str = ""
    source_kind: str = ""
    profile: str = ""

    @property
    def is_discord_thread(self) -> bool:
        return self.platform == "discord" and bool(self.thread_id)


def read_hermes_command_context(
    get_env: GetEnv | None = None,
) -> HermesCommandContext:
    if get_env is None:
        from gateway.session_context import get_session_env

        get_env = get_session_env
    return HermesCommandContext(
        platform=get_env("HERMES_SESSION_PLATFORM", ""),
        chat_id=get_env("HERMES_SESSION_CHAT_ID", ""),
        chat_name=get_env("HERMES_SESSION_CHAT_NAME", ""),
        thread_id=get_env("HERMES_SESSION_THREAD_ID", ""),
        user_id=get_env("HERMES_SESSION_USER_ID", ""),
        user_name=get_env("HERMES_SESSION_USER_NAME", ""),
        session_id=get_env("HERMES_SESSION_ID", ""),
        invocation_message_id=discord_timestamp_floor_snowflake(
            get_env("HERMES_SESSION_MESSAGE_ID", "")
        ),
        profile=get_env("HERMES_SESSION_PROFILE", ""),
    )
