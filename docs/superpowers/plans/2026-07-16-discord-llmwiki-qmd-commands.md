# Discord LLM Wiki and QMD Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Hermes-native `/llmwiki-ingest`, `/llmwiki-note`, and `/llmwiki-find` commands backed by the existing Obsidian vault and one QMD `obsidian` collection, then make successful `/archive` writes schedule an index refresh.

**Architecture:** Keep Discord registration in the existing `ai-agent-commands` plugin and put all behavior in transport-neutral Runtime v2 modules. Store immutable source/note records and mutable canonical pages in the existing `raw/` and `wiki/` trees; acquire every URL through one injectable `abx-dl` adapter; execute QMD with argument arrays through an injectable adapter; persist a dirty marker and serialize index work across all seven gateway processes.

**Tech Stack:** Python 3.11, pytest/pytest-asyncio, Hermes Agent plugin API, official ArchiveBox `abx-dl` CLI and plugin bundle, QMD CLI, Obsidian Markdown, existing Runtime v2 sanitization and locking patterns.

## Global Constraints

- Canonical design: `docs/superpowers/specs/2026-07-13-discord-slash-commands-obsidian-design.md`.
- Vault root comes only from `OBSIDIAN_VAULT_PATH`; deployed value is `/home/ubuntu/Obsidian`.
- QMD collection name is exactly `obsidian`, rooted at `/home/ubuntu/Obsidian`, with `**/*.md` included.
- QMD config, index, models, locks, and dirty state stay outside the Google Drive-mounted vault.
- Use `QMD_EMBED_MODEL=hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf` for Korean retrieval.
- Invoke QMD and `abx-dl` with `shell=False`; never interpolate user text into a shell command.
- Production `abx-dl` runs with `--no-install`; install and pin its package and extractor dependencies before gateway deployment.
- Runtime v2 has one URL acquisition adapter. Do not add site-specific production fallbacks for YouTube, Instagram, Threads, articles, or PDFs.
- Raw records are immutable. Canonical pages, `wiki/index.md`, and `wiki/log.md` follow the existing atomic-write and idempotency policy.
- `/llmwiki-find` is read-only. It runs a fast incremental QMD update but never waits for a full embedding rebuild.
- Missing models, QMD, `abx-dl`, extractor dependencies, inaccessible pages, malformed output, and timeouts fail closed with sanitized Korean responses.
- Do not modify Hermes Core or add a standalone Discord webhook service.

## File Map

- Create `src/runtime_architecture_v2/qmd_search.py`: typed QMD subprocess adapter and JSON result parser.
- Create `src/runtime_architecture_v2/qmd_indexing.py`: durable dirty marker, cross-process lock, debounced update/embed worker, and reconciliation entry point.
- Create `src/runtime_architecture_v2/llmwiki_models.py`: immutable data exchanged by retrieval, storage, summaries, and commands.
- Create `src/runtime_architecture_v2/llmwiki_sources.py`: single-URL parsing, URL normalization, one bounded `abx-dl` subprocess adapter, and deterministic artifact selection.
- Create `src/runtime_architecture_v2/llmwiki_store.py`: immutable raw source/note records, canonical pages, and idempotent index/log writes.
- Create `src/runtime_architecture_v2/llmwiki_commands.py`: transport-neutral note, ingest, and find orchestration plus user-facing result rendering.
- Modify `hermes_plugins/ai-agent-commands/__init__.py`: register the three commands and construct reviewed dependencies.
- Modify `hermes_plugins/ai-agent-commands/plugin.yaml`: declare the expanded command plugin version without secrets.
- Modify `docs/operations/discord-save-slash-command.md`: add QMD prerequisites, reconciliation, smoke tests, and rollback notes while retaining the existing runbook.
- Create focused test modules matching each new Runtime v2 module; extend `tests/test_runtime_architecture_v2_ai_agent_plugin.py` and `tests/test_discord_save_operational_guards.py`.

---

### Task 1: QMD CLI Adapter and Ranked Search

**Files:**
- Create: `src/runtime_architecture_v2/qmd_search.py`
- Create: `tests/test_runtime_architecture_v2_qmd_search.py`

