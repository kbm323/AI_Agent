# AI_Agent — 한국어 사용설명서

> Hermes-first AI 버추얼 엔터테인먼트 회사 런타임

이 저장소는 Discord 안에서 **개인비서 Bot + 6개 회사 팀장 Bot**이 회의/작업/검증/보고를 수행하는 AI 회사 운영 코어를 구현한다.

---

## 1. 개요

### 이게 뭔가요?

AI_Agent는 Hermes Agent를 운영본부로 삼아, opencode-go를 직원 실행 계층으로, GLM/Codex를 감사실로 구성한 가상 회사 런타임이다.

### 핵심 개념

```
Discord는 무대다.
Hermes는 운영본부다.
opencode-go는 직원 실행 계층이다.
GLM/Codex는 감사실이다.
MeetingRun은 모든 회의/작업/검증/보고의 장부다.
```

### 구조도

```
Discord mention → MeetingRun 생성
  → Qwen-style routing (어떤 팀이 필요한지 판단)
  → Hermes-native scheduling (어떻게 실행할지 결정)
  → Hermes provider Worker 실행 (opencode-go provider로 실제 작업 수행)
  → GLM Validator + Codex Auditor 검증 (품질 평가)
  → 결과 보고 + Discord projection (결과 전달)
  → Decision log / Recovery checkpoint (기록/복구)
```

---

## 2. 실행 중인 Discord 봇 (7대)

| 프로필 | Discord명 | 전용 채널 | 채널 ID | 채널 용도 |
|--------|-----------|-----------|---------|-----------|
| `aicompanyassistant` | `비서` | `#일일-브리핑` | `1507063720025522267` | 개인비서: 사용자 질의 접수, 개인 Second Brain 관리, 일일/주간 브리핑, 할 일 추출 — 개인 비서 레이어, 회사 부서 역할 아님 |
| `aicompanyceo` | `대표` | `#회의실-전략결정` | `1505600167221526621` | CEO/코디네이터: 회사 기본 진입점, 요청 라우팅, 최종 종합 보고서, 회의 개회/폐회 |
| `aicompanycontent` | `콘텐츠팀장` | `#콘텐츠-메인` | `1505927982722580500` | 콘텐츠 총괄: 기획/대본/편집/썸네일 방향 — 콘텐츠팀 의견·합의 도출 |
| `aicompanyart` | `아트팀장` | `#아트-메인` | `1505928014800752671` | 아트 총괄: 콘셉트/캐릭터/리깅/애니메이션/VFX/무대 — 아트팀 의견·리스크 제시 |
| `aicompanytech` | `기술팀장` | `#기술-메인` | `1505928578016219247` | 기술 총괄: R&D/파이프라인/인프라/개발/자동화 — 기술적 타당성·실행 상태 보고 |
| `aicompanymarketing` | `마케팅팀장` | `#마케팅-메인` | `1505931658426060970` | 마케팅 총괄: SNS/커뮤니티/IP/굿즈/성장 — 시장·팬·성장 관점 제시 |
| `aicompanyquality` | `품질관리팀장` | `#전체-리뷰` | `1507063654397378561` | 품질/검증: GLM+Codex 기반 위험 평가 및 최종 검증 결과 투영 — 판정·블로커·수정 요청 |

### 채널별 메시지 유형

| 채널 | 주로 오가는 메시지 |
|------|-------------------|
| `#일일-브리핑` | 일일/주간 요약, 할 일 알림, 개인 메모·지식 쿼리 응답 |
| `#회의실-전략결정` | 멀티봇 회의 조율, 최종 의사결정 보고서, 경영진 라우팅 |
| `#콘텐츠-메인` | 콘텐츠 기획안, 대본 초안, 썸네일 방향 제안, 편집 피드백 |
| `#아트-메인` | 콘셉트 아트 방향, 캐릭터 디자인 피드백, 애니메이션 리뷰 |
| `#기술-메인` | 아키텍처 결정, 파이프라인 상태, 장애 보고, 기술 조사 결과 |
| `#마케팅-메인` | SNS 전략, 커뮤니티 인사이트, IP·굿즈 기획, 성장 지표 |
| `#전체-리뷰` | 교차 검증 보고서, 품질 게이트 통과/실패, 블로커 목록, 수정 요청 |

모든 봇은 멘션 게이트(@봇이름)로만 반응하며, 관리자 권한 없이 운영된다.

---

## 3. 빠른 시작

