# Phase 25 — Hermes Gateway Command Surface Verification

> Canonical baseline: `docs/runtime-architecture-v2.md`
> Phase status: COMPLETE
> Date: 2026-06-25 KST

## Decision

Phase 25 is complete as a Hermes-first command-surface verification layer.

It does not implement or register standalone Discord slash commands. It preserves the current command-surface priority:

```text
1. Hermes existing Discord command and gateway behavior
2. Hermes-supported custom skill/command surface
3. Bot mention natural-language command
4. Separate Discord Adapter that implements standalone slash commands
```

## Implementation

Added:

```text
src/runtime_architecture_v2/command_surface.py
```

Core objects:

```text
CommandSurfaceMode
CommandSurfaceDecision
HermesGatewayCommandSurfacePolicy
```

The verified default policy records:

```text
standalone_slash_adapter_enabled = False
interaction_endpoint_enabled = False
permission_mutation_allowed = False
administrator_allowed = False
```

The policy allows Hermes Gateway / mention-based command paths only when:

```text
DISCORD_REQUIRE_MENTION=true
DISCORD_THREAD_REQUIRE_MENTION=true
DISCORD_FREE_RESPONSE_CHANNELS empty
```

It fail-closes when:

```text
standalone slash adapter is requested without explicit enablement
mention gate is disabled
thread mention gate is disabled
free-response channels are configured
permission mutation is allowed
Administrator is allowed
unknown command surface is requested
```

## Gate Status

```text
Gate 1 — Discord interaction security:
DEFERRED_NO_LIVE_INTERACTION_ENDPOINT

Gate 4 — Slash command registration:
DEFERRED_STANDALONE_SLASH_NOT_DEFAULT
```

This is intentional. Phase 25 verifies that those gates are not silently promoted to production when no live interaction endpoint or standalone slash registration has been approved.

## Tests

Added:

```text
tests/test_runtime_architecture_v2_phase25_command_surface.py
```

Covered behaviors:

```text
- current policy priority order
- default standalone slash adapter disabled
- interaction endpoint disabled
- permission mutation disabled
- Administrator disabled
- Hermes existing gateway allowed with safe posture
- bot mention natural-language allowed with safe posture
- standalone slash adapter blocked by default
- missing mention gate blocked
- missing thread mention gate blocked
- free-response channels blocked
- interaction endpoint enablement blocked for every surface
- permission mutation, Administrator posture, and unknown surfaces blocked
- verification report records Gate 1 / Gate 4 deferred
```

## Verification Evidence

```text
python3 -m pytest tests/test_runtime_architecture_v2_phase25_command_surface.py -q
-> 7 passed

python3 -m pytest tests/test_runtime_architecture_v2_*.py -q
-> 244 passed

python3 -m pytest tests/ -q
-> 5529 passed

ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py
-> No issues found

Static diff security scan scope:
- `git diff -- src tests docs README.md`
- added-line secret assignment patterns
- shell/eval/exec/pickle/SQL formatting patterns
- hardcoded cookie/token fragments (OpenCode auth cookie prefix, local auth-cookie variable assignment, Discord bot-token assignment, GitHub token assignment)
-> unstaged_secret_pattern_findings: 0

Independent review:
- Tool: `delegate_task`
- First review: BLOCKED interaction endpoint fail-closed gap.
- Regression added: interaction endpoint enabled blocks every `CommandSurfaceMode`.
- Second review: PASS, `security_concerns=[]`, `logic_errors=[]`.

Ouroboros QA:
- Tool: `mcp_ouroboros_ouroboros_qa`
- Verdict: PASS
- Score: 0.93 / 1.00
```

Additional full verification is recorded in the phase completion report / commit gate.

## Out of Scope Preserved

```text
No slash command registration.
No interaction endpoint deployment.
No Ed25519 endpoint wiring.
No permission mutation.
No live Discord mutation.
No service supervision.
No worker/validator/auditor live smoke.
```

## Remaining Phases

| Phase | Content |
|---|---|
| Phase 26 | Live Worker / Validator / Auditor Boundary Smoke — quota-gated opencode-go, GLM validator, Codex auditor boundary smoke. |
| Phase 27 | Always-on Service Supervision Pilot — start/stop/status/heartbeat/log bounds for the 7 Hermes profiles without permission expansion. |
| Phase 28 | Full Live Closed-loop Pilot — Hermes Gateway input to MeetingRun to workers/validation/projection in controlled smoke channel. |
| Phase 29 | 24h Live Pilot & Production Runbook — bounded operations proof, recovery, quota/cost evidence, final production readiness verdict. |
