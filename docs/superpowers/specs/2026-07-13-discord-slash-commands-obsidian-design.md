# Discord Commands, Obsidian, and LLM Wiki Design

Status: APPROVED CANONICAL DESIGN
Created: 2026-07-13 KST
Last updated: 2026-07-16 KST
Canonical architecture: `docs/runtime-architecture-v2.md`

## 1. Decision

The live user-facing command surface uses separate top-level Hermes commands.
Each command accepts the one optional free-form text field supported by the
official `PluginContext.register_command()` API:

```text
/meeting-start <natural-language meeting topic>
/meeting-report <optional natural-language report request>
/llmwiki-ingest <natural-language request containing a URL>
/llmwiki-find <natural-language search request>
/llmwiki-note <natural-language note>
/archive
```

Discord may label the free-form field as `args`; the user selects the command
and enters natural language without typing that label. Structured subcommands
and named options remain deferred until Hermes exposes a stable plugin API.

`/archive thread`, `/archive conversation`, and `/archive meeting` are not
separate commands. The user always invokes `/archive`; the system determines
the source and document type from Discord context and any linked `MeetingRun`.

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
messages are the visible conversation projection. `/archive` combines both when
the current thread is linked to a `MeetingRun`.

Existing Phase 21 command schemas and routing are evolved rather than replaced.
Phase 25 safety gates remain in force until the live custom Slash Command path
has been explicitly enabled and verified. Prefer a Hermes-supported custom
command surface. If Hermes exposes no suitable interaction primitive, use a
small Discord adapter that receives application-command interactions and calls
Runtime v2 without duplicating Hermes memory, sessions, provider state, or
meeting orchestration.

The existing `ai-agent-commands` plugin owns transport adaptation only: command
registration, normalized Hermes context, host LLM access where required, and
sanitized responses. Transport-neutral Runtime v2 services own meeting state,
reporting, ingestion, vault writes, and retrieval. Domain services must not
import Discord adapter classes.

## 3. Command Contract

### 3.1 `/archive`

`/archive` has no required option and saves the current context through these
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

### 3.2 Meeting commands

`/meeting-start` treats the complete free-form text as the meeting topic and
objectives. It rejects blank input, creates one Runtime v2 `MeetingRun`, creates
or resolves the Discord meeting thread through the existing projection path,
and persists the `thread_id -> meeting_run_id` relationship.

```text
/meeting-start <natural-language meeting topic>
/meeting-report <optional natural-language report request>
```

`/meeting-report` resolves the current thread's linked `MeetingRun`. Blank input
requests the default on-demand report. Nonblank text customizes presentation or
emphasis without changing meeting evidence. It fails safely outside a linked
meeting thread. Meeting completion and report generation never write to
Obsidian automatically; the user invokes `/archive` when the discussion is
worth retaining.

### 3.3 LLM Wiki commands

Each operation is a separate top-level command with free-form natural-language
input:

```text
/llmwiki-ingest <natural-language request containing one URL>
/llmwiki-find <natural-language search request>
/llmwiki-note <natural-language note>
```

`/archive` preserves URLs mentioned in conversation but does not ingest their
contents. `/llmwiki-ingest` owns URL extraction, source retrieval, source-type
detection, deduplication, raw source storage, Source Summary creation, and
index/log updates. Missing, multiple, malformed, and unsupported URLs are
rejected without guessing.

`/llmwiki-note` sanitizes and stores the complete free-form note using the
existing raw and canonical Markdown policy. `/llmwiki-find` is read-only and
searches the complete Obsidian vault through the QMD policy in Section 10.

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

- Each `/archive` writes a new immutable transcript snapshot under
  `raw/chat-logs/`, ending at the command invocation.
- One canonical page under `wiki/conversations/` is updated for that Discord
  thread and points to every raw snapshot.

This preserves evidence while presenting one continuously updated conversation
page in Obsidian. A repeated `/archive` is idempotent for the same Discord message
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

`/archive` does not silently run missing workers, validators, reports, or URL
ingestion. It saves available evidence and summarizes the current discussion.
This preserves the existing rule that default meetings do not automatically
generate final reports or Second Brain artifacts.

## 9. Index and Log Policy

Every successful `/archive`, `/llmwiki-ingest`, or `/llmwiki-note` appends an
idempotent entry to `wiki/log.md`.

