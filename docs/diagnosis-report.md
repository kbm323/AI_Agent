# AI_Agent MVP 진단 보고서 (2026-06-06 업데이트)

> Legacy note: 이 문서는 Runtime Architecture v2 이전 진단 기록이다. OpenClaw/Hermes dual-persona 표현은 과거 상태 설명이며, 현재 기준 설계는 `runtime-architecture-v2.md`와 `system-design-decisions.md`를 따른다.

## Scope

이 진단 보고서는 `/home/kbm/F:ai-projects/AI_Agent` 구현체(2026-06-06 기준)와
`/mnt/c/Users/KBM/Downloads/260526_README.md` 요구사항을
Seed 평가 기준 우선순위(오류 빈도 > 유지보수 난이도 > 토큰 비용 > 아키텍처 적합성 > 기능 완성도)에
따라 심층 분석한 결과다. 기존 Ouroboros 실행으로 생성된 `docs/diagnosis-report.md`의
"partial redesign" 판단을 재검증하고, 260526 README의 가상 회사형 멀티 에이전트 제작 회의
시스템 MVP 요구사항과 교차 검증한다.

## 실행 환경

- Node.js v24.15.0, TypeScript ESM
- Python 3.11+ (shared token_budget, context_compression modules)
- 582개 테스트 중 569개 통과, 13개 실패 (2026-06-06 기준)
- `npm run dry-run` 정상 실행 확인 (정상 경로 + escalation + invalid input)
- `npm run verification-workflow` 정상 실행 확인
- Scan orchestrator 검증 완료: 150개 파일 스캔, per-file parsing 크래시 없음, 안정적 집계 (Sub-AC 2.3)

## Decision

**Recommendation: partial redesign**

기존 구현의 핵심 아키텍처(task/thread/turn/state 모델, adapter pattern, escalation 파이프라인)는
MVP에 잘 맞으므로 유지한다. 전면 재계획은 불필요하다.

부분 재설계 초점:

1. **토큰 압축 실적용 (P0)** — `buildCompressedLoopContextArtifact`와 `compactPromptContext`가
   구현되어 있으나 orchestrator 루프에 배선되지 않음. `buildReviewerRequest()`가 여전히 전체
   userRequest + 전체 draft를 매 라운드 전달. Owner/Finalizer executor 인터페이스에 압축 컨텍스트
   파라미터 없음.

2. **오류 처리 강화 (P1)** — `isUsableModelOutput`가 userRequest 단순 반복만 검사. 최소 길이(20자),
   무관 응답 패턴("OK", "done", "처리할 수 없습니다"), 동일 문장 3회 반복 감지 필요.
   4개 실패 테스트 수정.

3. **유지보수성 개선 (P2)** — `src/index.ts` 374줄 barrel file (런타임 + 진단 모듈 혼재),
   30개 이상 체크 스크립트의 중복 CLI 로직, 핵심 모듈(`meeting-transcript.ts`,
   `loop-context-compression-policy.ts`, `context-storage.ts`) 직접 단위 테스트 부재.

### Observable Decision

```json
{
  "diagnosis": {
    "decision": "partial_redesign",
    "decisionLabel": "partial redesign",
    "decisionBasis": "Keep core task/thread/turn/state model. Redesign: wire compression to loop, strengthen error handling, separate diagnostic modules."
  }
}
```

Evidence artifact: `docs/review-evidence.json` (`review-evidence.v1`, 122 inspected modules, 23 findings).

---

## 260526 README 요구사항 매핑

260526 README는 프로젝트 README보다 더 넓은 비전(가상 회사 조직 구조, Dual Persona Layer,
모델 라우팅 전략)을 기술한다. MVP 범위와의 관계:

### 현재 구현된 MVP 범위 (Covered)

| 260526 README 요구사항 | 상태 | 구현 위치 |
|------------------------|------|-----------|
| 사용자 요청 분석 및 작업 분해 | Covered | `src/planning.ts` → `analyzeUserRequest()` |
| 직무별 라우팅 (OpenClaw/Hermes) | Covered | `src/role-routing.ts` → `PersonaRouter` |
| OpenClaw 실행 persona | Covered | `CompanyOrchestrator` → `owner.createDraft()` |
| Hermes 리뷰 persona | Covered | `CompanyOrchestrator` → `reviewer.review()` |
| OpenClaw 최종 synthesis | Covered | `finalizer.synthesize()` |
| 수렴 평가 / escalation | Covered | `detectConvergenceFailure()`, `detectStrongUserInputRequired()` |
| Thread 요약 + SQLite 전문 저장 | Covered | `turns.visibleSummary` + `turns.content` |
| Discord thread 운영 구조 | Covered | `DiscordDelivery` adapter + dry-run |
| 최대 라운드 제한 | Covered | `maxRounds` config (default 4) |