**Interfaces:**
- Produces: `QmdMatch`, `QmdSearchResult`, `QmdCommandResult`, `QmdRunner`, and `QmdClient`.
- `QmdClient.update() -> QmdCommandResult`
- `QmdClient.embed() -> QmdCommandResult`
- `QmdClient.query(query: str, *, limit: int = 5) -> QmdSearchResult`
- Later tasks inject the same `QmdClient` into indexing and command services.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_query_uses_argument_vector_and_parses_vault_relative_matches():
    runner = FakeRunner(stdout='[{"file":"wiki/a.md","score":0.9,"snippet":"alpha"}]')
    result = QmdClient(runner=runner).query("한국어 질문", limit=3)
    assert runner.calls == [["qmd", "query", "한국어 질문", "--json", "-c", "obsidian", "-n", "3"]]
    assert result.matches == (QmdMatch(path="wiki/a.md", snippet="alpha", score=0.9),)

def test_query_falls_back_to_bm25_when_hybrid_query_fails():
    runner = SequenceRunner([failed("model unavailable"), succeeded(BM25_JSON)])
    result = QmdClient(runner=runner).query("query")
    assert runner.calls[1][:3] == ["qmd", "search", "query"]
    assert result.fallback == "bm25"

def test_query_rejects_blank_input_and_absolute_result_paths():
    with pytest.raises(ValueError, match="blank_query"):
        QmdClient(runner=FakeRunner()).query("  ")
    assert QmdClient(runner=AbsolutePathRunner()).query("x").error == "unsafe_result"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_search.py -q`

Expected: collection fails because `qmd_search` does not exist.

- [ ] **Step 3: Implement the typed adapter**

```python
@dataclass(frozen=True)
class QmdMatch:
    path: str
    snippet: str
    score: float

@dataclass(frozen=True)
class QmdSearchResult:
    ok: bool
    matches: tuple[QmdMatch, ...] = ()
    fallback: str = ""
    error: str = ""

@dataclass(frozen=True)
class QmdCommandResult:
    ok: bool
    error: str = ""

@dataclass(frozen=True)
class QmdRawResult:
    exit_code: int
    stdout: str
    stderr: str

class QmdRunner(Protocol):
    def __call__(self, argv: list[str], timeout_seconds: float) -> QmdRawResult:
        pass

class QmdClient:
    def query(self, query: str, *, limit: int = 5) -> QmdSearchResult:
        normalized = query.strip()
        if not normalized:
            raise ValueError("blank_query")
        primary = self._run(["qmd", "query", normalized, "--json", "-c", "obsidian", "-n", str(limit)])
        if primary.ok:
            return _parse_search_result(primary.stdout)
        fallback = self._run(["qmd", "search", normalized, "--json", "-c", "obsidian", "-n", str(limit)])
        return _parse_search_result(fallback, fallback="bm25")
```

Use `subprocess.run(..., capture_output=True, text=True, timeout=120, shell=False, check=False)`. Parse only a top-level JSON list or QMD's documented result envelope, cap results at `limit`, convert `qmd://obsidian/<path>` or relative result paths to safe vault-relative `PurePosixPath` values, and reject absolute paths or another collection. Map executable absence, timeout, nonzero exit, malformed JSON, and unsafe paths to stable error codes without returning stderr.

