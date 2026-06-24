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

Status: DEFERRED unless a live interaction endpoint is enabled.

### Gate 2 — Guild/channel allowlist

Required before live posting outside controlled smoke channels:

```text
- allowed guild IDs explicit
- allowed channel IDs explicit per profile
- no implicit fallback to arbitrary channel IDs
- first smoke target should be #시스템-로그 or another controlled ops channel
- user-visible channels require explicit smoke approval
```

Status: PARTIAL. Home channels are verified; allowlist enforcement must be reviewed per adapter before production.

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

Status: DEFERRED unless Phase 21 adapter is moved from deterministic/test coverage to live endpoint operation.

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

Status: PARTIAL. Architecture and quota scripts exist; live worker smoke remains a later boundary.

### Gate 7 — Quota/cost monitoring

Required before always-on operation:

```text
- run scripts/check_all_quota.sh before live worker batches
- define pause thresholds for Codex/OpenCode Go
- watchdog should prefer SIGTERM pause over ouroboros cancel
- quota exhaustion must save state or fail safely, not spin/retry indefinitely
```

Status: AVAILABLE. Current quota checker exists and was used before Phase 23.

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

Status: DEFERRED. Phase 23 documents boundary only.

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

Status: PARTIAL. Projection sanitizer exists; live smoke verification remains separate.

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
