# AI_Agent Architecture

## Scope

AI_Agent implements the reusable core and repository-managed plugin support for
Phase 2-A of the OpenClaw/Hermes virtual AI company workflow.

The final runtime center is OpenClaw, not the standalone `start:discord`
harness.

## Main Components

```text
Discord
  -> OpenClaw gateway / local plugin
  -> AI_Agent orchestration core
  -> SQLite state store
  -> OpenClaw owner draft / finalizer
  -> Hermes reviewer
  -> Discord thread timeline
```

## Data Model

Current core tables:

- `tasks`: durable task records.
- `turns`: full and visible turn records.
- `decisions`: escalation and approval decisions.

Phase 2-A adds or reserves minimal long-term tables:

- `lore_entries`: character, world, and content-lore notes.
- `brand_decisions`: brand direction and approved standards.
- `approval_records`: user approvals and high-risk decision logs.

## Task Flow

```text
parent channel message
  -> OpenClaw gateway/plugin detects the request
  -> OpenClaw creates a task thread
  -> parent receives only "Agent discussion started -> <thread>"
  -> AI_Agent creates task record
  -> route task to content/art/tech/marketing/executive
  -> OpenClaw owner draft capture
  -> reviewer request stored in SQLite
  -> Hermes review through internal CLI/API
  -> if internal route fails, Discord polling fallback
  -> OpenClaw accepts/rejects/partially accepts feedback
  -> escalation check
  -> OpenClaw final synthesis in the same thread
```

## Team Routing

Phase 2-A includes minimal routing:

| Route | Purpose |
| --- | --- |
| `content` | MV ideas, storylines, hooks, audience retention |
| `art` | character visuals, concept art, style, brand visuals |
| `tech` | code, Unreal/VFX, automation, APIs, server work |
| `marketing` | titles, thumbnails, shorts, SNS copy, positioning |
| `executive` | priority, budget, legal/IP, brand and release risk |

Routing selects prompts and model configuration. Phase 2-B expands this into
team-specific workflows.

## Review Routes

Hermes review route preference:

1. CLI/API/local gateway call.
2. Discord polling fallback.

The polling fallback captures the next Hermes bot message in the same thread.
If Hermes posts in another thread, the task moves to `waiting_for_user` with a
`needs_user_decision` reason.

Timeout is controlled by:

```text
AI_AGENT_HERMES_TIMEOUT_SECONDS=600
```

Debug-only mention timeline:

```text
AI_AGENT_DEBUG_MENTIONS=false
```

When debug mentions are disabled, the system may still store the full reviewer
request in SQLite and show only compact timeline entries in Discord.

## Verdicts

Internal values:

- `agree`
- `partial_agree`
- `disagree`
- `needs_user_decision`

Display labels:

- `동의`
- `부분동의`
- `비동의`
- `사용자결정필요`

## Parent Channel Rule

The parent channel is only a launcher.

Allowed:

```text
Agent discussion started -> <thread>
```

Not allowed:

- OpenClaw draft
- Hermes reviewer request
- Hermes review
- final synthesis

## Thread Rule

The thread contains the visible timeline.

- User request is visible.
- OpenClaw draft may be visible.
- Hermes request is compact unless debug mentions are enabled.
- Hermes review is visible.
- OpenClaw final synthesis is visible.
- Full prompts and long source content are stored in SQLite.

## Escalation

The orchestrator pauses when:

- Hermes returns `needs_user_decision`.
- The same unresolved issue repeats 3 times.
- The task touches budget, legal/IP, brand, or external release.
- The task touches payment, git push, deploy, delete, or another irreversible
  action.
- Hermes violates the same-thread rule.

Paused tasks use `waiting_for_user`.

When the user replies in the same thread:

- store the reply as `user_decision`
- resume the existing task
- suppress duplicate default OpenClaw replies when applicable
- post final synthesis in the same thread when ready

## Runtime Notes

`start:discord` remains a development harness. It is useful for validating
thread/task/state behavior, but the production target is OpenClaw local plugin
integration.

The OpenClaw local plugin source of truth should be copied into this repository
once it is found. The requested source location to search first is:

```text
~/.openclaw/local-plugins
```

Current Codex Desktop session could not access WSL; see
`docs/SESSION_HANDOFF.md` for the latest status.
