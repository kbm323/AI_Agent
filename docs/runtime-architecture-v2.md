# AI Virtual Entertainment Company — Runtime Architecture v2

> Hermes-first Multi-Agent Meeting System의 실제 운영 아키텍처.
>
> 기준 Seed: `seed_176a489b1d25`
> Interview: `interview_20260619_051314`

## 1. Decision

Runtime Architecture v2의 root aggregate는 `MeetingRun`이다.

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

## 2. Design Principle

```text
Discord는 무대다.
Hermes는 운영본부다.
opencode-go는 직원 실행 계층이다.
GLM/Codex는 감사실이다.
OpenClaw는 외근 실행팀이다.
MeetingRun은 모든 회의/작업/검증/보고의 장부다.
```

## 3. Runtime Process Topology

```text
[Discord Gateway]
  receives mentions / commands / replies
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
[Priority Queue]
  schedules MeetingRun by urgency/type/quota
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
        +--> [OpenClaw Executor Adapter]
        +--> [GLM Validator]
        +--> [Codex Auditor]
        |
        v
[Discord Projection Layer]
  posts selected events as team-lead bot messages
        |
        v
[Storage]
  packets / outputs / decisions / checkpoints / logs
```

## 4. Discord Bot Topology

Actual Discord bots are team-lead level interaction endpoints.
They are not source-of-truth state holders.

| Bot | Responsibility | User @mention | Projection role |
|---|---|---:|---|
| CEO/Coordinator Bot | default entrypoint, routing, final report | yes | final synthesis, meeting open/close |
| Content Lead Bot | content, script, editing, thumbnail direction | yes | content team opinions/consensus |
| Art Lead Bot | concept, character, rigging, animation, VFX, stage | yes | art team opinions/risks |
| Tech Lead Bot | R&D, pipeline, infrastructure, development, automation | yes | technical feasibility/execution status |
| Marketing Lead Bot | SNS, community, IP, goods, growth | yes | market/fan/growth perspective |
| Business Support Lead Bot | BD, legal, finance, HR, risk | yes | business/legal/finance risk |
| Validation/Audit Bot | GLM/Codex risk and final validation projection | yes | verdict, blockers, correction requests |

Internal specialists are workers, not Discord bot accounts.
Examples: content_pd, script_writer, concept_artist, rigger, pipeline_rd,
web_app_developer, legal_reviewer, data_analyst.

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

Used for opencode-go, Hermes wrapper, or OpenClaw execution.

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
  "openclaw_required": false,
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
  "runner": "opencode_go | hermes_wrapper | openclaw",
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

Recommended project-local storage:

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
  queue.db
```

SQLite is appropriate for queue/state.
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
-> opencode-go CLI dispatched
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
-> load queue.db + meeting_run.json + latest checkpoint
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
openclaw_tasks_active: very limited
```

Starvation prevention:

```text
priority aging
max defer count
manual promote command
```

## 11. Model and Quota Policy

```text
Qwen: first-pass classification/routing
Kimi/MiniMax/DeepSeek/Qwen: domain worker reasoning by role
GLM: contradiction, risk, legal/business concern, excessive optimism
Codex: code audit, critical final approval, system design audit
opencode-go: worker execution wrapper
OpenClaw: browser/external tool-use/long external automation only
```

Quota behavior:

```text
hourly exhausted -> wait/defer or fallback
weekly/monthly exhausted -> block high-cost route and report
Codex unavailable -> mark codex_audit_pending or use lower-confidence conditional verdict
GLM unavailable -> require user-visible warning if risk validation skipped
```

## 12. Security and Permission Model

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
L4-L5 require explicit approval or preconfigured allowlist.
Secrets never appear in Discord projection.
Worker packets reference secret names, not secret values.
Discord permissions are bot-specific and least-privilege.
All destructive actions must be logged with meeting_run_id.
```

## 13. Observability

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
Integrate via:

```text
adapter modules
CLI wrappers
JSON packet files
SQLite queue/state
Discord gateway/bot adapters
Ouroboros seed/evaluator artifacts
```

Prohibited as initial approach:

```text
patching Hermes core loop
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