`wiki/index.md` receives or updates an entry only when at least one condition is
true:

- the record is a Runtime v2 meeting;
- it contains a decision;
- it contains an action item;
- it is explicitly promoted later through an LLM Wiki operation.

Ordinary conversation snapshots remain discoverable through their canonical
page and the log without crowding the main index.

## 10. QMD Search Policy

QMD is a search layer over the existing vault structure; it does not replace or
reorganize `raw/`, `wiki/`, `wiki/index.md`, or `wiki/log.md`.

- Collection name: `obsidian`
- Collection root: `/home/ubuntu/Obsidian`
- Include pattern: `**/*.md`
- Query path: `qmd query "<query>" --json -c obsidian`
- Primary retrieval: hybrid search with ranking and evidence snippets
- Fallback: BM25 keyword search when embeddings or reranking are unavailable

The collection includes the whole vault so meetings, conversations, source
records, summaries, and ordinary notes can be found through one command. QMD
configuration, indexes, downloaded models, and caches remain on server-local
storage outside the Google Drive-mounted vault.

Korean retrieval uses Qwen3 Embedding 0.6B through
`QMD_EMBED_MODEL=hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf`
instead of QMD's default embedding model, whose CJK coverage is limited. Model
installation and ARM64 compatibility must pass an explicit installation probe
before live use.

Freshness uses a hybrid policy:

1. `/archive`, `/llmwiki-ingest`, and `/llmwiki-note` enqueue one coalesced
   background `qmd update` and incremental `qmd embed` job after a successful
   vault write.
2. `/llmwiki-find` performs a serialized stale check and fast incremental
   `qmd update` before querying.
3. Search never waits for a full embedding rebuild. It uses the latest available
   embeddings or falls back to BM25.
4. A periodic reconciliation job repairs missed background updates.

Concurrent update jobs use one lock and debounce duplicate requests. Search
results contain rank, vault-relative path, and a short evidence snippet. Search
is read-only and must not expose local absolute paths or QMD internal errors.
The Hermes plugin calls the QMD CLI through a transport-neutral adapter; QMD MCP
may be added later for exploratory agent use but is not on the Slash Command
critical path.

## 11. Attachments and URLs

The first implementation stores attachment metadata and Discord URLs only. It
does not download binary attachments into the vault. URL content retrieval is
owned by `/llmwiki-ingest`, not `/archive`.

This boundary can be extended later without changing the Slash Command
contract.

## 12. Permissions and Privacy

- Any member permitted to read and participate in the current thread may invoke
  `/archive`.
- The bot must be able to view the thread, read message history, and send a
  response in the thread.
- Private-thread and DM records are marked `visibility: private`.
- Administrator permission is never required.
- Tokens, passwords, bearer values, credentials, and uncontrolled mentions are
  redacted before any Obsidian write.
- Error messages and logs must not contain bot tokens, interaction signatures,
  message contents that failed redaction, or local credential paths.

## 13. Error Handling and Recovery

Commands fail closed with concise Korean guidance for blank or malformed input,
missing Discord context, missing thread or `MeetingRun` linkage, unsupported or
ambiguous URLs, unavailable history, model, QMD, or vault, permission failures,
and concurrent duplicate invocations. Responses and logs must not expose raw
exceptions, credentials, message content that failed sanitization, or local
absolute paths. A failure states whether retrying is useful.

Discord history is fetched using pagination in chronological order. Progress is
checkpointed by thread ID and latest message ID. If collection or summarization
fails, the command reports a recoverable failure and a later `/archive` resumes
without overwriting raw evidence.

Large transcripts may be summarized in chunks, followed by a deterministic
final merge. The complete redacted raw transcript is retained even when the
summary must be chunked.

## 14. Discord Response

On success, `/archive` responds in the current thread with:

- document title;
- classification (`conversation` or `meeting`);
- number of newly saved messages;
- Obsidian-relative canonical path;
- one-line summary;
- whether the operation created, updated, or found no new messages.

On failure, it states the safe reason and whether retrying is useful. Interaction
acknowledgement must meet Discord's response deadline; long processing continues
through a deferred response or follow-up message.

## 15. Automatic Save Policy

