# Trustworthy Multi-Agent Meetings Design

**Date:** 2026-07-22

**Status:** Approved for implementation

## Purpose

Upgrade the existing Runtime Architecture v2 meeting pipeline from a transient
two-round role simulation into a meeting system whose visible discussion,
agreement state, audit trail, and on-demand reports share one durable source of
truth.

The existing Discord commands, `MeetingRun` storage, role routing, model policy,
and seven Hermes projection profiles remain in place. The change extends those
boundaries instead of replacing them.

## Goals

- Persist every visible meeting statement with its role, round, model, and
  generation status.
- Derive agreement from the discussion content instead of participant count.
- Distinguish live model output, deterministic replacement text, and failed
  generation in both stored data and Discord-visible results.
- Generate `/meeting-report` output from the same statements the user saw in
  Discord.
- Preserve the real Discord user, guild, parent channel, thread, priority, and
  invocation identity on the `MeetingRun`.
- Prevent duplicate meeting starts for the same Discord interaction.
- Keep full reports available without silently truncating them.
- Remove redundant visible-role worker calls while retaining independently
  selected internal specialist work.

## Non-Goals

- Turning the seven Discord-facing Hermes profiles into seven independent
  persistent reasoning processes. They remain projection endpoints for Runtime
  v2 worker output.
- Replacing `opencode-go`, the existing role model policies, or the Discord
  Gateway.
- Adding automatic Obsidian publication. `/archive` remains the explicit
  persistence command for user-facing Second Brain documents.
- Supporting arbitrary Discord threads as meeting rooms without verifying that
  they belong to the configured CEO meeting channel.

## Canonical Artifacts

Each meeting directory remains:

```text
runtime/meeting_runs/<meeting_run_id>/
```

It contains three canonical records:

```text
meeting_run.json       routing, lifecycle, Discord provenance, artifact links
meeting_session.json   participants and complete round-by-round transcript
meeting_outcome.json   agreement status, decisions, disagreements, actions
```

Existing packet, worker output, validation, and report artifacts remain
compatible. Missing session or outcome files are treated as legacy meetings,
not as corrupt current meetings.

Writes use the repository's guarded JSON storage conventions and replace the
target atomically. The session is saved after every completed round so a process
failure does not erase already-visible discussion.

## Session Model

`BotMessage` is extended with backward-compatible fields:

```text
generation_status: live | replacement | failed
model: model identifier or empty string
provider: provider identifier or empty string
error_code: sanitized failure category or empty string
```

Existing serialized messages without these fields load with
`generation_status=replacement`, because their origin cannot be proven.

`MultiBotSession` continues to contain participants and rounds. It also records
`schema_version=1` and timestamps needed for durable reconstruction. Complete
message text is stored in the session; the 160-character transcript reduction
is used only when constructing bounded model prompts.

The stored session, not process memory, is the source for later reporting.

## Meeting Outcome

The pipeline introduces a structured `MeetingOutcome` with these statuses:

```text
agreed               all material issues resolved
partial_agreement    decisions exist but named disagreements remain
blocked              validation found a blocking issue
needs_user_decision  synthesis failed or human judgment is required
```

The outcome contains:

```text
summary
agreements[]
disagreements[]
action_items[]
evidence_refs[]
validator_notes[]
generation_status
model
error_code
```

After round two, the validation model receives the persisted transcript and
returns strict structured JSON using the `validation_audit` role model policy.
Evidence references identify transcript
messages by round and role. The parser rejects unknown statuses, empty evidence
for an `agreed` result, and malformed action items.

Synthesis failure never becomes agreement. It produces
`needs_user_decision`, records a sanitized error, and reports the degraded state
to Discord.

## Discussion Flow

The visible flow remains concise:

```text
1. CEO opens the agenda.
2. Six visible roles provide round-one positions.
3. Each role receives a bounded transcript of all round-one positions.
4. Six visible roles provide round-two agreement, rebuttal, and conditions.
5. The validation model evaluates the persisted transcript.
6. The outcome and any replacement or failed statements are reported.
```

Round two is a real cross-reference step, but prompt compliance alone does not
prove agreement. Only the structured outcome can set the final state.

The personal assistant remains outside the six-role meeting. It may initiate or
report on meetings but is not a decision-making participant.

## Failure Semantics

Individual message generation follows these rules:

- Successful provider output is stored as `live`.
- A provider failure may produce deterministic role text only when continuity
  is useful; that text is stored as `replacement` with an error category.
- Empty or unusable output is stored as `failed` and remains visibly identified.
- Replacement and failed messages are included in outcome evidence but cannot
  silently support an `agreed` result.
- `agreed` requires live statements from all six visible roles in both rounds.
- `partial_agreement` requires at least four live visible roles in both rounds,
  including a live `validation_audit` role.
- Any lower live-response count forces `needs_user_decision` even when later
  worker activity succeeds.

Raw provider errors, tokens, paths, and stack traces are never stored in
user-visible fields.

