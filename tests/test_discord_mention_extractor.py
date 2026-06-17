"""Tests for the Discord message mention extractor (Sub-AC 2a).

Verifies that raw Discord gateway MESSAGE_CREATE event payloads are
correctly parsed, bot @mentions are detected via both the gateway
``mentions`` array and raw-content fallback, and cleaned message text
with metadata (author, channel, guild) is returned.

Test categories:
1. Basic mention extraction — happy path
2. No mention / non-command messages
3. Edge cases — multiple mentions, empty content, DMs, self-messages
4. Raw-content fallback (empty mentions array)
5. Content edge cases — whitespace, Korean text, mixed content
6. Validation — empty bot_user_id raises ValueError
7. Metadata correctness — author, channel, guild, message_id
"""

from __future__ import annotations

import pytest

from src.discord_mention_extractor import (
    DiscordMention,
    DiscordMessageEvent,
    DiscordUser,
    ExtractedMentionCommand,
    NoMentionResult,
    _count_bot_mentions,
    _count_raw_mentions,
    _strip_bot_mentions,
    extract_mention_command,
)

# ── Shared test constants ───────────────────────────────────────────────

_BOT_ID = "123456789012345678"
_BOT_NAME = "AI_Company"
_USER_ID = "111111111111111111"
_USER_NAME = "pd_kim"
_CHANNEL_ID = "222222222222222222"
_GUILD_ID = "333333333333333333"
_MESSAGE_ID = "444444444444444444"


# ── Test fixture helpers ────────────────────────────────────────────────


def _make_bot() -> DiscordUser:
    return DiscordUser(id=_BOT_ID, username=_BOT_NAME, bot=True)


def _make_user(
    user_id: str = _USER_ID,
    username: str = _USER_NAME,
) -> DiscordUser:
    return DiscordUser(id=user_id, username=username, bot=False)


def _make_bot_mention() -> DiscordMention:
    return DiscordMention(id=_BOT_ID, username=_BOT_NAME, bot=True)


def _make_event(
    *,
    content: str,
    mentions: tuple[DiscordMention, ...] = (),
    author: DiscordUser | None = None,
    channel_id: str = _CHANNEL_ID,
    guild_id: str = _GUILD_ID,
    message_id: str = _MESSAGE_ID,
) -> DiscordMessageEvent:
    if author is None:
        author = _make_user()
    return DiscordMessageEvent(
        id=message_id,
        content=content,
        author=author,
        channel_id=channel_id,
        guild_id=guild_id,
        mentions=mentions,
    )


# ── 1. Basic mention extraction (happy path) ────────────────────────────