- [ ] **Step 4: Run focused tests and lint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_search.py -q`

Expected: all QMD adapter tests pass.

Run: `npm run lint:ruff`

Expected: exit 0.

- [ ] **Step 5: Commit the adapter**

```bash
git add src/runtime_architecture_v2/qmd_search.py tests/test_runtime_architecture_v2_qmd_search.py
git commit -m "feat: add QMD search adapter"
```

### Task 2: Durable QMD Index Scheduling

**Files:**
- Create: `src/runtime_architecture_v2/qmd_indexing.py`
- Create: `tests/test_runtime_architecture_v2_qmd_indexing.py`

**Interfaces:**
- Consumes: `QmdClient.update()` and `QmdClient.embed()` from Task 1.
- Produces: `QmdIndexScheduler(runtime_root: Path, client: QmdClient)`.
- `mark_dirty() -> None` atomically persists `runtime/qmd/dirty.json`.
- `schedule() -> bool` coalesces process-local requests and starts one daemon worker.
- `refresh_for_search() -> QmdCommandResult` serializes and runs only `qmd update`.
- `reconcile() -> QmdReconcileResult` runs update plus incremental embed and clears the marker only after both succeed.

- [ ] **Step 1: Write failing scheduler tests**

```python
def test_mark_dirty_survives_scheduler_recreation(tmp_path):
    first = QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient())
    first.mark_dirty()
    assert QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient()).dirty is True

def test_reconcile_coalesces_and_clears_only_after_update_and_embed(tmp_path):
    client = FakeQmdClient(update_ok=True, embed_ok=True)
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=client)
    scheduler.mark_dirty()
    assert scheduler.reconcile().ok is True
    assert client.calls == ["update", "embed"]
    assert scheduler.dirty is False

def test_failed_embed_preserves_dirty_marker(tmp_path):
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient(embed_ok=False))
    scheduler.mark_dirty()
    assert scheduler.reconcile().ok is False
    assert scheduler.dirty is True
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_indexing.py -q`

Expected: import failure for `qmd_indexing`.

- [ ] **Step 3: Implement durable coalescing and locking**

Persist state under `<AI_AGENT_ROOT>/runtime/qmd/`, use an atomic temporary-file replace for `dirty.json`, and use a bounded OS file lock at `runtime/qmd/index.lock`. `schedule()` must never create more than one worker thread per process. The worker waits five seconds to debounce, then calls `reconcile()`. `refresh_for_search()` acquires the same cross-process lock and runs update only. Cancellation or gateway shutdown may leave `dirty.json`, which is intentional recovery state.

```python
@dataclass(frozen=True)
class QmdReconcileResult:
    ok: bool
    updated: bool = False
    embedded: bool = False
    error: str = ""
```

- [ ] **Step 4: Verify scheduler tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_indexing.py -q`

Expected: all scheduler tests pass, including two scheduler instances contending on one lock.

- [ ] **Step 5: Commit the scheduler**

```bash
git add src/runtime_architecture_v2/qmd_indexing.py tests/test_runtime_architecture_v2_qmd_indexing.py
git commit -m "feat: add durable QMD indexing scheduler"
```

### Task 3: LLM Wiki Vault Store

**Files:**
- Create: `src/runtime_architecture_v2/llmwiki_models.py`
- Create: `src/runtime_architecture_v2/llmwiki_store.py`
- Create: `tests/test_runtime_architecture_v2_llmwiki_store.py`

**Interfaces:**
- Produces in `llmwiki_models.py`: `LlmWikiSource`, `LlmWikiSummary`, and `LlmWikiWriteResult`.
- Produces in `llmwiki_store.py`: `LlmWikiStore`.
- `save_note(text: str, *, author: str) -> LlmWikiWriteResult`
- `save_source(source: LlmWikiSource, summary: LlmWikiSummary) -> LlmWikiWriteResult`
- Raw paths: `raw/notes/` and `raw/sources/`; canonical paths: `wiki/notes/` and `wiki/sources/`.

- [ ] **Step 1: Write failing immutable-write and idempotency tests**

```python
def test_save_note_writes_raw_canonical_and_log(tmp_path):
    result = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path).save_note(
        "아이디어", author="aicompanyassistant"
    )
    assert result.raw_path.startswith("raw/notes/")
    assert result.canonical_path.startswith("wiki/notes/")
    assert (tmp_path / "wiki/log.md").read_text(encoding="utf-8").count(result.record_id) == 1

def test_same_normalized_url_and_content_is_unchanged(tmp_path):
    store = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path)
    first = store.save_source(SOURCE, SUMMARY)
    second = store.save_source(SOURCE, SUMMARY)
    assert (first.status, second.status) == ("created", "unchanged")
    assert list((tmp_path / "raw/sources").glob("*.md")) == [tmp_path / first.raw_path]

def test_store_sanitizes_secrets_and_rejects_containment_escape(tmp_path):
    result = LlmWikiStore(vault_root=tmp_path, runtime_root=tmp_path).save_note(
        "token=super-secret @everyone", author="assistant"
    )
    text = (tmp_path / result.raw_path).read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "@everyone" not in text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_store.py -q`