## Discord Provenance And Routing

`GatewayMeetingTrigger` provenance is passed through the multi-bot boundary and
persisted on `MeetingRun`:

```text
user_id
guild_id
channel_id
thread_id
priority
platform
invocation_id
```

`channel_id` means the parent channel. `thread_id` means the active meeting
thread. These values must never be substituted with Phase 14 fixture values in
live Gateway execution.

New meetings may be started only from the verified CEO meeting parent channel.
The command creates one shared thread. A command issued inside an existing
thread may continue only when the thread is already linked to a stored
`MeetingRun`; otherwise it returns a user-facing instruction to start from the
CEO meeting channel. This avoids treating a thread ID as its own parent channel.

Every visible role posts through its existing profile token into the single
verified meeting thread. The parent channel is validated independently from the
thread identifier.

## Idempotency

When available, the Discord interaction ID is the canonical invocation ID. The
meeting store records the ID before provider execution and returns the existing
meeting result when the same interaction is delivered again.

For adapters that cannot provide an interaction ID, a short-lived deduplication
key based on profile, user, channel or thread, and normalized topic prevents
accidental retries for 90 seconds. It does not block an intentional later
meeting on the same topic.

## Reports

`/meeting-report` loads `meeting_session.json` and `meeting_outcome.json`.

- Summary reports use the outcome summary and cite supporting role/round
  statements.
- Agreement reports show agreements, unresolved disagreements, and the outcome
  status.
- Action reports return the stored structured action items.
- Full reports include the agenda, participant status, round digest, outcome,
  failure disclosure, and action items.

No report generator may replace the session with an empty set of rounds or use
the agenda as the consensus text.

Discord receives a compact report that fits one response. The complete Markdown
report is saved as `reports/<export_type>.md` under the meeting directory and
the response identifies its `MeetingRun`. Content is never silently cut in the
middle of a section. The existing `/archive` flow can then save the complete
meeting into the Second Brain.

Legacy meetings without a session or outcome return an explicit legacy-data
notice and use only verifiable stored worker artifacts. They do not fabricate
agreements or actions.

## Call And Cost Control

The two visible rounds require twelve role calls for six live roles. The existing
second set of six visible-role worker calls is removed. Worker outputs for
visible roles are derived from their stored final statements.

Only selected internal specialists run as additional independent tasks. One
structured outcome evaluation runs after the transcript is persisted.
Independent calls within a round run with at most three concurrent provider
requests, while round two waits for round one and outcome evaluation waits for
round two.

Expected baseline:

```text
12 visible-role calls + selected specialists + 1 outcome evaluation
```

## Compatibility And Migration

- Existing `meeting_run.json` files continue to load.
- `BotMessage.from_dict` supplies safe defaults for new evidence fields.
- Existing report exports detect legacy meetings and disclose reduced evidence.
- Existing Discord command names remain unchanged.
- Existing role IDs, profile mappings, and channel IDs remain unchanged.
- No stored meeting is rewritten automatically.

## Testing Strategy

Tests are added in red-green order for each behavior:

1. Session and outcome store round-trip, legacy loading, and atomic replacement.
2. Real Gateway provenance reaches the persisted `MeetingRun`.
3. Six roles create two persisted rounds with twelve messages.
4. Round-two prompts contain bounded round-one statements from every role.
5. Provider failure records `replacement` or `failed` instead of silent success.
6. Outcome parsing supports all four statuses and fails closed on malformed data.
7. Reports contain unique transcript evidence and structured actions rather than
   fixture text.
8. Duplicate invocation returns the existing meeting without provider or
   Discord duplication.
9. Parent-channel and existing-thread routing fail closed at the correct
   boundary.
10. Compact Discord reports preserve complete sections and point to the full
    stored artifact.
11. The full Runtime v2 meeting, command, archive, and smoke suites remain green.

A final bounded live smoke is separate from automated tests because it consumes
provider capacity and posts to Discord. It verifies one thread, twelve ordered
messages, six profile identities, stored transcript, stored outcome, and an
on-demand report. It must not rotate existing bot tokens.

## Implementation Order

1. Add durable session and outcome models and stores.
2. Pass and persist real Gateway and Discord provenance.
3. Record message generation status and fail visibly.
4. Add content-based structured outcome evaluation.
5. Rebuild reports from canonical session and outcome artifacts.
6. Correct parent-channel and linked-thread behavior.
7. Add invocation idempotency.
8. Remove duplicate visible-role worker calls and add bounded concurrency.
9. Add compact/full report delivery and legacy notices.
10. Run full automated verification and prepare the bounded live smoke.

## Acceptance Criteria

The implementation is complete when a six-role meeting produces two durable
rounds, every message has generation evidence, agreement is content-derived and
fails closed, reports reproduce the visible discussion, provenance is real,
duplicates do not create new meetings, Discord routing respects the CEO parent
channel, and the relevant automated suites pass without exposing secrets.
