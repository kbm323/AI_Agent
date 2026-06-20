# Loop Context Compression Policy

> Legacy note: 이 문서는 과거 loop role 명칭을 보존한다. OpenClaw 관련 필드는 Runtime Architecture v2 기준에서는 legacy turn/source labels로만 해석한다.

Schema: `loop-context-compression-policy.v1`

## Retained Fields

- `tasks.user_request` (retained): Keep the original user request as the replay and audit source of truth.
- `turns.content` (retained): Keep complete OpenClaw, Hermes, final synthesis, and escalation text outside normal loop prompts.
- `decisions.reasons` (retained): Keep exact escalation and convergence reasons for deterministic failure analysis.

## Summarized Fields

- `tasks.user_request_summary` (summarized): Expose a bounded summary of the request to each meeting iteration.
- `turns.visibleSummary` (summarized): Expose role, kind, round, and bounded summary instead of raw turn content.
- `compressedLoopContext.acceptedFeedback` (summarized): Carry only actionable Hermes feedback that OpenClaw accepted.
- `compressedLoopContext.rejectedFeedback` (summarized): Carry only rejected feedback labels and rationale summaries to prevent repeated debate.
- `compressedLoopContext.escalationReasons` (summarized): Carry concise blockers when convergence fails or user input is required.

## Dropped Fields

- `turns.content.rawPromptEcho` (dropped): Prompt echoes are redundant after raw turn storage and visible summaries exist.
- `turns.content.intermediateScratchpad` (dropped): Private scratchpad-style text must not be replayed into meeting context.
- `duplicatePriorRoundFullText` (dropped): Older full-text rounds are represented by summaries and retained only in raw storage.

## Iteration Boundaries

- `request_analysis_to_openclaw`: starts after `task_breakdown_and_role_routing`, ends before `openclaw_owner_draft`, carries `tasks.user_request_summary`, `role_routes`, `active_task_ids`
- `openclaw_to_hermes`: starts after `openclaw_owner_draft`, ends before `hermes_review`, carries `tasks.user_request_summary`, `latest_openclaw_summary`, `accepted_constraints`
- `hermes_to_next_openclaw_or_final`: starts after `hermes_review`, ends before `next_openclaw_draft_or_final_synthesis`, carries `tasks.user_request_summary`, `latest_openclaw_summary`, `latest_hermes_verdict`, `acceptedFeedback`, `rejectedFeedback`, `escalationReasons`

## Deterministic Ordering

- `schemaVersion`
- `retainedFields.path`
- `summarizedFields.path`
- `droppedFields.path`
- `iterationBoundaries.name`
- `validationSections`
