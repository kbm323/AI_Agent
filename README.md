# AI_Agent

Hermes-first AI Virtual Entertainment Company runtime.

이 레포는 Discord 안에서 개인비서 Bot + 6개 회사 팀장 Bot이 회의/작업/검증/보고를 수행하는 AI 회사 운영 코어를 구현한다. 현재 기준 설계는 OpenClaw 기반 구 MVP가 아니라 `MeetingRun` 중심 Runtime Architecture v2다.

Canonical final-system document:

```text
docs/runtime-architecture-v2.md
```

Live Discord surface verified on 2026-06-25 02:43 KST:

```text
Entertainment guild
- aicompanyassistant / 비서 / #일일-브리핑
- aicompanyceo / 대표 / #전략-회의실
- aicompanycontent / 콘텐츠팀장 / #콘텐츠-메인
- aicompanyart / 아트팀장 / #아트-메인
- aicompanytech / 기술팀장 / #기술-메인
- aicompanymarketing / 마케팅팀장 / #마케팅-메인
- aicompanyquality / 품질관리팀장 / #전체-리뷰
```

```text
Discord는 무대다.
Hermes는 운영본부다.
opencode-go는 직원 실행 계층이다.
GLM/Codex는 감사실이다.
MeetingRun은 모든 회의/작업/검증/보고의 장부다.
```

## Current Architecture

```text
Discord mention / Hermes-native command surface
  -> MeetingRun 생성
  -> Qwen-style routing policy
  -> Hermes-native scheduling policy
  -> opencode-go Worker / Validator / Auditor boundary
  -> GLM Validator + Codex Auditor validation policy
  -> Report phase
  -> Discord projection layer
  -> Decision log / recovery checkpoint
```

핵심 원칙:

- Hermes Core는 수정 최소화한다.
- Hermes가 이미 제공하는 gateway, session, memory, skills, provider/auth, approval, cron/background/Kanban 기능은 재구현하지 않는다.
- AI_Agent는 MeetingRun 도메인 schema, policy, adapter, packet, simulation 계층만 추가한다.
- Discord thread/message는 source of truth가 아니라 사용자-facing projection이다.
- source of truth는 `meeting_run_id` 기준 project-local runtime artifact다.
- 장기 지식은 Second Brain plain markdown에 저장하고, Hermes memory는 최소 운영 기억만 둔다.

## Current Implementation Status

완료된 Runtime Architecture v2 phase:

```text
Phase 1  Schema Layer
Phase 2  File Store / State / Logs
Phase 3  Routing / Queue / Scheduling Policy
Phase 4  Worker Execution Boundary
Phase 4.5 opencode-go Live Smoke Boundary
Phase 5  Validation Layer
Phase 6  Discord Projection Layer
Phase 7  Runtime Orchestrator / full fake MeetingRun flow
Phase 8  Security / quota / observability policies
Phase 9  End-to-end simulation CLI
Phase 10 Live adapter wiring boundaries
Phase 11 Final verification
Phase 12.1 Discord live projection smoke
Phase 12.2 opencode-go worker live smoke
Phase 12.3 Bot permission inventory / hardening decision
Phase 12.4 Token rotation decision
Phase 12.5 Personal assistant UX/channel cleanup decision
Phase 13   Live Company Workflow Pilot
Phase 14   Multi-bot Operational Protocol
Phase 15   Persistent Second Brain / Knowledge Loop
Phase 16   Autonomous Scheduling / Kanban Operations
Phase 17   Production Readiness / Monitoring / Recovery
Phase 18   Live Kanban Autonomous Dispatch Loop
Phase 19   Autonomous Scheduling Daemon
Phase 20   29-role Org Chart Registry
Phase 21   Discord Interaction Webhook / Slash Command artifact (not default command surface)
Phase 22   Always-on Autonomous Company Runtime
Phase 23   Runtime v2 Alignment & Hardening
Phase 24   Live Boundary Inventory & Allowlist Foundation
```

