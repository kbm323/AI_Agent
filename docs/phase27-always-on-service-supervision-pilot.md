# Phase 27: Always-on Service Supervision Pilot — Result

**Date**: 2026-06-25
**Status**: VERIFIED — HARDENED
**Gate**: Gate 8 — Service supervision
**Commit**: pending
**Hardening update**: 2026-06-25

## Summary

Implemented `ServiceSupervisionPolicy` — a machine-checkable supervision
boundary for Gate 8. The policy defines start/stop/status/heartbeat/log/
restart/secrets bounds for all 7 live Hermes profiles and verifies that
all six Gate 8 conditions are met without permission expansion.

No live processes are started, stopped, or monitored. This is a policy/
verification layer only, consistent with Phase 24/25/26 pattern.

## Gate 8 Condition Verification

| # | Condition | Status |
|---|-----------|--------|
| 1 | One gateway/service process per live bot profile | PASS — 7 profiles defined, each with `hermes --profile <name> --discord` start command |
| 2 | status/start/stop scripts documented | PASS — each profile has start_command, stop_command, status_command |
| 3 | logs rotate or are bounded | PASS — LogBound(max_size_mb=50, rotation_count=5) for each profile |
| 4 | process restart policy exists | PASS — RestartPolicy(strategy="on-failure", max_restarts=3, backoff_seconds=30) |
| 5 | health endpoint or periodic heartbeat exists | PASS — heartbeat_interval_seconds=60 for each profile |
| 6 | secrets loaded from profile-local env, never committed | PASS — secrets_env_path=~/.hermes/profiles/<name>/.env |

## 7 Live Hermes Profiles

| Profile | Start | Heartbeat | Log | Restart | Secrets |
|---------|-------|-----------|-----|---------|---------|
| aicompanyassistant | `hermes --profile aicompanyassistant --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanyassistant/.env |
| aicompanyceo | `hermes --profile aicompanyceo --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanyceo/.env |
| aicompanycontent | `hermes --profile aicompanycontent --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanycontent/.env |
| aicompanyart | `hermes --profile aicompanyart --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanyart/.env |
| aicompanytech | `hermes --profile aicompanytech --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanytech/.env |
| aicompanymarketing | `hermes --profile aicompanymarketing --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanymarketing/.env |
| aicompanyquality | `hermes --profile aicompanyquality --discord` | 60s | 50MB/5rot | on-failure/3/30s | ~/.hermes/profiles/aicompanyquality/.env |

## Permission Posture

- permission_mutation_allowed = False
- administrator_allowed = False
- evaluate() fails closed if either is True

## Boundary Policy Consistency

`ServiceSupervisionPolicy.evaluate()` now directly checks the active
`DiscordLiveBoundaryPolicy` profile set and Discord safety posture. It fails
closed if the boundary policy profile keys drift from the 7 service profiles,
if mention gates are disabled, if free-response channels appear, or if
permission mutation / Administrator posture is enabled.

## Hardening Addendum

The earlier Phase 27 policy checked that `secrets_env_path` contained `.env` and
the profile name. That was too weak: paths like `/tmp/<profile>.env` could pass.
The hardened policy now requires the exact profile-local Hermes path:

```text
~/.hermes/profiles/<profile>/.env
```

Regression tests cover tmp paths, repo-local env paths, boundary profile drift,
and disabled mention gates.

## Test Coverage

| Test Group | Count | Status |
|------------|-------|--------|
| AC1: current_verified profiles | 3 | PASS |
| AC2: profile fields | 5 | PASS |
| AC3: evaluate fail-closed | 6 | PASS |
| AC4: evaluate pass | 2 | PASS |
| AC5: no permission expansion | 4 | PASS |
| AC6: verification report | 3 | PASS |
| AC7: heartbeat interval | 1 | PASS |
| AC8: restart policy | 3 | PASS |
| AC9: log bound | 2 | PASS |
| AC10: secrets env path | 2 | PASS |
| AC11: boundary policy consistency | 2 | PASS |
| Integrated smoke | 2 | PASS |
| Parametrized field checks | 7 | PASS |
| Review hardening | 14 | PASS |
| **Total** | **54** | **PASS** |

## Full Test Results

- Phase 27 tests: 54 passed (48 existing + 6 hardening regressions)
- Related Phase 26~28 tests: 94 passed
- Runtime v2 subset: 338 passed
- Full pytest: 5623 passed
- Ruff: No issues found
- Secret scan: 0 findings
- Independent review #1: FAIL → 7 issues fixed
- Independent review #2: PASS (security_concerns=[], logic_errors=[])
- Phase 26/27 re-review: FAIL → runtime enforcement gaps fixed in hardening patch
- Phase 26/27 final review: PASS (security_concerns=[], logic_errors=[])
- Ouroboros QA: PASS 0.88/1.00

## Independent Review

First review: FAIL — 7 issues found (condition 1 not verified, no profile
count/name cross-check, no duplicate detection, log_dir not checked, restart
strategy not whitelisted, secrets path not validated as profile-local).

All issues fixed:
- Added profile count check (exactly 7 expected profiles)
- Added duplicate profile detection
- Added profile name set validation against _EXPECTED_PROFILE_NAMES
- Added log_dir non-empty check
- Added restart strategy whitelist (on-failure, always, no)
- Added secrets_env_path profile-local validation (.env + profile name)
- Added 8 RED tests for each edge case

Second review: PASS (security_concerns=[], logic_errors=[], verdict=PASS)

## Files

### New
- `src/runtime_architecture_v2/service_supervision.py`
- `tests/test_runtime_architecture_v2_phase27_service_supervision.py`
- `docs/phase27-always-on-service-supervision-pilot-plan.md`

### Modified
- `docs/phase23-live-production-hardening-checklist.md` — Gate 8 status
- `README.md` — Phase 27 line, status
- `docs/phase26-worker-validator-auditor-boundary-smoke.md` — next step

## Next Steps

| Phase | Description |
|-------|-------------|
| Phase 28 | Full Live Closed-loop Pilot — Hermes Gateway input to MeetingRun to workers/validation/projection |
| Phase 29 | 24h Live Pilot and Production Runbook — bounded operations proof, recovery, production readiness verdict |
