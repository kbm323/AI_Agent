# Phase 16 Autonomous Scheduling / Kanban Operations Implementation Plan

> **For Hermes:** Use test-driven-development and the established phase gate: plan/AC → implementation → tests/lint/security scan → Ouroboros QA → independent review → commit/push → GitHub remote verification.

**Goal:** Convert a completed AI company MeetingRun into a Hermes-native Kanban operation plan with deterministic fan-out/fan-in cards, dependency metadata, and dry-run dispatch verification.

**Architecture:** Add a domain-only `kanban_ops.py` module under Runtime Architecture v2. It builds `KanbanCardSpec` records from `MeetingRun`, `MultiBotSession`, `WorkerTask`, priority policy, scheduling policy, and Phase 15 knowledge context. It does not implement a custom queue store and does not call Hermes Kanban unless an explicit injected client is provided.

**Tech Stack:** Python stdlib, dataclasses, pathlib, JSON, pytest, ruff.

---

## Acceptance Criteria

```text
AC1 KanbanCardSpec serializes deterministic Hermes-native card metadata.
AC2 Phase 16 builds parallel worker fan-out cards from MeetingRun participants/tasks.
AC3 Phase 16 builds a review/fan-in card whose parents are the worker cards.
AC4 All cards carry priority/concurrency/scheduling metadata and queue_store=none.
AC5 Knowledge context is included in card bodies after redacting uncontrolled mentions and secret-like values.
AC6 Mixed meeting_run_id worker tasks are rejected fail-closed.
AC7 Dry-run dispatch never calls a live Kanban client and returns stable dry_run refs.
AC8 Injected-client dispatch preserves dependency mapping from local card IDs to returned Hermes card refs.
AC9 Pilot writes a JSON plan artifact under ignored runtime/ and records refs in MeetingRun metadata.
AC10 CLI dry-run emits machine-readable JSON and does not call live workers, live Discord, or live Kanban.
AC11 final gate runs tests, changed-file ruff, security scan, Ouroboros QA, independent review, commit/push, and GitHub remote verification.
```

## Out of Scope

```text
Hermes Core modification
Custom queue database/store
Live Kanban creation by default
Discord slash-command changes
Long-running autonomous daemon
Production monitoring/recovery, which remains Phase 17
```

## Design Principle

```text
Hermes-native first: AI_Agent plans domain cards and dependencies; Hermes Kanban remains the execution substrate.
```

## Tasks

### Task 16.1 — RED tests

**Files:**
- Create: `tests/test_runtime_architecture_v2_phase16_kanban_operations.py`

Run:

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase16_kanban_operations.py -q
```

Expected: fail because `kanban_ops.py` and CLI do not exist.

### Task 16.2 — Implement Kanban operation module

**Files:**
- Create: `src/runtime_architecture_v2/kanban_ops.py`

Implement:
- `KanbanCardSpec`
- `KanbanOperationPlan`
- `KanbanDispatchResult`
- `build_kanban_operation_plan()`
- `dispatch_kanban_operation_plan()`
- `run_phase16_kanban_pilot()`

### Task 16.3 — Implement CLI

**Files:**
- Create: `scripts/run_phase16_kanban_pilot.py`

Add dry-run CLI that emits sorted, indented JSON.

### Task 16.4 — Docs and README

**Files:**
- Create: `docs/phase16-autonomous-scheduling-kanban-operations.md`
- Modify: `README.md`

Document what is proven, what remains, and the new module/script.

### Task 16.5 — Final gate

Run:

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase16_kanban_operations.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_phase14_multi_bot.py -q
python3 -m ruff check src/runtime_architecture_v2/kanban_ops.py scripts/run_phase16_kanban_pilot.py tests/test_runtime_architecture_v2_phase16_kanban_operations.py
python3 scripts/run_phase16_kanban_pilot.py --mode dry-run
```

Then run static security scan, Ouroboros QA, independent review, fix blockers, re-run gates, commit, push, and verify remote commit.
