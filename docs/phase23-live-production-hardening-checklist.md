# Phase 23 Live Production Hardening Checklist

> Scope: Boundary document only. Phase 23 does not mutate live Discord permissions, tokens, channels, or service processes.
> Canonical baseline: `docs/runtime-architecture-v2.md`
> Live Discord surface last verified: 2026-06-25 02:43 KST

## Decision

Phase 13~22 completed the deterministic Runtime Architecture v2 implementation path. Phase 23 hardens the integration contract and documents the live-production boundary. A system is not considered fully live-production-ready until the checklist below is executed and verified.

## Current verified live surface

```text
Guild: Entertainment (1505600166676271244)
Discord-facing bot accounts: 7
- aicompanyassistant / 비서 / #일일-브리핑
- aicompanyceo / 대표 / #전략-회의실
- aicompanycontent / 콘텐츠팀장 / #콘텐츠-메인
- aicompanyart / 아트팀장 / #아트-메인
- aicompanytech / 기술팀장 / #기술-메인
- aicompanymarketing / 마케팅팀장 / #마케팅-메인
- aicompanyquality / 품질관리팀장 / #전체-리뷰

All 7 profiles:
- DISCORD_REQUIRE_MENTION=true
- DISCORD_THREAD_REQUIRE_MENTION=true
- DISCORD_FREE_RESPONSE_CHANNELS empty
```

## Hardening gates

### Gate 1 — Discord interaction security

Required before accepting production slash/webhook traffic:

```text
- Ed25519 signature verification using raw body bytes
- timestamp/replay protection
- interaction token never logged
- request body size cap
- malformed payload returns safe error
- public key loaded from profile-local environment only
```

Status: DEFERRED → PHASE 25 VERIFIED. `HermesGatewayCommandSurfacePolicy` records that no live interaction endpoint is enabled in the default Hermes-first command surface. Interaction security remains deferred because Phase 25 deliberately does not promote the Phase 21 artifact to a live endpoint.

### Gate 2 — Guild/channel allowlist

Required before live posting outside controlled smoke channels:

```text
- allowed guild IDs explicit
- allowed channel IDs explicit per profile
- no implicit fallback to arbitrary channel IDs
- first smoke target should be #시스템-로그 or another controlled ops channel
- user-visible channels require explicit smoke approval
```

Status: PARTIAL → PHASE 24 FOUNDATION COMPLETE. Home channels are verified by name; `DiscordLiveBoundaryPolicy` now fail-closes live projection on guild/profile/channel mismatch before HTTP. The current policy is symbolic until a token-safe real channel-ID inventory is run, so arbitrary live Discord posts are blocked rather than silently allowed.

### Gate 3 — Discord permission inventory

Required before changing roles/permissions:

```text
- record current permissions for all 7 bot accounts
- keep Administrator disabled by default
- minimum permissions: read/send in assigned channels, thread access if required
- no broad server management permission unless explicitly justified
- document any permission delta before applying it
```

Status: DEFERRED. Phase 23 does not mutate permissions.

### Gate 4 — Slash command registration

Required before treating Discord app commands as production entrypoints:

```text
- command schema reviewed
- guild-scoped registration used first for testing
- command names aligned with Hermes-first UX
- deferred interaction response type=5 is completed by PATCH @original or followup
- command logs contain interaction IDs but not tokens
```

Status: DEFERRED → PHASE 25 VERIFIED. Standalone slash registration remains out of scope by default. `HermesGatewayCommandSurfacePolicy` fail-closes standalone slash adapter requests unless a later explicit adapter decision enables them.

### Gate 5 — Kanban live client dependency

Required before live autonomous scheduling mutates an external board:

```text
- injected Kanban client only
- missing client fails closed in live mode
- card creation failures fail the relevant subphase
- dry-run remains side-effect free
- created card IDs persisted in MeetingRun/phase artifact
```

Status: PARTIAL. Deterministic policies exist; live dependency behavior must be smoke-tested separately.

### Gate 6 — Worker/validator/auditor live boundary

Required before live model execution is considered production:

