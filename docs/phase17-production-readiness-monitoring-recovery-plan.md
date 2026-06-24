# Phase 17 Production Readiness / Monitoring / Recovery Implementation Plan

> **For Hermes:** Use test-driven-development and the established phase gate: plan/AC → implementation → tests/lint/security scan → Ouroboros QA → independent review → commit/push → GitHub remote verification.

**Goal:** Add deterministic production health scanning and checkpoint-aware recovery triage to Runtime Architecture v2, without long-running daemons or live alerting endpoints.

**Architecture:** Add a domain-only `production.py` module under Runtime Architecture v2. It scans existing `runtime/meeting_runs/` artifacts, produces a health report (counts by state, stuck detection, stale run detection), and generates recovery suggestions based on existing RecoveryCheckpoints. Quota watchdog integration is referenced but not implemented as a live daemon.

**Tech Stack:** Python stdlib, dataclasses, pathlib, pytest, ruff.

---

## Acceptance Criteria

```text
AC1  RunHealth schema carries deterministic run state + age + task counts.
AC2  HealthReport aggregates all MeetingRuns with state distribution counts.
AC3  Stuck detection: runs in non-terminal state > N hours are flagged.
AC4  Recovery triage: suggests resume/reclaim/manual per stuck run based on checkpoint state.
AC5  Health report does not leak meeting_run_id to stdout metadata (safe summary only).
AC6  CLI dry-run emits machine-readable JSON health report.
AC7  CLI does not call live Discord, live workers, live Kanban, or provider dashboards.
AC8  Hermes Core is untouched; no new daemon or background process is started.
AC9  Tests cover health scanning, stuck detection, recovery triage, empty/no-run states, and CLI.
AC10 Final gate runs tests, changed-file ruff, security scan, Ouroboros QA, independent review, commit/push, and GitHub remote verification.
```

## Out of Scope

```text
Long-running daemon
Live alerting/webhook
Real-time metrics push
Quota watchdog execution (referenced but not run)
Discord integration
Live worker dispatch
```

## Design Principle

```text
Scan existing artifacts, report deterministically, suggest recovery — execute nothing live.
```

## Tasks

### Task 17.1 — RED tests

**Files:**
- Create: `tests/test_runtime_architecture_v2_phase17_production.py`

Run:

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase17_production.py -q
```

Expected: fail because `production.py` and CLI do not exist.

### Task 17.2 — Implement production module

**Files:**
- Create: `src/runtime_architecture_v2/production.py`

Implement:
- `RunHealth`
- `HealthReport`
- `RecoverySuggestion`
- `scan_health()`
- `triage_recovery()`
- `run_phase17_health_check()`

### Task 17.3 — Implement CLI

**Files:**
- Create: `scripts/run_phase17_health_check.py`

Add dry-run CLI that emits sorted, indented JSON.

### Task 17.4 — Docs and README

**Files:**
- Create: `docs/phase17-production-readiness-monitoring-recovery.md`
- Modify: `README.md`

### Task 17.5 — Final gate

Run tests, ruff, CLI, security scan, Ouroboros QA, independent review, commit, push, remote verify.
