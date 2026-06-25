# Phase 24 Live Boundary Inventory & Allowlist Foundation Plan

> Status: IMPLEMENTATION PLAN
> Canonical baseline: `docs/runtime-architecture-v2.md`
> Previous boundary checklist: `docs/phase23-live-production-hardening-checklist.md`
> Scope: Hermes-first live boundary hardening. No Discord permission, token, channel, or service process mutation.

## Decision

Phase 24 hardens the live Discord projection boundary without expanding the command surface or changing bot permissions.

The phase implements an explicit guild/profile/channel allowlist guard for live Discord projection and records the current verified safety posture:

```text
Hermes-first command surface remains the default.
Standalone slash commands are not a Phase 24 requirement.
Live bot permissions remain unchanged by default.
Mention-gated and thread-mention-gated behavior stays required.
Free-response channels stay empty.
Administrator remains disallowed.
```

## Reasoning

Phase 23 identified these gates as incomplete:

```text
Gate 2 — Guild/channel allowlist: PARTIAL
Gate 3 — Discord permission inventory: DEFERRED
Gate 9 — Projection safety: PARTIAL
```

The safe next step is not to mutate Discord. The safe next step is to make AI_Agent fail closed before live projection if the guild/profile/channel boundary is not explicitly allowed.

## Acceptance Criteria

### AC1 — Hermes-first command surface preserved

```text
- Do not add a new standalone slash-command requirement.
- Do not treat `/meeting`, `/cancel`, `/status`, or `/summon` as core architecture.
- Keep Hermes Gateway / mention / Hermes-supported command surface as default.
```

### AC2 — Bot permissions preserved

```text
- Do not modify Discord roles or permissions.
- Preserve no-Administrator policy.
- Preserve DISCORD_REQUIRE_MENTION=true.
- Preserve DISCORD_THREAD_REQUIRE_MENTION=true.
- Preserve empty DISCORD_FREE_RESPONSE_CHANNELS.
```

### AC3 — Live boundary allowlist exists

```text
- Define current verified Entertainment guild boundary.
- Define exactly 7 allowed live profiles.
- Define one home-channel allowlist entry per profile.
- Unknown guild fails closed.
- Unknown profile fails closed.
- Unknown channel fails closed.
```

### AC4 — Live Discord sink enforces boundary before HTTP

```text
- If boundary policy blocks the event, no HTTP call is made.
- Block result is structured and sanitized.
- Existing injected HTTP/env testing pattern remains.
```

### AC5 — Documentation names the correct next phases

```text
- Phase 24 result distinguishes implementation from live production readiness.
- Remaining phases avoid saying “slash command hardening” as a default requirement.
```

## Out of Scope

```text
- Discord permission changes
- Token rotation
- Channel creation/deletion
- Live service process start/stop
- Standalone slash command registration
- Separate Discord interaction endpoint
- Full live worker/validator/auditor smoke
```

## Design Principle

```text
Inventory first.
Allowlist second.
Fail closed third.
Mutate Discord never, unless a concrete live need is approved.
```
