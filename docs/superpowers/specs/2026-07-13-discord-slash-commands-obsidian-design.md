# Discord Slash Commands and Obsidian Save Design

Status: APPROVED DESIGN BASELINE  
Date: 2026-07-13 KST  
Canonical architecture: `docs/runtime-architecture-v2.md`

> Command-surface update: the grouped `/meeting` and `/llmwiki` shapes in this
> document are superseded by
> `2026-07-16-discord-natural-language-commands-design.md`. The `/save` design
> below remains authoritative for retention behavior and is deployed as
> `/archive` because `/save` conflicts with a Hermes built-in command.

## 1. Decision

The live user-facing command surface is intentionally small:

```text
/meeting  start and operate a Runtime Architecture v2 meeting
/llmwiki  ingest, find, and create LLM Wiki material
/save     save the current Discord conversation to Obsidian
```

`/save thread`, `/save conversation`, and `/save meeting` are not separate
commands. The user always invokes `/save`; the system determines the source
and document type from Discord context and any linked `MeetingRun`.

Discord's native thread feature owns conversation organization. AI_Agent does
not create a second thread abstraction or require a command merely to open a
thread.

## 2. Architecture Alignment

Runtime Architecture v2 remains authoritative:

```text
Discord interaction
  -> Hermes Gateway or verified-gap Discord interaction adapter
  -> normalized slash-command request
  -> command router
  -> Runtime v2 / LLM Wiki service
  -> Discord acknowledgement
```

For meetings, `MeetingRun` remains the business source of truth. Discord
messages are the visible conversation projection. `/save` combines both when
the current thread is linked to a `MeetingRun`.

Existing Phase 21 command schemas and routing are evolved rather than replaced.
Phase 25 safety gates remain in force until the live custom Slash Command path
has been explicitly enabled and verified. Prefer a Hermes-supported custom
command surface. If Hermes exposes no suitable interaction primitive, use a
small Discord adapter that receives application-command interactions and calls
Runtime v2 without duplicating Hermes memory, sessions, provider state, or
meeting orchestration.

## 3. Command Contract

### 3.1 `/save`

`/save` has no required option and saves the current context through these
rules:

1. In a Discord thread, save the thread from its first message through the
   command invocation.
2. If the thread is linked to a `MeetingRun`, classify it as `meeting` and add
   Runtime v2 meeting metadata and available meeting artifacts.
3. Otherwise classify it as `conversation`, including one-to-one bot threads.
4. In a guild text channel outside a thread, reject the request with a short
   instruction to create or enter a thread because the conversation boundary
   is ambiguous.
5. In a DM, use the current Hermes session boundary when it is available;
   otherwise reject safely instead of guessing a start point.

Meeting detection is based on a persisted `thread_id -> meeting_run_id`
relationship, not participant count or keywords.

### 3.2 `/meeting`

Initial supported operations:

```text
/meeting start topic:<topic>
/meeting report
```

`/meeting start` creates a Runtime v2 `MeetingRun` and associates its Discord
thread. `/meeting report` creates the existing on-demand final report for the
current meeting. Meeting completion does not automatically write to Obsidian;
the user invokes `/save` when the discussion is worth retaining.

### 3.3 `/llmwiki`

Initial supported operations:

```text
/llmwiki ingest url:<url>
/llmwiki find query:<query>
/llmwiki note text:<text>
```

`/save` preserves URLs mentioned in conversation but does not ingest their
contents. `/llmwiki ingest` owns source retrieval, source-type detection,
deduplication, raw source storage, Source Summary creation, and index/log
updates.

## 4. Obsidian Storage

The vault root is resolved from `OBSIDIAN_VAULT_PATH`; the deployed value is
expected to be `/home/ubuntu/Obsidian`.

All Discord conversation types share one raw folder:

```text
raw/chat-logs/
wiki/conversations/
wiki/index.md
wiki/log.md
```

