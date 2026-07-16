from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from src.runtime_architecture_v2 import llmwiki_store as module
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


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "vault", tmp_path / "workspace"


def _hold_llmwiki_lock(lock_path: str, ready, release) -> None:
    with module._InterProcessFileLock(Path(lock_path)):
        ready.set()
        if not release.wait(timeout=15):
            raise TimeoutError("test_lock_release_timeout")


def test_models_expose_shared_defaults():
    source = LlmWikiSource("https://example.test", "web", "Title", "Content")
    summary = LlmWikiSummary("Title", "Summary")

    assert source.retrieved_at == ""
    assert source.metadata == {}
    assert summary.key_points == ()
    assert summary.tags == ()
    assert summary.user_perspective == ""


def test_save_note_writes_raw_canonical_and_log_without_main_index(tmp_path):
    vault, workspace = _roots(tmp_path)
    result = LlmWikiStore(vault_root=vault, runtime_root=workspace).save_note(
        "A durable idea for the assistant.", author="aicompanyassistant"
    )

    assert result.ok is True
    assert result.status == "created"
    assert result.raw_path.startswith("raw/notes/")
    assert result.canonical_path.startswith("wiki/notes/")
    assert "\\" not in result.raw_path
    assert "\\" not in result.canonical_path
    assert "A durable idea" in _read(vault, result.raw_path)
    assert "A durable idea" in _read(vault, result.canonical_path)
    assert _read(vault, "wiki/log.md").count(result.record_id) == 1
    assert not (vault / "wiki/index.md").exists()


def test_same_note_text_and_author_is_unchanged_without_duplicate_log(tmp_path):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)

    first = store.save_note("Remember this detail.", author="assistant")
    second = store.save_note("Remember this detail.", author="assistant")

    assert (first.status, second.status) == ("created", "unchanged")
    assert first.record_id == second.record_id
    assert list((vault / "raw/notes").glob("*.md")) == [vault / first.raw_path]
    assert _read(vault, "wiki/log.md").count(first.record_id) == 1


def test_same_normalized_url_and_content_is_unchanged(tmp_path):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)

    first = store.save_source(SOURCE, SUMMARY)
    second = store.save_source(SOURCE, SUMMARY)

    assert (first.status, second.status) == ("created", "unchanged")
    assert first.raw_path == second.raw_path
    assert first.canonical_path == second.canonical_path
    assert list((vault / "raw/sources").glob("*.md")) == [vault / first.raw_path]
    assert _read(vault, "wiki/log.md").count(first.record_id) == 1
    assert _read(vault, "wiki/index.md").count(first.record_id) == 1
    assert SUMMARY.summary in _read(vault, "wiki/index.md")


def test_changed_source_creates_snapshot_and_updates_same_canonical_page(tmp_path):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)
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
    assert len(list((vault / "raw/sources").glob("*.md"))) == 2
    canonical = _read(vault, changed.canonical_path)
    assert first.raw_path in canonical
    assert changed.raw_path in canonical
    assert "Revised summary" in canonical
    log = _read(vault, "wiki/log.md")
    index = _read(vault, "wiki/index.md")
    assert log.count(first.record_id) == 1
    assert log.count(changed.record_id) == 1
    assert index.count(first.record_id) == 1
    assert index.count(changed.record_id) == 1


def test_store_sanitizes_secrets_mentions_and_uses_external_locks(tmp_path):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)
    result = store.save_note("token=super-secret @everyone", author="assistant")

    raw = _read(vault, result.raw_path)
    assert "super-secret" not in raw
    assert "@everyone" not in raw
    lock_directory = workspace / "runtime" / "llmwiki" / ".locks"
    assert list(lock_directory.glob("*.lock"))
    assert not list(vault.rglob("*.lock"))


