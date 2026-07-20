# Task 4 Report: Safe Multi-Source URL Retrieval

## Status

Task 4 is complete in local branch `feature/llmwiki-commands` at commit
`d3f3475`. Every URL now crosses one bounded `abx-dl` acquisition adapter; the
previous direct HTTP and direct `yt-dlp` production paths were removed rather
than retained as fallbacks. Offline contract, integration, lint, diff, and
secret checks pass. The Ubuntu ARM64 installation/live-source probe remains a
deployment check for Task 7 and is not claimed here.

## 2026-07-20 Unified Adapter Decision

- One Runtime v2 adapter invokes the official ArchiveBox `abx-dl` CLI for
  generic web, YouTube, Instagram, Threads, PDFs, and future URL sources.
- Runtime v2 selects bounded artifacts by role: transcript, clean article text,
  social caption/description metadata, then rendered text.
- Runtime v2 does not maintain per-domain production adapters.
- Production runs use `shell=False`, a sanitized environment, a temporary
  output root, `--no-install`, fixed timeout/byte/file-count limits, and stable
  sanitized failures.
- Full ArchiveBox collection storage, Web UI, REST API, large media retention,
  authenticated personas, and recursive crawling are outside this task.
- The direct standard-library HTTP and direct `yt-dlp` paths are removed after
  the unified adapter passes offline tests and an Ubuntu ARM64 probe.
- The previous downstream `yt-dlp` redirect/peer-validation blocker is
  superseded by this architecture decision; the replacement receives a new
  subprocess, output-containment, and live-probe review.

## Unified Adapter Implementation Evidence

- Generic web, YouTube, Instagram, and Threads fixtures use the same injected
  `AbxDlRunner` exactly once with `--no-install` and a temporary `--dir`.
- Artifact selection is deterministic: Korean transcript/subtitles, cleaned
  article text, social caption/description metadata, then rendered DOM text.
- Output roots, regular files, symlinks, cumulative bytes, file counts,
  `index.jsonl`, timeout cleanup, environment sanitization, stable errors, and
  provenance metadata are covered by contract tests.
- A failed or unusable `abx-dl` result does not fall back to direct networking.
- Focused source tests: `35 passed, 1 skipped`.
- Task 1-4 integration tests: `84 passed, 1 skipped`.
- Runtime v2 broad regression with `PYTHONUTF8=1`: `731 passed, 1 skipped`, with
  two unrelated existing Phase 14 live-Discord tests still failing because the
  current live-publish safety gate blocks their placeholder-token setup.
- Ruff and `git diff --check`: pass.

## RED Evidence

- The initial focused test run failed during collection with
  `ModuleNotFoundError: src.runtime_architecture_v2.llmwiki_sources`.
- The VTT header regression test then failed with generated VTT headers in the
  transcript content: `Kind: captions Language: en transcript text`.

## GREEN Evidence

- `uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with pytest --with pytest-asyncio --with pyyaml pytest tests/test_runtime_architecture_v2_llmwiki_sources.py -q`
  - `19 passed in 0.08s`
- `uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with ruff ruff check src/runtime_architecture_v2/llmwiki_sources.py tests/test_runtime_architecture_v2_llmwiki_sources.py`
  - `All checks passed!`

## Changed Files

- `src/runtime_architecture_v2/llmwiki_sources.py`
- `tests/test_runtime_architecture_v2_llmwiki_sources.py`
- `.superpowers/sdd/task-4-report.md`

## Commits

- Historical direct adapter: `feat: add safe LLM Wiki source retrieval`
- Production unified adapter: `d3f3475 refactor: use unified abx-dl source adapter`

## Concerns

- Tests use an injected `abx-dl` runner by design; they make no live network
  requests and do not require `abx-dl` to be installed.
- No live external endpoint is exercised in this offline test suite.

## Review Fix Evidence

### RED

- Added review regressions initially produced 15 failures for permissive URL
  parsing, synchronous DNS, title-only HTML, missing yt-dlp limits, and IPv6
  Host headers.
- Follow-up shared-budget and pre-transcript-deadline tests each failed before
  their implementation changes.
- The injected-fetcher deadline regression failed with `retrieval_failed`
  after blocking past the deadline.

### GREEN

- `uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with pytest --with pytest-asyncio --with pyyaml pytest tests/test_runtime_architecture_v2_llmwiki_sources.py -q`
  - `37 passed in 0.22s`
- `uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with ruff ruff check src/runtime_architecture_v2/llmwiki_sources.py tests/test_runtime_architecture_v2_llmwiki_sources.py`
  - `All checks passed!`

### Review Scope

- The 30-second budget now covers bounded daemonized DNS and injected fetcher
  calls, redirects, default HTTP connect/read operations, yt-dlp, and bounded
  VTT reads.
- yt-dlp uses `Popen` with stdout capped at 10 MiB, discarded stderr,
  `--no-playlist`, and process termination on deadline/output overflow. Its
  metadata and all VTT files share one 10 MiB byte budget.
- DNS/fetch workers abandoned on a deadline are daemon threads, so they cannot
  block interpreter shutdown. Offline tests do not use live network access or
  require yt-dlp.

### Final Verification

Command:

`uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with pytest --with pytest-asyncio --with pyyaml pytest tests/test_runtime_architecture_v2_llmwiki_sources.py -q`

Output:

`37 passed in 0.25s`

Command:

`uv --cache-dir C:\\Users\\KBM\\Documents\\Oracle\\AI_Agent\\.uv-cache run --offline --with ruff ruff check src/runtime_architecture_v2/llmwiki_sources.py tests/test_runtime_architecture_v2_llmwiki_sources.py`

Output:

`All checks passed!`

Command:

`git diff --check`

Output:

`Exit code: 0` (Git emitted only its LF-to-CRLF working-copy warning for the
unstaged report.)
