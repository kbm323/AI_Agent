from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscordAttachment:
    attachment_id: str
    filename: str
    content_type: str
    size: int
    url: str


@dataclass(frozen=True)
class DiscordAuthor:
    user_id: str
    display_name: str
    bot: bool = False


@dataclass(frozen=True)
class DiscordMessage:
    message_id: str
    created_at: str
    content: str
    author: DiscordAuthor
    attachments: tuple[DiscordAttachment, ...] = ()


@dataclass(frozen=True)
class DiscordConversation:
    guild_id: str
    parent_channel_id: str
    thread_id: str
    thread_name: str
    visibility: str
    messages: tuple[DiscordMessage, ...]


@dataclass(frozen=True)
class BotIdentity:
    role: str
    hermes_profile: str


@dataclass(frozen=True)
class ParticipantIdentity:
    role: str
    hermes_profile: str
    discord_name: str
    discord_user_id: str


def load_bot_identities(path: str | Path) -> dict[str, BotIdentity]:
    raw_identities = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        str(discord_user_id): BotIdentity(
            role=str(identity["role"]),
            hermes_profile=str(identity["hermes_profile"]),
        )
        for discord_user_id, identity in raw_identities.items()
    }


class ParticipantResolver:
    def __init__(self, identities: Mapping[str, BotIdentity]) -> None:
        self._identities = dict(identities)

    def resolve(self, author: DiscordAuthor) -> ParticipantIdentity:
        known = self._identities.get(author.user_id)
        return ParticipantIdentity(
            role=known.role if known else "",
            hermes_profile=known.hermes_profile if known else "",
            discord_name=author.display_name,
            discord_user_id=author.user_id,
        )
