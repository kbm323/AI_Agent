from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from src.runtime_architecture_v2.conversation_summary import (
    ActionItem,
    ConversationSummary,
    HermesConversationSummarizer,
)


class FakeLlm:
    def __init__(
        self,
        *,
        parsed: object,
        error: Exception | None = None,
        text: str = "",
    ) -> None:
        self.parsed = parsed
        self.error = error
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def acomplete_structured(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(parsed=self.parsed, text=self.text)


def _parsed_summary() -> dict[str, object]:
    return {
        "summary": "콘텐츠 방향을 합의했다.",
        "key_ideas": ["쇼츠 우선"],
        "decisions": ["3편 제작"],
        "unresolved_questions": [],
        "action_items": [{"text": "대본 작성", "owner": "콘텐츠팀장"}],
        "user_perspective": "",
    }


_FAKE_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\\n"
    "RkFLRSBQUklWQVRFIEtFWSBNQVRFUklBTA==\\n"
    "-----END PRIVATE KEY-----\\n"
)
_FAKE_GOOGLE_SERVICE_ACCOUNT_JSON = (
    '{"type":"service_account","project_id":"safe-project",'
    '"private_key_id":"fake-key-id",'
    f'"private_key":"{_FAKE_PRIVATE_KEY}",'
    '"client_email":"fake-service@safe-project.iam.gserviceaccount.com",'
    '"client_id":"1234567890",'
    '"auth_uri":"https://accounts.example.test/o/oauth2/auth",'
    '"token_uri":"https://oauth2.example.test/token"}'
)


@pytest.mark.asyncio
async def test_hermes_summarizer_maps_structured_result():
    llm = FakeLlm(parsed=_parsed_summary())

    result = await HermesConversationSummarizer(llm).summarize("transcript")

    assert result == ConversationSummary(
        summary="콘텐츠 방향을 합의했다.",
        key_ideas=("쇼츠 우선",),
        decisions=("3편 제작",),
        action_items=(ActionItem(text="대본 작성", owner="콘텐츠팀장"),),
    )
    assert result.important is True


@pytest.mark.asyncio
async def test_hermes_summarizer_uses_bounded_host_owned_structured_call():
    llm = FakeLlm(parsed=_parsed_summary())

    await HermesConversationSummarizer(llm).summarize(
        "token=TRANSCRIPT_SECRET @everyone"
    )

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["input"] == [
        {
            "type": "text",
            "text": "[REDACTED_SECRET] @[redacted-mention]",
        }
    ]
    assert call["json_mode"] is True
    assert call["temperature"] == 0
    assert call["max_tokens"] == 1800
    assert call["timeout"] == 120
    assert call["purpose"] == "discord_conversation_save"
    assert call["instructions"]
    assert set(call["json_schema"]["required"]) == {
        "summary",
        "key_ideas",
        "decisions",
        "unresolved_questions",
        "action_items",
        "user_perspective",
    }
    assert not {
        "provider",
        "model",
        "agent_id",
        "profile",
    }.intersection(call)


@pytest.mark.asyncio
async def test_hermes_summarizer_removes_url_userinfo_from_exact_llm_input():
    llm = FakeLlm(parsed=_parsed_summary())
    transcript = (
        "Message: https://alice:p%40ss@example.test/private\n"
        "Attachment: https://bob%3Aencoded%40cdn.example.test/file.pdf"
    )

    await HermesConversationSummarizer(llm).summarize(transcript)

    exact_input = llm.calls[0]["input"]
    assert exact_input == [
        {
            "type": "text",
            "text": (
                "Message: https://example.test/private\n"
                "Attachment: https://cdn.example.test/file.pdf"
            ),
        }
    ]
    assert "alice" not in str(exact_input)
    assert "p%40ss" not in str(exact_input)
    assert "bob" not in str(exact_input)
    assert "encoded" not in str(exact_input)