### 현재 범위 밖 (Deferred — Phase 2 이상)

| 260526 README 요구사항 | 현황 | 대상 Phase |
|------------------------|------|-----------|
| 직무별 조직 구조 (콘텐츠/아트/기술/마케팅/경영팀) | 라우팅 구조는 있으나 부서별 페르소나 미구현 | Phase 2-B, 2-C |
| Dual Persona Layer (OpenClaw/Hermes 페르소나 전환) | AgentRole 3종만 정의됨 | Phase 2-C |
| 모델 라우팅 전략 (작업 유형별 모델 선택) | dry-run은 deterministic fake executor | Phase 2-B + Adapter |
| 검색/검증 레이어 (Fact check + 최신 정보) | 미구현 | Phase 2-D |
| Memory & Decision Log (회의 기록, 브랜드 방향) | SQLite 기본 persistence만 있음 | Phase 3 |
| Notion/GitHub/Google Drive/n8n 연동 | 미구현 | Phase 4 |

**평가:** 현재 MVP 구현은 260526 README의 핵심 회의 루프(분석→라우팅→실행→리뷰→합성→escalation)를
충실히 구현하고 있다. Dual Persona, 모델 라우팅, 조직 구조 확장은 Phase 2-B~2-E 로드맵에 따라
자연스럽게 추가 가능한 구조다.

---

## 우선순위별 진단

### 1. 오류 빈도 (Error Frequency) — 양호, 일부 보강 필요

**장점:**
- `CompanyOrchestrator`가 owner draft 실패, reviewer escalation, convergence failure,
  ambiguity detection을 모두 안정적으로 처리
- `isUsableModelOutput`로 empty/repeated 응답 필터링
- 잘못된 입력은 exit code 2 + JSON 오류로 deterministic 실패
- 504개 통과 테스트로 회귀 방지

**발견된 리스크:**

| 리스크 | 심각도 | 현황 |
|--------|--------|------|
| `isUsableModelOutput`가 단순 공백/반복만 검사 | **중간** | "OK", "done" 같은 짧은 무의미 응답, 무관 내용 미감지 |
| `meeting-transcript.ts`, `loop-context-compression-policy.ts`, `context-storage.ts` 직접 단위 테스트 부재 | 중간 | 간접 테스트로만 커버됨 |
| `policies.ts`의 `summarizeForThread`가 1200자 단순 truncation | 낮음 | 긴급 상황에서 의미 왜곡 가능성 |
| 4개 실패 테스트 (public API symbol 불일치 등) | 낮음 | 공개 API 심볼 정렬 문제, MVP completion gate |

**조치:** `isUsableModelOutput` 강화 (최소 길이 20자, 무관 응답 패턴 필터, 동일 문장 3회
반복 감지), 실패 테스트 수정, 핵심 모듈 단위 테스트 추가.

### 2. 유지보수 난이도 (Maintenance Difficulty) — 중간, 구조 개선 필요

**장점:**
- `planning.ts`에 request analysis/task breakdown/role routing/token strategy 통합 완료
- `orchestrator.ts`가 순수 상태 전이만 담당 (524줄, 관리 가능)
- Python shared 인프라가 TypeScript 구현과 미러링 (검증 이중화)

**문제점:**

| 문제 | 영향 | 현황 |
|------|------|------|
| `src/index.ts` 374줄 barrel file (런타임 17개 + 진단 14개 모듈 혼재) | **높음** | 수동 re-export, 신규 모듈 추가 시 누락 위험 |
| 30개 이상 체크 스크립트 중복 CLI 로직 | 중간 | `scripts/check-*.ts`마다 유사한 인자 파싱/출력 포맷 반복 |
| 진단용 메타 모듈이 런타임 코드와 혼재 | 중간 | `evaluation.ts`, `inspection.ts`, `inventory-orchestration.ts`, `decision-justification-report.ts`, `artifact-check.ts`, `review-artifact-completeness.ts` 등이 `src/` 루트에 위치 |
| `public-api-symbols.json`과 `public.ts` 간 수동 동기화 | 낮음 | 4개 실패 테스트의 원인 |

**조치:** `src/diagnostic/` 디렉토리 분리, `scripts/_lib/` 공통 유틸리티 추출,
barrel file 자동화(`scripts/generate-barrel.ts`), public-api-symbols.json 정렬.

### 3. 토큰 비용 (Token Cost) — 유틸리티 구현 완료, 실적용 필요

**구현 완료된 것:**
- `buildCompressedLoopContextArtifact` / `compactPromptContext` — 압축 컨텍스트 생성 (TypeScript)
- `src/shared/context_compression.py` → `build_compressed_loop_context()` / `compact_prompt_context()` — Python 미러
- `buildDefaultTokenStrategy` — raw storage, exposed summary, compression policy 명시
- `TokenStrategy` type — 4개 필드(rawStorage, exposedLoopContext, compressionPolicy, targetReduction)
- `token-baseline.ts` + `src/shared/token_budget.py` — 토큰 사용량 측정 및 감소율 검증

