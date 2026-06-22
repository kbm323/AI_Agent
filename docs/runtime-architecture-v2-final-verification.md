# Runtime Architecture v2 Final Verification

Status: Phase 11 completed locally

## Scope

This document is the Phase 11 final verification record for Runtime Architecture v2.
It verifies the deterministic MeetingRun runtime, fake/simulation path, live adapter
boundaries, Discord multibot profile wiring, and Seed acceptance criteria.

## Provider capacity check

Latest pre-work quota check:

```text
OpenCode Go: LOW, monthly 96%, weekly 1%, hourly 1%
Codex: OK, monthly 0%, weekly 19%, hourly 34%
Decision: continue local/TDD/final verification work; avoid unnecessary heavy Go usage.
```

Quota scripts no longer store provider dashboard cookies in tracked files. Local
quota credentials are read from environment or ignored `.env.local`.

## Verification commands

```bash
pytest tests/test_runtime_architecture_v2_*.py -v
python3 scripts/simulate_runtime_architecture_v2.py --scenario all
pytest -q
ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py
pytest tests/test_quota_scripts_no_hardcoded_secrets.py -q
```

Observed results:

```text
Runtime v2 focused pytest + quota hygiene regression: 92 passed
Simulation smoke: top_ok=True, scenario_count=7, used_live_adapters=False
Full pytest: 5373 passed
Focused ruff: No issues found
Quota script secret hygiene regression: 4 passed
Hardcoded provider auth-cookie scan: 0 findings
```

## Simulation smoke coverage

`python3 scripts/simulate_runtime_architecture_v2.py --scenario all` covered:

```text
fast_qa                    scenario_ok=True state=completed expected=completed
meeting                    scenario_ok=True state=completed expected=completed
worker_execution           scenario_ok=True state=completed expected=completed
dual_validation_pass       scenario_ok=True state=completed expected=completed
validation_correction_loop scenario_ok=True state=active    expected=active
crash_recovery             scenario_ok=True state=failed    expected=failed
worker_failure             scenario_ok=True state=failed    expected=failed
```

All scenarios used fake/injected boundaries only:

```text
used_live_adapters=False
requires_custom_queue_store=False
```

## Live boundary status

Implemented and tested boundaries:

```text
Discord projection:
- LiveDiscordProjectionSink behind injected HTTP/env boundary
- token read from environment, not deterministic tests
- allowed_mentions disables mention parsing
- content redaction/sanitization retained

opencode-go worker:
- OpenCodeGoWorkerRunner behind injected subprocess boundary
- packet write and command construction deterministic
- timeout/exception paths return structured failed worker task results

validator planner:
- GLM/Codex validator tasks planned as OPENCODE_GO executions
- quota policy evaluated before validator dispatch
- blocked quota degrades/fails closed instead of dispatching
```

Operational gateway status at Phase 11 start:

```text
aicompanyceo         running
aicompanyassistant   running
aicompanycontent     running
aicompanyart         running
aicompanytech        running
aicompanymarketing   running
aicompanyquality     running
```

## Seed acceptance criteria verdict

### schema_completeness

Verdict: PASS

Evidence:
- `src/runtime_architecture_v2/schemas.py` defines MeetingRun state, worker task
  state/runner, validation verdict values, projection/checkpoint/report packet
  schema objects.
- `store.py` persists `meeting_run.json`, decision log, audit log, checkpoints,
  worker packets, validation verdicts, and final reports by `meeting_run_id`.

### runtime_flow_coverage

Verdict: PASS

Evidence:
- Simulation smoke covers fast Q&A, meeting, worker execution, dual validation,
  validation correction loop, crash recovery, and worker failure scenarios.
- Failure-path scenarios intentionally end in `active` or `failed` when that is
  the expected safe state; `scenario_ok=True` verifies expected-state matching.

### implementation_readiness

Verdict: PASS

Evidence:
- Runtime v2 modules exist for schema, store, routing, queue policy, scheduling
  policy, workers, validation, projection, policies, orchestrator, and simulation CLI.
- Hermes Core remains untouched; live dependencies are adapter/wrapper boundaries.
- Phase order and verification commands are documented in the implementation plan
  and README.

### operations_readiness

Verdict: PASS

Evidence:
- Priority/limited parallelism policy tests are included in focused runtime v2 tests.
- Security, quota, and observability gates are deterministic and fail closed.
- Recovery checkpoints and audit/decision JSONL are written by MeetingRun store.
- Quota scripts now avoid tracked secrets and support ignored local credentials.

### discord_ux_fidelity

Verdict: PASS

Evidence:
- Seven team-facing Hermes profiles are configured for Discord operation:
  CEO, assistant, content, art, tech, marketing, quality.
- `docs/discord-multibot-profiles.md` records bot/profile/channel mapping.
- Gateway processes were verified running for all seven profiles.
- Projection design keeps Discord as user-facing surface, not source of truth.

### quota_model_policy

Verdict: PASS

Evidence:
- Qwen/router, GLM/Codex validator/auditor, and opencode-go-first worker roles are
  encoded in docs and runtime policy objects.
- ValidatorExecutionPlanner checks quota before validator dispatch.
- Provider quota monitoring is available via `scripts/check_all_quota.sh`; secrets
  are supplied from env or ignored `.env.local`.

### testability

Verdict: PASS

Evidence:
- MeetingRun simulation runs without Discord or live model calls.
- Worker, projection, validation, security, quota, and observability boundaries are
  covered with injected fakes.
- Focused runtime tests passed and full project tests passed.

## Phase 11 decision

Runtime Architecture v2 is locally verified through Phase 11.

Remaining operational items are outside the deterministic Phase 11 verification gate:

```text
1. Push local commit(s) to origin/main after GitHub credentials are fixed.
2. Optionally reset Discord/provider tokens that were ever exposed in chat or local history.
3. Optionally re-invite bots with narrower Discord permission integers.
4. Proceed to post-v2 live operational hardening / Phase 12 only after deciding scope.
```