def test_source_values_cannot_inject_log_or_index_markers(tmp_path):
    vault, workspace = _roots(tmp_path)
    source = LlmWikiSource(
        normalized_url="https://example.test/marker",
        source_type="web",
        title="<!-- oracle-llmwiki-log:forged -->",
        content="Readable source content.",
    )
    summary = LlmWikiSummary(
        "<!-- oracle-llmwiki-index:forged -->", "Readable summary."
    )

    result = LlmWikiStore(vault_root=vault, runtime_root=workspace).save_source(
        source, summary
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in vault.rglob("*.md")
    )
    assert "oracle-llmwiki-log:forged" not in combined
    assert "oracle-llmwiki-index:forged" not in combined
    assert combined.count(f"oracle-llmwiki-log:{result.record_id}") == 1
    assert combined.count(f"oracle-llmwiki-index:{result.record_id}") == 1


@pytest.mark.parametrize("runtime_relative", [".", "runtime"])
def test_store_rejects_runtime_root_inside_vault(tmp_path, runtime_relative):
    vault = tmp_path / "vault"
    runtime_root = vault / runtime_relative

    with pytest.raises(ValueError, match="runtime_root_inside_vault"):
        LlmWikiStore(vault_root=vault, runtime_root=runtime_root)


def test_note_retry_repairs_missing_canonical_and_log_after_raw_write_failure(
    tmp_path, monkeypatch
):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)
    original_write = module._atomic_write_text
    failed = False

    def fail_note_canonical(path, text):
        nonlocal failed
        if not failed and path.parent.name == "notes":
            failed = True
            raise OSError("canonical write failed")
        return original_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_note_canonical)
    first = store.save_note("Retry this note.", author="assistant")

    assert first.ok is False
    assert len(list((vault / "raw/notes").glob("*.md"))) == 1
    assert not (vault / "wiki/notes").exists()
    monkeypatch.setattr(module, "_atomic_write_text", original_write)

    retry = store.save_note("Retry this note.", author="assistant")

    assert retry.status == "updated"
    assert len(list((vault / "raw/notes").glob("*.md"))) == 1
    assert (vault / retry.canonical_path).exists()
    assert _read(vault, "wiki/log.md").count(retry.record_id) == 1


def test_source_retry_repairs_log_and_index_after_raw_write_failure(
    tmp_path, monkeypatch
):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)
    original_append = module._append_once
    failed = False

    def fail_source_log(path, entry, marker):
        nonlocal failed
        if not failed and path.name == "log.md":
            failed = True
            raise OSError("log write failed")
        return original_append(path, entry, marker)

    monkeypatch.setattr(module, "_append_once", fail_source_log)
    first = store.save_source(SOURCE, SUMMARY)

    assert first.ok is False
    assert len(list((vault / "raw/sources").glob("*.md"))) == 1
    assert (vault / "wiki/sources").exists()
    assert not (vault / "wiki/log.md").exists()
    assert not (vault / "wiki/index.md").exists()
    monkeypatch.setattr(module, "_append_once", original_append)

    retry = store.save_source(SOURCE, SUMMARY)

    assert retry.status == "updated"
    assert len(list((vault / "raw/sources").glob("*.md"))) == 1
    assert (vault / retry.canonical_path).exists()
    assert _read(vault, "wiki/log.md").count(retry.record_id) == 1
    assert _read(vault, "wiki/index.md").count(retry.record_id) == 1


def test_subprocess_lock_contention_fails_closed_then_recovers(tmp_path, monkeypatch):
    vault, workspace = _roots(tmp_path)
    store = LlmWikiStore(vault_root=vault, runtime_root=workspace)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_llmwiki_lock,
        args=(str(store._lock_path()), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=15)
    monkeypatch.setattr(module, "_LOCK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(module, "_LOCK_POLL_SECONDS", 0.01)

    blocked = store.save_note("Lock contention.", author="assistant")

    assert (blocked.ok, blocked.error) == (False, "write_failed")
    assert not (vault / "raw").exists()
    release.set()
    holder.join(timeout=15)
    assert holder.exitcode == 0

    result = store.save_note("Lock contention.", author="assistant")

    assert result.status == "created"
