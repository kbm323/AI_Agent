# OpenCode Go Worker Execution Path — Architecture Decision Record

> **Status:** Needs revision after GPT/Codex review — Option C selected as target architecture  
> **Date:** 2026-06-29 KST  
> **Context:** Phase 31 live-provider completion — opencode-go timeout blocker was locally resolved, but the execution path must be aligned with Runtime Architecture v2 before the 24h pilot.

---

## 1. Decision

**Approved architecture decision:** choose **Option C — Hermes Provider Integration**.

**Short-term rule:** do **not** make direct HTTP from AI_Agent the default live worker path. Option B may only be used as a temporary compatibility bridge if a Hermes provider API gap is explicitly confirmed and documented.

```text
Approved target:
  AI_Agent MeetingRun / WorkerTask
    -> thin Hermes provider adapter
      -> Hermes provider/auth/model/fallback layer
        -> opencode-go API

Temporary bridge, only if Hermes provider surface is unavailable:
  AI_Agent WorkerTask
    -> explicitly flagged compatibility adapter
      -> opencode-go API
```

The 24h pilot is **No-Go** until provider/auth/live-boundary preflight is unified or the temporary bridge is guarded by explicit fail-closed checks.

---

## 2. Current State

### 2.1 What the Architecture Says

`docs/runtime-architecture-v2.md` §2 (Design Principle):

```text
Use Hermes first.
Extend with adapters second.
Create custom infrastructure only when Hermes has no fitting primitive.
```

§14 (Implementation Boundaries):

```text
Allowed integration methods:
  - Hermes provider/auth/model config
  - CLI wrappers
  - JSON packet files
  - adapter modules

Prohibited:
  - reimplementing Hermes provider/auth/fallback systems
```

§4.1 (Model Assignment): all 7 live bot profiles use `opencode-go/<model>` at the Hermes profile/provider level. Hermes already knows how to route opencode-go for the Coordinator's own inference. The Worker execution layer currently bypasses that path.

### 2.2 What the Implementation Does

```text
AI_Agent Worker Layer

  meeting_e2e.py
    OpenCodeGoRoleOutputProvider
      -> _default_opencode_go_runner()  (duplicate #1)
        -> subprocess: opencode-go CLI
          -> ~/.local/bin/opencode-go (local Python wrapper, outside repo)
            -> urllib -> https://opencode.ai/zen/go/v1

  workers.py
    OpenCodeGoWorkerRunner
      -> _default_opencode_go_runner()  (duplicate #2)
        -> subprocess: opencode-go CLI
        -> child env is intentionally scrubbed / explicit-env based

Hermes Coordinator LLM path

  ~/.hermes/config.yaml:
    fallback_providers:
      - provider: opencode-go
        model: deepseek-v4-pro
        base_url: https://opencode.ai/zen/go/v1
        api_mode: chat_completions

  Hermes handles provider config, auth, fallback, and model routing for its own calls.
```

### 2.3 Problems Identified

| # | Problem | Severity after GPT review |
|---|---------|---------------------------|
| 1 | Two `_default_opencode_go_runner` functions with different signatures | Warning |
| 2 | `meeting_e2e.py` passes full `os.environ`, while `workers.py` uses scrubbed explicit env | Blocker before 24h pilot |
| 3 | CLI wrapper (`~/.local/bin/opencode-go`) lives outside repo and is not version-controlled | Blocker before reproducible pilot |
| 4 | Subprocess + temp context-file I/O per worker call | Minor by itself |
| 5 | Hermes already has opencode-go provider/auth/model config, but workers bypass it | Architecture drift |
| 6 | Direct HTTP would create separate auth/retry/fallback semantics unless tightly constrained | Blocker if made default |

---

## 3. Options Reassessed

### Option A: Consolidate Current CLI Path

Keep CLI wrapper, merge runner behavior, fix env/error schema.

```text
AI_Agent -> shared opencode-go CLI boundary -> local wrapper -> API
```

| Factor | Assessment |
|--------|------------|
| Effort | Low |
| Risk | Low operational change, but leaves architecture drift |
| Runtime Architecture v2 alignment | Weak |
| Use case | Emergency compatibility only |

### Option B: Direct HTTP from AI_Agent

Remove CLI wrapper and call the opencode-go API from Python directly.

```text
AI_Agent -> _call_opencode_go_api(model, messages) -> API
```

| Factor | Assessment |
|--------|------------|
| Effort | Medium |
| Risk | Medium-High for architecture drift/live auth divergence |
| Runtime Architecture v2 alignment | Not acceptable as default live path |
| Use case | Temporary bridge only, after Hermes provider API gap is proven |

Why it is not the approved default:

```text
Direct HTTP removes CLI overhead, but it also creates a parallel provider client:
- endpoint handling
- API key loading
- timeout/retry behavior
- fallback semantics
- auth failure classification
- quota/rate-limit interpretation
- redaction/logging policy

Those are Hermes platform responsibilities under Runtime Architecture v2.
```

### Option C: Hermes Provider Integration

AI_Agent workers call a Hermes-native provider surface.

```text
AI_Agent -> Hermes chat_completion(provider="opencode-go", model="glm-5.2", messages=[...])
         -> Hermes provider/auth/model/fallback layer
         -> API
```

| Factor | Assessment |
|--------|------------|
| Effort | Medium-High; requires Hermes provider surface confirmation |
| Risk | Medium implementation risk, lowest architecture risk |
| Runtime Architecture v2 alignment | Strongest / approved |
| Use case | Target and production path |

---

