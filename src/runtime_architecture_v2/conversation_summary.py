"""Sanitized structured summaries for saved Discord conversations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .knowledge import sanitize_knowledge_text

_SUMMARY_FIELDS = {
    "summary",
    "key_ideas",
    "decisions",
    "unresolved_questions",
    "action_items",
    "user_perspective",
}
_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_ideas": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        "user_perspective": {"type": "string"},
    },
    "required": sorted(_SUMMARY_FIELDS),
    "additionalProperties": False,
}
_SUMMARY_INSTRUCTIONS = (
    "Summarize the Discord transcript into the requested six-field schema. "
    "Keep the summary concise, preserve concrete decisions and unresolved "
    "questions, and include only explicit action items and owners."
)


class StructuredLlm(Protocol):
    """Small host-owned LLM surface consumed by this adapter."""

    async def acomplete_structured(self, **kwargs: Any) -> object: ...


@dataclass(frozen=True)
class ActionItem:
    text: str
    owner: str = ""


@dataclass(frozen=True)
class ConversationSummary:
    summary: str
    key_ideas: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    action_items: tuple[ActionItem, ...] = ()
    user_perspective: str = ""

    @property
    def important(self) -> bool:
        return bool(self.decisions or self.action_items)


class HermesConversationSummarizer:
    """Map Hermes host-owned structured output into local immutable data."""

    def __init__(self, llm: StructuredLlm) -> None:
        self._llm = llm

    async def summarize(self, transcript: str) -> ConversationSummary:
        safe_transcript = sanitize_knowledge_text(transcript)
        try:
            result = await self._llm.acomplete_structured(
                instructions=_SUMMARY_INSTRUCTIONS,
                input=[{"type": "text", "text": safe_transcript}],
                json_schema=_SUMMARY_SCHEMA,
                json_mode=True,
                temperature=0,
                max_tokens=1800,
                timeout=120,
                purpose="discord_conversation_save",
            )
            return _conversation_summary_from_parsed(result.parsed)
        except Exception:
            return _fallback_summary(safe_transcript)


def _conversation_summary_from_parsed(parsed: object) -> ConversationSummary:
    if not isinstance(parsed, Mapping) or set(parsed) != _SUMMARY_FIELDS:
        raise ValueError("invalid_summary_object")

    summary = _required_string(parsed, "summary")
    key_ideas = _string_tuple(parsed, "key_ideas")
    decisions = _string_tuple(parsed, "decisions")
    unresolved_questions = _string_tuple(parsed, "unresolved_questions")
    user_perspective = _required_string(parsed, "user_perspective")

    raw_action_items = parsed["action_items"]
    if not isinstance(raw_action_items, list):
        raise ValueError("invalid_action_items")
    action_items: list[ActionItem] = []
    for raw_item in raw_action_items:
        if not isinstance(raw_item, Mapping):
            raise ValueError("invalid_action_item")
        if "text" not in raw_item or not set(raw_item).issubset({"text", "owner"}):
            raise ValueError("invalid_action_item")
        text = raw_item["text"]
        owner = raw_item.get("owner", "")
        if not isinstance(text, str) or not isinstance(owner, str):
            raise ValueError("invalid_action_item")
        action_items.append(
            ActionItem(
                text=sanitize_knowledge_text(text),
                owner=sanitize_knowledge_text(owner),
            )
        )

    return ConversationSummary(
        summary=sanitize_knowledge_text(summary),
        key_ideas=tuple(sanitize_knowledge_text(value) for value in key_ideas),
        decisions=tuple(sanitize_knowledge_text(value) for value in decisions),
        unresolved_questions=tuple(
            sanitize_knowledge_text(value) for value in unresolved_questions
        ),
        action_items=tuple(action_items),
        user_perspective=sanitize_knowledge_text(user_perspective),
    )


def _required_string(parsed: Mapping[object, object], field: str) -> str:
    value = parsed[field]
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    return value


def _string_tuple(parsed: Mapping[object, object], field: str) -> tuple[str, ...]:
    values = parsed[field]
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise ValueError(f"invalid_{field}")
    return tuple(values)


def _fallback_summary(safe_transcript: str) -> ConversationSummary:
    last_message = next(
        (
            line.strip()
            for line in reversed(safe_transcript.splitlines())
            if line.strip()
        ),
        "",
    )
    return ConversationSummary(summary=last_message[:240])
