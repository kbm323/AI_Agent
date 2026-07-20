from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from src.runtime_architecture_v2.llmwiki_commands import (
    HermesSourceSummarizer,
    render_llmwiki_find,
    render_llmwiki_ingest,
    render_llmwiki_note,
    run_llmwiki_find,
    run_llmwiki_ingest,
    run_llmwiki_note,
)
from src.runtime_architecture_v2.llmwiki_models import (
    LlmWikiSource,
    LlmWikiSummary,
    LlmWikiWriteResult,
)
from src.runtime_architecture_v2.llmwiki_sources import SourceError
from src.runtime_architecture_v2.qmd_search import (
    QmdCommandResult,
    QmdMatch,
    QmdSearchResult,
)

SOURCE = LlmWikiSource(
    normalized_url="https://example.com/article",
    source_type="article",
    title="Source title",
    content="First useful point.\n\nSecond useful point.",
    retrieved_at="2026-07-20T00:00:00+00:00",
)
SUMMARY = LlmWikiSummary(
    title="Structured title",
    summary="Structured summary",
    key_points=("First point",),
    tags=("research",),
    user_perspective="Useful for the project.",
)


@dataclass
class FakeRetriever:
    source: LlmWikiSource = SOURCE
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def retrieve(self, url: str) -> LlmWikiSource:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.source


@dataclass
class FakeSummarizer:
    summary: LlmWikiSummary = SUMMARY
    error: Exception | None = None
    calls: list[tuple[LlmWikiSource, str]] = field(default_factory=list)

    async def summarize(
        self, source: LlmWikiSource, *, request: str = ""
    ) -> LlmWikiSummary:
        self.calls.append((source, request))
        if self.error is not None:
            raise self.error
        return self.summary


@dataclass
class FakeStore:
    source_result: LlmWikiWriteResult = LlmWikiWriteResult(
        ok=True,
        status="created",
        record_id="source-1",
        raw_path="raw/sources/source-1.md",
        canonical_path="wiki/sources/source-1.md",
    )
    note_result: LlmWikiWriteResult = LlmWikiWriteResult(
        ok=True,
        status="created",
        record_id="note-1",
        raw_path="raw/notes/note-1.md",
        canonical_path="wiki/notes/note-1.md",
    )
    source_calls: list[tuple[LlmWikiSource, LlmWikiSummary]] = field(
        default_factory=list
    )
    note_calls: list[tuple[str, str]] = field(default_factory=list)

    def save_source(
        self, source: LlmWikiSource, summary: LlmWikiSummary
    ) -> LlmWikiWriteResult:
        self.source_calls.append((source, summary))
        return self.source_result

    def save_note(self, text: str, *, author: str) -> LlmWikiWriteResult:
        self.note_calls.append((text, author))
        return self.note_result


@dataclass
class FakeScheduler:
    refresh_result: QmdCommandResult = QmdCommandResult(ok=True)
    calls: list[str] = field(default_factory=list)

    def mark_dirty(self) -> None:
        self.calls.append("mark_dirty")

    def schedule(self) -> bool:
        self.calls.append("schedule")
        return True

    def refresh_for_search(self) -> QmdCommandResult:
        self.calls.append("refresh_for_search")
        return self.refresh_result


@dataclass
class FakeQmd:
    result: QmdSearchResult = QmdSearchResult(
        ok=True,
        matches=(QmdMatch(path="wiki/a.md", snippet="evidence", score=0.9),),
    )
    calls: list[tuple[str, int]] = field(default_factory=list)

    def query(self, query: str, *, limit: int = 5) -> QmdSearchResult:
        self.calls.append((query, limit))
        return self.result


@pytest.mark.asyncio
async def test_ingest_retrieves_summarizes_writes_and_marks_qmd_dirty():
    retriever = FakeRetriever()
    summarizer = FakeSummarizer()
    store = FakeStore()
    scheduler = FakeScheduler()

    result = await run_llmwiki_ingest(
        request="Summarize https://example.com/article for the wiki",
        retriever=retriever,
        summarizer=summarizer,
        store=store,
        scheduler=scheduler,
    )

    assert result.ok is True
    assert result.status == "created"
    assert result.summary == "Structured summary"
    assert result.path == "wiki/sources/source-1.md"
    assert retriever.calls == ["https://example.com/article"]
    assert summarizer.calls == [
        (SOURCE, "Summarize https://example.com/article for the wiki")
    ]
    assert store.source_calls == [(SOURCE, SUMMARY)]
    assert scheduler.calls == ["mark_dirty", "schedule"]


