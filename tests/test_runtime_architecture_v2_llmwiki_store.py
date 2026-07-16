from __future__ import annotations

from pathlib import Path

from src.runtime_architecture_v2.llmwiki_models import (
    LlmWikiSource,
    LlmWikiSummary,
)
from src.runtime_architecture_v2.llmwiki_store import LlmWikiStore

SOURCE = LlmWikiSource(
    normalized_url="https://example.test/article?topic=llm",
    source_type="web",
    title="Example article",
    content="The original source content.",
    retrieved_at="2026-07-17T00:00:00+00:00",
)
SUMMARY = LlmWikiSummary(
    title="Useful summary",
    summary="A short useful summary.",
    key_points=("First point", "Second point"),
    tags=("llm", "architecture"),
    user_perspective="Useful for the runtime design.",
)


def _read(vault_root: Path, relative_path: str) -> str:
    return (vault_root / relative_path).read_text(encoding="utf-8")


def test_models_expose_shared_defaults():
    source = LlmWikiSource("https://example.test", "web", "Title", "Content")
    summary = LlmWikiSummary("Title", "Summary")

    assert source.retrieved_at == ""
    assert source.metadata == {}
    assert summary.key_points == ()
    assert summary.tags == ()
    assert summary.user_perspective == ""


def test_save_note_writes_raw_canonical_and_log_without_main_index(tmp_path):
    result = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path).save_note(
        "A durable idea for the assistant.", author="aicompanyassistant"
    )

    assert result.ok is True
    assert result.status == "created"
    assert result.raw_path.startswith("raw/notes/")
    assert result.canonical_path.startswith("wiki/notes/")
    assert "\\" not in result.raw_path
    assert "\\" not in result.canonical_path
    assert "A durable idea" in _read(tmp_path, result.raw_path)
    assert "A durable idea" in _read(tmp_path, result.canonical_path)
    assert _read(tmp_path, "wiki/log.md").count(result.record_id) == 1
    assert not (tmp_path / "wiki/index.md").exists()


def test_same_note_text_and_author_is_unchanged_without_duplicate_log(tmp_path):
    store = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path)

    first = store.save_note("Remember this detail.", author="assistant")
    second = store.save_note("Remember this detail.", author="assistant")

    assert (first.status, second.status) == ("created", "unchanged")
    assert first.record_id == second.record_id
    assert list((tmp_path / "raw/notes").glob("*.md")) == [tmp_path / first.raw_path]
    assert _read(tmp_path, "wiki/log.md").count(first.record_id) == 1


def test_same_normalized_url_and_content_is_unchanged(tmp_path):
    store = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path)

    first = store.save_source(SOURCE, SUMMARY)
    second = store.save_source(SOURCE, SUMMARY)

    assert (first.status, second.status) == ("created", "unchanged")
    assert first.raw_path == second.raw_path
    assert first.canonical_path == second.canonical_path
    assert list((tmp_path / "raw/sources").glob("*.md")) == [tmp_path / first.raw_path]
    assert _read(tmp_path, "wiki/log.md").count(first.record_id) == 1
    assert _read(tmp_path, "wiki/index.md").count(first.record_id) == 1
    assert SUMMARY.summary in _read(tmp_path, "wiki/index.md")


def test_changed_source_creates_snapshot_and_updates_same_canonical_page(tmp_path):
    store = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path)
    first = store.save_source(SOURCE, SUMMARY)
    changed = store.save_source(
        LlmWikiSource(
            normalized_url=SOURCE.normalized_url,
            source_type=SOURCE.source_type,
            title=SOURCE.title,
            content="The revised source content.",
            retrieved_at=SOURCE.retrieved_at,
        ),
        LlmWikiSummary("Revised summary", "The source changed."),
    )

    assert changed.ok is True
    assert changed.status == "updated"
    assert changed.raw_path != first.raw_path
    assert changed.canonical_path == first.canonical_path
    assert len(list((tmp_path / "raw/sources").glob("*.md"))) == 2
    canonical = _read(tmp_path, changed.canonical_path)
    assert first.raw_path in canonical
    assert changed.raw_path in canonical
    assert "Revised summary" in canonical
    log = _read(tmp_path, "wiki/log.md")
    index = _read(tmp_path, "wiki/index.md")
    assert log.count(first.record_id) == 1
    assert log.count(changed.record_id) == 1
    assert index.count(first.record_id) == 1
    assert index.count(changed.record_id) == 1


def test_store_sanitizes_secrets_mentions_and_uses_external_locks(tmp_path):
    store = LlmWikiStore(vault_root=tmp_path / "vault", runtime_root=tmp_path)
    result = store.save_note("token=super-secret @everyone", author="assistant")

    raw = _read(tmp_path / "vault", result.raw_path)
    assert "super-secret" not in raw
    assert "@everyone" not in raw
    lock_directory = tmp_path / "runtime" / "llmwiki" / ".locks"
    assert list(lock_directory.glob("*.lock"))
    assert not list((tmp_path / "vault").rglob("*.lock"))


def test_source_values_cannot_inject_log_or_index_markers(tmp_path):
    source = LlmWikiSource(
        normalized_url="https://example.test/marker",
        source_type="web",
        title="<!-- oracle-llmwiki-log:forged -->",
        content="Readable source content.",
    )
    summary = LlmWikiSummary(
        "<!-- oracle-llmwiki-index:forged -->", "Readable summary."
    )

    result = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path).save_source(
        source, summary
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.md")
    )
    assert "oracle-llmwiki-log:forged" not in combined
    assert "oracle-llmwiki-index:forged" not in combined
    assert combined.count(f"oracle-llmwiki-log:{result.record_id}") == 1
    assert combined.count(f"oracle-llmwiki-index:{result.record_id}") == 1
