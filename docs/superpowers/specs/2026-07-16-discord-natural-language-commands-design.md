# Discord Natural-Language Command Surface

Status: APPROVED DESIGN BASELINE
Date: 2026-07-16 KST
Canonical architecture: `docs/runtime-architecture-v2.md`

## 1. Decision

Use separate top-level Hermes plugin commands instead of Discord subcommand
groups. Each command accepts one optional free-form text field supplied by the
current official `PluginContext.register_command()` API.

```text
/meeting-start <natural-language meeting topic>
/meeting-report <optional natural-language report request>

/llmwiki-ingest <natural-language request containing a URL>
/llmwiki-find <natural-language search request>
/llmwiki-note <natural-language note>

/archive
```

Discord may visibly label the free-form field as `args`. The user does not type
the label. They select the command and type natural language into the field.
Structured Discord subcommands and named options are deferred until Hermes
offers a stable plugin API for them.

This decision supersedes the grouped `/meeting start|report` and
`/llmwiki ingest|find|note` command shapes in
`2026-07-13-discord-slash-commands-obsidian-design.md`. The `/archive` behavior
and manual retention policy remain unchanged.

## 2. Meeting Commands

### 2.1 `/meeting-start`

The complete free-form text is the meeting topic and objectives. The command:

1. rejects blank input with concise usage guidance;
2. creates a Runtime Architecture v2 `MeetingRun`;
3. creates or resolves the Discord meeting thread through the existing
   projection path;
4. persists the `thread_id -> meeting_run_id` relationship;
5. starts the existing meeting workflow without duplicating orchestration in
   the plugin; and
6. replies with the meeting ID, thread, initial state, and a concise summary.

Example user intent:

```text
/meeting-start 신제품 마케팅 방향에 대해서 회의하자
```

### 2.2 `/meeting-report`

The command resolves the current thread's `MeetingRun`. Blank input requests the
default on-demand report. Nonblank text customizes presentation or emphasis but
does not alter meeting evidence.

```text
/meeting-report
/meeting-report 핵심 결정과 담당자별 할 일을 브리핑해줘
```

The command fails safely outside a thread or when no MeetingRun is linked.
Generating a report does not write to Obsidian.

## 3. LLM Wiki Commands

### 3.1 `/llmwiki-ingest`

Extract one supported URL from the free-form request, classify its source type,
deduplicate it, retain raw source material, create or update a Source Summary,
and update the LLM Wiki index and log. Reject missing, multiple, malformed, or
unsupported URLs without guessing.

### 3.2 `/llmwiki-find`

Use the complete free-form text as the retrieval query. Return concise ranked
matches with vault-relative paths and short evidence snippets. It is read-only.

### 3.3 `/llmwiki-note`

Use the complete free-form text as a manually authored note. Sanitize secrets
and unsafe mentions, persist the raw note and canonical Markdown page, and
update the LLM Wiki log. Blank notes are rejected.

## 4. Archive Boundary

`/archive` remains the single explicit retention action for Discord
conversations and meetings.

- Runtime meeting state and local meeting artifacts persist automatically.
- Obsidian conversation and meeting records do not persist automatically.
- In a Discord thread, `/archive` saves through the invocation boundary.
- A linked MeetingRun produces a meeting-classified document with available
  evidence.
- A normal guild channel remains rejected because it has no reliable
  conversation boundary.
- `/meeting-report` never invokes `/archive` implicitly.

There is no `/meeting-save` command.

## 5. Plugin Architecture

The existing `ai-agent-commands` plugin owns only transport adaptation:

- register the five natural-language commands plus `/archive`;
- pass the current Hermes profile, command context, and host LLM where needed;
- return sanitized user-facing responses; and
- delegate all business behavior to transport-neutral Runtime v2 services.

Meeting and LLM Wiki services must not import Discord adapter classes. The
plugin must not duplicate MeetingRun state transitions, report generation,
source ingestion, vault writing, or retrieval logic.

## 6. Error Handling

Commands fail closed with concise Korean guidance for:

- blank or malformed input;
- invocation outside Discord where Discord context is required;
- missing thread or MeetingRun linkage;
- unsupported or ambiguous URLs;
- unavailable source history, model, or vault;
- permission or authorization failures; and
- concurrent duplicate invocations.

Responses and logs must not expose tokens, credentials, raw exceptions, or
uncontrolled mentions.

## 7. Delivery Order

1. Implement and verify `/meeting-start`.
2. Implement and verify `/meeting-report`.
3. Verify meeting-thread `/archive` classification and manual retention.
4. Implement `/llmwiki-ingest`.
5. Implement `/llmwiki-find`.
6. Implement `/llmwiki-note`.
7. Install the same plugin revision across all seven Hermes profiles.
8. Restart gateways sequentially and verify Discord command registration.

## 8. Acceptance Criteria

1. Users provide natural-language text without learning `topic:`, `query:`, or
   subcommand syntax.
2. `/meeting-start` creates and links one durable MeetingRun.
3. `/meeting-report` uses the current linked MeetingRun and supports an optional
   natural-language emphasis request.
4. Meeting reports and completion never auto-write to Obsidian.
5. `/archive` remains the only explicit Discord conversation retention action.
6. LLM Wiki ingestion, retrieval, and notes remain separate top-level commands.
7. Every command uses the official Hermes plugin command path without Hermes
   Core modification.
8. Tests cover parsing, idempotency, authorization, safe failures, domain
   delegation, and Discord response rendering.