@pytest.mark.asyncio
async def test_hermes_summarizer_redacts_quoted_assignments_from_exact_llm_input():
    llm = FakeLlm(parsed=_parsed_summary())
    transcript = (
        'Keep {"password":"LLM_PASSWORD","name":"Oracle"} '
        'credential="LLM_CREDENTIAL" auth: "LLM AUTH"'
    )

    await HermesConversationSummarizer(llm).summarize(transcript)

    assert llm.calls[0]["input"] == [
        {
            "type": "text",
            "text": (
                'Keep {[REDACTED_SECRET],"name":"Oracle"} '
                "[REDACTED_SECRET] [REDACTED_SECRET]"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_hermes_summarizer_redacts_complete_yaml_scalars_from_exact_input():
    llm = FakeLlm(parsed=_parsed_summary())
    transcript = (
        "Config:\n"
        "  password: 'llm''s secret'\n"
        "  token: plain llm secret\n"
        "  credential: |2-\n"
        "    literal llm secret\n"
        "  auth: >+2\n"
        "    folded llm secret\n"
        "  note: keep llm context"
    )

    await HermesConversationSummarizer(llm).summarize(transcript)

    assert llm.calls[0]["input"] == [
        {
            "type": "text",
            "text": (
                "Config:\n"
                "  [REDACTED_SECRET]\n"
                "  [REDACTED_SECRET]\n"
                "  [REDACTED_SECRET]\n"
                "    [REDACTED_SECRET]\n"
                "  [REDACTED_SECRET]\n"
                "    [REDACTED_SECRET]\n"
                "  note: keep llm context"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_hermes_summarizer_redacts_namespaced_and_flow_yaml_from_exact_input():
    llm = FakeLlm(parsed=_parsed_summary())
    transcript = (
        "DISCORD_BOT_TOKEN=llm-token\n"
        '"client_secret": >-\n'
        "  llm block secret\n"
        "settings: {password: plain llm secret, mode: safe}\n"
        "note: keep llm context"
    )

    await HermesConversationSummarizer(llm).summarize(transcript)

    assert llm.calls[0]["input"] == [
        {
            "type": "text",
            "text": (
                "[REDACTED_SECRET]\n"
                "[REDACTED_SECRET]\n"
                "  [REDACTED_SECRET]\n"
                "settings: {[REDACTED_SECRET], mode: safe}\n"
                "note: keep llm context"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_hermes_summarizer_redacts_google_private_key_from_exact_input():
    llm = FakeLlm(parsed=_parsed_summary())

    await HermesConversationSummarizer(llm).summarize(_FAKE_GOOGLE_SERVICE_ACCOUNT_JSON)

    expected = _FAKE_GOOGLE_SERVICE_ACCOUNT_JSON.replace(
        f'"private_key":"{_FAKE_PRIVATE_KEY}"',
        "[REDACTED_SECRET]",
    )
    assert llm.calls[0]["input"] == [{"type": "text", "text": expected}]
    assert _FAKE_PRIVATE_KEY not in str(llm.calls[0]["input"])


@pytest.mark.asyncio
async def test_hermes_summarizer_sanitizes_every_returned_string():
    llm = FakeLlm(
        parsed={
            "summary": "token=SUMMARY_SECRET @everyone",
            "key_ideas": ["password=IDEA_SECRET @here"],
            "decisions": ["api_key=DECISION_SECRET @everyone"],
            "unresolved_questions": ["secret=QUESTION_SECRET @here"],
            "action_items": [
                {
                    "text": "passwd=ACTION_SECRET @everyone",
                    "owner": "token=OWNER_SECRET @here",
                }
            ],
            "user_perspective": "Bearer PERSPECTIVE_SECRET @everyone",
        }
    )

    result = await HermesConversationSummarizer(llm).summarize("safe transcript")

    returned = (
        result.summary,
        *result.key_ideas,
        *result.decisions,
        *result.unresolved_questions,
        *(item.text for item in result.action_items),
        *(item.owner for item in result.action_items),
        result.user_perspective,
    )
    combined = "\n".join(returned)
    raw_secrets = (
        "SUMMARY_SECRET",
        "IDEA_SECRET",
        "DECISION_SECRET",
        "QUESTION_SECRET",
        "ACTION_SECRET",
        "OWNER_SECRET",
        "PERSPECTIVE_SECRET",
    )
    assert all(secret not in combined for secret in raw_secrets)
    assert "@everyone" not in combined
    assert "@here" not in combined
    assert "[REDACTED_SECRET]" in combined
    assert "@[redacted-mention]" in combined


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parsed",
    [
        None,
        "not a JSON object",
        {},
        {
            "summary": "wrong list types",
            "key_ideas": "not-a-list",
            "decisions": [],
            "unresolved_questions": [],
            "action_items": [],
            "user_perspective": "",
        },
        {
            "summary": "bad action item",
            "key_ideas": [],
            "decisions": [],
            "unresolved_questions": [],
            "action_items": [{"owner": "missing text"}],
            "user_perspective": "",
        },
    ],
)
async def test_hermes_summarizer_falls_back_on_parse_or_schema_failure(
    parsed: object,
):
    llm = FakeLlm(parsed=parsed, text="token=RAW_MODEL_SECRET")
    last_message = "token=LAST_MESSAGE_SECRET @everyone " + ("x" * 260)
    transcript = f"first message\n\n{last_message}\n   "

    result = await HermesConversationSummarizer(llm).summarize(transcript)

    assert result == ConversationSummary(
        summary=("[REDACTED_SECRET] @[redacted-mention] " + ("x" * 260))[:240]
    )
    assert "RAW_MODEL_SECRET" not in result.summary


@pytest.mark.asyncio
async def test_hermes_summarizer_falls_back_without_exposing_exception_text():
    llm = FakeLlm(
        parsed=None,
        error=RuntimeError("provider failed token=EXCEPTION_SECRET"),
    )

    result = await HermesConversationSummarizer(llm).summarize(
        "first\n\nlast safe message"
    )

    assert result == ConversationSummary(summary="last safe message")
    assert "EXCEPTION_SECRET" not in result.summary


@pytest.mark.asyncio
async def test_summary_dataclasses_are_frozen_and_use_tuple_collections():
    result = await HermesConversationSummarizer(
        FakeLlm(parsed=_parsed_summary())
    ).summarize("transcript")

    assert isinstance(result.key_ideas, tuple)
    assert isinstance(result.decisions, tuple)
    assert isinstance(result.unresolved_questions, tuple)
    assert isinstance(result.action_items, tuple)
    with pytest.raises(FrozenInstanceError):
        result.summary = "changed"
    with pytest.raises(FrozenInstanceError):
        result.action_items[0].owner = "changed"


def test_conversation_summary_is_important_for_decisions_or_actions_only():
    assert ConversationSummary(summary="plain").important is False
    assert ConversationSummary(summary="decision", decisions=("yes",)).important
    assert ConversationSummary(
        summary="action", action_items=(ActionItem("do it"),)
    ).important
