# Phase 25 — Hermes Gateway Command Surface Verification Plan

> Canonical baseline: `docs/runtime-architecture-v2.md`
> Phase status: IMPLEMENTATION PLAN
> Date: 2026-06-25 KST

## Decision

Phase 25 verifies the Hermes-first Discord command surface without introducing a standalone slash-command system.

The default command path remains:

```text
Hermes existing Discord Gateway / supported command behavior
  -> bot mention natural-language command
  -> MeetingRun domain coordinator
```

Standalone commands such as `/meeting`, `/cancel`, `/status`, and `/summon` remain out of scope unless a later explicit adapter decision approves them.

## Reasoning

The canonical architecture says AI_Agent should not rebuild Hermes Gateway or command infrastructure unless a verified Hermes gap exists. Phase 21 contains a deterministic Discord interaction webhook/slash-command artifact, but that artifact is not the default command surface and is not promoted to live endpoint operation in Phase 25.

Phase 25 therefore adds a machine-checkable command-surface policy/report that preserves the existing live posture:

```text
1. Hermes existing Discord command and gateway behavior
2. Hermes-supported custom skill/command surface
3. Bot mention natural-language command
4. Separate Discord Adapter that implements standalone slash commands
```

## Constraints

```text
No standalone slash command registration.
No Discord interaction endpoint enablement.
No Ed25519 endpoint deployment.
No bot permission mutation.
No Administrator permission.
No free-response channels.
Mention gate remains required.
Thread mention gate remains required.
```

## Acceptance Criteria

```text
AC1: A Phase 25 command-surface policy exists in code.
AC2: The policy encodes Hermes-first priority order.
AC3: The default verified policy disables standalone slash adapter and interaction endpoint operation.
AC4: Hermes existing gateway and bot mention natural-language surfaces are allowed only when mention gates hold and free-response channels are empty.
AC5: Standalone slash adapter requests fail closed by default.
AC6: Missing mention gate, missing thread mention gate, free-response channels, permission mutation, or Administrator posture fail closed.
AC7: The policy emits a stable verification report showing Gate 1 and Gate 4 deferred because no live interaction endpoint or standalone slash registration is enabled.
AC8: Documentation records Phase 25 as verification, not slash-command implementation.
```

## Out of Scope

```text
Slash command registration.
Discord app interaction endpoint hosting.
Deferred interaction response completion.
Permission/role mutation.
Live Discord mutation.
Service supervision.
Worker/validator/auditor live smoke.
24h live pilot.
```

## Design Principle

```text
Hermes-first architecture requires Hermes-first Discord UI.
Phase 25 verifies the command surface before any later adapter expansion.
```