현재 실제 구동 범위:

```text
실제/결정적 로컬 구동:
- MeetingRun schema
- RoutingResult / WorkerTask / ValidationVerdict / RecoveryCheckpoint schema
- project-local file store
- decision/audit JSONL logs
- fake worker simulation
- routing / priority / scheduling policies
- validation correction-loop policy
- Discord-safe projection formatter / fake projection sink
- deterministic RuntimeOrchestrator full fake flow
- deterministic security / quota / observability policy gates
- deterministic end-to-end simulation CLI
- live Discord projection sink behind injected HTTP/env boundary
- opencode-go WorkerRunner behind injected subprocess boundary
- quota-gated GLM/Codex validator execution planner
- Phase 11 final verification record
- Phase 12.1 Discord REST live projection smoke record

실제 외부 경계 검증 완료:
- opencode-go CLI discovery
- Hermes binary discovery
- OpenCode binary discovery
- opencode-go live smoke 1회 성공
- Phase 12.1 Discord REST live projection smoke record
- Phase 12.2 opencode-go worker live smoke record
- Phase 12.3 Discord permission inventory and hardening decision
- Phase 12.4 token rotation decision: do not rotate now
- Phase 12.5 personal assistant UX/channel cleanup decision
- Phase 13 live company workflow pilot
- Phase 14 multi-bot operational protocol
- Phase 15 persistent Second Brain / knowledge loop
- Phase 16 autonomous scheduling / Kanban operations
- Phase 17 production readiness / monitoring / recovery
- Phase 18 live kanban autonomous dispatch loop
- Phase 19 autonomous scheduling daemon
- Phase 20 29-role org chart registry
- Phase 21 Discord interaction webhook artifact (not the default command surface)
- Phase 22 unified company runtime
- Phase 23 Runtime v2 alignment and fail-closed hardening
- Phase 24 live boundary inventory and allowlist foundation

현재 상태 구분:
- Phase 13~22 planned implementation complete
- Runtime v2 deterministic orchestration layer complete
- Phase 23 fail-closed alignment/hardening complete
- Phase 24 live boundary allowlist foundation complete
- Live production hardening remains; see `docs/phase23-live-production-hardening-checklist.md`

아직 남은 작업:
- Hermes Gateway command surface verification
- live worker / validator / auditor boundary smoke
- always-on service supervision
- full live closed-loop pilot
- 24h live pilot and production runbook
```

## Runtime v2 Modules

```text
src/runtime_architecture_v2/
  schemas.py            # MeetingRun, WorkerTask, ValidationVerdict 등 도메인 schema
  store.py              # runtime/meeting_runs/<id>/ file store, logs, checkpoints
  routing.py            # FakeQwenRouter와 route policy
  queue_policy.py       # priority / bounded concurrency policy
  scheduling_policy.py  # Hermes-native scheduling mapping
  workers.py            # FakeWorkerRunner, opencode-go WorkerRunner/live-smoke boundary
  validation.py         # GLM/Codex role policy, quota-gated execution planner, correction loop
  projection.py         # Discord-safe formatter, fake sink, live sink, Phase 24 boundary allowlist
  policies.py           # security, quota, observability policy gates
  orchestrator.py       # deterministic fake MeetingRun full-flow orchestrator
  pilot.py              # Phase 13 bounded live company workflow pilot
  multi_bot.py          # Phase 14 multi-bot conversation protocol + live boundary-guarded projection
  knowledge.py          # Phase 15 repo-local Second Brain / knowledge loop
  kanban_ops.py         # Phase 16 Hermes-native Kanban operation planning
  production.py         # Phase 17 health scanning / recovery triage
  dispatch_loop.py      # Phase 18 live autonomous dispatch loop
  daemon.py             # Phase 19 autonomous scheduling daemon
  bot_registry.py       # Phase 20 29-role org chart registry (not 29 Discord accounts)
  discord_webhook.py    # Phase 21 Discord interaction webhook
  autonomous_company.py # Phase 22 unified company runtime
  # Phase 23 docs: phase23-runtime-v2-alignment-hardening.md,
  #                phase23-live-production-hardening-checklist.md
  simulation_cli.py     # python -m deterministic e2e simulation runner
```

