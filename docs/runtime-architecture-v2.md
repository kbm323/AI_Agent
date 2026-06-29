# AI Virtual Entertainment Company — Runtime Architecture v2

> Canonical final-system document for the current AI Virtual Entertainment Company runtime.
> 이 파일이 최신 최종 아키텍처 기준 문서다.
>
> 기준 Seed: `seed_176a489b1d25`
> Interview: `interview_20260619_051314`
> Document status: CURRENT FINAL BASELINE
> Last updated: 2026-06-27 KST
> Last live Discord verification: 2026-06-25 02:43 KST
> Decision precedence: latest user decisions and live Discord verification > this file > `docs/system-design-decisions.md` historical decision log > phase result documents > README.

## 1. Decision

Runtime Architecture v2의 root aggregate는 `MeetingRun`이다.

The implementation direction is **Hermes-native-first**.
AI_Agent must reuse Hermes built-in runtime capabilities wherever they already
exist, and only add domain-specific Coordinator/Adapter/Schema logic that
Hermes does not provide.

```text
Hermes provides the platform.
AI_Agent provides the MeetingRun domain coordinator.

Do not rebuild Hermes Gateway, memory, sessions, skills, provider/auth,
approvals, cron/background execution, or Kanban unless a verified gap exists.
```

```text
Discord Message / Mention / Command
  -> MeetingRun 생성
  -> Routing
  -> Queue
  -> Meeting / Worker / Validation / Report phases
  -> Discord projection
  -> Decision log / recovery checkpoint
```

Discord thread/message는 source of truth가 아니라 사용자-facing projection이다.
Queue Job은 실행 단위이고, Hermes session은 runtime context다.
업무 도메인의 추적/복구/보고 기준은 항상 `meeting_run_id`다.
Hermes-native resources such as session IDs, cron jobs, background processes,
Kanban tasks, skills, and memory entries may be referenced by MeetingRun, but
they are not replaced by AI_Agent-specific duplicates.

## 2. Design Principle

```text
Discord는 무대다.
Hermes는 운영본부다.
opencode-go는 직원 실행 계층이다.
GLM/Codex는 감사실이다.
MeetingRun은 모든 회의/작업/검증/보고의 장부다.
```

Implementation principle:

```text
Use Hermes first.
Extend with adapters second.
Create custom infrastructure only when Hermes has no fitting primitive.
```

## 2.1 User Idea Ledger — Current Final Baseline

This section exists to keep the user's latest product/system ideas in one place.
When new ideas are accepted, update this section first or together with the
relevant detailed architecture section.