Expected: import failure for `llmwiki_store`.

- [ ] **Step 3: Implement the store with existing vault conventions**

Use `sanitize_knowledge_text()` and `sanitize_url()` from `knowledge.py`. Derive a stable source ID from the normalized URL and a snapshot ID from normalized URL plus retrieved content. Write UTF-8 Markdown through same-directory temporary files and `Path.replace()`. Use one process-local vault lock and one cross-process lock under `<AI_AGENT_ROOT>/runtime/llmwiki/.locks/`; never put locks in Google Drive. Add idempotent markers `<!-- oracle-llmwiki-log:<record_id> -->` and `<!-- oracle-llmwiki-index:<record_id> -->`. Promote source summaries to `wiki/index.md`. Notes are logged and QMD-searchable but do not enter the main index in the first implementation.

- [ ] **Step 4: Verify store and existing conversation tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_store.py tests/test_runtime_architecture_v2_obsidian_conversations.py -q`

Expected: all tests pass and existing `/archive` storage behavior is unchanged.

- [ ] **Step 5: Commit the store**

```bash
git add src/runtime_architecture_v2/llmwiki_models.py src/runtime_architecture_v2/llmwiki_store.py tests/test_runtime_architecture_v2_llmwiki_store.py
git commit -m "feat: add LLM Wiki vault store"
```

### Task 4: Unified `abx-dl` URL Acquisition

**Files:**
- Modify: `src/runtime_architecture_v2/llmwiki_sources.py`
- Modify: `tests/test_runtime_architecture_v2_llmwiki_sources.py`

**Interfaces:**
- Consumes: `LlmWikiSource` from `llmwiki_models.py` in Task 3.
- Produces: `extract_single_url(text: str) -> str`, `normalize_source_url(url: str) -> str`, `AbxDlRunner`, `AbxDlRunResult`, and `SourceRetriever.retrieve(url: str) -> LlmWikiSource`.
- Acquisition contract: every supported URL is sent to the same `abx-dl` runner. Runtime v2 selects artifacts by content role rather than dispatching on the source domain.

- [x] **Step 1: Replace direct-fetch tests with failing unified-adapter contract tests**

```python
@pytest.mark.parametrize("text", ["no url", "https://a.example https://b.example"])
def test_extract_single_url_rejects_missing_or_ambiguous_input(text):
    with pytest.raises(SourceError):
        extract_single_url(text)

@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "https://www.youtube.com/watch?v=video123",
        "https://www.instagram.com/p/post123/",
        "https://www.threads.net/@author/post/post123",
    ],
)
def test_every_source_uses_the_same_abxdl_runner(url, abxdl_fixture_runner):
    source = SourceRetriever(abxdl_runner=abxdl_fixture_runner).retrieve(url)
    assert abxdl_fixture_runner.calls == 1
    assert abxdl_fixture_runner.argv[0] == "abx-dl"
    assert "--no-install" in abxdl_fixture_runner.argv
    assert abxdl_fixture_runner.argv[-1] == normalize_source_url(url)
    assert source.content

def test_artifact_priority_is_transcript_then_clean_text_then_metadata(
    abxdl_fixture_runner,
):
    abxdl_fixture_runner.add("subtitles/video.ko.vtt", "WEBVTT\n\n00:00.000 --> 00:01.000\nspoken")
    abxdl_fixture_runner.add("readability/content.txt", "article")
    abxdl_fixture_runner.add("seo/metadata.json", '{"description":"caption"}')
    source = SourceRetriever(abxdl_runner=abxdl_fixture_runner).retrieve(YOUTUBE_URL)
    assert source.content == "spoken"