## 4. GPT/Codex Review Verdict

Independent GPT/Codex architecture review returned this verdict:

```text
ADR as written is not approved.
Choose Option C as the architecture decision.
Option B is not a recommended short-term path; it is only a temporary bridge if
Hermes provider API surface is unavailable and the gap is documented.
24h pilot before provider/auth/live-boundary preflight is No-Go.
```

Key review points:

- The ADR correctly identifies the dual-runner, env divergence, and out-of-repo CLI wrapper problems.
- The original recommendation was too optimistic about Option B.
- Option B risks reimplementing Hermes provider/auth/fallback responsibilities.
- Option C is the only path fully aligned with “Use Hermes First”.
- Any non-Hermes bridge must be explicit, temporary, fail-closed, and backed by a migration ticket to Option C.

---

## 5. Required 24h Pilot Go/No-Go Gate

Before the 24h pilot, all of the following must pass:

### Provider/Auth

- [ ] Hermes opencode-go provider preflight succeeds.
- [ ] Worker path and Hermes path use the same model/base_url/auth source.
- [ ] Missing auth fails closed; it must not silently fall back to fake output.
- [ ] Quota/rate-limit/provider errors are classified and surfaced.
- [ ] Codex/GPT escalation unavailability becomes `audit_pending` or a user-visible blocker.

### Worker Boundary

- [ ] `meeting_e2e.py` and `workers.py` use a single shared live-worker boundary, or both delegate to Hermes provider.
- [ ] Full `os.environ` propagation is removed from live worker mode or strictly allowlisted.
- [ ] Worker packets carry secret references only, never secret values.
- [ ] Output artifacts include provider/model/duration/status/error_ref but no raw secrets.
- [ ] Timeout, provider failure, malformed response, and missing credentials are tested.

### Discord / External Mutation

- [ ] Discord bot token preflight succeeds.
- [ ] Target guild/channel/thread permission preflight succeeds.
- [ ] `DISCORD_REQUIRE_MENTION=true` remains true.
- [ ] No global free-response channels are configured.
- [ ] Discord projection failure makes the top-level result non-OK.
- [ ] Secret-like output is blocked from final report/evidence/Discord projection.

### Operations

- [ ] Kill switch and rollback procedure are documented.
- [ ] Recovery checkpoint can resume or clearly stop failed/timed-out workers.
- [ ] Pilot runbook separates smoke success from production readiness.

If any item fails:

```text
24h pilot = No-Go.
Simulation or dry-run only.
```

---

## 6. Temporary Bridge Rules

If Hermes provider integration cannot be completed before the pilot window, a temporary bridge may be used only under these rules:

1. The Hermes provider API gap is documented.
2. The bridge is behind an explicit feature flag.
3. The bridge does not implement custom fallback chains.
4. Auth/provider/rate-limit failures hard fail or produce user-visible degraded state.
5. The bridge reads Hermes config as source of truth where possible and never stores secret values.
6. Live Discord mutation is blocked unless provider/auth preflight passes.
7. An Option C migration issue/plan is created in the same phase.

This bridge may be current CLI consolidation or direct HTTP, but neither is the approved target architecture.

---

## 7. Evidence Collected So Far

### Repository tests

```text
python -m pytest \
  tests/test_runtime_live_adapters.py \
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py \
  tests/test_phase30_team_synthesis.py \
  tests/test_phase30_dynamic_specialist_routing.py \
  tests/test_runtime_architecture_v2_opencode_live_smoke.py -q

Result: 42 passed
```

### Local opencode-go wrapper smoke

The local wrapper was rewritten outside the repo at `~/.local/bin/opencode-go`; therefore this is environment evidence, not repository-controlled implementation evidence.

```text
glm-5.2              OK
deepseek-v4-flash    OK
qwen3.7-plus         OK
kimi-k2.7-code       OK
mimo-v2.5            OK
```

Single-call smoke examples:

```text
GLM-5.2 via local wrapper:
  Exit: 0
  Time: 4.6s
  Output: {"status": "ok"}

DeepSeek V4 Flash via local wrapper:
  Exit: 0
  Time: 2.1s
  Output: OK
```

Direct API curl evidence:

```text
POST https://opencode.ai/zen/go/v1/chat/completions
Status: 200
Time: ~3.2s
Model backend: accounts/fireworks/models/glm-5p2
```

---

## 8. Next-Step Checklist

### Immediate

- [ ] Investigate Hermes importable/provider call surface.
- [ ] Define the thin `HermesProviderWorkerRunner` interface.
- [ ] Decide whether existing worker packet schema maps to Hermes `messages` directly or via a small domain prompt adapter.
- [ ] Add fail-closed tests for missing auth, timeout, malformed response, quota/rate-limit, and secret redaction.

### If Hermes provider surface exists

- [ ] Implement Option C directly.
- [ ] Remove dependency on out-of-repo `~/.local/bin/opencode-go` for production worker path.
- [ ] Keep CLI wrapper only as a developer smoke utility, not as production dependency.

### If Hermes provider surface does not exist yet

- [ ] Document the gap.
- [ ] Add a temporary bridge behind an explicit flag.
- [ ] Unify env policy and runner schema.
- [ ] Create Option C migration plan and acceptance criteria.
- [ ] Run only supervised smoke, not 24h unattended pilot, until bridge preflight is proven.

---

## 9. Final Verdict

```text
Option C is selected.
Option B is not approved as the default short-term live path.
24h pilot is blocked until provider/auth/live-boundary preflight is unified or a
strict temporary bridge is documented and guarded.
```
