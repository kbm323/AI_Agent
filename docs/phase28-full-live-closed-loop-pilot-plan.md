# Phase 28: Full Live Closed-loop Pilot — Plan

**Date**: 2026-06-25
**Status**: PLANNED
**Gate**: Gate 9 — Projection safety + full controlled closed-loop smoke

## Decision

Phase 28 implements a controlled Hermes-first closed-loop pilot:

Hermes Gateway input → MeetingRun → routing/scheduling → worker execution →
validation → Gate 9 projection safety → controlled projection publish.

This is not a standalone Discord interaction endpoint and not an always-on
production claim. Unit tests use deterministic/fake or injected boundaries only.
Real Discord projection is allowed only when explicitly requested and an injected
HTTP callable is provided; otherwise the run fails closed.

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC1 | ClosedLoopPilotPolicy.current_verified() composes Phase 24~27 guardrails |
| AC2 | Policy verifies Hermes Gateway command surface, worker boundary, service supervision, and projection safety |
| AC3 | Gateway input produces MeetingRun and records the full stage sequence |
| AC4 | Default run is controlled-dry-run with no real Discord and no real worker CLI |
| AC5 | Projection safety constrains allowed mentions, breaks mass mentions, caps content length, omits raw worker output, preserves trace ID, and redacts secret-like values |
| AC6 | controlled_live_projection=True requires injected HTTP callable |
| AC7 | Live projection failure makes top-level ok=False |
| AC8 | Controlled live projection uses allowed_mentions={"parse": []} |
| AC9 | Policy failures fail closed before projection |
| AC10 | Artifact contains trace ID and stage list |

## Implementation

### New files
- `src/runtime_architecture_v2/closed_loop_pilot.py`
- `tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py`
- `docs/phase28-full-live-closed-loop-pilot-plan.md`
- `docs/phase28-full-live-closed-loop-pilot.md`

### Modified files
- `docs/phase23-live-production-hardening-checklist.md`
- `README.md`

## TDD Evidence

RED:
- Initial test run failed with `ModuleNotFoundError: runtime_architecture_v2.closed_loop_pilot`.

GREEN:
- Implemented Phase 28 closed-loop pilot module.
- Phase 28 tests: 17 passed.

## Verification Plan

1. Phase 28 tests
2. Related Phase 25~28 tests
3. Ruff
4. Runtime v2 subset
5. Full pytest
6. Static secret scan
7. Independent review
8. Ouroboros QA
9. Commit/push/remote verification
