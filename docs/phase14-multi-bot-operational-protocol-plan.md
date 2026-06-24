# Phase 14 Multi-bot Operational Protocol — Implementation Plan

**Goal:** Phase 13이 증명한 단일 live worker pilot을 넘어, `버추얼컴퍼니-Hermes` 개인비서 Bot + 6개 회사 팀장 Bot이 Discord에서 협업하는 multi-bot operational protocol을 설계·구현·검증한다.

**Architecture:** Phase 14 builds on Runtime Architecture v2 and Phase 13 pilot boundaries. Keep Hermes Core untouched. Extend the MeetingRun domain layer with multi-bot coordination: bot conversation protocol, multi-worker dispatch, Discord projection routing per bot persona, and meeting flow with multiple participants.

**Tech Stack:** Python, pytest, Runtime Architecture v2 modules, ignored `runtime/` artifacts, Discord REST projection sink, opencode-go worker boundary, Hermes gateway profiles.

---

## Current Context

```text
Phase 13 완료: 단일 live worker pilot 성공
Phase 12 완료: Discord live projection smoke, opencode-go live smoke
Gateway: 7/7 hermes-aicompany tmux sessions running
Quota: Go OK, Codex OK
Git: clean main...origin/main
```

Phase 13이 증명한 것:
```text
단일 회사 요청 → MeetingRun 생성 → 역할 라우팅 → worker 1명 실행 → 보고서 생성
```

Phase 13이 못한 것 (= Phase 14 목표):
```text
Multi-bot Operational Protocol
  → 개인비서 Bot + 6개 회사 팀장 Bot이 하나의 MeetingRun에서 협업
  → Bot 간 대화 프로토콜
  → 다중 live worker 동시 실행
  → MeetingPhase의 multi-participant 회의 흐름
  → Discord에서 Bot들이 '회사'처럼 보이는 UX
```

---

## Phase 14 Scope Decision

Phase 14 is:
```text
Multi-bot Operational Protocol
  → Bot 간 협업 프로토콜 정의 + 최소 2개 Bot live 협업 실행
```

Phase 14 is not:
```text
Full production autonomy
Always-on autonomous company operation
Second Brain knowledge loop (Phase 15)
Kanban/scheduling autonomy (Phase 16)
Production monitoring (Phase 17)
29개 직무 전체 Discord Bot 전개
Discord slash command 재구현
```

### In Scope

- Define multi-bot conversation protocol: bot-to-bot message format, turn-taking, mention rules.
- Implement MeetingPhase with multiple bot participants (회의 라운드).
- Extend pilot orchestration to dispatch 2+ live workers (content_lead + one other).
- Add bot persona projection routing: which bot posts what on Discord.
- Define multi-bot projection rules: what's visible, what's internal summary.
- Add multi-bot MeetingRun scenario fixture.
- Add focused tests for new multi-bot coordination code.
- Execute one multi-bot live pilot (2 live workers, 2+ bots projecting).
- Keep all tokens hidden and all runtime output out of git.

### Out of Scope

- 7개 Bot 모두 live worker로 동시 실행 (2개까지 허용)
- Discord slash command implementation
- Long-running autonomous loops
- Persistent Second Brain write-back
- Bot permission mutation
- Token rotation

---

## Phase 14 Acceptance Criteria

```text
AC-14.0 Canonical Phase 14 plan exists and is committed.
AC-14.1 Multi-bot conversation protocol is defined: message format, turn sequence, mention rules.
AC-14.2 MeetingPhase state machine supports 3+ bot participants with round-based opinion/rebuttal/consensus flow.
AC-14.3 Bot persona projection routing is defined: each bot role → Discord message persona.
AC-14.4 Multi-bot projection rules are defined: what shows on Discord vs stays internal.
AC-14.5 Pilot orchestrator dispatches 2 live workers (content_lead + 1 other role) in one MeetingRun.
AC-14.6 At least 2 distinct bot personas produce Discord-safe output in one pilot run.
AC-14.7 Focused tests pass for all new multi-bot coordination code.
AC-14.8 One live multi-bot pilot execution succeeds.
AC-14.9 Secret scans show 0 tracked secret findings.
AC-14.10 Final documentation records what is proven and what remains.
```

---

## Multi-bot Conversation Protocol

### Bot-to-Bot Message Format