### 3.1 테스트 실행

```bash
# 전체 테스트 (5664개)
cd /home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent
PYTHONPATH=src python3 -m pytest tests/ -q

# Runtime v2 전용 테스트만
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_*.py -q

# Lint 검사
ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_*.py
```

### 3.2 시뮬레이션 실행

실제 Discord/opencode-go 연결 없이 로컬 시뮬레이션으로 전체 파이프라인을 검증한다.

```bash
# 모든 시나리오 한 번에
python3 scripts/simulate_runtime_architecture_v2.py --scenario all

# 특정 시나리오만
python3 scripts/simulate_runtime_architecture_v2.py --scenario meeting
python3 scripts/simulate_runtime_architecture_v2.py --scenario worker_failure
python3 scripts/simulate_runtime_architecture_v2.py --scenario fast_qa
```

지원 시나리오: `fast_qa`, `meeting`, `worker_execution`, `dual_validation_pass`, `validation_correction_loop`, `crash_recovery`, `worker_failure`, `all`

출력은 JSON 리포트와 `runtime/phase9-simulation/` 아래 파일로 저장된다.

### 3.3 단일 MeetingRun 수동 실행

```bash
python3 -m src.runtime_architecture_v2.simulation_cli \
  --root runtime/phase9-simulation \
  --meeting-run-id mr_demo \
  --trigger-text "콘셉트 기획과 코드 구현, 마케팅 전략까지 같이 회의해줘" \
  --user-id user-1 \
  --channel-id channel-1 \
  --thread-id thread-1
```

### 3.4 쿼터 확인

```bash
bash scripts/check_all_quota.sh
```

출력 예시:
```
✅ 📦 Go: M:33% W:70% H:0%
🟡 LOW 🤖 Codex: M:0% W:47% H:78% Hourly 78%
✅ Both available
```

---

## 4. 파일 구조

```
src/runtime_architecture_v2/   ← 핵심 런타임 모듈
  schemas.py                   MeetingRun, WorkerTask 등 도메인 스키마
  store.py                     파일 저장소, 로그, 체크포인트
  routing.py                   Qwen 라우팅 정책
  queue_policy.py              우선순위/동시성 정책
  scheduling_policy.py         Hermes-native 스케줄링
  workers.py                   Worker 실행 계층 (fake + Hermes provider)
  validation.py                GLM/Codex 검증 정책
  projection.py                Discord 메시지 투사 + Phase 24 경계 허용목록
  policies.py                  보안/쿼터/관측 정책 게이트
  orchestrator.py              전체 MeetingRun 흐름 조정자
  command_surface.py           Hermes-first 명령 표면 정책
  worker_boundary_smoke.py     Worker 경계 스모크
  service_supervision.py       상시 서비스 감독 정책
  closed_loop_pilot.py         폐쇄 루프 제어 파일럿
  live_pilot_runbook.py        24h 파일럿 + 프로덕션 런북
  pilot.py                     Phase 13 회사 워크플로우 파일럿
  multi_bot.py                 Phase 14 멀티봇 프로토콜
  knowledge.py                 Phase 15 세컨드 브레인/지식 루프
  kanban_ops.py                Phase 16 Kanban 운영 계획
  production.py                Phase 17 상태 점검/복구 분류
  dispatch_loop.py             Phase 18 자율 디스패치 루프
  daemon.py                    Phase 19 자율 스케줄링 데몬
  bot_registry.py              Phase 20 29역할 조직도
  discord_webhook.py           Phase 21 Discord 웹훅
  autonomous_company.py        Phase 22 통합 회사 런타임

docs/
  runtime-architecture-v2.md   ← 정식 설계 문서
  phase1-29-cross-phase-risk-audit.md ← 전단계 리스크 감사

second_brain/
  company/                     회사 지식 베이스
  personal/                    개인비서 지식 베이스

seeds/
  seed_runtime_architecture_v2.yaml  ← Ouroboros Seed

tests/
  test_runtime_architecture_v2_*.py  ← v2 전용 테스트
```

---

## 5. 설계 원칙

- **Hermes Core 수정 최소화** — Hermes가 제공하는 gateway, session, memory, skills, provider/auth, cron/Kanban은 재구현하지 않는다.
- **AI_Agent는 MeetingRun 도메인만 추가** — schema, policy, adapter, packet, simulation 계층만 구현.
- **Discord는 투사면일 뿐** — source of truth는 `meeting_run_id` 기준 파일 아티팩트.
- **장기 지식은 Second Brain markdown에** — Hermes memory는 최소 운영 기억만 유지.
- **Fail-closed가 기본** — 검증 증거 없으면 통과하지 않는다.
- **예외 메시지는 절대 생(raw) 노출 안 함** — 오류 코드로 변환 후 전달.