Conversation and meeting records are distinguished with frontmatter rather
than separate raw directories:

```yaml
type: conversation | meeting
discord_thread_id: "..."
meeting_run_id: "..."  # meeting only
```

The user-facing filename starts with `YYYY-MM-DD_<thread-title>`. Stable
identity uses Discord guild/channel/thread IDs, so renamed threads and repeated
saves do not create unrelated records.

## 5. Immutable Raw and Repeat Save

Vault policy requires raw inputs to remain immutable. Therefore repeat saves
use two coordinated records:

- Each `/save` writes a new immutable transcript snapshot under
  `raw/chat-logs/`, ending at the command invocation.
- One canonical page under `wiki/conversations/` is updated for that Discord
  thread and points to every raw snapshot.

This preserves evidence while presenting one continuously updated conversation
page in Obsidian. A repeated `/save` is idempotent for the same Discord message
range: if the latest saved message ID has not changed, no new snapshot is
created.

## 6. Document Content

The immutable snapshot contains:

- save timestamp and source range;
- Discord guild, channel, and thread identifiers and display names;
- participant identity metadata;
- chronological message text;
- referenced URLs;
- attachment names, content types, sizes, and Discord URLs;
- secret-redaction markers when applicable.

The canonical page contains:

- title and source links;
- concise summary;
- key ideas;
- decisions;
- unresolved questions and disagreements;
- action items and owners when known;
- participants;
- user perspective section;
- related notes;
- links to immutable transcript snapshots.

The raw transcript is never rewritten by summarization. The canonical page can
be regenerated or updated after later saves.

## 7. Participant Identity

Message text uses stable, readable role names such as `대표`, `비서`,
`콘텐츠팀장`, `아트팀장`, `기술팀장`, `마케팅팀장`, and `품질관리팀장`.
Identification never relies on a mutable Discord nickname alone.

Each participant record keeps:

```yaml
role: 콘텐츠팀장
hermes_profile: aicompanycontent
discord_name: current display name
discord_user_id: stable Discord snowflake
```

Unknown human participants retain their Discord display name and user ID and
are not assigned a company role.

## 8. Meeting-Specific Enrichment

When `type: meeting`, the canonical page also includes:

- `meeting_run_id`, state, agenda, and Runtime v2 timestamps;
- visible team-lead discussion;
- available worker and validation evidence references;
- consensus and dissent;
- unresolved risks;
- decisions and action items;
- requested on-demand report links when those artifacts already exist.

`/save` does not silently run missing workers, validators, reports, or URL
ingestion. It saves available evidence and summarizes the current discussion.
This preserves the existing rule that default meetings do not automatically
generate final reports or Second Brain artifacts.

## 9. Index and Log Policy

Every successful save appends an idempotent entry to `wiki/log.md`.

`wiki/index.md` receives or updates an entry only when at least one condition is
true:

- the record is a Runtime v2 meeting;
- it contains a decision;
- it contains an action item;
- it is explicitly promoted later through an LLM Wiki operation.

Ordinary conversation snapshots remain discoverable through their canonical
page and the log without crowding the main index.

## 10. Attachments and URLs

The first implementation stores attachment metadata and Discord URLs only. It
does not download binary attachments into the vault. URL content retrieval is
owned by `/llmwiki ingest`, not `/save`.

This boundary can be extended later without changing the Slash Command
contract.

## 11. Permissions and Privacy

- Any member permitted to read and participate in the current thread may invoke
  `/save`.
- The bot must be able to view the thread, read message history, and send a
  response in the thread.
- Private-thread and DM records are marked `visibility: private`.
- Administrator permission is never required.
- Tokens, passwords, bearer values, credentials, and uncontrolled mentions are
  redacted before any Obsidian write.
- Error messages and logs must not contain bot tokens, interaction signatures,
  message contents that failed redaction, or local credential paths.

## 12. Long Threads and Recovery