```text
1. Final goal 기준 설계
   - Do not shrink the system to an MVP during design.
   - Implementation may be phased, but architecture targets the complete final company.

2. Hermes-first operating model
   - Hermes is the platform/operating layer.
   - AI_Agent owns only the virtual-company domain coordinator, schemas, adapters, and policies.
   - Avoid Hermes Core modifications unless a verified gap exists.

3. MeetingRun as source of truth
   - Discord messages/threads are user-facing projections.
   - The durable business object is meeting_run_id / MeetingRun.
   - Recovery, audit, validation, reports, and Second Brain ingestion attach to MeetingRun.

4. opencode-go-first workers and GPT escalation
   - opencode-go is the unified worker/validator/auditor wrapper.
   - Qwen handles routing/classification where needed.
   - Discord-facing team leads use role-fit opencode-go models by default.
   - GLM is the default validator/contradiction/risk-review model role.
   - Codex/GPT-5.5 is not a daily team-lead default; it is the gated final auditor, high-confidence reviewer, and irreversible-risk escalation path.
   - Model order means quota fallback order, not quality ranking.

5. OpenClaw removed
   - OpenClaw is not part of Runtime Architecture v2.
   - Remaining OpenClaw mentions are historical/legacy evidence only unless explicitly reintroduced.

6. Discord surface is intentionally small
   - Live Discord-facing bots = 7 accounts in the Entertainment guild.
   - Personal assistant: `aicompanyassistant` / live username `비서`.
   - Company team leads: CEO, Content, Art, Tech, Marketing, Quality/Validation.
   - 29-role registry is the internal company org chart, not 29 Discord bot accounts.
   - Business Support / Legal / Finance / HR are internal roles, not live Discord bots by default.
   - Discord channels are company operation surfaces, not merely bot rooms; the current live channel function matrix is `docs/discord-channel-function-matrix.md` and `src/runtime_architecture_v2/discord_channels.py`.

7. Personal assistant is separate from the company org chart
   - The assistant handles user intake, personal support, personal Second Brain, schedules/briefings, and action item extraction.
   - It can reference company outputs, but it is not one of the 29 company roles.

8. Company and personal knowledge are separated
   - Company Second Brain stores company strategy/research/meeting decisions.
   - Personal Second Brain stores personal notes/goals/schedules/reminders/user-support context.
   - Hermes memory stays compact and durable only.

8.1 Notion-backed schedule and idea management
   - The shared Notion internal connection is available to all 7 live bot profiles through profile `.env` files.
   - `NOTION_SECOND_BRAIN_ROOT_PAGE_ID` points to `SECOND BRAIN OS`.
   - `NOTION_SCHEDULE_DATA_SOURCE_ID` points to the `할 일` data source.
   - `NOTION_IDEA_DATA_SOURCE_ID` points to the `노트` data source.
   - Worker provider execution is Hermes-first; provider/auth/model credentials stay in Hermes runtime. Notion adapters may still use `NOTION_API_KEY` / `NOTION_API_TOKEN` aliases, but worker model credentials must not be copied into subprocess env.
   - AI-created schedule/idea records should stay under `SECOND BRAIN OS`; use PAT/MCP only when explicitly choosing a broader user-scoped access model.

9. Safety posture
   - Bots are mention-gated by default.
   - No global free-response channels by default.
   - No Administrator permission by default.
   - Discord live mutations require explicit purpose; document/defer when not required.

10. Phase discipline
   - Before implementation: plan and acceptance criteria.
   - After implementation: Ouroboros QA / independent review / security scan / tests / lint / commit / push / remote verification.
   - Phase completion reports must include remaining phases.
```

## 3. Runtime Process Topology

```text
[Discord Gateway]
  receives Hermes-native mentions / supported commands / replies
        |
        v
[CEO/Coordinator Bot Adapter]
  normalizes input into TriggerRequest
        |
        v
[Hermes Meeting Coordinator]
  creates MeetingRun
  classifies request
  invokes Qwen Router
        |
        v
[Hermes-native Scheduling Layer]
  prefers Hermes Kanban / background / cron primitives
  applies MeetingRun priority policy only as domain metadata
        |
        v
[Runtime Orchestrator]
  drives sub state machines:
  - MeetingPhase
  - WorkerTask
  - ValidationCycle
  - ReportPhase
        |
        +--> [opencode-go Worker Runner]
        +--> [GLM Validator]
        +--> [Codex Auditor]
        |
        v
[Discord Projection Layer]
  posts selected events as team-lead bot messages
        |
        v
[Storage]
  project-local MeetingRun artifacts only
  Hermes-owned memory/session/skill/provider state remains in Hermes
```

## 4. Discord Bot Topology

Actual Discord-facing bots are projection/intake endpoints, not source-of-truth
state holders. Hermes Discord Gateway remains the preferred transport layer.
Additional bot accounts should not introduce independent state, memory, routing,
or command infrastructure unless Hermes Gateway cannot support the required
interaction pattern.

Current live topology is exactly 7 Discord-facing bots in the `Entertainment`
Discord guild. This was verified through Discord REST on 2026-06-25 02:43 KST
using each profile's bot token without printing secrets.

The 7 live accounts are 1 personal assistant plus 6 company team-lead bots. The
29-role registry is an internal org chart and does not mean 29 Discord bot
accounts.