@pytest.mark.asyncio
async def test_unchanged_ingest_does_not_schedule_an_index_write():
    store = FakeStore(
        source_result=LlmWikiWriteResult(
            ok=True,
            status="unchanged",
            canonical_path="wiki/sources/source-1.md",
        )
    )
    scheduler = FakeScheduler()

    result = await run_llmwiki_ingest(
        request="https://example.com/article",
        retriever=FakeRetriever(),
        summarizer=FakeSummarizer(),
        store=store,
        scheduler=scheduler,
    )

    assert result.ok is True
    assert result.status == "unchanged"
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_ingest_returns_stable_source_error_without_leaking_exception_text():
    retriever = FakeRetriever(error=SourceError("unsupported_source"))

    result = await run_llmwiki_ingest(
        request="https://example.com/article",
        retriever=retriever,
        summarizer=FakeSummarizer(),
        store=FakeStore(),
        scheduler=FakeScheduler(),
    )

    assert result.ok is False
    assert result.error == "unsupported_source"
    assert "example.com" not in render_llmwiki_ingest(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_text", ["", "no URL", "https://a.test https://b.test"]
)
async def test_ingest_rejects_blank_missing_or_ambiguous_url(input_text):
    retriever = FakeRetriever()

    result = await run_llmwiki_ingest(
        request=input_text,
        retriever=retriever,
        summarizer=FakeSummarizer(),
        store=FakeStore(),
        scheduler=FakeScheduler(),
    )

    assert result.ok is False
    assert result.error == "invalid_url"
    assert retriever.calls == []


class FakeStructuredLlm:
    def __init__(self, parsed: object = None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def acomplete_structured(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(parsed=self.parsed)


@pytest.mark.asyncio
async def test_hermes_source_summarizer_uses_strict_schema_and_sanitizes_output():
    llm = FakeStructuredLlm(
        {
            "title": "Safe title",
            "summary": "Summary @everyone",
            "key_points": ["Point one"],
            "tags": ["tag-one"],
            "source_type": "article",
            "user_perspective": "User view",
        }
    )

    summary = await HermesSourceSummarizer(llm).summarize(
        SOURCE, request="Focus on implementation"
    )

    assert summary.title == "Safe title"
    assert "@everyone" not in summary.summary
    assert summary.key_points == ("Point one",)
    assert llm.calls[0]["temperature"] == 0
    schema = llm.calls[0]["json_schema"]
    assert isinstance(schema, dict)
    assert set(schema["required"]) == {
        "title",
        "summary",
        "key_points",
        "tags",
        "source_type",
        "user_perspective",
    }
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_hermes_source_summarizer_falls_back_deterministically():
    llm = FakeStructuredLlm(error=RuntimeError("private provider detail"))

    summary = await HermesSourceSummarizer(llm).summarize(SOURCE)

    assert summary.title == "Source title"
    assert summary.summary == "First useful point. Second useful point."
    assert summary.tags == ("article",)
    assert "private provider detail" not in summary.summary


@pytest.mark.asyncio
async def test_note_writes_complete_text_and_marks_qmd_dirty():
    store = FakeStore()
    scheduler = FakeScheduler()

    result = await run_llmwiki_note(
        "A practical note",
        author="aicompanyassistant",
        store=store,
        scheduler=scheduler,
    )

    assert result.ok is True
    assert result.path == "wiki/notes/note-1.md"
    assert store.note_calls == [("A practical note", "aicompanyassistant")]
    assert scheduler.calls == ["mark_dirty", "schedule"]
    assert "wiki/notes/note-1.md" in render_llmwiki_note(result)


@pytest.mark.asyncio
async def test_note_rejects_blank_text_before_store():
    store = FakeStore()

    result = await run_llmwiki_note(
        "   ",
        author="assistant",
        store=store,
        scheduler=FakeScheduler(),
    )

    assert result.ok is False
    assert result.error == "invalid_input"
    assert store.note_calls == []


@pytest.mark.asyncio
async def test_find_refreshes_then_returns_ranked_relative_paths():
    scheduler = FakeScheduler()
    qmd = FakeQmd()

    result = await run_llmwiki_find(
        "search term", qmd=qmd, scheduler=scheduler, limit=3
    )

    assert result.ok is True
    assert result.matches[0].path == "wiki/a.md"
    assert scheduler.calls == ["refresh_for_search"]
    assert qmd.calls == [("search term", 3)]
    rendered = render_llmwiki_find(result)
    assert "wiki/a.md" in rendered
    assert "evidence" in rendered


@pytest.mark.asyncio
async def test_find_queries_stale_index_when_fast_refresh_fails():
    scheduler = FakeScheduler(
        refresh_result=QmdCommandResult(ok=False, error="command_failed")
    )
    qmd = FakeQmd(
        result=QmdSearchResult(
            ok=True,
            matches=(QmdMatch(path="wiki/old.md", snippet="old", score=0.5),),
            fallback="bm25",
        )
    )

    result = await run_llmwiki_find("term", qmd=qmd, scheduler=scheduler)

    assert result.ok is True
    assert result.fallback == "bm25"
    assert result.stale is True
    assert "BM25" in render_llmwiki_find(result)


@pytest.mark.asyncio
async def test_find_returns_sanitized_stable_failure():
    qmd = FakeQmd(result=QmdSearchResult(ok=False, error="malformed_result"))

    result = await run_llmwiki_find(
        "term", qmd=qmd, scheduler=FakeScheduler()
    )

    assert result.ok is False
    assert result.error == "malformed_result"
    assert "malformed_result" not in render_llmwiki_find(result)