class TestBasicMentionExtraction:
    """Verify the happy path: bot mentioned, content cleaned, metadata correct."""

    def test_single_mention_at_start(self):
        """Mention at the beginning of the message."""
        event = _make_event(
            content=f"<@{_BOT_ID}> 새로운 캐릭터 디자인 검토 부탁해요",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.is_command is True
        assert result.content == "새로운 캐릭터 디자인 검토 부탁해요"
        assert result.author_id == _USER_ID
        assert result.author_name == _USER_NAME
        assert result.channel_id == _CHANNEL_ID
        assert result.guild_id == _GUILD_ID
        assert result.message_id == _MESSAGE_ID
        assert result.mention_count == 1

    def test_single_mention_at_end(self):
        """Mention at the end of the message."""
        event = _make_event(
            content=f"뮤직비디오 기획 회의 요청합니다 <@{_BOT_ID}>",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "뮤직비디오 기획 회의 요청합니다"

    def test_single_mention_in_middle(self):
        """Mention in the middle of the message."""
        event = _make_event(
            content=f"안녕하세요 <@{_BOT_ID}> 님, 신규 프로젝트 검토 부탁드립니다",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "안녕하세요 님, 신규 프로젝트 검토 부탁드립니다"

    def test_nickname_mention_format(self):
        """Discord nickname mention format <@!bot_id>."""
        event = _make_event(
            content=f"<@!{_BOT_ID}> 캐릭터 기획서 리뷰 요청",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "캐릭터 기획서 리뷰 요청"

    def test_message_with_only_mention(self):
        """Message content is just the mention — should reject as empty."""
        event = _make_event(
            content=f"<@{_BOT_ID}>",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "empty_after_strip"

    def test_korean_text_preserved(self):
        """Korean characters must survive the extraction intact."""
        korean_agenda = "신규 걸그룹 '루나'의 비주얼 콘셉트와 SNS 마케팅 전략 수립"
        event = _make_event(
            content=f"<@{_BOT_ID}> {korean_agenda}",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == korean_agenda

    def test_english_text_preserved(self):
        """English text must survive the extraction intact."""
        english_text = "Please review the new character design for Project Starlight"
        event = _make_event(
            content=f"<@{_BOT_ID}> {english_text}",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == english_text

    def test_mixed_korean_english_preserved(self):
        """Mixed Korean + English text must survive intact."""
        mixed = "API 리팩토링과 CI/CD pipeline 개선이 필요합니다"
        event = _make_event(
            content=f"<@{_BOT_ID}> {mixed}",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == mixed


# ── 2. No mention / non-command messages ────────────────────────────────


class TestNoMention:
    """Messages that do not mention the bot should be rejected."""

    def test_no_mention_at_all(self):
        """Plain user message with no @mention."""
        event = _make_event(
            content="오늘 회의는 몇 시인가요?",
            mentions=(),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "no_mention"

    def test_mentions_other_user_not_bot(self):
        """Mention of a different user — not the bot."""
        other_mention = DiscordMention(id="99999", username="other_user", bot=False)
        event = _make_event(
            content="<@99999> 이거 확인해주세요",
            mentions=(other_mention,),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "no_mention"

    def test_mentions_other_bot_not_ours(self):
        """Mention of a different bot — not our bot."""
        other_bot = DiscordMention(id="99999", username="OtherBot", bot=True)
        event = _make_event(
            content="<@99999> help",
            mentions=(other_bot,),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "no_mention"

    def test_empty_content(self):
        """Empty message content."""
        event = _make_event(content="", mentions=())
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "empty_content"

    def test_whitespace_only_content(self):
        """Message with only whitespace characters."""
        event = _make_event(content="   \n\t  ", mentions=())
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.is_command is False
        assert result.reason == "empty_content"


# ── 3. Edge cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Non-trivial scenarios the extractor must handle correctly."""

    def test_multiple_bot_mentions(self):
        """Multiple mentions of the bot in one message — count them all."""
        event = _make_event(
            content=f"<@{_BOT_ID}> <@{_BOT_ID}> 긴급 회의 요청합니다",
            mentions=(_make_bot_mention(), _make_bot_mention()),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "긴급 회의 요청합니다"
        assert result.mention_count == 2

    def test_multiple_mentions_mixed(self):
        """Bot mentioned alongside other users."""
        other = DiscordMention(id="99999", username="other_user", bot=False)
        event = _make_event(
            content=f"<@{_BOT_ID}> <@99999> 기획안 검토해주세요",
            mentions=(_make_bot_mention(), other),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "<@99999> 기획안 검토해주세요"
        assert result.mention_count == 1

    def test_dm_channel_no_guild(self):
        """Direct message — guild_id is empty string."""
        event = _make_event(
            content=f"<@{_BOT_ID}> 개인 회의 요청",
            mentions=(_make_bot_mention(),),
            guild_id="",
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.guild_id == ""
        # DM-based meetings should still work — guild_id empty is valid
        assert result.is_command is True

    def test_self_message_ignored(self):
        """Bot mentions itself — should be ignored to prevent loops."""
        bot_author = _make_bot()
        event = _make_event(
            content=f"<@{_BOT_ID}> I am responding",
            author=bot_author,
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.reason == "self_message"

    def test_nickname_mention_in_mentions_array(self):
        """Gateway may send <@!id> in mentions array too."""
        # The mention id is the same — the exclamation mark is a
        # content formatting detail.
        event = _make_event(
            content=f"<@!{_BOT_ID}> 프로젝트 리뷰",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "프로젝트 리뷰"


# ── 4. Raw-content fallback (empty mentions array) ──────────────────────


class TestRawContentFallback:
    """When the gateway ``mentions`` array is empty, the extractor falls
    back to scanning the raw ``content`` string for mention patterns."""

    def test_raw_content_fallback_detects_mention(self):
        """Standard mention in content but empty mentions array."""
        event = _make_event(
            content=f"<@{_BOT_ID}> API 문서 업데이트 필요합니다",
            mentions=(),  # mentions array is empty — unusual but handled
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "API 문서 업데이트 필요합니다"

    def test_raw_content_fallback_nickname_format(self):
        """Nickname mention in content with empty mentions array."""
        event = _make_event(
            content=f"<@!{_BOT_ID}> 서버 마이그레이션 계획 수립",
            mentions=(),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "서버 마이그레이션 계획 수립"

    def test_raw_content_fallback_no_match(self):
        """No mention in content and empty mentions array."""
        event = _make_event(
            content="그냥 일반 메시지입니다",
            mentions=(),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, NoMentionResult)
        assert result.reason == "no_mention"


# ── 5. Content edge cases ───────────────────────────────────────────────


class TestContentEdgeCases:
    """Content transformations that must not corrupt the message."""

    def test_leading_trailing_whitespace_cleaned(self):
        """Extra whitespace around the cleaned content is collapsed."""
        event = _make_event(
            content=f"  <@{_BOT_ID}>   캐릭터 디자인   검토   ",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == "캐릭터 디자인 검토"

    def test_newlines_preserved_as_spaces(self):
        """Newlines in the content are collapsed to spaces."""
        event = _make_event(
            content=f"<@{_BOT_ID}>\n첫 번째 안건\n두 번째 안건\n세 번째 안건",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert "첫 번째 안건" in result.content
        assert "두 번째 안건" in result.content
        assert "세 번째 안건" in result.content

    def test_special_characters_preserved(self):
        """Emoji and special characters must survive extraction."""
        special = "🎬 신규 애니메이션 프로젝트 #기획 #2026Q3 — 예산: $50K~$80K"
        event = _make_event(
            content=f"<@{_BOT_ID}> {special}",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.content == special

    def test_url_in_content_preserved(self):
        """URLs in the message must not be corrupted."""
        event = _make_event(
            content=(
                f"<@{_BOT_ID}> 참고자료: https://docs.google.com/doc/abc 참고해주세요"
            ),
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert "https://docs.google.com/doc/abc" in result.content

    def test_code_blocks_in_content(self):
        """Message with code blocks must be preserved."""
        code_msg = "다음 API 엔드포인트 검토: ```GET /api/v1/characters```"
        event = _make_event(
            content=f"<@{_BOT_ID}> {code_msg}",
            mentions=(_make_bot_mention(),),
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert "GET /api/v1/characters" in result.content


# ── 6. Metadata correctness ─────────────────────────────────────────────


class TestMetadataCorrectness:
    """All metadata fields must correctly reflect the source event."""

    def test_author_id_from_different_user(self):
        """author_id matches the event author, not the bot."""
        custom_user = _make_user(user_id="custom_author_42", username="art_director")
        event = _make_event(
            content=f"<@{_BOT_ID}> 비주얼 콘셉트 회의",
            mentions=(_make_bot_mention(),),
            author=custom_user,
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.author_id == "custom_author_42"
        assert result.author_name == "art_director"

    def test_channel_and_guild_propagated(self):
        """channel_id and guild_id are passed through from the event."""
        event = _make_event(
            content=f"<@{_BOT_ID}> 회의 요청",
            mentions=(_make_bot_mention(),),
            channel_id="ch_creative",
            guild_id="guild_entertainment",
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.channel_id == "ch_creative"
        assert result.guild_id == "guild_entertainment"

    def test_message_id_propagated(self):
        """message_id is preserved for tracing and deduplication."""
        event = _make_event(
            content=f"<@{_BOT_ID}> test",
            mentions=(_make_bot_mention(),),
            message_id="msg_abc_123",
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.message_id == "msg_abc_123"

    def test_guild_id_empty_for_dm(self):
        """DM channels should propagate empty guild_id correctly."""
        event = _make_event(
            content=f"<@{_BOT_ID}> DM meeting request",
            mentions=(_make_bot_mention(),),
            guild_id="",
        )
        result = extract_mention_command(event, bot_user_id=_BOT_ID)
        assert isinstance(result, ExtractedMentionCommand)
        assert result.guild_id == ""


# ── 7. Validation ───────────────────────────────────────────────────────


class TestValidation:
    """Input validation guards."""

    def test_empty_bot_user_id_raises(self):
        """Empty bot_user_id must raise ValueError."""
        event = _make_event(
            content=f"<@{_BOT_ID}> test",
            mentions=(_make_bot_mention(),),
        )
        with pytest.raises(ValueError, match="bot_user_id must not be empty"):
            extract_mention_command(event, bot_user_id="")

    def test_whitespace_bot_user_id_raises(self):
        """Whitespace-only bot_user_id must raise ValueError."""
        event = _make_event(
            content=f"<@{_BOT_ID}> test",
            mentions=(_make_bot_mention(),),
        )
        with pytest.raises(ValueError, match="bot_user_id must not be empty"):
            extract_mention_command(event, bot_user_id="   ")

    def test_none_bot_user_id_raises(self):
        """None bot_user_id raises (parameter is required)."""
        event = _make_event(
            content=f"<@{_BOT_ID}> test",
            mentions=(_make_bot_mention(),),
        )
        with pytest.raises(
            (ValueError, TypeError),
        ):
            extract_mention_command(event, bot_user_id=None)  # type: ignore[arg-type]


# ── 8. Internal helpers ─────────────────────────────────────────────────


class TestInternalHelpers:
    """Unit tests for the private helper functions."""

    def test_count_bot_mentions_single(self):
        mentions = (_make_bot_mention(),)
        assert _count_bot_mentions(mentions, _BOT_ID) == 1

    def test_count_bot_mentions_multiple(self):
        mentions = (
            _make_bot_mention(),
            _make_bot_mention(),
            DiscordMention(id="other", username="other", bot=False),
        )
        assert _count_bot_mentions(mentions, _BOT_ID) == 2

    def test_count_bot_mentions_none(self):
        mentions = (DiscordMention(id="other", username="other", bot=False),)
        assert _count_bot_mentions(mentions, _BOT_ID) == 0

    def test_count_bot_mentions_empty(self):
        assert _count_bot_mentions((), _BOT_ID) == 0

    def test_count_raw_mentions_standard(self):
        content = f"<@{_BOT_ID}> hello <@{_BOT_ID}> world"
        assert _count_raw_mentions(content, _BOT_ID) == 2

    def test_count_raw_mentions_nickname(self):
        content = f"<@!{_BOT_ID}> hello"
        assert _count_raw_mentions(content, _BOT_ID) == 1

    def test_count_raw_mentions_none(self):
        assert _count_raw_mentions("no mention here", _BOT_ID) == 0

    def test_count_raw_mentions_similar_but_different_id(self):
        """Should not match a different bot ID that overlaps textually."""
        content = "<@111111111111111111> hello"  # different ID
        assert _count_raw_mentions(content, _BOT_ID) == 0

    def test_strip_bot_mentions_standard(self):
        result = _strip_bot_mentions(
            f"<@{_BOT_ID}> 회의 부탁합니다",
            _BOT_ID,
        )
        assert result == "회의 부탁합니다"

    def test_strip_bot_mentions_nickname(self):
        result = _strip_bot_mentions(
            f"<@!{_BOT_ID}> 회의 부탁합니다",
            _BOT_ID,
        )
        assert result == "회의 부탁합니다"

    def test_strip_bot_mentions_no_mention(self):
        content = "그냥 메시지입니다"
        result = _strip_bot_mentions(content, _BOT_ID)
        assert result == "그냥 메시지입니다"


# ── 9. NoMentionResult sentinel ─────────────────────────────────────────


class TestNoMentionResult:
    """NoMentionResult sentinel behaviour."""

    def test_is_command_false(self):
        result = NoMentionResult()
        assert result.is_command is False

    def test_default_reason_empty(self):
        result = NoMentionResult()
        assert result.reason == ""

    def test_custom_reason(self):
        result = NoMentionResult(reason="custom_reason")
        assert result.reason == "custom_reason"

    def test_not_an_extracted_command(self):
        """Type check — NoMentionResult is not ExtractedMentionCommand."""
        result = NoMentionResult()
        assert not isinstance(result, ExtractedMentionCommand)