def test_abxdl_failure_does_not_fall_back_to_direct_network(abxdl_failure_runner):
    with pytest.raises(SourceError, match="unsupported_source"):
        SourceRetriever(abxdl_runner=abxdl_failure_runner).retrieve(SAFE_URL)
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_sources.py -q`

Expected: failures because `SourceRetriever` still accepts direct HTTP and
`yt-dlp` runners and has no `AbxDlRunner` contract.

- [x] **Step 3: Implement the single adapter and deterministic artifact selector**

Preserve the existing single-URL extraction and normalization rules. Reject
literal loopback, private, link-local, multicast, reserved, and unspecified
targets before starting the subprocess. Replace `_StdlibHttpFetcher`, direct
`yt-dlp` dispatch, and domain classification with one injected `AbxDlRunner`.
The default runner executes `abx-dl` with `shell=False`, `--no-install`, a
temporary output directory, a sanitized environment, a 120-second wall-clock
limit, a 10 MiB cumulative textual-output limit, and a 256-file limit. Kill the
process group on timeout or overflow and never include stdout, stderr, local
paths, cookies, or environment values in `SourceError`.

Read `index.jsonl` plus files contained under the temporary output root; reject
symlinks and resolved paths outside that root. Select usable evidence in this
order: `.vtt`/`.srt` transcript text, cleaned Markdown or article text from
`trafilatura`/`defuddle`/`readability`, `ytdlp` or `gallerydl` text/JSON
description and caption fields, then `htmltotext` or rendered-DOM text. Derive
`source_type` from the successful artifact plugin, defaulting to `web`; do not
branch on Instagram, Threads, YouTube, or other hostnames. If no bounded text
artifact exists, return `unsupported_source`. Delete the temporary directory
after constructing `LlmWikiSource`.

- [x] **Step 4: Run security and retrieval tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_sources.py -q`

Expected: all tests pass, including one-run behavior for generic web, YouTube,
Instagram, and Threads fixtures; artifact priority; path containment;
file-count and byte limits; malformed JSONL; timeout cleanup; sanitized
environment; and stable failure without a direct-network fallback.

- [x] **Step 5: Commit retrieval**

```bash
git add src/runtime_architecture_v2/llmwiki_sources.py tests/test_runtime_architecture_v2_llmwiki_sources.py
git commit -m "refactor: use unified abx-dl source adapter"
```

### Task 5: Transport-Neutral LLM Wiki Commands

**Files:**
- Create: `src/runtime_architecture_v2/llmwiki_commands.py`
- Create: `tests/test_runtime_architecture_v2_llmwiki_commands.py`

**Interfaces:**
- Consumes: `SourceRetriever`, `LlmWikiStore`, `QmdClient`, `QmdIndexScheduler`, and Hermes host `StructuredLlm`.
- Produces: `run_llmwiki_ingest()`, `run_llmwiki_note()`, `run_llmwiki_find()` and matching render functions.

- [x] **Step 1: Write failing orchestration tests**

```python
@pytest.mark.asyncio
async def test_ingest_retrieves_summarizes_writes_and_marks_qmd_dirty():
    result = await run_llmwiki_ingest(
        request="이 링크를 정리해줘 https://example.com/a",
        retriever=RETRIEVER,
        summarizer=SUMMARIZER,
        store=STORE,
        scheduler=SCHEDULER,
    )
    assert result.ok is True
    assert SCHEDULER.calls == ["mark_dirty", "schedule"]

@pytest.mark.asyncio
async def test_find_refreshes_index_then_returns_ranked_relative_paths():
    result = await run_llmwiki_find("검색어", qmd=QMD, scheduler=SCHEDULER)
    assert SCHEDULER.calls == ["refresh_for_search"]
    assert result.matches[0].path == "wiki/a.md"
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_commands.py -q`

Expected: import failure for `llmwiki_commands`.

- [x] **Step 3: Implement command services and structured summarization**

