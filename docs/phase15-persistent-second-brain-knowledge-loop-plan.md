# Phase 15 Persistent Second Brain / Knowledge Loop Implementation Plan

> **For Hermes:** Use test-driven-development and the established phase gate: plan/AC → implementation → tests/lint/security scan → Ouroboros QA → independent review → commit/push → GitHub remote verification.

**Goal:** Persist AI company meeting outcomes into a repo-local plain markdown Second Brain and retrieve relevant knowledge for later meetings.

**Architecture:** Add a domain-only `knowledge.py` module under Runtime Architecture v2. It writes sanitized raw meeting records, Obsidian-compatible wiki notes, `wiki/index.md`, and append-only `wiki/log.md`; retrieval is deterministic keyword scoring over markdown files. Hermes Core, Obsidian APIs, vector DBs, and live Discord surfaces are untouched.

**Tech Stack:** Python stdlib, dataclasses, pathlib, markdown/YAML-frontmatter-compatible text, pytest, ruff.

---

## Acceptance Criteria

```text
AC1 KnowledgeEntry schema round-trips to dict and stable markdown frontmatter.
AC2 Phase 15 writes repo-local knowledge under knowledge/raw and knowledge/wiki.
AC3 raw notes preserve source details; wiki notes contain concise summary, links, tags, and Obsidian-compatible [[wikilinks]].
AC4 secret-like text and uncontrolled mentions are redacted before writing knowledge artifacts.
AC5 wiki/index.md and wiki/log.md are updated deterministically and append-only where appropriate.
AC6 MeetingRun metadata records generated knowledge artifact paths/IDs without changing Hermes Core.
AC7 retrieval returns relevant wiki notes using deterministic term scoring.
AC8 CLI dry-run emits machine-readable JSON and does not call live workers or live Discord.
AC9 tests cover schema, write loop, redaction, retrieval, CLI, and path safety.
AC10 final gate runs tests, changed-file ruff, security scan, Ouroboros QA, independent review, commit/push, and GitHub remote verification.
```

## Out of Scope

```text
Obsidian app integration
Dataview, Canvas, plugin config
Vector DB / embeddings
Long-running autonomous daemon
Discord slash-command changes
Live worker execution
```

## Obsidian-compatible later principle

```text
Plain markdown first, Obsidian-compatible later.
```

The repo-local shape must remain directly openable as an Obsidian vault or movable into one later:

```text
knowledge/
  AGENTS.md
  raw/
  wiki/
    index.md
    log.md
    meetings/
```

## Tasks

### Task 15.1 — RED tests

**Files:**
- Create: `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`

Write tests for:
- `KnowledgeEntry` serialization and frontmatter.
- `write_meeting_knowledge()` creates raw/wiki/index/log files.
- redaction removes secret-like strings and `@everyone`/`@here`.
- `retrieve_knowledge_context()` finds relevant notes.
- `run_phase15_knowledge_loop_pilot()` returns `ok=true` in dry-run.
- CLI outputs JSON.

Run:

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase15_knowledge_loop.py -q
```

Expected: fail because module/CLI do not exist.

### Task 15.2 — Implement knowledge module

**Files:**
- Create: `src/runtime_architecture_v2/knowledge.py`

Implement:
- `KnowledgeEntry`
- `KnowledgeWriteResult`
- `KnowledgeContextResult`
- `write_meeting_knowledge()`
- `retrieve_knowledge_context()`
- `run_phase15_knowledge_loop_pilot()`

### Task 15.3 — Implement CLI

**Files:**
- Create: `scripts/run_phase15_knowledge_loop_pilot.py`

Add dry-run CLI that emits sorted, indented JSON.

### Task 15.4 — Docs and README

**Files:**
- Create: `docs/phase15-persistent-second-brain-knowledge-loop.md`
- Modify: `README.md`

Document what is proven, what remains, and the new module/script.

### Task 15.5 — Final gate

Run:

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase13_pilot.py -q
python3 -m ruff check src/runtime_architecture_v2/knowledge.py scripts/run_phase15_knowledge_loop_pilot.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py
python3 scripts/run_phase15_knowledge_loop_pilot.py --mode dry-run
```

Then run static security scan, Ouroboros QA, independent review, fix blockers, re-run gates, commit, push, and verify remote commit.