```text
Discord message from bot:
{
  "bot_role": "content_lead",
  "meeting_run_id": "mr_...",
  "round": 1,
  "type": "opinion | rebuttal | consensus | escalation | final_report",
  "content": "...",
  "mentions": ["marketing_lead"],  // 다른 Bot @mention
  "visible_on_discord": true       // false = internal only
}
```

### Turn Sequence (MeetingPhase)

```text
Round 1 — Opinions:
  CEO opens meeting → 각 팀장 Bot이 자기 의견 제시
  (content_lead → marketing_lead → quality_lead 등)

Round 2 — Rebuttals:
  각 Bot이 다른 Bot 의견에 반론/보완
  (@mention으로 대상 지정)

Consensus / Escalation:
  합의 도달 → CEO가 합의안 정리
  합의 실패 → CEO가 escalation → 사용자에게 판단 요청
```

### Mention Rules

```text
- Bot은 다른 Bot을 @mention할 수 있다 (rebuttal, 질문)
- 사용자는 어떤 팀장 Bot이든 @mention 가능
- 기본 진입점은 CEO/Coordinator Bot
- @mention 없는 봇 간 내부 논의는 Discord에 노출되지 않는다
```

---

## Multi-bot Projection Rules

### What shows on Discord

```text
YES:
- CEO의 meeting open/close 메시지
- 각 팀장 Bot의 opinion 요약 (라운드별)
- 합의안 / escalation 알림
- 최종 보고서 (CEO + Validation Audit Bot)
- 사용자 @mention에 대한 직접 응답

NO:
- Worker raw output
- Internal packet JSON
- Bot 간 내부 negotiation 전문
- Token/quota 정보
- System error stack trace
```

### Bot Persona Projection

```text
content_lead    → "콘텐츠 팀장" — 아이디어, 기획, 스크립트 관점
marketing_lead  → "마케팅 팀장" — 시장성, 팬 반응, 성장 관점
quality_lead    → "검증 팀장" — 리스크, 품질, 반론 관점
tech_lead       → "기술 팀장" — 구현 가능성, 인프라 관점
art_lead        → "아트 팀장" — 비주얼, 연출, 에셋 관점
business_lead   → "사업지원 팀장" — 법무, 재무, HR 관점
ceo_coordinator → "대표" — 종합, 라우팅, 최종 결정
```

---

## Implementation Tasks

### Task 14.0: Promote plan to canonical docs

**Files:**
- Create: `docs/phase14-multi-bot-operational-protocol-plan.md`
- Modify: `README.md`

**Steps:**
1. Write this plan into docs.
2. Update README: add Phase 14 as current next phase.
3. Commit and push.

---

### Task 14.1: Define multi-bot conversation protocol schema

**Files:**
- Create/modify: `src/runtime_architecture_v2/multi_bot.py`

**Implementation target:**

```python
@dataclass
class BotMessage:
    bot_role: str
    meeting_run_id: str
    round: int
    msg_type: str  # opinion, rebuttal, consensus, escalation, final_report
    content: str
    mentions: tuple[str, ...]
    visible_on_discord: bool

@dataclass
class MeetingRound:
    round_number: int
    phase: str  # opinions, rebuttals, consensus
    messages: tuple[BotMessage, ...]

@dataclass
class MultiBotSession:
    meeting_run_id: str
    participants: tuple[str, ...]  # bot roles
    rounds: tuple[MeetingRound, ...]
    consensus_reached: bool
    escalation_required: bool
```

**Test cases:**
```text
BotMessage serialization round-trips
MeetingRound can hold 3+ bot messages
MultiBotSession tracks consensus state
```

---

### Task 14.2: Extend MeetingPhase for multi-bot meeting flow

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Test: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Implementation target:**

```python
def run_meeting_phase(
    run: MeetingRun,
    participants: tuple[str, ...],
    *,
    rounds: int = 2,
    live_bot_roles: tuple[str, ...] = ("content_lead",),
    fake_bot_roles: tuple[str, ...] = ("marketing_lead", "quality_lead"),
    command_runner=None,
    workdir: str = ".",
) -> MultiBotSession:
    ...
```

Rules:
```text
Round 1: 각 participant가 opinion 생성 (live는 opencode-go, fake는 injected)
Round 2: participant 간 rebuttal (서로 @mention)
Consensus: CEO가 합의안 정리
Escalation: 합의 불가 시 사용자에게 판단 요청
```