Add `HermesSourceSummarizer`, using `acomplete_structured()` with temperature 0 and a strict schema containing `title`, `summary`, `key_points`, `tags`, `source_type`, and `user_perspective`. Sanitize parsed output and provide a deterministic fallback from retrieved title/text. Run blocking retrieval, store, QMD, and scheduler calls through `asyncio.to_thread()`. Return stable error codes and render concise Korean responses containing status, summary, and vault-relative path; never include absolute paths, stderr, or raw exceptions.

- [x] **Step 4: Verify command services**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_llmwiki_commands.py -q`

Expected: all command tests pass for blank input, duplicate URL, unsupported source, fallback summary, QMD BM25 fallback, and sanitized failures.

- [x] **Step 5: Commit command services**

```bash
git add src/runtime_architecture_v2/llmwiki_commands.py tests/test_runtime_architecture_v2_llmwiki_commands.py
git commit -m "feat: add LLM Wiki command services"
```

### Task 6: Hermes Commands and Archive Index Hook

**Files:**
- Modify: `hermes_plugins/ai-agent-commands/__init__.py`
- Modify: `hermes_plugins/ai-agent-commands/plugin.yaml`
- Modify: `tests/test_runtime_architecture_v2_ai_agent_plugin.py`

**Interfaces:**
- Registers `/archive`, `/llmwiki-ingest`, `/llmwiki-find`, and `/llmwiki-note` through `ctx.register_command()`.
- Uses the same `OBSIDIAN_VAULT_PATH`, `AI_AGENT_ROOT`, `ctx.llm`, `QmdClient`, and `QmdIndexScheduler` dependency construction for all profiles.

- [x] **Step 1: Extend plugin tests before registration code**

```python
def test_plugin_registers_archive_and_three_llmwiki_commands():
    plugin = _load_plugin()
    ctx = FakePluginContext()
    plugin.register(ctx)
    assert list(ctx.commands) == [
        "archive", "llmwiki-ingest", "llmwiki-find", "llmwiki-note"
    ]
    assert ctx.commands["llmwiki-find"]["args_hint"] == "검색어"

@pytest.mark.asyncio
async def test_archive_created_or_updated_marks_qmd_dirty(monkeypatch, tmp_path):
    scheduler = stub_scheduler(monkeypatch)
    stub_archive_result(monkeypatch, status="updated")
    handler = registered_command("archive")
    await handler("")
    assert scheduler.calls == ["mark_dirty", "schedule"]
```

- [x] **Step 2: Run plugin tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_ai_agent_plugin.py -q`

Expected: command-list and archive-index assertions fail.

- [x] **Step 3: Register handlers without domain duplication**

Keep the plugin responsible only for environment validation, late imports, dependency construction, invocation deduplication, and response transport. Use one helper that resolves and validates `AI_AGENT_ROOT` and `OBSIDIAN_VAULT_PATH`; create command-specific invocation keys; pass the raw free-form string unchanged to Runtime v2 services. After `/archive` returns `created` or `updated`, mark and schedule QMD refresh before rendering. Do not refresh on `unchanged` or failure.

- [x] **Step 4: Verify plugin and archive regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_ai_agent_plugin.py tests/test_runtime_architecture_v2_save_command.py tests/test_runtime_architecture_v2_obsidian_conversations.py -q`

Expected: all tests pass and `/archive` retains its exact no-argument contract.

- [x] **Step 5: Commit plugin integration**

```bash
git add hermes_plugins/ai-agent-commands/__init__.py hermes_plugins/ai-agent-commands/plugin.yaml tests/test_runtime_architecture_v2_ai_agent_plugin.py
git commit -m "feat: register LLM Wiki Discord commands"
```

### Task 7: Official Server Setup, Reconciliation, and Rollout Evidence

**Files:**
- Create: `scripts/run_qmd_reconcile.py`
- Create: `tests/test_runtime_architecture_v2_qmd_reconcile_cli.py`
- Modify: `docs/operations/discord-save-slash-command.md`
- Modify: `tests/test_discord_save_operational_guards.py`

**Interfaces:**
- `python -m scripts.run_qmd_reconcile --root <AI_AGENT_ROOT>` exits 0 only when update and incremental embed succeed.
- Operations use one QMD collection and one five-minute systemd timer; all seven profiles share it.

- [x] **Step 1: Write failing reconciliation and runbook guard tests**

```python
def test_reconcile_cli_preserves_dirty_state_on_qmd_failure(tmp_path):
    result = run_cli(tmp_path, qmd_exit=1)
    assert result.returncode == 1
    assert (tmp_path / "runtime/qmd/dirty.json").exists()

