"""Discord message mention extractor for the AI_Agent meeting system.

Sub-AC 2a: Receives raw Discord message event payload (MESSAGE_CREATE
gateway event), detects bot @mention, and returns cleaned message text
with metadata (author, channel, guild).  Testable with mock Discord
gateway message objects.

This module bridges Discord's gateway event format and the meeting
command pipeline.  It extracts structured command information from
bot-@mention messages, producing the building blocks for a
MeetingCommandRequest ready for the meeting trigger.

Track A (Discord Integration Surface) specifies two initiation paths:
/meeting slash command and @AI_Company mention.  This module handles
the mention path — slash commands are processed by the gateway adapter
before reaching this extractor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Discord message event (raw gateway payload) ────────────────────────


@dataclass(frozen=True)
class DiscordUser:
    """Minimal user object from a Discord gateway MESSAGE_CREATE event.

    Mirrors the subset of the Discord API User structure that the
    extractor needs to identify the author and the bot itself.
    """

    id: str
    """Discord snowflake ID."""

    username: str
    """User's display name (not nickname)."""

    bot: bool = False
    """True when the user is a bot account."""


@dataclass(frozen=True)
class DiscordMention:
    """A user mention parsed from a Discord gateway event payload.

    Discord emits ``mentions`` as an array in every MESSAGE_CREATE
    event.  Each entry contains at minimum the mentioned user's id
    and username.
    """

    id: str
    """Discord snowflake ID of the mentioned user."""

    username: str
    """Username of the mentioned user."""

    bot: bool = False
    """True when the mentioned user is a bot account."""


@dataclass(frozen=True)
class DiscordMessageEvent:
    """Typed representation of a Discord gateway MESSAGE_CREATE event.

    Captures the minimal set of fields required for bot-mention
    detection and command extraction.  This is a projection of the
    full Discord gateway payload — only the fields used by the
    extractor are modeled here.

    Design note: the dataclass is frozen so test fixtures can be
    treated as value objects and compared directly in assertions.
    """

    id: str
    """Discord message snowflake ID."""

    content: str
    """The full raw message text (may include ``<@bot_id>`` mentions)."""

    author: DiscordUser
    """The user who sent the message."""

    channel_id: str
    """Discord channel snowflake ID."""

    mentions: tuple[DiscordMention, ...] = ()
    """Array of users mentioned in the message (from the gateway event)."""

    guild_id: str = ""
    """Discord guild/server ID (empty for DM channels)."""


# ── Extraction result ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractedMentionCommand:
    """Cleaned message text with source metadata after mention extraction.

    When a valid @bot mention is detected the ``content`` field
    contains the user's message with the bot mention stripped and
    surrounding whitespace collapsed.  When no valid mention is found
    ``is_command`` is ``False`` and the caller should ignore this
    message (it was not directed at the bot).
    """

    content: str
    """Cleaned message text with bot mention removed."""

    author_id: str
    """Discord user ID of the message author."""

    author_name: str
    """Username of the message author."""

    channel_id: str
    """Discord channel ID where the message was sent."""

    guild_id: str
    """Discord guild ID (empty string for DMs)."""

    message_id: str
    """Original Discord message snowflake."""

    is_command: bool = True
    """True when a valid bot mention was detected and extracted."""

    mention_count: int = 1
    """Number of bot mentions found in the message (for diagnostics)."""


# ── Sentinel for non-command messages ───────────────────────────────────


@dataclass(frozen=True)
class NoMentionResult:
    """Returned when a message contains no bot mention.

    This is a lightweight sentinel that avoids creating a full
    ExtractedMentionCommand with dummy fields.  Callers can check
    ``is_command`` without type-guessing.
    """

    is_command: bool = False
    """Always False — this message was not directed at the bot."""

    reason: str = ""
    """Why the message was rejected (e.g. 'no_mention', 'empty_content')."""


# ── Public API ─────────────────────────────────────────────────────────


