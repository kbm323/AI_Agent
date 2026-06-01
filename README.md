# AI_Agent

Discord-based Virtual AI Company orchestration core for an OpenClaw-centered,
Hermes-reviewed multi-agent production workflow.

## Current Source Of Truth

The latest external system design is:

- `C:\Users\KBM\Downloads\260526_README.md`

Keep that original file outside this repository unchanged. This repo carries a
working summary in [docs/source-of-truth.md](docs/source-of-truth.md) and
implementation architecture in [docs/architecture.md](docs/architecture.md).

`merged-system.md` is now reference material only.

## Target

AI_Agent is not intended to replace OpenClaw. The repo name is historical.

OpenClaw remains the operational center:

- receives Discord project requests
- creates or uses task threads
- decomposes work and produces the owner draft
- calls Hermes for reviewer-only critique
- decides which feedback to accept or reject
- writes the final synthesis

Hermes is the senior reviewer and creative meeting partner:

- critiques OpenClaw's draft
- detects risks and feasibility issues
- performs fact/quality review when needed
- returns a verdict
- does not make the final decision

## Phase 2-A Goal

```text
parent channel request
  -> OpenClaw creates a Discord thread
  -> parent channel receives only a thread-start notice
  -> OpenClaw routes the task to a team
  -> OpenClaw creates the owner draft
  -> Hermes reviews in reviewer-only mode in the same thread
  -> OpenClaw captures the next Hermes response
  -> OpenClaw writes final synthesis in the same thread
  -> user replies in the thread resume the existing task
```

Phase 2-A includes minimal team routing:

- `content`
- `art`
- `tech`
- `marketing`
- `executive`

Phase 2-B expands the team workflows. Phase 2-C expands the persona layer.

## Operating Rules

- Discord is the primary operating interface.
- Channel = project.
- Thread = task.
- Parent channel is a launcher only.
- Hermes must respond only in the existing task thread.
- Internal Hermes CLI/API review is the default execution path.
- Discord polling is the fallback path.
- Real `@Hermes` mention timeline is shown only in debug mode.
- User approval is required for budget, legal/IP, brand/public release,
  payment, git push, deployment, deletion, and other irreversible actions.
- If the same unresolved issue repeats 3 times, escalate to the user.

## Development Commands

The host PowerShell environment may not expose `npm`. The bundled Codex Node
runtime can run the current tests:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/*.test.ts
```

The package scripts remain:

```bash
npm test
npm run dry-run
npm run start:discord
npm run inspect:latest
npm run live:start
npm run live:status
npm run live:stop
```

## Handoff

After each completed work stage, update [docs/SESSION_HANDOFF.md](docs/SESSION_HANDOFF.md)
so another AI session or chat window can continue without relying on hidden
conversation memory.
