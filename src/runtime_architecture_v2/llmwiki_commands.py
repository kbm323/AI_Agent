"""Transport-neutral orchestration for LLM Wiki commands."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from .knowledge import sanitize_knowledge_text
from .llmwiki_models import LlmWikiSource, LlmWikiSummary, LlmWikiWriteResult
from .llmwiki_sources import SourceError, extract_single_url
from .qmd_search import QmdCommandResult, QmdMatch, QmdSearchResult

_SUMMARY_FIELDS = {
    "key_points",
    "source_type",
    "summary",
    "tags",
    "title",
    "user_perspective",
}
_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "source_type": {"type": "string"},
        "user_perspective": {"type": "string"},
    },
    "required": sorted(_SUMMARY_FIELDS),
    "additionalProperties": False,
}
_SUMMARY_INSTRUCTIONS = (
    "Summarize the retrieved source into the requested six-field schema. "
    "Preserve concrete claims, keep key points concise, use short search tags, "
    "and distinguish the user's requested perspective from source evidence."
)
_MAX_SOURCE_PROMPT_CHARS = 40_000


class StructuredLlm(Protocol):
    async def acomplete_structured(self, **kwargs: Any) -> object: ...


class SourceSummarizer(Protocol):
    async def summarize(
        self, source: LlmWikiSource, *, request: str = ""
    ) -> LlmWikiSummary: ...


@dataclass(frozen=True)
class LlmWikiCommandResult:
    ok: bool
    status: str = ""
    summary: str = ""
    path: str = ""
    matches: tuple[QmdMatch, ...] = ()
    fallback: str = ""
    stale: bool = False
    error: str = ""


class HermesSourceSummarizer:
    """Convert Hermes structured output into a sanitized source summary."""

    def __init__(self, llm: StructuredLlm) -> None:
        self._llm = llm

    async def summarize(
        self, source: LlmWikiSource, *, request: str = ""
    ) -> LlmWikiSummary:
        safe_request = _clean(request)
        safe_content = _clean(source.content)[:_MAX_SOURCE_PROMPT_CHARS]
        prompt = (
            f"Source title: {_clean(source.title)}\n"
            f"Source type: {_clean(source.source_type)}\n"
            f"User request: {safe_request}\n\n"
            f"Source content:\n{safe_content}"
        )
        try:
            result = await self._llm.acomplete_structured(
                instructions=_SUMMARY_INSTRUCTIONS,
                input=[{"type": "text", "text": prompt}],
                json_schema=_SUMMARY_SCHEMA,
                json_mode=True,
                temperature=0,
                max_tokens=1800,
                timeout=120,
                purpose="llmwiki_source_ingest",
            )
            return _summary_from_parsed(result.parsed)
        except Exception:
            return _fallback_summary(source)


async def run_llmwiki_ingest(
    *,
    request: str,
    retriever: Any,
    summarizer: SourceSummarizer,
    store: Any,
    scheduler: Any,
) -> LlmWikiCommandResult:
    """Retrieve, summarize, persist, and enqueue one URL for indexing."""

    try:
        url = extract_single_url(request)
    except SourceError:
        return _failure("invalid_url")
    try:
        source = await asyncio.to_thread(retriever.retrieve, url)
    except SourceError as exc:
        return _failure(exc.code)
    except Exception:
        return _failure("retrieval_failed")

    try:
        summary = await summarizer.summarize(source, request=request)
    except Exception:
        summary = _fallback_summary(source)
    try:
        write = await asyncio.to_thread(store.save_source, source, summary)
    except Exception:
        return _failure("write_failed")
    result = _from_write(write, summary=summary.summary)
    if result.ok and result.status in {"created", "updated"}:
        stale = not await _mark_and_schedule(scheduler)
        result = _with_stale(result, stale)
    return result


async def run_llmwiki_note(
    text: str,
    *,
    author: str,
    store: Any,
    scheduler: Any,
) -> LlmWikiCommandResult:
    """Persist one free-form note and enqueue an incremental index refresh."""

    if not isinstance(text, str) or not text.strip():
        return _failure("invalid_input")
    try:
        write = await asyncio.to_thread(store.save_note, text, author=author)
    except Exception:
        return _failure("write_failed")
    result = _from_write(write)
    if result.ok and result.status in {"created", "updated"}:
        stale = not await _mark_and_schedule(scheduler)
        result = _with_stale(result, stale)
    return result


async def run_llmwiki_find(
    query: str,
    *,
    qmd: Any,
    scheduler: Any,
    limit: int = 5,
) -> LlmWikiCommandResult:
    """Refresh QMD metadata, then search the existing whole-vault collection."""

    if not isinstance(query, str) or not query.strip():
        return _failure("invalid_input")
    stale = True
    try:
        refresh = await asyncio.to_thread(scheduler.refresh_for_search)
        stale = not isinstance(refresh, QmdCommandResult) or not refresh.ok
    except Exception:
        stale = True
    try:
        search = await asyncio.to_thread(qmd.query, query.strip(), limit=limit)
    except (TypeError, ValueError):
        return _failure("invalid_input")
    except Exception:
        return _failure("command_failed")
    if not isinstance(search, QmdSearchResult) or not search.ok:
        error = (
            search.error
            if isinstance(search, QmdSearchResult)
            else "command_failed"
        )
        return _failure(error or "command_failed")
    return LlmWikiCommandResult(
        ok=True,
        status="found",
        matches=search.matches,
        fallback=search.fallback,
        stale=stale,
    )


def render_llmwiki_ingest(result: LlmWikiCommandResult) -> str:
    if not result.ok:
        return _render_failure(result.error, operation="ingest")
    state = {
        "created": "새 자료를 저장했습니다.",
        "updated": "기존 자료에 새 원문을 추가했습니다.",
        "unchanged": "이미 같은 자료가 저장되어 있습니다.",
    }.get(result.status, "자료를 저장했습니다.")
    lines = [state]
    if result.summary:
        lines.append(f"요약: {_display(result.summary, 360)}")
    if result.path:
        lines.append(f"경로: `{result.path}`")
    if result.stale:
        lines.append("검색 색인 갱신은 다음 조정 작업에서 재시도됩니다.")
    return "\n".join(lines)


def render_llmwiki_note(result: LlmWikiCommandResult) -> str:
    if not result.ok:
        return _render_failure(result.error, operation="note")
    state = (
        "이미 같은 노트가 저장되어 있습니다."
        if result.status == "unchanged"
        else "노트를 저장했습니다."
    )
    lines = [state]
    if result.path:
        lines.append(f"경로: `{result.path}`")
    if result.stale:
        lines.append("검색 색인 갱신은 다음 조정 작업에서 재시도됩니다.")
    return "\n".join(lines)


def render_llmwiki_find(result: LlmWikiCommandResult) -> str:
    if not result.ok:
        return _render_failure(result.error, operation="find")
    if not result.matches:
        return "검색 결과가 없습니다."
    lines = ["검색 결과:"]
    for index, match in enumerate(result.matches, start=1):
        snippet = _display(match.snippet, 220)
        suffix = f" - {snippet}" if snippet else ""
        lines.append(f"{index}. `{match.path}`{suffix}")
    if result.fallback == "bm25":
        lines.append("임베딩 검색을 사용할 수 없어 BM25 결과를 표시했습니다.")
    if result.stale:
        lines.append("빠른 색인 갱신에 실패하여 기존 색인을 검색했습니다.")
    return "\n".join(lines)


async def _mark_and_schedule(scheduler: Any) -> bool:
    def schedule() -> None:
        scheduler.mark_dirty()
        scheduler.schedule()

    try:
        await asyncio.to_thread(schedule)
        return True
    except Exception:
        return False


def _from_write(
    write: object, *, summary: str = ""
) -> LlmWikiCommandResult:
    if not isinstance(write, LlmWikiWriteResult):
        return _failure("write_failed")
    if not write.ok:
        return _failure(write.error or "write_failed")
    if write.status not in {"created", "updated", "unchanged"}:
        return _failure("write_failed")
    path = _safe_relative_path(write.canonical_path)
    if not path:
        return _failure("unsafe_path")
    return LlmWikiCommandResult(
        ok=True,
        status=write.status,
        summary=_clean(summary),
        path=path,
    )


def _summary_from_parsed(parsed: object) -> LlmWikiSummary:
    if not isinstance(parsed, Mapping) or set(parsed) != _SUMMARY_FIELDS:
        raise ValueError("invalid_summary")
    title = _required_text(parsed, "title")
    summary = _required_text(parsed, "summary")
    _required_text(parsed, "source_type")
    user_perspective = _required_text(parsed, "user_perspective", allow_blank=True)
    key_points = _text_tuple(parsed, "key_points")
    tags = _text_tuple(parsed, "tags")
    return LlmWikiSummary(
        title=_clean(title),
        summary=_clean(summary),
        key_points=tuple(_clean(value) for value in key_points),
        tags=tuple(_clean(value) for value in tags),
        user_perspective=_clean(user_perspective),
    )


def _required_text(
    parsed: Mapping[object, object], field: str, *, allow_blank: bool = False
) -> str:
    value = parsed[field]
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        raise ValueError(f"invalid_{field}")
    return value


def _text_tuple(parsed: Mapping[object, object], field: str) -> tuple[str, ...]:
    values = parsed[field]
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise ValueError(f"invalid_{field}")
    return tuple(value for value in values if value.strip())


def _fallback_summary(source: LlmWikiSource) -> LlmWikiSummary:
    title = _clean(source.title).strip() or "Untitled source"
    content = _display(source.content, 480)
    return LlmWikiSummary(
        title=title,
        summary=content,
        tags=(_clean(source.source_type).strip() or "web",),
    )


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        return ""
    return path.as_posix()


def _with_stale(
    result: LlmWikiCommandResult, stale: bool
) -> LlmWikiCommandResult:
    return LlmWikiCommandResult(
        ok=result.ok,
        status=result.status,
        summary=result.summary,
        path=result.path,
        matches=result.matches,
        fallback=result.fallback,
        stale=stale,
        error=result.error,
    )


def _failure(error: str) -> LlmWikiCommandResult:
    return LlmWikiCommandResult(ok=False, status="failed", error=error)


def _render_failure(error: str, *, operation: str) -> str:
    if error == "invalid_url":
        return "요청에 정확히 하나의 공개 URL을 포함해 주세요."
    if error in {"unsupported_source", "unsafe_target"}:
        return "이 주소에서는 저장할 수 있는 공개 텍스트를 가져오지 못했습니다."
    if error == "missing_dependency":
        return "URL 수집 도구를 사용할 수 없습니다. 서버 설정을 확인해 주세요."
    if error == "invalid_input":
        if operation == "note":
            return "저장할 노트 내용을 입력해 주세요."
        if operation == "find":
            return "검색어를 입력해 주세요."
    if operation == "find":
        return "검색을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    return "저장을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _clean(value: object) -> str:
    return sanitize_knowledge_text(str(value)).strip()


def _display(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", _clean(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