| Hermes profile | Live Discord username | Live home channel | Responsibility | User @mention | Projection role |
|---|---|---|---|---:|---|
| `aicompanyassistant` | `비서` | `#일일-브리핑` | personal assistant / secretary: user intake, private/personal support, personal Second Brain, daily/weekly briefing, action-item extraction | yes | assistant/intake layer; not a company department role |
| `aicompanyceo` | `대표` | `#회의실-전략결정` | CEO/Coordinator Bot: company default entrypoint, routing, final report | yes | final synthesis, meeting open/close |
| `aicompanycontent` | `콘텐츠팀장` | `#콘텐츠-메인` | Content Lead Bot: content, script, editing, thumbnail direction | yes | content team opinions/consensus |
| `aicompanyart` | `아트팀장` | `#아트-메인` | Art Lead Bot: concept, character, rigging, animation, VFX, stage | yes | art team opinions/risks |
| `aicompanytech` | `기술팀장` | `#기술-메인` | Tech Lead Bot: R&D, pipeline, infrastructure, development, automation | yes | technical feasibility/execution status |
| `aicompanymarketing` | `마케팅팀장` | `#마케팅-메인` | Marketing Lead Bot: SNS, community, IP, goods, growth | yes | market/fan/growth perspective |
| `aicompanyquality` | `품질관리팀장` | `#전체-리뷰` | Quality/Validation Bot: GLM/Codex risk and final validation projection | yes | verdict, blockers, correction requests |

Live safety settings verified on 2026-06-25:

```text
Guild: Entertainment (1505600166676271244)
All 7 bot tokens returned /users/@me = 200.
All 7 bot accounts are members of the Entertainment guild.
All 7 configured home channels are accessible.
All 7 profiles use DISCORD_REQUIRE_MENTION=true.
All 7 profiles use DISCORD_THREAD_REQUIRE_MENTION=true.
All 7 profiles have no DISCORD_FREE_RESPONSE_CHANNELS configured.
Each member currently has 1 Discord role.
```

Internal specialists are workers, not Discord bot accounts.
Examples: content_pd, script_writer, concept_artist, rigger, pipeline_rd,
web_app_developer, legal_reviewer, data_analyst, business_support_lead,
partnership_manager, finance_hr, qa_specialist.

Research and support are capabilities delegated to the relevant team or internal
role, not separate Discord lead bots by default:

```text
technical research / model-tool-API evaluation -> Tech Lead
market research / newsletter strategy / audience insight -> Marketing Lead
content reference research / editorial angle -> Content Lead
visual reference / style / animation research -> Art Lead
legal / contract / finance / policy research -> internal Business Support role via 대표 or relevant lead
published or decision-critical claims -> Validation/Audit
personal schedules / private notes / personal briefing -> `비서` personal assistant (`aicompanyassistant`)
```

The Personal Assistant is a separate user-support/intake layer, not part of the
29-role company org chart. It may use a personal Second Brain for schedules,
reminders, private notes, and user support, while company work remains under the
company team-lead structure.

## 4.1 Discord-facing Model Assignment

The 7 live Hermes profiles are persistent Discord persona/gateway endpoints.
Their default models should be selected for their daily conversation and routing
responsibility, while `MeetingRun.worker_tasks[].model_policy` remains the more
granular source for internal specialist execution.

GPT-5.5/Codex is intentionally **not** the primary model for every bot. It is a
scarce high-confidence auditor used after role-specialized opencode-go models
and GLM validation when the decision is high-risk, externally visible, or
technically irreversible.

| Hermes profile | Live username | Primary model | Fallback chain | GPT/Codex role |
|---|---|---|---|---|
| `aicompanyassistant` | `비서` | `opencode-go/qwen3.7-plus` | `opencode-go/deepseek-v4-flash` | None by default; escalate only for private-data, account, or irreversible user-support risk |
| `aicompanyceo` | `대표` | `opencode-go/qwen3.7-max` | `opencode-go/qwen3.7-plus` -> `opencode-go/glm-5.2` | Final-arbiter escalation for strategy, budget, brand, external commitments, and model-conflict resolution |
| `aicompanycontent` | `콘텐츠팀장` | `opencode-go/kimi-k2.6` | `opencode-go/qwen3.7-plus` | External-publication or brand-risk review only |
| `aicompanyart` | `아트팀장` | `opencode-go/minimax-m3` | `opencode-go/minimax-m2.7` -> `opencode-go/deepseek-v4-pro` | Asset/license/brand-risk review only |
| `aicompanytech` | `기술팀장` | `opencode-go/deepseek-v4-pro` | `opencode-go/deepseek-v4-flash` -> `opencode-go/kimi-k2.7-code` | Code/security/data-loss/deployment audit |
| `aicompanymarketing` | `마케팅팀장` | `opencode-go/qwen3.7-max` | `opencode-go/qwen3.7-plus` -> `opencode-go/kimi-k2.6` | Public campaign, partnership, or reputation-risk review |
| `aicompanyquality` | `품질관리팀장` | `opencode-go/glm-5.2` | `opencode-go/glm-5.1` | Primary escalation endpoint for `openai-codex/gpt-5.5` final audit |

