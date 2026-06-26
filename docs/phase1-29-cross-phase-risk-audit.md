# Phase 1~29 Cross-Phase Risk Audit

**Date**: 2026-06-26
**Scope**: Phase 1~29 runtime architecture, live-boundary, artifact persistence, path/id validation, fail-closed semantics
**Method**: risk-pattern audit across phases, plus three independent read-only reviews split by Phase 1~11, Phase 12~23, and Phase 24~29.

## Decision

The audit was required because Phase 28/29 review exposed a repeated class of bugs:

- input validation after an early failure branch
- raw exception text in persisted artifacts or user-facing responses
- ID/path validation gaps
- live-boundary defaults that could reach real credentials or network
- artifact serializers that sanitize only some construction paths

The audit therefore prioritized cross-phase risk patterns over chronological feature-by-feature review.

## Critical Findings Fixed

### 1. Store dot-id path escape

**Finding**: `MeetingRunStore` rejected slash traversal, but accepted `.`, `..`, and dot-prefixed IDs.

**Fix**:
- `MeetingRunStore._validate_id()` now rejects:
  - empty IDs
  - `.`
  - `..`
  - dot-prefixed IDs
  - regex mismatches

**Regression tests**:
- `test_store_rejects_dot_and_hidden_meeting_run_ids`
- `test_store_rejects_dot_checkpoint_ids`

### 2. Discord webhook raw command exception leak

**Finding**: command handler exceptions were returned to Discord users as raw exception text.

**Fix**:
- `route_command()` now returns the sanitized code `command_handler_exception` instead of `str(exc)`.

**Regression test**:
- `test_handler_exception_returns_sanitized_error`

### 3. Runtime orchestrator raw worker exception leak

**Finding**: `RuntimeOrchestrator._run_workers()` persisted `str(exc)` into failed worker task errors and audit logs.

**Fix**:
- `WorkerRunError.code` is preserved when available.
- Generic exceptions collapse to `worker_runner_exception`.
- Raw exception message is not persisted.

**Regression test**:
- `test_orchestrator_fails_closed_when_worker_runner_raises`

### 4. LiveDiscordProjectionSink default live side-effect risk

**Finding**: default constructor used process environment and default HTTP client when `env`/`http_post` were omitted.

**Fix**:
- `env=None` now means an empty mapping, not `os.environ`.
- `http_post=None` now blocks with `live_http_client_required`.
- Real HTTP remains possible only via explicit injected callable.

**Regression test**:
- `test_live_discord_projection_sink_default_constructor_never_uses_process_env_or_http`

### 5. ValidatorExecutionPlanner path/id validation gap

**Finding**: validator packet/output paths used raw `meeting_run_id`, allowing unsafe IDs to affect paths.

**Fix**:
- `ValidatorExecutionPlanner.plan()` rejects unsafe meeting run IDs before quota evaluation or path construction.
- Returns `ValidatorExecutionPlan(status="invalid_meeting_run_id")` with a fail-closed `PolicyDecision`.

**Regression test**:
- `test_validator_execution_planner_rejects_unsafe_meeting_run_ids`

## Passing Areas

- Phase 13 live company workflow pilot: dry-run/live-worker separation and redaction coverage.
- Phase 14 multi-bot protocol: fanout limits and sanitized projection boundaries.
- Phase 15 knowledge loop: secret and mention redaction, safe ID checks.
- Phase 16 kanban operations: live client dependency fail-closed and safe IDs.
- Phase 19 daemon: health gate, idempotency, top-level `ok` consistency for tested paths.
- Phase 21 runtime v2 webhook manifest/router: dry-run manifest and unsupported-mode fail-closed.
- Phase 22 always-on company: live dispatch failure propagates to top-level failure.
- Phase 24~29 hardening chain after fixes: command surface, worker smoke, service supervision, closed-loop pilot, and 24h readiness proof have targeted fail-closed coverage.

## Remaining Warnings / Deferred Work

These were not fixed in this pass because they are not immediate Critical blockers for the current controlled-readiness boundary, but should be tracked before real production expansion.

### Warning: older non-runtime-v2 helpers still use raw exception text

Observed in legacy/utility modules such as:
- `src/runtime_smoke_packet.py`
- `src/gdrive_artifact_reader.py`
- some `StoreError` corrupt-load diagnostics

Current judgement:
- Not all are live/user-facing production paths.
- Before exposing any of these artifacts externally, convert raw exception text to sanitized error codes.

### Warning: Phase 18 dispatch-loop summary semantics

Independent audit found one path where an inner kanban dispatch result can be failed while the top-level batch result remains `ok=True`.

Current judgement:
- Needs a focused Phase 18 follow-up if the dispatch-loop result becomes production-gating evidence.
- Not fixed in this Critical pass to avoid broad behavior changes outside live-boundary blockers.

### Deferred: symbolic Discord channel allowlist

Phase 24 uses profile-local symbolic channel names (`home:<profile>:#channel`) rather than real Discord snowflake channel inventory.

Current judgement:
- Correct for controlled readiness proof.
- Real production rollout still needs token-safe channel ID inventory and live verification.

### Deferred: actual 24h live pilot

Phase 29 is a bounded readiness proof and simulation, not a real 24-hour live operation.

Current judgement:
- Correct for CI/TDD and production-readiness gating.
- Actual 24h pilot remains an operational activity, not a unit-testable phase artifact.

## Verification Evidence

After the Critical fixes:

```text
Targeted critical regression set: 116 passed
```

Full verification, QA, independent review, and commit/push are recorded in the final task summary for this audit.
