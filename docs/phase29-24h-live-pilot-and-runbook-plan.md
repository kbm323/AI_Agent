# Phase 29: 24h Live Pilot & Production Runbook — Plan

**Date**: 2026-06-25
**Status**: PLANNED
**Gate**: Gate 10 — Production readiness

## Decision

Phase 29 is the final production-readiness gate. It does not execute an actual
24-hour live run; instead, it proves that a bounded 24-hour live pilot is ready
to start by verifying all prior gates, a completed production runbook, and
recoverability/quota/cost controls.

The deliverable is a machine-checkable `ProductionReadinessVerdict` that returns
`READY` only when every production gate is closed.

## Acceptance Criteria

1. `TwentyFourHourLivePilotPolicy.current_verified()` defines bounded pilot
   constraints: max runs per hour, max cost, allowed operating window, allowed
   channels, mention gating, rollback plan presence, quota alert channel.
2. `ProductionRunbook.current_verified()` contains all required pre-flight
   sections and `is_complete()` fails closed on any missing/empty section.
3. `ProductionReadinessVerdict.evaluate()` requires all Gate 5-9 statuses to be
   `pass`.
4. `ProductionReadinessVerdict.evaluate()` requires runbook completion and pilot
   policy pass.
5. `simulate_24h_pilot()` returns a verdict and synthetic observations without
   actually sleeping for 24 hours.
6. Missing gate, failed gate, incomplete runbook, or out-of-bounds pilot
   constraints produce `NOT_READY` with explicit blockers.
7. The verdict artifact serializes to JSON and excludes raw secret inputs.

## Out of Scope

- Actual 24-hour continuous live operation.
- Real Discord permission mutation.
- Hermes Core modification.
- Standalone slash command deployment.

## Design Principle

Final gate = fail-closed composition of all earlier gates plus operational
readiness evidence. No single module can claim production readiness on its own.