---

### Task 14.3: Add multi-bot projection routing

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `src/runtime_architecture_v2/projection.py`

**Implementation target:**

```python
def route_bot_projection(
    message: BotMessage,
    *,
    live_discord: bool = False,
    target_channel_id: str = "",
) -> ProjectionPublishResult:
    """Route a bot message through the correct persona projection."""
    ...

BOT_PERSONAS: dict[str, str] = {
    "content_lead": "콘텐츠 팀장",
    "marketing_lead": "마케팅 팀장",
    ...
}
```

---

### Task 14.4: Extend pilot for multi-bot live execution

**Files:**
- Create/modify: `src/runtime_architecture_v2/pilot.py` (add multi-bot variant)
- Create: `scripts/run_phase14_multi_bot_pilot.py`

**CLI behavior:**

```bash
python3 scripts/run_phase14_multi_bot_pilot.py --mode live-worker --max-live-workers 2
```

Expected:
```json
{
  "pilot_id": "phase14_multi_bot_operational_pilot",
  "mode": "live-worker",
  "meeting_run_id": "...",
  "top_level_state": "completed",
  "live_worker_count": 2,
  "fake_worker_count": 1,
  "bot_participants": ["content_lead", "marketing_lead", "quality_lead"],
  "rounds_completed": 2,
  "consensus_reached": true,
  "projection_messages_posted": 5,
  "ok": true
}
```

---

### Task 14.5: Add multi-bot tests

**Files:**
- Create: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Test cases:**
```text
BotMessage serialization round-trips
MeetingRound holds 3+ bot messages
MultiBotSession consensus state tracking
multi-bot MeetingPhase with 2 live + 1 fake worker
projection routing selects correct persona per bot role
dry-run produces all bot messages through fake runners
live-worker mode rejects max-live-workers > 2
bot messages redact secrets and raw worker dumps
multi-bot projection respects visible_on_discord flag
```

---

### Task 14.6: Execute one multi-bot live pilot

**Pre-checks:**
```bash
git status --short --branch
bash scripts/check_all_quota.sh
bash scripts/status_discord_multibot_gateways.sh
```

**Run:**
```bash
python3 scripts/run_phase14_multi_bot_pilot.py --mode live-worker --max-live-workers 2
```

**Optional live Discord:**
```bash
python3 scripts/run_phase14_multi_bot_pilot.py --mode live-worker --max-live-workers 2 --live-discord
```

---

### Task 14.7: Final documentation

**Files:**
- Create: `docs/phase14-multi-bot-operational-protocol.md`
- Modify: `README.md`

---

## Pilot Scenario

```text
"AI virtual entertainment company — 신규 버추얼 아이돌 그룹의 데뷔 컨셉을 회의해줘.
콘텐츠 팀장이 아이디어 내고, 마케팅 팀장이 시장성 검토하고, 검증 팀장이 리스크 체크해줘."
```

Expected route:
```text
request_type: creative_meeting
meeting_phase: 2-round multi-bot
participants: content_lead, marketing_lead, quality_lead
live_worker_roles: content_lead, marketing_lead (2 live)
fake_worker_roles: quality_lead (1 fake, acts as validation)
projection: Discord-safe summary from each bot persona
```

---

## Phase 14 Risks and Guardrails

- **Quota exhaustion**: max 2 live workers, pre-check quota
- **Discord message flood**: limit round messages, summary-only projection
- **Bot confusion**: deterministic projection routing, no autonomous bot-to-bot loops
- **Scope creep**: Phase 14 is multi-bot protocol only — not full autonomy, not Second Brain, not monitoring

---

## Suggested Later Phases

```text
Phase 15: Persistent Second Brain / Knowledge Loop
Phase 16: Autonomous Scheduling / Kanban Operations
Phase 17: Production Readiness / Monitoring / Recovery
```

---

## Recommended Execution Order

```text
14.0 promote plan to docs
14.1 define multi-bot conversation protocol schema
14.2 extend MeetingPhase for multi-bot flow
14.3 add multi-bot projection routing
14.4 extend pilot for multi-bot live execution
14.5 add multi-bot tests
14.6 execute one multi-bot live pilot
14.7 final documentation
```