Runtime interpretation:

```text
normal production work:
  role-specialized opencode-go model -> GLM validation if required -> report

critical or irreversible work:
  role-specialized opencode-go model -> GLM validation -> GPT-5.5/Codex audit -> user escalation when needed
```

GPT-5.5/Codex escalation triggers:

```text
- code, automation, security, deployment, or data-loss risk
- Discord permission, token, channel, gateway, or live-boundary mutation
- budget, legal, contract, IP, brand, public-campaign, or external publication risk
- GLM returns non-pass, low confidence, or explicit escalation recommendation
- team-lead models disagree on a material conclusion
- production pilot/runbook gate, release, or rollback decision
- user explicitly requests final audit, independent review, or "really verify it"
```

Operational constraints:

```text
- Do not set all 7 bot profiles to GPT-5.5 by default.
- Do not spend premium opencode-go models on high-volume assistant/intake traffic.
- Keep Codex/GPT as an escalation lane so quota remains available for the decisions where it matters most.
- Changing profile model/fallback config requires profile-local config edits and gateway restart; do it after active pilots finish.
```

## 4.2 Command Surface

The command surface is Hermes-native first.
Standalone Discord slash commands such as `/meeting`, `/cancel`, `/status`,
or `/summon` are not core requirements unless Hermes Gateway officially
supports the required custom slash-command surface or a separate Discord
adapter is deliberately added.

Priority order:

```text
1. Hermes existing Discord command and gateway behavior
2. Hermes-supported custom skill/command surface
3. Bot mention natural-language command
4. Separate Discord Adapter that implements standalone slash commands
```

Default meeting initiation:

```text
@Hermes meeting: 버추얼 아이돌 뮤비 회의 열어줘
```

If Hermes Gateway supports the needed slash surface:

```text
/hermes meeting agenda:"버추얼 아이돌 뮤비 회의"
/hermes cancel meeting_run_id:"mr_..."
/hermes status meeting_run_id:"mr_..."
```

Optional standalone adapter commands:

```text
/meeting
/cancel
/status
/summon
```

These standalone commands are adapter features, not the core architecture.

Design rule:

```text
Hermes-first architecture requires Hermes-first Discord UI.
Command handling must follow what Hermes Gateway actually supports before
inventing independent Discord slash commands.
Default command interpretation should be a Hermes skill / natural-language
intent layer, not a separate Discord command framework.
```

## 5. MeetingRun Top-Level State Machine

Top-level state is intentionally small.
Detailed behavior lives in sub state machines.

```text
created
  -> classified
  -> routed
  -> queued
  -> active
  -> validating
  -> reporting
  -> completed
```

Terminal and interruption states:

```text
failed
cancelled
paused
```

Valid top-level states:

```text
created | classified | routed | queued | active | validating | reporting | completed | failed | cancelled | paused
```

## 6. Sub State Machines

### 6.1 MeetingPhase

Used when the request requires multi-role discussion.

```text
agenda_built
  -> participants_selected
  -> round_1_opinions
  -> round_2_rebuttals
  -> consensus_built
  -> consensus_ready | escalation_required
```

Fast-path requests may skip MeetingPhase.

### 6.2 WorkerTask

Used for opencode-go or Hermes wrapper execution.

```text
created
  -> packet_written
  -> dispatched
  -> running
  -> output_collected
  -> completed | failed | timed_out
```

### 6.3 ValidationCycle

```text
pending
  -> glm_review
  -> codex_review_if_required
  -> approve | revise | reject | escalate
```

Codex is risk/importance gated.
GLM is the default contradiction/risk reviewer.

### 6.4 ReportPhase

```text
drafting
  -> discord_posting
  -> decision_log_written
  -> memory_update_if_durable
  -> done
```

Only durable decisions go to Hermes memory.
Raw meeting logs stay in project storage.

