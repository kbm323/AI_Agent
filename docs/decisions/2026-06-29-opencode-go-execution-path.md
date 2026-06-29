# OpenCode Go Worker Execution Path — Architecture Decision Record

> **Status:** Draft for GPT review  
> **Date:** 2026-06-29 KST  
> **Context:** Phase 31 live-provider completion — opencode-go timeout blocker resolved,  
> now evaluating the execution path for production hardening.

---

## 1. Current State

### 1.1 What the Architecture Says

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

§4.1 (Model Assignment): All 7 live bot profiles already use `opencode-go/<model>` in their Hermes
config — Hermes **natively** handles opencode-go as a provider for the Coordinator's own
inference. The Worker execution layer, however, bypasses this and runs opencode-go via CLI.

### 1.2 What the Implementation Does

```
┌─────────────────────────────────────────────────────────┐
│ AI_Agent Worker Layer                                    │
│                                                         │
│ ┌─ meeting_e2e.py ─────────────────────────────────────┐ │
│ │ OpenCodeGoRoleOutputProvider                         │ │
│ │   → _default_opencode_go_runner()  (duplicate #1)    │ │
│ │     → subprocess: opencode-go CLI                    │ │
│ │       → ~/.local/bin/opencode-go (Python wrapper)    │ │
│ │         → urllib → https://opencode.ai/zen/go/v1    │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                         │
│ ┌─ workers.py ─────────────────────────────────────────┐ │
│ │ OpenCodeGoWorkerRunner                               │ │
│ │   → _default_opencode_go_runner()  (duplicate #2)    │ │
│ │     → subprocess: opencode-go CLI (same wrapper)     │ │
│ │     → BUT: env stripped to PATH only                  │ │
│ └──────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Hermes (Coordinator's own LLM)                           │
│                                                         │
│ config.yaml:                                            │
│   fallback_providers:                                   │
│     - provider: opencode-go                             │
│       model: deepseek-v4-pro                            │
│       base_url: https://opencode.ai/zen/go/v1            │
│       api_mode: chat_completions                         │
│                                                         │
│ Hermes handles: auth, streaming, retries, fallback       │
│ This session is already using this path.                │
└─────────────────────────────────────────────────────────┘
```

### 1.3 Problems Identified

| # | Problem | Severity |
|---|---------|----------|
| 1 | Two `_default_opencode_go_runner` functions with different signatures | Warning |
| 2 | `workers.py` runner strips env to PATH-only (security design leaking into transport) | Warning |
| 3 | CLI wrapper (`~/.local/bin/opencode-go`) lives outside repo, no version control | Warning |
| 4 | Subprocess overhead: fork + Python interpreter per worker call | Minor |
| 5 | File-based context-file I/O adds unnecessary disk round-trips | Minor |
| 6 | Hermes already has a working opencode-go provider — unused by workers | Gap |

---

## 2. Options

### Option A: Consolidate CLI Path (minimal)

Keep CLI wrapper, merge the two runners into one, fix env handling.

```
AI_Agent → _call_opencode_go(argv) → subprocess opencode-go → API
```

| Factor | Assessment |
|--------|-----------|
| Effort | ~30 min |
| Risk | Low — existing behavior preserved |
| Alignment with "Use Hermes first" | Weak — still bypasses Hermes provider |
| Cleanliness | Improved but still has subprocess + file I/O overhead |

### Option B: Direct HTTP from Python (recommended)

Remove CLI wrapper entirely. Call opencode-go API directly from Python via `httpx` or `urllib`.

```
AI_Agent → _call_opencode_go_api(model, messages) → urllib/httpx → API
```

| Factor | Assessment |
|--------|-----------|
| Effort | ~2 hours |
| Risk | Low-Medium — API contract same, just transport changes |
| Alignment with "Use Hermes first" | Medium — uses Hermes `.env` for auth, but still separate HTTP stack |
| Cleanliness | High — single call site, no subprocess, no file I/O, in-memory messages |

### Option C: Hermes Provider Integration (target architecture)