Discord history is fetched using pagination in chronological order. Progress is
checkpointed by thread ID and latest message ID. If collection or summarization
fails, the command reports a recoverable failure and a later `/save` resumes
without overwriting raw evidence.

Large transcripts may be summarized in chunks, followed by a deterministic
final merge. The complete redacted raw transcript is retained even when the
summary must be chunked.

## 13. Discord Response

On success, `/save` responds in the current thread with:

- document title;
- classification (`conversation` or `meeting`);
- number of newly saved messages;
- Obsidian-relative canonical path;
- one-line summary;
- whether the operation created, updated, or found no new messages.

On failure, it states the safe reason and whether retrying is useful. Interaction
acknowledgement must meet Discord's response deadline; long processing continues
through a deferred response or follow-up message.

## 14. Automatic Save Policy

Automatic saving is disabled. Ending a meeting, archiving a Discord thread, or
generating a report does not implicitly save it to Obsidian. `/save` is the
single explicit retention action for both one-to-one and multi-bot discussion.

## 15. Components

Implementation should keep these boundaries:

1. Slash command definitions and registration manifest.
2. Discord interaction normalization and routing.
3. Discord thread-history reader.
4. Context classifier and `MeetingRun` resolver.
5. Obsidian path, raw snapshot, canonical page, index, and log writer.
6. Secret and mention sanitizer shared with the existing knowledge loop.
7. Save orchestration service returning a transport-independent result.
8. Discord response renderer.

The storage and classification layers must not depend directly on Discord HTTP
clients so they can be tested with fixtures.

## 16. Live Registration and Deployment

Slash commands are registered with Discord only after tests and manifest review.
Registration uses existing profile credentials without printing or copying bot
tokens. Guild-scoped registration is used for the first live smoke because it
updates quickly and limits blast radius. Global registration is a later explicit
operation.

The initial live smoke runs in a designated test thread. Existing seven Hermes
Gateway profiles are restarted only after configuration validation. Rollback
removes or restores the registered guild command manifest and returns the
command-surface policy to its prior disabled state.

## 17. Verification

Required automated coverage:

- `/save`, `/meeting`, and `/llmwiki` manifest schemas;
- nested Discord interaction payload parsing;
- thread-only and DM-boundary validation;
- `thread_id -> meeting_run_id` resolution;
- role/profile/Discord-ID participant mapping;
- immutable raw snapshot creation;
- repeated save with no new messages;
- canonical page update and snapshot links;
- conversation versus meeting enrichment;
- secret and mention redaction;
- index/log idempotency;
- URL and attachment metadata preservation;
- pagination, checkpoint, retry, and partial failure;
- Discord response rendering;
- Runtime v2 meeting, on-demand export, knowledge-loop, and gateway regressions.

Before live registration, run focused Python tests, the established Runtime v2
regression suite, `npm run typecheck`, `git diff --check`, and the repository's
secret scan. After registration, verify one ordinary conversation save, one
Runtime v2 meeting save, one repeated no-change save, and one safe failure.

## 18. Acceptance Criteria

The design is complete when all of the following are true:

1. The user only needs `/save`, regardless of participant count or meeting type.
2. Discord native threads remain the conversation boundary.
3. Meetings are identified by persisted `MeetingRun` linkage.
4. All raw transcripts live under `raw/chat-logs/` and remain immutable.
5. Repeated saves update one canonical conversation page without duplicate
   snapshots for an unchanged message range.
6. Meetings include available Runtime v2 evidence without triggering unrelated
   work.
7. Every save is logged; only important records enter the main index.
8. URLs are preserved but ingested only through `/llmwiki ingest`.
9. Participant display is readable and identity remains traceable through
   Hermes profile and Discord user ID.
10. Secrets and unsafe mentions cannot be written to the vault.
11. The live command path respects Hermes-first and Phase 25 safety policy.
12. Existing Runtime v2 behavior remains regression-clean.
