# Phase 26 — Live Worker / Validator / Auditor Boundary Smoke

> Canonical baseline: `docs/runtime-architecture-v2.md`
> Phase status: COMPLETE — HARDENED
> Date: 2026-06-25 KST
> Hardening update: 2026-06-25 KST

## Decision

Phase 26 is complete as a worker/validator/auditor boundary smoke
verification and output sanitization layer.

It does not execute live CLI calls. It adds a machine-checkable boundary
smoke policy and integrates output sanitization into the existing
opencode-go smoke runner.

## Implementation

Added:

```text
src/runtime_architecture_v2/worker_boundary_smoke.py
tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py
```

Modified:

```text
src/runtime_architecture_v2/workers.py
```

Core objects:

```text
BoundarySmokeCheck
BoundarySmokeResult
BoundarySmokeStatus
LiveWorkerBoundarySmokePolicy
sanitize_worker_output
```

The verified default policy checks 8 boundary conditions:

```text
1. packet_based_input — opencode-go calls use file-based JSON packet input
2. model_provider_recorded — GLM validator and Codex auditor paths record model/provider
3. timeout_fail_closed — timeout produces structured TIMED_OUT result
4. nonzero_exit_fail_closed — non-zero exit produces structured FAILED result
5. output_sanitized — raw stdout/stderr with secret-like values is sanitized
6. quota_gate_checked — quota gate checked before worker batches
7. no_shell_true — subprocess calls use shell=False / argv list
8. no_direct_env_passthrough — unit tests inject fake runners
```

Output sanitization:

```text
sanitize_worker_output() redacts:
- api_key=..., token=..., password=..., credential=..., secret=... assignments
- Bearer token patterns
- truncates output to 4096 characters
```

OpenCodeGoSmokeRunner now sanitizes stdout/stderr before persisting output
files by default. OpenCodeGoWorkerRunner also sanitizes stdout/stderr before
persistence. Runner exceptions are converted into structured failed artifacts
with sanitized error codes rather than raw exception text.

The default opencode-go process boundary uses an explicit environment mapping
instead of implicit parent-environment inheritance. The default mapping is empty
unless a caller passes an allowlist explicitly.

## Gate Status

```text
Gate 5 — Kanban live client dependency:
PARTIAL (unchanged; deterministic policies exist, live smoke separate)

Gate 6 — Worker/validator/auditor live boundary:
VERIFIED_BOUNDARY_SMOKE_POLICY_EXISTS

Gate 7 — Quota/cost monitoring:
AVAILABLE
```

## Tests

Added:

```text
tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py
```

Covered behaviors:

```text
- sanitize_worker_output redacts secret assignments in stdout
- sanitize_worker_output redacts bearer tokens in stderr
- sanitize_worker_output preserves normal output
- sanitize_worker_output truncates overlong output
- current policy checks all 8 boundary conditions
- policy passes when all conditions hold
- policy fails closed on packet_based_input violation
- policy fails closed on output_sanitized violation
- policy fails closed on no_shell_true violation
- verification report records Gate 6/7 status
- smoke runner sanitizes output when secrets present
- smoke runner preserves output when no secrets
- worker runner sanitizes output by default
- smoke runner sanitizes output by default
- smoke runner exceptions become structured failed artifacts without raw leak
- default worker/smoke runners use explicit env mapping
- persisted command metadata redacts secret-like prompt values
```

## Verification Evidence

```text
python3 -m pytest tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py -q
-> 18 passed

python3 -m pytest tests/ -k "runtime_architecture_v2" -q
-> 338 passed

ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py
-> No issues found
```

## Out of Scope Preserved

```text
No live CLI execution in tests.
No live Discord mutation.
No service supervision.
No full closed-loop live pilot.
No 24h live pilot.
```

## Remaining Phases

| Phase | Content |
|---|---|
| Phase 27 | Always-on Service Supervision Pilot — start/stop/status/heartbeat/log bounds for the 7 Hermes profiles without permission expansion. |
| Phase 28 | Full Live Closed-loop Pilot — Hermes Gateway input to MeetingRun to workers/validation/projection in controlled smoke channel. |
| Phase 29 | 24h Live Pilot & Production Runbook — bounded operations proof, recovery, quota/cost evidence, final production readiness verdict. |