```text
- opencode-go calls use file-based JSON packet input
- GLM validator and Codex auditor paths record model/provider used
- timeout/non-zero exit/schema failure fail closed
- raw stdout/stderr with secret-like values is sanitized before persistence/projection
- quota gate checked before large worker batches
```

Status: PARTIAL → PHASE 26 HARDENED. `LiveWorkerBoundarySmokePolicy` checks 8 boundary conditions (packet-based input, model/provider recording, timeout/non-zero-exit fail-closed, output sanitization, quota gate, no shell=True, no direct env passthrough). `sanitize_worker_output` redacts secret-like patterns from stdout/stderr. `OpenCodeGoWorkerRunner` and `OpenCodeGoSmokeRunner` now sanitize stdout/stderr before persistence by default, runner exceptions become structured failed artifacts, and default opencode-go process execution uses an explicit environment mapping instead of implicit parent-environment passthrough. No live CLI execution in tests.

### Gate 7 — Quota/cost monitoring

Required before always-on operation:

```text
- run scripts/check_all_quota.sh before live worker batches
- define pause thresholds for Codex/OpenCode Go
- watchdog should prefer SIGTERM pause over ouroboros cancel
- quota exhaustion must save state or fail safely, not spin/retry indefinitely
```

Status: AVAILABLE → PHASE 26 VERIFIED. Current quota checker exists and is verified by `LiveWorkerBoundarySmokePolicy` as a required boundary condition before worker batches.

### Gate 8 — Service supervision

Required before claiming always-on production:

```text
- one gateway/service process per live bot profile if presence/mentions are required
- status/start/stop scripts documented
- logs rotate or are bounded
- process restart policy exists
- health endpoint or periodic heartbeat exists
- secrets loaded from profile-local env, never committed
```

Status: PHASE 27 HARDENED. ServiceSupervisionPolicy.current_verified() defines
start/stop/status/heartbeat/log_bound/restart_policy/secrets_env_path for all
7 live Hermes profiles. evaluate() verifies all 6 Gate 8 conditions with
fail-closed posture, directly checks DiscordLiveBoundaryPolicy profile/safety
posture, and requires exact profile-local env paths under
`~/.hermes/profiles/<profile>/.env`. No permission expansion. Profile names
match DiscordLiveBoundaryPolicy exactly.

### Gate 9 — Projection safety

Required before posting AI-generated content to user-visible channels:

```text
- allowed_mentions constrained
- @everyone/@here broken or redacted
- content length capped for Discord
- raw worker output omitted from final projection
- trace IDs preserved
- secret-like assignments/bearer tokens redacted
```

Status: PHASE 28 VERIFIED. ProjectionSafetyPolicy.current_verified() verifies
allowed_mentions constraint, mass-mention breaking, content cap, raw worker
output omission, trace ID preservation, and secret-like redaction. Phase 28
closed-loop pilot verifies Hermes Gateway input → MeetingRun → workers →
validation → projection in controlled smoke mode. Live projection remains
injected-boundary only and fails closed when publish is blocked or failed.

### Gate 10 — Production readiness / 24h bounded live pilot

Required before claiming the system is ready for bounded production operations:

```text
- all Gate 5-9 statuses verified pass
- production runbook complete
- 24h pilot bounds declared (max runs/hour, max cost, window, channels)
- recovery evidence present (checkpoint interval, rollback command, incident channel, manual override)
- quota/cost evidence present (budget cap, hourly max, model thresholds, alert channel)
```

Status: PHASE 29 VERIFIED. ProductionReadinessVerdict.evaluate() fails closed
unless Gate 5-9 are pass, the runbook is complete, the 24h live pilot policy
passes, and recovery/quota/cost evidence meet bounded constraints.
simulate_24h_pilot() returns a verdict and synthetic observations without
actually running for 24 hours.

## Production-readiness wording

Use these labels consistently:

```text
Phase 13~22 planned implementation complete
  - deterministic Runtime Architecture v2 modules and tests implemented

Runtime v2 deterministic orchestration layer complete
  - MeetingRun-oriented core and phase integrations exist

Live production hardening remains
  - external Discord/Kanban/model/service boundaries still need controlled smoke execution and ops supervision
```

Do not state that the system is a fully live autonomous company until all live gates above are executed and recorded.