AI_Agent workers call Hermes's native provider layer. Hermes handles auth, retries, streaming.

```
AI_Agent → Hermes chat_completion(provider="opencode-go", model="glm-5.2", messages=...)
         → Hermes provider layer → API
```

| Factor | Assessment |
|--------|-----------|
| Effort | ~1 day (needs Hermes internal API surface for AI_Agent) |
| Risk | Medium — new integration surface |
| Alignment with "Use Hermes first" | **Strongest** — Hermes is the platform, AI_Agent is the domain |
| Cleanliness | Highest — zero duplication, single auth/config source |

---

## 3. Design Principle Analysis

### "Use Hermes First"

```text
Architecture says: Hermes provider/auth/model config is an allowed integration method.
CLI wrappers are also allowed — but as an adapter layer, not the primary path.

Current state: Hermes already manages opencode-go credentials, fallback chains,
and model routing for its own inference. The Worker layer should leverage this
rather than building a parallel auth/transport stack.
```

### Prohibited Pattern Check

| Pattern | Status |
|---------|--------|
| Reimplementing Hermes provider/auth | ⚠️ Current CLI path reimplements auth (reads `.env` separately) |
| Reimplementing fallback systems | ⚠️ CLI path has no fallback; Hermes provider does |
| CLI wrappers | ✅ Allowed, but should be thin adapters |

---

## 4. Current Hermes opencode-go Provider Config

```yaml
# From ~/.hermes/config.yaml
fallback_providers:
  - provider: opencode-go
    model: deepseek-v4-pro
    base_url: https://opencode.ai/zen/go/v1
    api_mode: chat_completions
```

Hermes already:
- Stores the API key (`OPENCODE_GO_API_KEY` in `~/.hermes/.env`)
- Has the correct base URL
- Uses `chat_completions` mode (OpenAI-compatible)
- Routes to this provider as a fallback after the primary (openai-codex/gpt-5.5)

The only missing piece: AI_Agent's Worker runner doesn't call Hermes's provider API.

---

## 5. Recommendation

**Short-term (Phase 31I): Option B — Direct HTTP**

Rationale:
- Removes CLI wrapper dependency entirely
- Eliminates subprocess + file I/O overhead
- Single Python module, version-controlled in repo
- Can be done now, before 24h pilot
- Sets up clean migration path to Option C

**Target (Phase 32+): Option C — Hermes Provider Integration**

Rationale:
- Aligns with "Use Hermes First"
- Zero duplicate auth/config
- Hermes handles retries, fallback, streaming natively
- Requires: Hermes to expose `chat_completion()` as an importable function, OR
  AI_Agent to call Hermes's internal provider via its config/auth layer

---

## 6. Questions for GPT Review

1. Does the current dual-runner + CLI-wrapper design violate the "Use Hermes First" principle, or is it an acceptable adapter pattern?
2. Between Option B (direct HTTP) and Option C (Hermes provider), which better serves the long-term architecture given that Hermes already has opencode-go configured as a fallback?
3. Is there a risk in Option B of diverging from Hermes's auth/fallback behavior that could cause silent failures in production?
4. For the 24h pilot: should we fix this before going live, or is the current CLI path stable enough?
5. Are there any security concerns with the current env-handling discrepancy between `workers.py` (stripped env) and `meeting_e2e.py` (full env)?

---

## 7. Supporting Evidence

### Live Smoke Test (2026-06-29 18:09 KST)

```
Test 1: GLM-5.2 via opencode-go CLI wrapper
  Exit: 0, Time: 4.6s, Output: {"status": "ok"}

Test 2: DeepSeek V4 Flash via opencode-go CLI wrapper
  Exit: 0, Time: 2.1s, Output: OK

Test 3: Minimal env (PATH+HOME only, simulating workers.py stripped env)
  Exit: 0, Time: 2.4s, Output: OK
```

### curl Direct Test (2026-06-29 17:53 KST)

```
curl opencode.ai/zen/go/v1/chat/completions
  Status: 200, Time: 3.2s
  Model: accounts/fireworks/models/glm-5p2
```