---

## 6. Phase 목록 (1~29 전체 완료)

| Phase | 내용 |
|-------|------|
| 1 | Schema Layer |
| 2 | File Store / State / Logs |
| 3 | Routing / Queue / Scheduling Policy |
| 4 | Worker Execution Boundary |
| 4.5 | opencode-go Live Smoke Boundary |
| 5 | Validation Layer |
| 6 | Discord Projection Layer |
| 7 | Runtime Orchestrator |
| 8 | Security / Quota / Observability |
| 9 | E2E Simulation CLI |
| 10 | Live Adapter Wiring Boundaries |
| 11 | Final Verification |
| 12.1~5 | Discord/Worker Live Smoke, Permission, Token, UX |
| 13 | Live Company Workflow Pilot |
| 14 | Multi-bot Operational Protocol |
| 15 | Persistent Second Brain / Knowledge Loop |
| 16 | Autonomous Scheduling / Kanban Operations |
| 17 | Production Readiness / Monitoring / Recovery |
| 18 | Live Kanban Autonomous Dispatch Loop |
| 19 | Autonomous Scheduling Daemon |
| 20 | 29-role Org Chart Registry |
| 21 | Discord Interaction Webhook |
| 22 | Always-on Autonomous Company Runtime |
| 23 | Runtime v2 Alignment & Hardening |
| 24 | Live Boundary Inventory & Allowlist |
| 25 | Hermes Gateway Command Surface |
| 26 | Worker/Validator/Auditor Boundary Smoke |
| 27 | Service Supervision Pilot |
| 28 | Full Live Closed-loop Pilot |
| 29 | 24h Live Pilot & Production Runbook |

---

## 7. 검증 정책 (Validation)

```
PASS / CONDITIONAL_PASS → CONTINUE → reporting
REVISE                  → REVISE   → active (재작업)
REJECT / FAIL           → STOP     → failed
ESCALATE / DEGRADED     → ASK_USER → paused (사용자 확인 필요)
증거 없음               → ASK_USER → paused
```

검증자 역할:

| 검증자 | 실행기 | 모델 |
|--------|--------|------|
| GLM Validator | opencode-go | glm-5.1 |
| Codex Auditor | opencode-go | codex |

---

## 8. 운영 명령어

```bash
# Kanban 자율 디스패치 (dry-run)
python3 scripts/run_phase18_autonomous_dispatch.py --mode dry-run

# 회사 사이클 실행
python3 scripts/run_phase22_company_cycle.py

# 상태 점검
python3 scripts/run_phase17_health_check.py

# 데몬 틱
python3 scripts/run_phase19_daemon_tick.py
```

---

## 9. 현재 검증 기준

```text
전체 pytest: 5664 passed
Runtime v2 테스트: 376 passed
Ruff lint (변경 파일): clean
Secret scan: clean
7개 Discord 봇: 실제 등록 완료
```

---

## 10. Git

```bash
# 브랜치: main
git clone https://github.com/kbm323/AI_Agent.git
cd AI_Agent

# 권장 작업 디렉토리
/home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent
```

---

## 11. 자주 하는 질문

**Q: Hermes Agent는 어디에 있나요?**
A: AI_Agent는 Hermes 위에서 돌아간다. Hermes가 설치되어 있어야 하고, 이 repo는 Hermes의 기술(skill) + 도메인 코드 계층이다.

**Q: 실제 Discord 봇을 띄우려면?**
A: Hermes gateway가 Discord와 연결되어 있어야 한다. 봇 토큰은 환경변수 `DISCORD_BOT_TOKEN`으로 주입하며, 코드 내 하드코딩은 없다.

**Q: opencode-go는 어떻게 설치하나요?**
A: 현재 기본 Worker 경로는 별도 opencode-go CLI가 아니라 Hermes provider runtime을 통해 opencode-go provider를 호출한다. CLI/wrapper 언급은 historical smoke 또는 debug 경로로만 취급한다.

**Q: 테스트는 얼마나 걸리나요?**
A: 전체 pytest 5664개 약 80초 (WSL, i7 기준).