## 7. Core Schemas

### 7.1 MeetingRun

```json
{
  "meeting_run_id": "mr_20260619_xxxxx",
  "top_level_state": "created",
  "trigger_request": {},
  "routing_result": {},
  "queue_policy": {},
  "meeting_phase": {},
  "worker_tasks": [],
  "validation_cycles": [],
  "report_phase": {},
  "decision_log_refs": [],
  "recovery_checkpoint_ref": "",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

### 7.2 TriggerRequest

```json
{
  "source": "discord",
  "guild_id": "",
  "channel_id": "",
  "thread_id": "",
  "message_id": "",
  "author_id": "",
  "mentioned_bot": "ceo_coordinator",
  "raw_text_ref": "packets/mr_x/input.txt",
  "attachments": [],
  "received_at": "ISO-8601"
}
```

### 7.3 RoutingResult

```json
{
  "request_type": "creative_planning | technical_execution | legal_risk | business_strategy | mixed | fast_qa",
  "meeting_required": true,
  "urgency": "low | normal | high | critical",
  "priority": 60,
  "teams": ["content", "art", "marketing"],
  "roles": ["content_pd", "concept_artist", "marketer"],
  "validators": ["glm_risk", "codex_final"],
  "execution_required": false,
  "estimated_rounds": 3,
  "projection_policy": "summary_only"
}
```

### 7.4 WorkerTask Packet

```json
{
  "meeting_run_id": "mr_...",
  "worker_task_id": "wt_...",
  "role": "pipeline_rd",
  "runner": "opencode_go | hermes_wrapper",
  "model_policy": {
    "preferred": "deepseek_v4_pro",
    "fallback": ["qwen", "kimi"],
    "requires_codex_audit": true
  },
  "input_context_refs": [],
  "instruction": "",
  "expected_output_schema": {},
  "timeout_seconds": 900,
  "max_retries": 1,
  "output_ref": "",
  "error_ref": ""
}
```

### 7.5 ValidationVerdict

```json
{
  "meeting_run_id": "mr_...",
  "validation_cycle_id": "vc_...",
  "validator": "glm_risk | codex_audit",
  "verdict": "pass | conditional_pass | revise | reject | escalate",
  "risk_level": "low | medium | high | critical",
  "blocking_issues": [],
  "non_blocking_issues": [],
  "missing_perspectives": [],
  "requires_user": false,
  "requires_codex": false,
  "summary_for_discord": ""
}
```

### 7.6 DiscordProjectionEvent

```json
{
  "meeting_run_id": "mr_...",
  "projection_event_id": "dp_...",
  "bot_persona": "tech_lead",
  "target": {
    "guild_id": "",
    "channel_id": "",
    "thread_id": ""
  },
  "visibility": "public_thread | control_channel | internal_log_only",
  "event_type": "meeting_opened | team_opinion | consensus | validation_verdict | final_report | failure_alert",
  "content_ref": "",
  "posted_message_id": "",
  "created_at": "ISO-8601"
}
```

### 7.7 RecoveryCheckpoint

```json
{
  "meeting_run_id": "mr_...",
  "top_level_state": "active",
  "sub_states": {
    "meeting_phase": "round_2_rebuttals",
    "worker_tasks": {},
    "validation_cycle": "pending",
    "report_phase": null
  },
  "last_completed_step": "",
  "idempotency_keys": [],
  "resume_action": "continue | retry_worker | request_user | mark_failed",
  "updated_at": "ISO-8601"
}
```

## 8. Storage Policy

Recommended project-local storage is limited to AI_Agent domain artifacts.
Hermes-owned state remains in Hermes storage and is referenced, not copied.

Hermes-owned storage:

```text
Hermes sessions / state.db     -> conversation/session history
Hermes memory                  -> durable user/project facts only
Hermes skills                  -> reusable procedures, prompts, rubrics
Hermes cron/background/Kanban  -> generic scheduling/execution primitives
Hermes provider/auth config    -> model/provider credentials and routing base
```

AI_Agent-owned project-local storage:

```text
runtime/
  meeting_runs/
    mr_*/
      meeting_run.json
      packets/
      worker_outputs/
      validation/
      discord_projection/
      checkpoints/
      final_report.md
  decision_log.jsonl
  audit_log.jsonl
  queue_policy.json