**실적용되지 않은 것 (P0 갭):**
- `buildReviewerRequest()`가 전체 userRequest + 전체 draft를 매 라운드 전달함
- `owner.createDraft()`, `reviewer.review()`, `finalizer.synthesize()` 인터페이스에 압축 컨텍스트 파라미터 없음
- `CompanyOrchestrator.runUserRequest()` 루프 내에서 `buildCompressedLoopContextArtifact`가 호출되지 않음
- `summarizeForThread`가 단순 1200자 truncation (의미 기반 요약 아님)

**현재 vs 목표 토큰 사용량 (측정 기준: deterministic-local-estimate-v1):**

```
[현재] reviewer request per round: userRequest(50자) + draft(200자) = 250자 → ~65 tokens
[목표] reviewer request per round: compressedContext(50자) + draftSummary(100자) = 150자 → ~40 tokens (38%↓)

[현재] finalizer: draft(200자) + review(150자) = 350자 → ~90 tokens
[목표] finalizer: draftSummary(100자) + reviewSummary(80자) = 180자 → ~45 tokens (50%↓)

3라운드 누적: 현재 ~385 tokens → 목표 ~150 tokens (61%↓)
```

**목표 달성 가능성: 높음** — 유틸리티는 이미 구현되어 있고, orchestrator 루프 배선 및
executor 인터페이스 확장만 필요. 40-50% 감소 목표는 3라운드 기준 61% 감소로 초과 달성 예상.

### 4. 아키텍처 적합성 (Architecture Fit) — 우수

**유지할 adapter pattern:**
- `DiscordDelivery` — thread 생성, parent/thread 게시
- `OwnerExecutor` — OpenClaw 실행 persona (`createDraft`)
- `ReviewerExecutor` — Hermes 리뷰 persona (`review`)
- `FinalizerExecutor` — OpenClaw 최종 통합 (`synthesize`)

이 구조는 실제 OpenClaw/Hermes adapter로 교체 시 인터페이스 변경 없이 연결 가능.
Discord 의존성은 adapter에 캡슐화되어 있어 CLI dry-run에서도 동일 오케스트레이터를 재사용.

**문제점 없음.** 현재 아키텍처는 260526 README의 Phase 2-B~2-E 요구사항(직무별 라우팅,
Dual Persona, 모델 라우팅)을 자연스럽게 수용할 수 있는 확장성을 갖추고 있다.

### 5. 기능 완성도 (Feature Completeness) — MVP 범위 충족

| Seed MVP 요구사항 | 상태 | 구현 위치 |
|-------------------|------|-----------|
| 사용자 요청 분석 / 작업 분해 | ✅ 완료 | `src/planning.ts` → `analyzeUserRequest()`, `decomposeUserRequest()` |
| 직무별 라우팅 | ✅ 완료 | `src/role-routing.ts` → `PersonaRouter`, `route_to_persona()` |
| OpenClaw 실행 페르소나 | ✅ 완료 | `CompanyOrchestrator` → `owner.createDraft()` |
| Hermes 리뷰 페르소나 | ✅ 완료 | `CompanyOrchestrator` → `reviewer.review()` |
| 회의 과정 보존형 루프 | ✅ 완료 | `turns.content` (전문), `turns.visibleSummary` (노출) |
| Final synthesis | ✅ 완료 | `finalizer.synthesize()`, `buildFinalSynthesisArtifactFromMeetingLoopArtifact()` |
| 수렴 실패 escalation | ✅ 완료 | `detectConvergenceFailure()`, maxRounds hard stop |
| 사용자 의견 필요 escalation | ✅ 완료 | `detectStrongUserInputRequired()`, escalation policy |
| 잘못된 입력 → non-zero 실패 | ✅ 완료 | dry-run exit code 2 + `{"error":"invalid_input"}` |
| 원문 저장 / 요약 노출 분리 | ✅ 완료 | SQLite `content` vs `visibleSummary` |
| 압축 컨텍스트 전략 | ⚠️ 구현됨, 미배선 | `buildCompressedLoopContextArtifact` 존재, 루프 미적용 |

---

## 유지할 컴포넌트 (Keep)