## Validation Policy

Phase 5 기준 validation verdict 흐름:

```text
PASS / CONDITIONAL_PASS
  -> CONTINUE
  -> reporting

REVISE
  -> REVISE
  -> active
  -> follow-up worker required

REJECT / legacy FAIL
  -> STOP
  -> failed

ESCALATE / DEGRADED
  -> ASK_USER
  -> paused

missing verdict evidence
  -> ASK_USER
  -> paused

mixed meeting_run_id verdicts
  -> ASK_USER
  -> paused
```

GLM/Codex 역할:

```text
GLM Validator
  runner: opencode_go
  preferred model: glm-5.1
  execution_role: validator

Codex Auditor
  runner: opencode_go
  preferred model: codex
  execution_role: auditor
  fallback_runner: codex_cli_only_if_opencode_go_unavailable
```

Codex CLI fallback은 현재 metadata policy일 뿐이며 unit test에서 live CLI/model 실행은 하지 않는다.

## Repository Layout

```text
config/
  routing_rules.yaml            # legacy routing rules / reference config

docs/
  runtime-architecture-v2.md    # canonical architecture document
  runtime-architecture-v2-implementation-plan.md
  runtime-architecture-v2-final-verification.md
  phase12-live-operational-hardening-plan.md
  phase12-live-smoke.md
  phase12-opencode-live-smoke.md
  phase12-discord-permission-hardening.md
  phase12-token-rotation-decision.md
  phase12-assistant-ux.md
  phase13-live-company-workflow-pilot-plan.md
  phase13-live-company-workflow-pilot.md
  system-design-decisions.md
  diagnosis-report.md           # legacy diagnosis; v2 이전 기록
  generated/                    # historical/generated verification evidence

seeds/
  seed_runtime_architecture_v2.yaml
  slim/                         # track-based slim packets

second_brain/
  company/                      # company raw/wiki markdown knowledge base
  personal/                     # personal assistant raw/wiki markdown base

src/
  runtime_architecture_v2/      # current v2 implementation
  *.py                          # legacy/shared meeting system modules retained for tests/history

scripts/
  *.ts, *.mjs                   # legacy verification/diagnosis scripts
  check_all_quota.sh            # provider quota snapshot helper
  run_phase13_company_workflow_pilot.py
  run_phase16_kanban_pilot.py
  run_phase17_health_check.py
  run_phase18_autonomous_dispatch.py
  run_phase19_daemon_tick.py
  run_phase20_bot_registry.py
  run_phase21_discord_webhook.py
  run_phase22_company_cycle.py

tests/
  test_runtime_architecture_v2_*.py
  other legacy/shared tests
```

## Local Runtime Artifacts

Runtime output is intentionally not committed.

Ignored paths:

```text
runtime/
meetings/
.runtime/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.code-review-graph/
.ouroboros/
```

`runtime/` may contain live smoke stdout/stderr and MeetingRun artifacts. Keep it out of git.

## Verification Commands

Recommended current checks:

```bash
# Runtime v2 focused tests
pytest tests/test_runtime_architecture_v2_*.py -q

# Full Python test suite
pytest -q

# New/changed v2 files only, because legacy files still have known lint debt
ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py
```

Recent baseline after Phase 10:

```text
pytest tests/test_runtime_architecture_v2_*.py -q  -> 88 passed
pytest -q                                         -> 5369 passed
```

## Runtime v2 Simulation CLI

Phase 9 adds the plan-required deterministic local simulation script that drives
MeetingRun scenarios with fake adapters only. It does not call live Discord,
live model execution, provider dashboards, opencode-go, or Hermes runtime APIs.

```bash
python3 scripts/simulate_runtime_architecture_v2.py --scenario fast_qa
python3 scripts/simulate_runtime_architecture_v2.py --scenario meeting
python3 scripts/simulate_runtime_architecture_v2.py --scenario worker_failure
python3 scripts/simulate_runtime_architecture_v2.py --scenario all
```

Supported scenarios:

```text
fast_qa
meeting
worker_execution
dual_validation_pass
validation_correction_loop
crash_recovery
worker_failure
all
```

The script prints a machine-readable JSON report and writes ignored runtime
artifacts under:

```text
runtime/phase9-simulation/runtime/meeting_runs/<meeting_run_id>/
```

The lower-level module entrypoint remains available for a single explicit
MeetingRun payload:

```bash
python3 -m src.runtime_architecture_v2.simulation_cli \
  --root runtime/phase9-simulation \
  --meeting-run-id mr_demo \
  --trigger-text "콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘" \
  --user-id user-1 \
  --channel-id channel-1 \
  --thread-id thread-1
```

Known caveat:

```text
ruff check .
```

still reports legacy lint debt in old debug/transition/test files. For phase work, gate the changed v2 files plus full pytest until legacy lint debt is intentionally retired.

## Runtime v2 Final Verification

Phase 11 final verification record:

```text
docs/runtime-architecture-v2-final-verification.md
```

Recent baseline after Phase 11:

```text
pytest tests/test_runtime_architecture_v2_*.py tests/test_quota_scripts_no_hardcoded_secrets.py -q -> 92 passed
python3 scripts/simulate_runtime_architecture_v2.py --scenario all -> top_ok=True, 7 scenarios
pytest -q                                                 -> 5373 passed
ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py tests/test_quota_scripts_no_hardcoded_secrets.py -> no issues
pytest tests/test_quota_scripts_no_hardcoded_secrets.py -q -> 4 passed
```

## Runtime v2 Live Adapter Boundaries

Phase 10 live adapter boundaries are wired behind injected, testable interfaces:

```text
Discord projection:
- LiveDiscordProjectionSink reads DISCORD_BOT_TOKEN from environment only
- unit tests inject http_post, so no live Discord call occurs during tests
- message content is sanitized and allowed_mentions disables mention parsing

opencode-go workers:
- OpenCodeGoWorkerRunner implements WorkerRunner
- packet writing and command construction are deterministic
- command_runner is injectable; tests cover success and timeout without live CLI calls

validators:
- ValidatorExecutionPlanner builds GLM/Codex validator worker tasks as OPENCODE_GO
- quota policy is evaluated before validator dispatch
- blocked quota returns degraded validation verdicts instead of dispatching
- Codex CLI remains fallback metadata only
```

## opencode-go Live Smoke Boundary

Phase 4.5 verified the local live boundary:

```text
opencode-go -> opencode run --model opencode-go/<model> --context-file <packet>
```

The smoke runner writes outputs under ignored runtime paths and classifies:

```text
succeeded
failed
 timed_out
missing expected stdout token
```

Unit tests use injected runners only. Live smoke is a deliberate separate action.

## Git / Branch State

Current canonical branch:

```text
main
```

Preserved historical branch:

```text
legacy/discord-gateway-history
```

Remote `master` has been removed. Use `main` for all new work.

## Important Docs

Start here:

```text
docs/runtime-architecture-v2.md
docs/runtime-architecture-v2-implementation-plan.md
docs/system-design-decisions.md
seeds/seed_runtime_architecture_v2.yaml
```

Legacy docs may mention OpenClaw or MVP. Treat those as historical evidence unless a v2 document explicitly re-adopts them.

## Next Phase

Next work is no longer a Runtime Architecture v2 implementation phase. Runtime v2
is verified through Phase 11. Continue with post-v2 operational hardening only
after selecting scope, for example:

```text
- GitHub credential repair and origin/main push
- live Discord projection smoke to #시스템-로그
- opencode-go live worker smoke under current quota
- Discord token reset and safer bot permission re-invite
- #개인-비서 channel creation / assistant UX hardening
```
