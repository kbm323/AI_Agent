# Source Of Truth

This project follows the latest user-provided system design:

1. `C:\Users\KBM\Downloads\260526_README.md`
2. Discord/OpenClaw workflow video transcript
3. Current repository implementation
4. EJClaw/NanoClaw reference ZIP, as background only

`merged-system.md` is reference material only and is no longer a design source
of truth.

## Product Definition

AI_Agent is the repository name for an OpenClaw-centered orchestration core. It
must not become a permanent independent Discord owner unless the user explicitly
changes the architecture.

The product is a Discord-based virtual AI content production company:

```text
user / representative
  -> Discord
  -> OpenClaw Bot as orchestrator, implementer, and final integrator
  -> team-specific execution route
  -> Hermes Bot as senior reviewer and critical meeting partner
  -> consensus / final synthesis
  -> user approval when required
```

## Roles

### OpenClaw

OpenClaw is the center runtime.

- Receives Discord project requests.
- Creates or uses the task thread.
- Analyzes and decomposes the request.
- Routes the work to a minimal team category.
- Produces the owner draft.
- Requests Hermes reviewer-only feedback.
- Classifies feedback as accepted, rejected, or partially accepted.
- Produces the final synthesis.
- Escalates important decisions to the user.

### Hermes

Hermes is not the final decision-maker.

- Reviews critically.
- Suggests alternatives.
- Checks risk, feasibility, and factuality.
- Responds only inside the existing task thread.
- Returns one of the required verdicts.

Internal verdict values:

- `agree`
- `partial_agree`
- `disagree`
- `needs_user_decision`

Korean display labels:

- `동의`
- `부분동의`
- `비동의`
- `사용자결정필요`

## Phase Order

### Phase 2-A

Build the inter-agent conversation pipeline in one Discord thread.

Completion criteria:

```text
parent channel request
  -> OpenClaw automatically creates a thread
  -> parent channel receives only a start notice
  -> OpenClaw routes the request to content/art/tech/marketing/executive
  -> OpenClaw drafts
  -> Hermes reviews in reviewer-only mode
  -> OpenClaw captures the next Hermes response
  -> OpenClaw writes final synthesis
  -> user replies in the thread resume the existing task
```

Phase 2-A includes minimal team routing and model selection. It does not include
full team-specific workflows or the full persona layer.

### Phase 2-B

Expand team routing into team-specific workflows, channel policies, and richer
team behavior.

### Phase 2-C

Add the full dual persona layer:

- OpenClaw execution personas.
- Hermes review personas.

### Phase 2-D

Add retrieval and verification:

- minimal web research
- source-backed summary
- Hermes fact review
- final OpenClaw synthesis

For Phase 2-A, minimal web search may be used when current information is
necessary.

### Phase 2-E

Add human approval and escalation paths for high-risk decisions.

### Phase 3

Add memory and decision logs:

- lore entries
- brand decisions
- approval records

Minimal DB tables for these concepts may be created during Phase 2-A.

## Discord Rules

- Discord is the primary operating interface.
- Channel = project.
- Thread = task.
- Parent channel is a launcher only.
- The parent channel should receive only a start notice.
- Hermes must stay in the same thread.
- If Hermes creates a new thread unexpectedly, mark the task as
  `needs_user_decision`.
- Thread resume is required in Phase 2-A.

## OpenClaw/Hermes Communication

Preferred review route:

1. Internal Hermes CLI/API call.
2. Discord polling fallback.

OpenClaw captures the next Hermes bot message as the Discord polling result.
Hermes timeout is configured by `AI_AGENT_HERMES_TIMEOUT_SECONDS`, defaulting to
600 seconds.

Actual `@Hermes` mention timeline is debug-only and controlled by
`AI_AGENT_DEBUG_MENTIONS=false` by default.

## Routing

Minimal Phase 2-A route values:

- `content`
- `art`
- `tech`
- `marketing`
- `executive`

Routing affects prompt selection and OpenClaw/Hermes model selection. Model
config should be environment-variable driven with role/team fallbacks.

## Escalation

OpenClaw must stop and ask the user when:

- Hermes returns `needs_user_decision`.
- The same unresolved issue repeats 3 times.
- The task touches budget, legal/IP, brand risk, or external publication.
- The task touches payment, git push, deployment, deletion, or any irreversible
  action.

Long-running disagreement should not become an infinite loop.

## Storage

Phase 2-A stores:

- task
- turn
- decision

Minimal long-term tables may be introduced:

- `lore_entries`
- `brand_decisions`
- `approval_records`

Long-term memory is mixed:

- SQLite stores operational history and decisions.
- Hermes memory stores reviewer learning and context.
- If they conflict, ask the user.

During development, SQLite schema reset or migration is allowed.

## Development Principles

- Prefer local plugin, config, and middleware before editing installed internals.
- OpenClaw/Hermes internal modification is allowed when necessary.
- Never patch `node_modules` casually.
- Token edits are allowed, but token values must never be printed in logs or
  responses.
- Real operation should be verified with a harness before treating it as stable.
- Search and cite sources when current information matters.
- The final decision belongs to the user.

## Handoff Rule

After each completed work stage, update `docs/SESSION_HANDOFF.md` with:

- completed work
- current blockers
- decisions made
- next recommended actions
- verification status
