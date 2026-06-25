# Phase 28: Full Live Closed-loop Pilot — Result

**Date**: 2026-06-25
**Status**: VERIFIED (pre-review)
**Gate**: Gate 9 — Projection safety + controlled full closed-loop smoke
**Commit**: pending

## Summary

Implemented `closed_loop_pilot.py`, a controlled Hermes-first closed-loop pilot
that verifies the full path:

```text
Hermes Gateway input
→ policy verification
→ MeetingRun creation
→ routing/scheduling
→ workers
→ validation
→ Gate 9 projection safety
→ fake or injected Discord projection publish
→ artifact
```

Default mode is `controlled-dry-run`: no real Discord call and no real worker CLI
call. Controlled live projection requires an injected HTTP callable; if it is not
provided, the run fails closed before any live boundary.

## New Runtime Objects

- `ClosedLoopStatus` — PASS / FAIL
- `GatewayInput` — synthetic Hermes Gateway input with trace_id
- `ProjectionSafetyPolicy` — Gate 9 verifier
- `ClosedLoopPilotPolicy` — Phase 24~27 guardrail composition
- `ClosedLoopPilotResult` — structured result/report
- `run_phase28_closed_loop_pilot()` — controlled pilot runner

## Guardrails Composed

| Prior phase | Guardrail |
|-------------|-----------|
| Phase 24 | `DiscordLiveBoundaryPolicy.current_verified()` |
| Phase 25 | `HermesGatewayCommandSurfacePolicy.current_verified()` |
| Phase 26 | `LiveWorkerBoundarySmokePolicy.current_verified()` |
| Phase 27 | `ServiceSupervisionPolicy.current_verified()` |
| Phase 28 | `ProjectionSafetyPolicy.current_verified()` |

## Gate 9 Projection Safety

`ProjectionSafetyPolicy.evaluate()` verifies:

1. allowed_mentions constrained
2. mass mentions broken/redacted
3. content length capped
4. raw worker output omitted
5. trace ID preserved
6. secret-like assignments / bearer tokens redacted

## Stage Sequence

Successful controlled dry-run returns exactly:

```text
gateway_input_received
policy_verified
meeting_run_created
meeting_run_routed
meeting_run_scheduled
workers_completed
validation_completed
projection_safety_verified
projection_published
artifact_written
```

## Tests

| Test Area | Count | Status |
|-----------|-------|--------|
| ProjectionSafetyPolicy | 7 | PASS |
| ClosedLoopPilotPolicy | 5 | PASS |
| Controlled closed-loop runner | 10 | PASS |
| **Total** | **22** | **PASS** |

## Verification Results

- Phase 28 tests: 22 passed
- Related Phase 25~28 tests: 89 passed
- Ruff: No issues found
- Runtime v2 subset: 326 passed
- Full pytest: 5611 passed
- Secret scan: 0 findings
- Independent review #1: FAIL → trace-after-cap issue fixed with RED regression
- Independent review #2: PASS (security_concerns=[], logic_errors=[])
- Reviewer suggestions addressed: exact trace reason, blocked publish does not call HTTP, invalid content cap fails closed
- Ouroboros QA: PASS 0.86/1.00

## Files

### New
- `src/runtime_architecture_v2/closed_loop_pilot.py`
- `tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py`
- `docs/phase28-full-live-closed-loop-pilot-plan.md`

### Modified
- `docs/phase23-live-production-hardening-checklist.md`
- `README.md`

## Remaining Work

| Phase | Description |
|-------|-------------|
| Phase 29 | 24h Live Pilot and Production Runbook — bounded operations proof, recovery, production readiness verdict |