second_brain/
  company/
    AGENTS.md
    raw/
    wiki/
      index.md
      log.md
  personal/
    AGENTS.md
    raw/
    wiki/
      index.md
      log.md
```

Second Brain policy:

```text
Company Second Brain -> strategy, research, market/tech/content knowledge,
validated meeting decisions, reusable company context.

Personal Second Brain -> schedules, reminders, private notes, personal goals,
and user-support context for the separate Personal Assistant layer.

raw/ is immutable source material.
wiki/ is synthesized markdown, Obsidian-compatible, and LLM Wiki style.
Hermes memory stores only compact durable operating facts.
context-mode/FTS5 may index markdown for search but is not source of truth.
```

Queue/state should use Hermes Kanban, background processes, cron, or Hermes
session references first. A dedicated `queue.db` is allowed only if simulation
proves Hermes-native primitives cannot express MeetingRun priority/concurrency
requirements.
JSON files are appropriate for packet handoff and debugging.
Markdown is appropriate for final reports.

## 9. Runtime Flow Coverage

### 9.1 Fast Q&A

```text
Discord mention
-> MeetingRun created
-> Qwen/router classifies fast_qa
-> no meeting round
-> optional lightweight validation
-> CEO Bot replies
-> completed
```

### 9.2 Meeting Request

```text
Discord mention
-> MeetingRun created
-> routed to teams/roles
-> queue
-> MeetingPhase round 1 opinions
-> round 2 rebuttals
-> consensus
-> GLM validation
-> CEO Bot final report + Validation Bot verdict
```

### 9.3 Worker Execution Request

```text
Discord mention
-> MeetingRun created
-> routing says execution_required
-> WorkerTask packet_written
-> Hermes provider worker dispatched
-> output_collected
-> GLM risk review
-> Codex audit if code/critical
-> report
```

### 9.4 Validation Failure Correction Loop

```text
Consensus or worker output
-> GLM/Codex verdict = revise
-> blocking issues converted to correction packet
-> relevant WorkerTask or MeetingPhase re-run
-> validation repeated
-> approve or escalate
```

### 9.5 Crash Recovery

```text
Process restarts
-> load Hermes-native execution state + meeting_run.json + latest checkpoint
-> find non-terminal MeetingRuns
-> inspect sub states
-> resume idempotent next action
-> post recovery notice only if user-visible delay occurred
```

### 9.6 Worker Timeout/Failure

```text
WorkerTask running exceeds timeout
-> mark timed_out
-> write error_ref
-> retry if retry budget remains
-> otherwise validation/report phase emits failure_alert
-> user sees concise status and next action
```

## 10. Queue and Concurrency Policy

Default queue policy is Hermes-native-first.

Reuse order:

```text
1. Hermes Kanban for durable task board / assignment / worker dispatch
2. Hermes background processes for bounded long-running tasks
3. Hermes cron for scheduled or retryable jobs
4. Hermes delegation only for short synchronous subtasks
5. Custom AI_Agent queue.db only after a verified gap
```

AI_Agent should add only MeetingRun-specific priority metadata and routing
policy on top of these primitives.

Priority score inputs:

```text
urgency
mentioned bot
request type
user role
deadline hints
blocked dependency
quota availability
current system load
```

Concurrency limits:

```text
global_meeting_runs_active: small fixed limit
per_team_active: 1-2
worker_tasks_active: bounded by provider/quota
codex_audits_active: very limited
```

Starvation prevention:

```text
priority aging
max defer count
manual promote command
```

Implementation rule:

```text
Do not build a generic queue system first.
Represent MeetingRun work in Hermes Kanban/background/cron where possible.
Only add a custom queue store for missing domain-specific semantics.
```

## 11. Model and Quota Policy

Hermes provider/auth/model configuration is the base layer. AI_Agent should not
rebuild provider credential pools, model selection plumbing, or generic fallback
logic. AI_Agent only decides domain-level model policy: which role, validator,
or risk class should request which Hermes-configured provider/model.

```text
Qwen: first-pass classification/routing
Kimi/MiniMax/DeepSeek/Qwen: domain worker reasoning by role
opencode-go: unified multi-model worker/validator/auditor execution wrapper
GLM: default contradiction/risk validator model executed through opencode-go
Codex: gated code/system auditor executed through opencode-go or Codex CLI
```

Execution role clarification:

```text
opencode-go is the default execution wrapper for worker, validator, and auditor
tasks. GLM and Codex are model/audit roles, not default standalone runtime
services.