Automatic saving is disabled. Ending a meeting, archiving a Discord thread, or
generating a report does not implicitly save it to Obsidian. `/archive` is the
single explicit retention action for both one-to-one and multi-bot discussion.

## 16. Components

Implementation should keep these boundaries:

1. Slash command definitions and registration manifest.
2. Discord interaction normalization and routing.
3. Discord thread-history reader.
4. Context classifier and `MeetingRun` resolver.
5. Obsidian path, raw snapshot, canonical page, index, and log writer.
6. Secret and mention sanitizer shared with the existing knowledge loop.
7. URL source classifier, retriever, deduplicator, and Source Summary service.
8. QMD CLI adapter, freshness scheduler, and single update lock.
9. Archive, LLM Wiki, and meeting orchestration services returning
   transport-independent results.
10. Discord response renderer.

The storage and classification layers must not depend directly on Discord HTTP
clients so they can be tested with fixtures.

## 17. Live Registration and Deployment

Slash commands are registered with Discord only after tests and manifest review.
Registration uses existing profile credentials without printing or copying bot
tokens. Guild-scoped registration is used for the first live smoke because it
updates quickly and limits blast radius. Global registration is a later explicit
operation.

The initial live smoke runs in a designated test thread. Existing seven Hermes
Gateway profiles are restarted only after configuration validation. Rollback
removes or restores the registered guild command manifest and returns the
command-surface policy to its prior disabled state.

## 18. Delivery Order

1. Confirm the shared Hermes command adapter, natural-language argument
   handling, authorization, safe responses, background jobs, and locking.
2. Add and probe the single QMD `obsidian` collection and retrieval adapter.
3. Implement and verify `/llmwiki-ingest`, `/llmwiki-note`, and
   `/llmwiki-find` against the existing vault structure.
4. Implement and verify `/meeting-start` and `/meeting-report` through Runtime
   v2 without duplicating meeting orchestration.
5. Connect successful `/archive` writes to the shared QMD update scheduler;
   retain all existing `/archive` behavior and tests.
6. Run focused and Runtime v2 regression tests, then smoke-test the Assistant
   profile.
7. Install the same plugin revision across all seven profiles and restart
   gateways sequentially.

## 19. Verification

Required automated coverage:

- `/archive`, `/meeting-start`, `/meeting-report`, and all three `/llmwiki-*`
  command registrations;
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
- single-collection QMD configuration and vault-relative result rendering;
- incremental update, coalesced embedding, locking, stale-index recovery, and
  BM25 fallback;
- Korean query and evidence-snippet retrieval fixtures;
- ingest source classification, deduplication, and unsupported URL handling;
- natural-language note persistence and blank-input rejection;
- URL and attachment metadata preservation;
- pagination, checkpoint, retry, and partial failure;
- Discord response rendering;
- Runtime v2 meeting, on-demand export, knowledge-loop, and gateway regressions.

Before live registration, run focused Python tests, the established Runtime v2
regression suite, `npm run typecheck`, `git diff --check`, and the repository's
secret scan. After registration, verify all five natural-language commands, one
ordinary conversation archive, one Runtime v2 meeting archive, one repeated
no-change archive, one Korean QMD query, and one safe failure.

## 20. Acceptance Criteria

The design is complete when all of the following are true:

1. The user only needs `/archive`, regardless of participant count or meeting type.
2. Discord native threads remain the conversation boundary.
3. Meetings are identified by persisted `MeetingRun` linkage.
4. All raw transcripts live under `raw/chat-logs/` and remain immutable.
5. Repeated saves update one canonical conversation page without duplicate
   snapshots for an unchanged message range.
6. Meetings include available Runtime v2 evidence without triggering unrelated
   work.
7. Every successful write is logged; only important records enter the main
   index.
8. URLs are preserved but ingested only through `/llmwiki-ingest`.
9. Participant display is readable and identity remains traceable through
   Hermes profile and Discord user ID.
10. Secrets and unsafe mentions cannot be written to the vault.
11. The live command path respects Hermes-first and Phase 25 safety policy.
12. One QMD `obsidian` collection searches every Markdown file in the vault
    without changing the existing folder structure.
13. Successful writes schedule background index updates, while search performs
    a fast freshness check and falls back safely when embeddings are stale.
14. Existing Runtime v2 behavior remains regression-clean.