def test_runbook_pins_single_collection_and_korean_model():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "qmd collection add /home/ubuntu/Obsidian --name obsidian" in text
    assert "Qwen3-Embedding-0.6B-Q8_0.gguf" in text

def test_runbook_pins_abxdl_and_disables_runtime_install():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "uv tool install abx-dl==" in text
    assert "abx-dl version" in text
    assert "--no-install" in text
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_reconcile_cli.py tests/test_discord_save_operational_guards.py -q`

Expected: missing CLI and runbook assertions fail.

- [x] **Step 3: Add reconciliation CLI and exact deployment procedure**

Document and later execute these gates in order: verify Node.js >=22; install
the official `@tobilu/qmd` package; verify QMD on Ubuntu ARM64; export the
pinned Qwen3 model; add the `obsidian` collection once; run `qmd update` and
`qmd embed -f`; verify one Korean JSON query; install the official PyPI
`abx-dl` package with an exact version through `uv tool install`; run
`abx-dl version` and `abx-dl plugins`; preinstall the reviewed text/metadata
extractor dependencies; verify `--no-install` probes for one generic article,
one YouTube video, one public Instagram post, and one public Threads post; and
record the successful package/plugin versions in the runbook. A source class
that cannot produce bounded text is a documented unsupported case, not a
reason to add a Runtime v2 site adapter. Then install a systemd oneshot service
and five-minute timer invoking the QMD reconciliation CLI; deploy one reviewed
plugin revision to Assistant; run all three commands and `/archive`; and deploy
the same hash to the remaining six profiles sequentially. Rollback disables
the timer, restores the prior plugin revision, removes the `abx-dl` runner from
the command dependency graph, and leaves the vault and QMD cache intact.

- [x] **Step 4: Run full verification before live deployment**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runtime_architecture_v2_qmd_search.py tests/test_runtime_architecture_v2_qmd_indexing.py tests/test_runtime_architecture_v2_llmwiki_sources.py tests/test_runtime_architecture_v2_llmwiki_store.py tests/test_runtime_architecture_v2_llmwiki_commands.py tests/test_runtime_architecture_v2_qmd_reconcile_cli.py tests/test_runtime_architecture_v2_ai_agent_plugin.py tests/test_runtime_architecture_v2_save_command.py tests/test_runtime_architecture_v2_obsidian_conversations.py tests/test_discord_save_operational_guards.py -q`

Expected: all focused tests pass.

Run: `npm run typecheck`

Expected: exit 0.

Run: `npm run lint:ruff`

Expected: exit 0.

Run: `git diff --check`

Expected: exit 0.

- [x] **Step 5: Commit operational support**

```bash
git add scripts/run_qmd_reconcile.py tests/test_runtime_architecture_v2_qmd_reconcile_cli.py docs/operations/discord-save-slash-command.md tests/test_discord_save_operational_guards.py
git commit -m "ops: add QMD reconciliation and rollout gates"
```

## Completion Gate

- [ ] The three LLM Wiki commands use the official Hermes plugin command path.
- [ ] One `obsidian` QMD collection covers every Vault Markdown file.
- [ ] Korean hybrid search works and BM25 fallback is tested.
- [ ] One pinned `abx-dl` adapter covers bounded text/metadata retrieval for
  accessible generic web, YouTube, Instagram, and Threads fixtures without
  site-specific Runtime v2 fallbacks.
- [ ] Raw source and note records remain immutable; repeat ingest is idempotent.
- [ ] Every successful write marks QMD dirty and schedules one coalesced refresh.
- [ ] `/archive` behavior and Runtime v2 regressions remain green.
- [ ] Assistant smoke evidence passes before the same revision reaches the other six profiles.