GLM Validator means a validation task executed through opencode-go with a GLM
model. It handles contradiction, risk, legal/business concern, and excessive
optimism checks.

Codex Auditor is a gated high-confidence audit role for code, architecture, and
critical approval. Prefer execution through opencode-go when supported; use a
separate Codex CLI only when opencode-go cannot provide the required audit path.

Validator/Auditor labels describe execution roles, not independent processes.
```

Quota behavior:

```text
hourly exhausted -> wait/defer or fallback
weekly/monthly exhausted -> block high-cost route and report
Codex unavailable -> mark codex_audit_pending or use lower-confidence conditional verdict
GLM unavailable -> require user-visible warning if risk validation skipped
```

Implementation rule:

```text
Provider credentials live in Hermes config/auth.
Quota checks may call existing external scripts/services.
MeetingRun stores only route decisions, quota blockage reasons, and audit refs.
```

## 12. Security and Permission Model

Hermes approval, toolset, redaction, profile, and gateway authorization features
are the base security layer. AI_Agent adds domain-level permission classification
only; it must not replace Hermes approval or redaction mechanisms.

Permission levels:

```text
L0: answer only
L1: meeting creation
L2: file/read-only analysis
L3: code/file modification in workspace
L4: external API/browser/Discord mutation
L5: delete/deploy/payment/account/security-sensitive action
```

Rules:

```text
L4-L5 require Hermes approval or preconfigured allowlist.
Secrets never appear in Discord projection.
Worker packets reference secret names, not secret values.
Discord permissions are bot-specific and least-privilege.
All destructive actions must be logged with meeting_run_id.
```

## 13. Observability

Hermes-native observability remains the operational base:

```text
Hermes logs / gateway status / doctor / insights
Hermes sessions stats and session_search
Hermes cron status
Hermes background process poll/log/wait
Hermes Kanban stats/runs/log/tail when Kanban is used
```

AI_Agent observability adds only MeetingRun-domain events and metrics.

Minimum events:

```text
meeting_run.created
meeting_run.routed
queue.enqueued
worker.dispatched
worker.completed
worker.failed
validation.verdict
report.posted
recovery.resumed
```

Minimum metrics:

```text
active MeetingRuns
queue length by priority
worker task duration
validation failure rate
timeout count
quota blocked count
Discord post failures
```

Failure alert targets:

```text
#마스터-컨트롤
CEO/Coordinator Bot DM/log
runtime/audit_log.jsonl
```

## 14. Implementation Boundaries

Hermes Core should not be modified for the first production-ready version.
Hermes-native capabilities should be used before creating any AI_Agent-specific
infrastructure.
Integrate via:

```text
Hermes Discord Gateway
Hermes skills for meeting/routing/report/validation procedures
Hermes memory for durable facts only
Hermes provider/auth/model config
Hermes approvals/redaction/toolsets
Hermes Kanban/background/cron where applicable
adapter modules
Hermes provider adapters
AI_Agent WorkerTask packet files
project-local MeetingRun artifacts
Discord projection adapters
Ouroboros seed/evaluator artifacts
```

Prohibited as initial approach:

```text
patching Hermes core loop
reimplementing Hermes Discord Gateway
reimplementing Hermes memory/session/skill systems
reimplementing Hermes provider/auth/fallback systems
building a generic queue before testing Hermes Kanban/background/cron
making 29 Discord bot accounts
using Discord history as database
building generic BPMN engine
storing raw meeting logs in Hermes memory
```

## 15. Completion Criteria

Runtime Architecture v2 is complete when:

```text
schemas are field-level defined
all required runtime scenarios are covered
process/file/DB/queue topology is explicit
Discord Bot topology is explicit
quota/model policy is explicit
security/observability/recovery are explicit
implementation plan can be executed task-by-task
simulation tests can verify behavior without live Discord
```
