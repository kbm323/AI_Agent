# Phase 12.2 opencode-go Worker Live Smoke

## Decision

Phase 12.2 was executed as a single bounded live worker smoke after explicit user approval.

The smoke was intentionally minimal:

```text
one worker task
one opencode-go invocation
one expected stdout token
no Ouroboros loop
no multi-agent fanout
no source-code mutation requested from the model
runtime output kept under ignored runtime/
```

## Preconditions

Before execution:

```text
git status: clean on main...origin/main
Go quota: available, monthly usage reset to 0%
Codex quota: available
```

The previous reason for deferral, Go monthly usage at 96%, no longer applied at execution time.

## Command Boundary

The live smoke used the existing Runtime Architecture v2 opencode-go boundary:

```text
OpenCodeGoSmokeRunner
OpenCodeGoPacketWrapper
opencode-go --model glm-5.1 --context-file <packet> --timeout-seconds 120 --prompt <prompt> --format json
```

Prompt:

```text
Return exactly this token and no extra explanation: OPENCODE_GO_SMOKE_OK
```

Expected stdout token:

```text
OPENCODE_GO_SMOKE_OK
```

Runtime artifact path:

```text
runtime/phase12_2_opencode_live_smoke/outputs/wt_phase12_2_live_smoke.json
```

This path is intentionally under ignored `runtime/` and is not committed.

## Verification Results

Focused deterministic tests before live smoke:

```text
pytest tests/test_runtime_architecture_v2_opencode_live_smoke.py tests/test_runtime_architecture_v2_opencode_wrapper.py -q
7 passed
```

Live smoke result:

```text
state: succeeded
error: <empty>
status: succeeded
exit_code: 0
timeout_occurred: false
duration_seconds: 12.0937
stdout_contains_expected: true
```

Observed stdout was JSON event output from OpenCode/opencode-go and contained the expected token.

## Post-Smoke Gateway Status

During final verification, no `hermes-aicompany` tmux sessions were present, so the existing gateway start script was run again.

Post-restart status:

```text
hermes-aicompany tmux sessions: 7/7
```

No profile, token, channel, or permission mutation was performed during this restart.

## Scope Boundaries

This verifies only the opencode-go worker execution boundary.

It does not claim:

```text
full Discord app interaction e2e
full MeetingRun live company workflow
multi-agent worker fanout
long-running validator/auditor loop
production readiness
```

It does claim:

```text
opencode-go binary is discoverable
Runtime Architecture v2 smoke runner can invoke opencode-go
context packet path is accepted by the runner
bounded timeout path completed without timeout
stdout can be checked for an expected success token
structured output file was written under runtime/
```

## Phase 12.2 Status

```text
opencode-go worker live smoke: PASS
bounded invocation: PASS
expected stdout token: PASS
runtime output ignored: PASS
source-code mutation by worker: NOT REQUESTED
```

Phase 12.2 is complete.
