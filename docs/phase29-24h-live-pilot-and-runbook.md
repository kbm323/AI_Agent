# Phase 29: 24h Live Pilot & Production Runbook — Result

**Date**: 2026-06-25
**Status**: VERIFIED (pre-review)
**Gate**: Gate 10 — Production readiness
**Commit**: pending

## Decision

Phase 29 proves bounded 24-hour live pilot readiness without running a real
24-hour operation. All prior gates (Gate 5-9), the production runbook, and
operational controls (recovery, quota, cost) must be verified before the system
can claim production readiness.

## Implementation

Added:

```text
src/runtime_architecture_v2/live_pilot_runbook.py
tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py
docs/phase29-24h-live-pilot-and-runbook-plan.md
docs/phase29-24h-live-pilot-and-runbook.md
```

Core objects:

```text
BoundedOpsEvidence
RecoveryEvidence
QuotaCostEvidence
LivePilotObservation
LivePilotDecision
TwentyFourHourLivePilotPolicy
ProductionRunbook
ProductionReadinessVerdict
```

## Bounded Pilot Constraints

`TwentyFourHourLivePilotPolicy.current_verified()` enforces:

```text
max_runs_per_hour              <= 10
max_cost_usd                   <= 100.0
allowed_window_hours           == ("09:00", "23:00")
allowed_channels               start with "home:"
mention_gated                  == True
rollback_plan                  present
quota_alert_channel            present
min_checkpoint_interval_seconds >= 60
```

## Model and Quota Operating Policy

Before applying the Phase 29 live-pilot configuration, keep the active 7-bot
model policy aligned with `docs/runtime-architecture-v2.md`:

```text
비서             -> opencode-go/qwen3.7-plus       -> opencode-go/deepseek-v4-flash fallback
대표             -> opencode-go/qwen3.7-max        -> opencode-go/qwen3.7-plus / opencode-go/glm-5.2 fallback; GPT/Codex escalation
콘텐츠팀장       -> opencode-go/kimi-k2.6          -> opencode-go/qwen3.7-plus fallback
아트팀장         -> opencode-go/minimax-m3         -> opencode-go/minimax-m2.7 / opencode-go/deepseek-v4-pro fallback
기술팀장         -> opencode-go/deepseek-v4-pro    -> opencode-go/deepseek-v4-flash / opencode-go/kimi-k2.7-code fallback; GPT/Codex audit
마케팅팀장       -> opencode-go/qwen3.7-max        -> opencode-go/qwen3.7-plus / opencode-go/kimi-k2.6 fallback
품질관리팀장     -> opencode-go/glm-5.2            -> opencode-go/glm-5.1 fallback; GPT/Codex final audit
```

GPT-5.5/Codex is reserved for final audit, high-risk irreversible operations,
release/runbook gates, code/security/data-loss risk, and GLM non-pass or
low-confidence verdicts. Do not use GPT-5.5 as the daily default for all 7 live
bot profiles during a 24-hour pilot; that turns the auditor into the bottleneck
and drains the quota lane needed for emergencies.

Profile model changes are operational mutations: apply them only after any
active supervised pilot finishes, restart the affected Hermes gateway profiles,
then run a 7-channel controlled smoke before extending the pilot window.

## Production Runbook

`ProductionRunbook.current_verified()` contains 6 required pre-flight sections:

```text
team_contacts
rollback_plan
quota_budget
incident_response
observability
discord_channels
```

Any missing or empty section makes `is_complete()` return `False`.

## Production Readiness Verdict

`ProductionReadinessVerdict.evaluate()` returns `READY` only when:

1. Gate 5-9 statuses are all `pass` and the set is exactly `{gate_5, ..., gate_9}`.
2. The production runbook is complete.
3. The 24h live pilot policy evaluates to `pass`.

Otherwise it returns `NOT_READY` with explicit blockers.

## 24h Pilot Simulation

`simulate_24h_pilot()` runs the verdict evaluation and produces synthetic
observations (pilot_start, checkpoint, quota_check, pilot_end) without sleeping.
It completes in milliseconds.

## Test Coverage

| Test Group | Count | Status |
|------------|-------|--------|
| Pilot policy pass | 1 | PASS |
| Pilot policy fail-closed constraints | 14 | PASS |
| Runbook completeness | 3 | PASS |
| Readiness verdict pass | 1 | PASS |
| Readiness verdict fail-closed (gate/runbook/pilot) | 3 | PASS |
| 24h simulation | 3 | PASS |
| Artifact persistence | 4 | PASS |
| **Total** | **29** | **PASS** |

## Verification Evidence

- Phase 29 tests: 29 passed
- Phase 25-29 related tests: 135 passed
- Runtime v2 subset: 372 passed
- Full pytest: 5657 passed
- Ruff: No issues found
- Secret scan: 0 findings
- Independent review #1: PASS with suggestions addressed
- Independent review #2: PASS (max_runs_per_hour <= 0 hardening applied)
- Post-review hardening: malformed window lengths fail closed, quota alert channel must be profile-local, gate reasons/blockers/observations are redacted at artifact serialization boundaries
- Ouroboros QA: PASS (0.82/1.00, pass threshold 0.80)

## Remaining Phases

None. Phase 29 completes the planned phase sequence.
