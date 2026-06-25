# Phase 27: Always-on Service Supervision Pilot — Plan

**Date**: 2026-06-25
**Status**: PLANNED
**Gate**: Gate 8 — Service supervision

## Goal

Define and verify the service supervision boundary for all 7 live Hermes
profiles without permission expansion. This is a policy/verification layer
only — no live processes are started, stopped, or monitored.

## Gate 8 Requirements

1. one gateway/service process per live bot profile if presence/mentions
   are required
2. status/start/stop scripts documented
3. logs rotate or are bounded
4. process restart policy exists
5. health endpoint or periodic heartbeat exists
6. secrets loaded from profile-local env, never committed

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC1 | current_verified() returns 7 profiles matching DiscordLiveBoundaryPolicy |
| AC2 | Each profile has start/stop/status/heartbeat/log_bound/restart_policy/secrets_env_path |
| AC3 | evaluate() fails closed when any Gate 8 condition is unmet |
| AC4 | evaluate() passes when all conditions met for all 7 profiles |
| AC5 | permission_mutation_allowed=False, administrator_allowed=False |
| AC6 | verification_report() produces structured output |
| AC7 | heartbeat_interval_seconds > 0 for all profiles |
| AC8 | restart_policy has strategy, max_restarts > 0, backoff_seconds > 0 |
| AC9 | log_bound has max_size_mb > 0, rotation_count > 0 |
| AC10 | secrets_env_path is profile-local, never committed |
| AC11 | 7 profiles match DiscordLiveBoundaryPolicy keys exactly |

## Implementation

### New files
- `src/runtime_architecture_v2/service_supervision.py`
  - `ServiceSupervisionStatus` enum (PASS, FAIL)
  - `LogBound` dataclass (max_size_mb, rotation_count, log_dir)
  - `RestartPolicy` dataclass (strategy, max_restarts, backoff_seconds)
  - `ServiceProfile` dataclass (7 fields per profile)
  - `ServiceSupervisionDecision` dataclass (status, reason, gate, profile_count)
  - `ServiceSupervisionPolicy` dataclass with current_verified() and evaluate()
- `tests/test_runtime_architecture_v2_phase27_service_supervision.py`
  - 40 tests covering AC1-AC11 + integrated smoke

### Modified files
- `docs/phase23-live-production-hardening-checklist.md` — Gate 8 status update
- `README.md` — Phase 27 line, status, description
- `docs/phase26-worker-validator-auditor-boundary-smoke.md` — Phase 27 next step

### Design principles
- No live process execution (policy/verification only)
- Fail-closed on any unmet condition
- No permission expansion (no Administrator, no permission mutation)
- Profile names match DiscordLiveBoundaryPolicy exactly
- Hermes-first: uses `hermes --profile <name> --discord` as start command
- Secrets from `~/.hermes/profiles/<name>/.env` (profile-local, never committed)

## Verification

1. Phase 27 tests: 40 passed
2. Ruff: No issues found
3. Runtime v2 subset: 296 passed
4. Full pytest: pending
5. Independent review: pending
6. Ouroboros QA: pending
7. Secret scan: pending
8. Git commit/push: pending