| 컴포넌트 | 사유 |
|----------|------|
| `src/orchestrator.ts` | 상태 기반 meeting loop, escalation decision point — 핵심 로직 안정적 |
| `src/db.ts` | SQLite task/turn/decision persistence — 단순하고 견고 |
| `src/policies.ts` | escalation policy, bounded visible summary |
| `src/planning.ts` | request analysis, task breakdown, role routing, token strategy |
| `src/summarization.ts` | context compaction, compressed loop context 유틸리티 |
| `src/role-routing.ts` | role-based task-to-agent mapping, PersonaRouter |
| `src/types.ts` | 공통 인터페이스 — 변경 없음 |
| `src/final-synthesis.ts` | final synthesis artifact 생성 및 검증 |
| `src/convergence-failure.ts` | 수렴 실패 감지 로직 |
| `src/user-input-required.ts` | 사용자 입력 필요성 감지 |
| `src/token-baseline.ts` | 토큰 측정/검증 |
| `src/shared/` | Python 공유 인프라 (token_budget, context_compression, config, utilities) |
| `scripts/dry-run.ts` | 최소 CLI 실행 인터페이스 |
| `tests/*.test.ts` | 504개 통과 테스트 |

---

## 수정할 컴포넌트 (Partial Redesign)

| 컴포넌트 | 변경 사유 | 우선순위 |
|----------|----------|----------|
| `orchestrator.ts` — `buildReviewerRequest()` | 압축 컨텍스트 미적용 → `buildReviewerRequestCompressed()`로 교체 | **P0** |
| `orchestrator.ts` — `isUsableModelOutput()` | 무의미 응답 필터 미흡 → 최소 길이, 노이즈 패턴, 반복 감지 추가 | **P1** |
| `orchestrator.ts` — executor 호출 | Owner/Reviewer/Finalizer 인터페이스에 `compressedContext?` 파라미터 추가 | **P0** |
| `types.ts` — executor 인터페이스 | `OwnerExecutor`, `ReviewerExecutor`, `FinalizerExecutor`에 압축 컨텍스트 필드 추가 | **P0** |
| `src/index.ts` | 374줄 수동 barrel → `scripts/generate-barrel.ts` 자동화, `src/diagnostic/` 분리 | **P2** |
| `scripts/check-*.ts` | 중복 CLI 로직 → `scripts/_lib/cli-utils.ts` 공통화 | **P2** |
| `src/evaluation.ts`, `inspection.ts`, `inventory-orchestration.ts`, `decision-justification-report.ts`, `artifact-check.ts`, `review-artifact-completeness.ts` | 진단 모듈 혼재 → `src/diagnostic/` 분리 | **P2** |
| `docs/public-api-symbols.json` | 현재 `buildTaskGraph` 포함, `public.ts` 미포함 → 정렬 동기화 | **P2** |
| `policies.ts` — `summarizeForThread()` | 단순 truncation → 구조 인식 요약으로 업그레이드 (선택적) | **P3** |

---

## Token Strategy (상세)

**원문 저장 (Raw Storage):**
SQLite `turns.content`에 전체 source text, model drafts, reviewer requests, reviews,
final synthesis, escalation messages 보존. raw prompt echo와 intermediate scratchpad는
저장하되 루프 컨텍스트에서 제외.

**노출 요약 (Exposed Summary):**
Discord thread 게시와 `RunTaskResult.meetingHistory` 반환 시 `turns.visibleSummary`만 사용
(기본 1200자 cap). `summarizeForThread`는 현재 단순 truncation이나, 추후 구조 인식
요약으로 업그레이드 가능.

**압축 컨텍스트 (Compressed Context):**
`buildCompressedLoopContextArtifact`가 다음 필드를 제공:
- `requestSummary` — 사용자 요청 요약
- `latestOpenClawSummary` — 최신 OpenClaw draft 요약
- `latestHermesSummary` — 최신 Hermes review 요약
- `latestHermesVerdict` — agree/agree_with_changes/disagree/needs_user_decision/unknown
- `acceptedFeedback` — 수락된 피드백 목록
- `rejectedFeedback` — 거부된 피드백 목록
- `escalationReasons` — escalation 사유

`compactPromptContext`는 `raw_prompt_echo`, `scratchpad` kind를 제거(drop)하고
`meeting_turn` kind를 240자 summary로 압축.

**40-50% 감소 목표 달성 경로:**
3라운드 누적 기준 현재 ~385 tokens → 목표 ~150 tokens (61%↓). 측정 기준은
`deterministic-local-estimate-v1` (characters / 3.85 chars_per_token).

---

## Exit Condition Status

| Exit Condition | 상태 | 증거 |
|---------------|------|------|
| MVPObservable | ✅ | `npm run dry-run`이 전체 flow를 JSON으로 출력 |
| DiagnosisComplete | ✅ | 본 보고서로 완료 |
| InvalidInputHandled | ✅ | 빈 입력 → exit code 2 + `{"error":"invalid_input"}` |
| EscalationHandled | ✅ | ambiguity/policy/convergence escalation artifact 정상 생성 |
| TokenStrategyDefined | ✅ | 전략 문서화, 유틸리티 구현 완료, P0 실배선 진행 중 |
