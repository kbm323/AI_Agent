# OpenClaw-Centered Hybrid Integration

## Principle

The user-authored README is the top source of truth.

The target is not a standalone AI_Agent Discord bot replacing OpenClaw. The target is:

```text
OpenClaw-centered virtual AI company
```

OpenClaw owns:

- Discord intake
- thread control
- owner draft
- final synthesis
- visible orchestration identity

Hermes operates as a hybrid reviewer:

- first choice: Hermes CLI/API direct review
- second choice: Hermes local API or gateway command
- fallback: Hermes Discord mention + polling

## Current Code Roles

`CompanyOrchestrator` and SQLite are reusable core pieces.

`start:discord` and `live:start` are development harnesses. They proved the flow works, but they are not the final center of operation.

`src/openclaw/pluginBridge.ts` defines the boundary expected by an OpenClaw plugin:

- consume captured OpenClaw draft
- build thread-safe OpenClaw draft post
- build Hermes reviewer request with captured draft
- route Hermes review through preferred hybrid routes

## Correct Final Flow

```text
Discord parent message
  -> OpenClaw gateway/plugin detects request
  -> OpenClaw creates thread
  -> parent notice only
  -> OpenClaw owner draft is captured inside OpenClaw/plugin hooks
  -> captured draft is posted in thread
  -> Hermes review route selected; internal CLI/API by default
  -> Hermes review stored and posted in thread
  -> OpenClaw final synthesis from isolated sources
  -> final stored and posted in thread
```

## Why Hermes Hybrid

Hermes has two useful faces:

- model/reviewer executor: best called directly through CLI/API for clean state and deterministic capture
- Discord bot identity: useful when the thread should visibly show Hermes speaking

The implementation should keep these separate:

```text
Hermes intelligence = CLI/API/gateway execution
Hermes visual identity = optional Discord posting account
```

## Current Live Behavior

The OpenClaw plugin now uses a compact Discord timeline:

- parent channel receives only `Agent discussion started -> <thread>`
- OpenClaw's parent reply is intercepted, suppressed, captured, and reposted as the thread draft
- reviewer request full prompt is stored in SQLite and sent internally by default
- Discord shows only a compact Hermes reviewer request timeline entry
- Hermes review is posted visibly
- final synthesis avoids repeating the full draft and full review
- escalation stores `waiting_for_user`
- a user reply in the thread stores `user_decision`, resumes the task, posts final synthesis, and suppresses duplicate OpenClaw auto-reply

## Next Integration Step

The OpenClaw plugin integration is now the live path. Next stabilization work:

1. Keep `AI_Agent` core DB/task/turn/policy code aligned with the plugin.
2. Move plugin config knobs into explicit config: reviewer route, compact timeline, escalation keywords, max rounds.
3. Keep parent channel launcher-only.
4. Keep final synthesis source-isolated to user request + captured OpenClaw draft + Hermes review.
5. Add repeatable live verification scripts for plugin DB/log inspection.