def extract_mention_command(
    event: DiscordMessageEvent,
    *,
    bot_user_id: str,
    bot_name: str = "",
) -> ExtractedMentionCommand | NoMentionResult:
    """Extract a cleaned meeting command from a Discord message event.

    Receives a raw Discord MESSAGE_CREATE event, detects whether the
    configured bot was @-mentioned, strips the mention from the
    content, and returns a typed result with all required metadata.

    The detection strategy honours how Discord represents @mentions
    in gateway events:

    1. **Gateway ``mentions`` array** — Discord populates the
       ``mentions`` field of the MESSAGE_CREATE event with the list
       of mentioned users.  This is the primary detection mechanism
       and works for both standard and nickname mentions.

    2. **Raw content fallback** — If the ``mentions`` array is empty
       (unusual but possible with certain client bugs or third-party
       integrations), the function falls back to scanning the raw
       ``content`` string for the literal ``<@bot_user_id>`` pattern.

    Args:
        event: A Discord MESSAGE_CREATE gateway event payload.
        bot_user_id: The Discord snowflake ID of the bot (e.g.
                     ``"123456789012345678"``).  Used to match
                     against the ``mentions`` array and as the
                     raw-content fallback pattern.
        bot_name: Optional bot display name for human-readable
                  diagnostic messages.

    Returns:
        * ``ExtractedMentionCommand`` with ``is_command=True`` when
          a valid bot mention was found and cleaned text was extracted.
        * ``NoMentionResult`` with ``is_command=False`` when the
          message does not contain a bot mention.

    Raises:
        ValueError: If ``bot_user_id`` is empty or contains only
                    whitespace.

    Edge cases handled:
        * Multiple bot mentions — processed once, extra mentions logged
          in ``mention_count``.
        * Empty content after mention removal — returned as a
          ``NoMentionResult`` with ``reason='empty_after_strip'``.
        * DM channels (no guild_id) — ``guild_id`` returned as empty
          string, which is valid for DM-based meetings.
        * Self-messages (bot mentions itself) — ignored via
          ``NoMentionResult`` to prevent infinite loops.
        * No mention at all — ``NoMentionResult`` with
          ``reason='no_mention'``.

    Examples:
        >>> from src.discord_mention_extractor import (
        ...     DiscordMessageEvent,
        ...     DiscordUser,
        ...     DiscordMention,
        ...     extract_mention_command,
        ... )
        >>> event = DiscordMessageEvent(
        ...     id="msg_1",
        ...     content="<@1234> 새로운 캐릭터 디자인 검토 부탁해요",
        ...     author=DiscordUser(id="user_1", username="pd_kim"),
        ...     channel_id="ch_1",
        ...     guild_id="guild_1",
        ...     mentions=(DiscordMention(id="1234", username="AI_Company", bot=True),),
        ... )
        >>> result = extract_mention_command(event, bot_user_id="1234")
        >>> assert result.is_command
        >>> assert result.content == "새로운 캐릭터 디자인 검토 부탁해요"
        >>> assert result.author_id == "user_1"
    """
    # ── Guard: validate bot_user_id ─────────────────────────────────
    if not bot_user_id or not bot_user_id.strip():
        raise ValueError("bot_user_id must not be empty")

    bot_user_id = bot_user_id.strip()

    # ── Guard: skip self-messages ───────────────────────────────────
    if event.author.id == bot_user_id:
        return NoMentionResult(
            is_command=False,
            reason="self_message",
        )

    # ── Guard: skip empty content early ─────────────────────────────
    if not event.content or not event.content.strip():
        return NoMentionResult(
            is_command=False,
            reason="empty_content",
        )

    content = event.content

    # ── Step 1: detect bot mention via the gateway mentions array ───
    mention_count = _count_bot_mentions(event.mentions, bot_user_id)

    if mention_count == 0:
        # Step 2: raw-content fallback scan
        mention_count = _count_raw_mentions(content, bot_user_id)

    if mention_count == 0:
        return NoMentionResult(
            is_command=False,
            reason="no_mention",
        )

    # ── Step 3: strip all bot mentions from the content ─────────────
    cleaned = _strip_bot_mentions(content, bot_user_id)

    # ── Guard: content empty after stripping mentions ───────────────
    if not cleaned or not cleaned.strip():
        return NoMentionResult(
            is_command=False,
            reason="empty_after_strip",
        )

    # ── Step 4: return the extracted command ────────────────────────
    return ExtractedMentionCommand(
        content=cleaned,
        author_id=event.author.id,
        author_name=event.author.username,
        channel_id=event.channel_id,
        guild_id=event.guild_id,
        message_id=event.id,
        is_command=True,
        mention_count=mention_count,
    )


# ── Internal helpers ────────────────────────────────────────────────────

# Discord mention formats we match against:
#  - <@123456789012345678>   (standard user mention)
#  - <@!123456789012345678>  (nickname mention — rare but valid)
_MENTION_RE_TEMPLATE = r"<@!?{bot_id}>"
"""Regex pattern for matching a bot mention in raw message content."""


def _count_bot_mentions(
    mentions: tuple[DiscordMention, ...],
    bot_user_id: str,
) -> int:
    """Count how many times the bot appears in the mentions array."""
    return sum(1 for m in mentions if m.id == bot_user_id)


def _count_raw_mentions(content: str, bot_user_id: str) -> int:
    """Count bot mentions in the raw content string via regex.

    Matches both ``<@bot_id>`` and ``<@!bot_id>`` patterns.
    """
    pattern = _MENTION_RE_TEMPLATE.format(bot_id=re.escape(bot_user_id))
    return len(re.findall(pattern, content))


def _strip_bot_mentions(content: str, bot_user_id: str) -> str:
    """Remove all bot mention patterns from the raw content.

    Collapses multi-whitespace and strips leading/trailing space.
    Preserves meaningful whitespace within the user's actual message.
    """
    pattern = _MENTION_RE_TEMPLATE.format(bot_id=re.escape(bot_user_id))
    cleaned = re.sub(pattern, "", content)
    # Remove any bare @ mention that might remain (covers edge cases
    # where the mention format is malformed but still clearly an @).
    # Collapse 2+ spaces into 1, then strip edges.
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned
