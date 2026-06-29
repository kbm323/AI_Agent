# Meeting System Full Implementation Audit — 2026-06-28

Created: 2026-06-28 14:29 KST
Scope: AI_Agent Runtime Architecture v2 meeting system
Canonical baseline: `docs/runtime-architecture-v2.md`

## Decision

본민님이 설계한 최종 회의 시스템은 아직 완벽히 구현된 상태가 아니다.

현재 상태는 다음에 가깝다.

```text
설계 문서, 도메인 모델, dry-run, controlled smoke, 일부 live Discord projection 검증은 되어 있음.
하지만 Discord 실제 요청 -> 회의 thread 생성 -> 7봇 실제 모델 발언/반박/합의 -> 최종보고/evidence/Second Brain 자동 축적까지 이어지는 최종 live E2E는 아직 완성/검증되지 않음.
```

## Final Target Baseline

최종 기준은 `docs/runtime-architecture-v2.md`의 Runtime Architecture v2다.

```text
Discord Message / Mention / Command
  -> MeetingRun 생성
  -> Routing
  -> Queue
  -> Meeting / Worker / Validation / Report phases
  -> Discord projection
  -> Decision log / recovery checkpoint
```

운영 원칙:

```text
Discord는 무대다.
Hermes는 운영본부다.
opencode-go는 직원 실행 계층이다.
GLM/Codex는 감사실이다.
MeetingRun은 모든 회의/작업/검증/보고의 장부다.
```

본민님 기준의 실제 완성 UX:

```text
Discord 요청
-> 회의 thread 생성
-> 7봇 실제 발언
-> 의견/반박/합의
-> 검증/최종보고
-> evidence 저장
-> 회사 Second Brain 축적/재사용
```

## Current Verified State

### 1. 7 Discord gateways

감사 중 자동시작 hook으로 7개 gateway를 복구했고, Hermes gateway 기준 running 상태를 확인했다.

```text
aicompanyassistant gateway_alive=true
aicompanyceo gateway_alive=true
aicompanycontent gateway_alive=true
aicompanyart gateway_alive=true
aicompanytech gateway_alive=true
aicompanymarketing gateway_alive=true
aicompanyquality gateway_alive=true
```

Discord 로그상 연결 확인:

```text
비서 connected
대표 connected
콘텐츠팀장 connected
아트팀장 connected
기술팀장 connected
마케팅팀장 connected
품질관리팀장 connected
```

주의:

```text
감사 전 확인 시 gw_aicompany* tmux 세션은 모두 꺼져 있었다.
새로 만든 autostart hook으로 복구는 가능하지만, 장시간 안정 운영 검증은 별도 필요하다.
```

### 2. Phase 28 closed-loop

Fresh verification:

```text
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py -q

56 passed
```

보장하는 것:

```text
Hermes Gateway input
-> policy verification
-> MeetingRun creation
-> routing/scheduling
-> workers
-> validation
-> Gate 9 projection safety
-> fake or injected Discord projection publish
-> artifact
```

한계:

```text
기본은 controlled-dry-run.
실제 Discord projection은 injected HTTP가 필요.
실제 worker CLI도 기본 path가 아님.
따라서 실제 사용자 Discord 요청으로 7봇 회의가 열린다는 증거는 아님.
```

### 3. Phase 29 readiness/runbook

문서상 Phase 29는 Gate 10 production readiness이며, bounded 24-hour live pilot readiness를 증명한다.

하지만 문서 자체가 다음을 명시한다.

```text
Phase 29 proves bounded 24-hour live pilot readiness without running a real 24-hour operation.
```

`docs/phase29-live-test-2026-06-26.md`도 다음을 명시한다.

```text
This was a bounded live test after Codex hourly quota reset.
It did not start an unbounded 24h autonomous operation.
```

판정:

```text
생산 준비성/정책/시뮬레이션은 구현됨.
실제 24h unattended operation은 아직 운영 증거 없음.
```

### 4. Phase 14 multi-bot meeting

Fresh dry-run inspection:

```text
ok True
participants ('content_lead', 'marketing_lead', 'quality_lead')
live_worker_count 0
fake_worker_count 3
rounds_completed 2
consensus True 모든 팀장의 의견을 수렴하여 합의에 도달했습니다.
projection_messages_posted 6
meeting_thread_status not_requested
```

코드상 제한:

```text
src/runtime_architecture_v2/multi_bot.py
- 기본 pilot 참여자는 content_lead, marketing_lead, quality_lead 3명.
- dry-run은 live_worker_count 0 / fake_worker_count 3.
- live-worker mode는 max_live_workers 1 또는 2만 허용.
- 3명 이상 live worker는 거부.
- live bot content 생성 실패 시 fake content로 fallback.
- consensus는 len(all_bots) >= 2 heuristic.
```

관련 테스트:

```text
tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_phase14_live_worker_mode_rejects_more_than_two_workers
```

판정:

```text
Phase 14는 다중 봇 회의 protocol/pilot이지 최종 7봇 live deliberation이 아니다.
```

### 5. Meeting thread smoke

파일:

```text
scripts/run_phase29_live_meeting_thread_smoke.py
```

Dry-run result:

```text
ok true
thread_status created
posted 6
```

한계:

```text
TEAM_LEAD_MESSAGES가 정적 하드코딩 문자열.
실제 팀장 모델 호출 결과가 아님.
CEO + 5팀장 = 6 projection이며, 비서까지 포함한 7봇 회의가 아님.
```

### 6. Discord slash/webhook

`src/runtime_architecture_v2/discord_webhook.py`에는 `/회의` -> MeetingRun 생성 구조가 있다.

하지만 현재 최종 정책:

```text
Hermes-native mention/natural-language command 우선.
Standalone slash command는 core requirement가 아니라 optional adapter feature.
```

판정:

```text
command intake 조각은 있지만, 최종 live meeting loop와 완전 결합됐다고 보기는 어렵다.
```

### 7. Company Second Brain

`src/runtime_architecture_v2/knowledge.py`는 repo-local plain markdown Second Brain을 구현한다.

한계:

```text
run_phase15_knowledge_loop_pilot(..., mode='dry-run')만 지원.
내부에서 Phase 14 dry-run output 사용.
live mode는 phase15_only_supports_dry_run.
```

판정:

```text
회사 회의 결과가 실제 live 회의 후 자동으로 Second Brain에 저장/검색/다음 회의 반영되는 최종 loop는 아직 아니다.
```

## Fresh Verification Evidence

실행한 검증:

```text
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py -q

Result: 56 passed
```

```text
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase15_knowledge_loop.py \
  tests/test_runtime_architecture_v2_phase21_discord_webhook.py -q

Result: 56 passed
```

```text
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_phase14_live_worker_mode_rejects_more_than_two_workers \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_phase14_dry_run_produces_multi_bot_output \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_phase14_live_discord_creates_shared_thread_and_posts_all_visible_messages -q

Result: 3 passed
```

```text
ruff check \
  src/runtime_architecture_v2/multi_bot.py \
  src/runtime_architecture_v2/projection.py \
  src/runtime_architecture_v2/closed_loop_pilot.py \
  src/runtime_architecture_v2/discord_webhook.py \
  src/runtime_architecture_v2/knowledge.py \
  scripts/run_phase29_live_meeting_thread_smoke.py \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase15_knowledge_loop.py \
  tests/test_runtime_architecture_v2_phase21_discord_webhook.py \
  tests/test_runtime_architecture_v2_phase28_closed_loop_pilot.py \
  tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py

Result: No issues found
```

```text
python3 scripts/run_phase29_live_meeting_thread_smoke.py

Result: dry-run ok=true, thread_status=created, posted=6
```

Gateway verification:

```text
hermes gateway list

Result:
- default: not running
- aicompanyassistant: running
- aicompanyceo: running
- aicompanycontent: running
- aicompanyart: running
- aicompanytech: running
- aicompanymarketing: running
- aicompanyquality: running
```

## Blockers / Missing Evidence

```text
1. opencode-go/Codex quota script was not found at the checked default paths.
   Observed: quota_script_not_found

2. Current shell env did not expose live model/provider variables.
   Observed: model=unknown provider=unknown

3. No new live Discord mutation was performed during this audit.
   Reason: user asked to check implementation completeness, not to create a live meeting thread.

4. Existing live evidence is bounded controlled projection evidence, not full final UX evidence.
```

## Critical Gaps

### Critical 1 — Real Discord request to full MeetingRun E2E is not complete

Missing final behavior:

```text
User Discord request
-> MeetingRun creation
-> meeting thread creation
-> 7 bot live participation
-> consensus
-> final report
-> evidence and recovery checkpoint
```

Current implementation is mostly controlled runner / dry-run / smoke oriented.

### Critical 2 — 7 live bot deliberation is not implemented

Current Phase14:

```text
participants: 3
live workers: 0 by default
max live workers: 2
fake fallback present
```

Final target requires 7 Discord-facing roles to speak/act according to live model/worker output.

### Critical 3 — Consensus is heuristic, not validated deliberation

Current consensus core:

```text
consensus_reached = len(all_bots) >= 2
```

This does not implement real disagreement analysis, rebuttal scoring, GLM contradiction review, or Codex/GPT escalation.

### Critical 4 — Final report/evidence/Second Brain live chain is incomplete

Artifacts and knowledge module exist, but live meeting output does not yet flow through:

```text
MeetingRun evidence
-> final_report.md
-> Discord final report
-> Company Second Brain raw/wiki/log
-> future meeting retrieval
```

as one fully verified live E2E chain.

### Critical 5 — Actual 24h unattended operation not performed

Phase29 is readiness/simulation, not real 24h live proof.

## Warnings

### Warning 1 — Meeting thread smoke uses static messages

`TEAM_LEAD_MESSAGES` is hardcoded in `scripts/run_phase29_live_meeting_thread_smoke.py`.

### Warning 2 — Assistant role in 7-bot meeting is not fully defined

The 7 live accounts include the personal assistant, but the company meeting topology often uses CEO + 5 team leads / internal validation. Need explicit policy:

```text
비서는 회의 participant인가?
비서는 서기/projection/intake 역할인가?
비서는 최종보고/일정/Second Brain 저장 담당인가?
```

### Warning 3 — Gateway autostart works, but long-running reliability needs observation

Audit initially found all `gw_aicompany*` tmux sessions down. Hook recovered them.

### Warning 4 — Fake fallback can hide live worker failures

Final live meeting mode should fail closed or mark degraded if a live worker fails, not silently fake successful participation.

## Deferred / Intentional Non-Gaps

```text
1. Standalone slash commands are optional adapter features, not core.
2. 29 Discord bot accounts are not required; final design uses 7 live accounts + 29 internal roles.
3. Discord role/permission live mutation is intentionally deferred unless explicitly approved.
```

## Overall Completeness Estimate

```text
Architecture / documentation:              80-90%
Domain model / tests / simulation:          70-80%
Controlled live projection:                 60-70%
Actual final meeting UX:                    35-45%
7-bot live deliberation:                    20-30%
Live Second Brain automatic loop:           30-40%
24h unattended production operation:         0-10%
```

## Final Verdict

```text
No — not fully implemented exactly as designed.
```

The system has a strong Runtime Architecture v2 foundation, safety gates, MeetingRun artifacts, dry-run pilots, controlled live projection, and gateway deployment. However, the final user-facing meeting system still needs a dedicated implementation phase to prove the full live E2E path.

## Recommended Next Phase

### Phase 30 — Real Discord Meeting E2E

Acceptance Criteria:

```text
AC1. User asks CEO or assistant in Discord to open a meeting.
AC2. A real MeetingRun is created and persisted.
AC3. A CEO-owned meeting thread is created.
AC4. The same thread receives live messages from CEO, Content, Art, Tech, Marketing, Quality, and Assistant/Secretary role according to policy.
AC5. Each role message is generated by the configured profile/model/worker path, not fake templates.
AC6. Round 1 opinions complete.
AC7. Round 2 rebuttals complete.
AC8. GLM validation runs on the discussion packet.
AC9. Codex/GPT escalation condition is evaluated and executed when required.
AC10. Final report is generated.
AC11. Final report is posted to the Discord thread.
AC12. MeetingRun artifact includes thread ID, message IDs, worker outputs, validation, final report, and status.
AC13. Company Second Brain receives sanitized raw/wiki/log entries.
AC14. Recovery can resume or inspect the meeting by meeting_run_id.
AC15. Tests cover dry-run, injected live HTTP, live-worker failure, validation fail, and Second Brain persistence.
AC16. One supervised live smoke is run and documented with evidence.
```
