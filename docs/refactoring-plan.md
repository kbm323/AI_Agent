# AI_Agent 리팩터링 계획 (2026-06-06 업데이트)

> Legacy note: 이 문서는 Runtime Architecture v2 이전 리팩터링 계획이다. OpenClaw adapter 표현은 과거 계획이며, 현재 실행 기준은 opencode-go-first + Hermes-native scheduling이다.

Decision basis: `docs/review-evidence.json` (`review-evidence.v1`, recommendation `partial_redesign`),
`docs/diagnosis-report.md` (2026-06-06 재진단).

## 평가 우선순위

이 계획은 Seed 평가 기준에 따라 변경 우선순위를 결정한다:

1. **오류 빈도** — 런타임 오류/워크플로우 실패를 줄이는 변경이 최우선
2. **유지보수 난이도** — 미래 수정 비용을 낮추는 구조 개선
3. **토큰 비용** — 루프 컨텍스트 압축으로 40-50% 토큰 감소
4. **아키텍처 적합성** — 기존 adapter 패턴 유지, 자연스러운 확장성 확보
5. **기능 완성도** — MVP 요구사항 충족 확인 후 Phase 2 로드맵 연결

## 현재 상태 요약

- **508개 테스트 중 504개 통과, 4개 실패**
- 핵심 MVP flow: 분석 → 라우팅 → OpenClaw 실행 → Hermes 리뷰 → Final synthesis → Escalation 모두 구현 완료
- 토큰 압축 유틸리티는 구현되어 있으나 orchestrator 루프에 미배선
- 진단 모듈이 런타임 코드와 혼재
- `summarizeForThread`가 단순 truncation

---

## Phase 1: 토큰 압축 실적용 + 오류 처리 강화 (P0/P1, Week 1)

### Phase 1-A: Reviewer Request 압축 배선

**목표:** `buildReviewerRequest()` 대신 압축 컨텍스트 기반 `buildReviewerRequestCompressed()` 사용.

**파일 변경:**

1. `src/orchestrator.ts` — `buildReviewerRequest` 함수 수정:
   - 압축 컨텍스트(`CompressedLoopContextArtifact`)를 받도록 확장
   - 전체 userRequest + draft 대신 `compressedContext.content` + `draftSummary(600자)` 전달
   - `buildReviewerRequestCompressed()` 함수 추가 (점진적 마이그레이션)

2. `src/orchestrator.ts` — `runUserRequest()` 루프 수정:
   - 매 라운드 시작 시 `buildCompressedLoopContextArtifact()` 호출
   - `buildReviewerRequestCompressed()`로 reviewer request 생성
   - 기존 `buildReviewerRequest`는 하위 호환성 유지 (deprecated 주석)

**예상 감소:** reviewer request 기준 40% (250자 → 150자)

### Phase 1-B: Owner/Finalizer Executor 컨텍스트 압축

**목표:** Owner, Reviewer, Finalizer executor 인터페이스에 압축 컨텍스트 전달.

**파일 변경:**

1. `src/types.ts` — executor 인터페이스 확장:
   ```ts
   interface OwnerExecutor {
     createDraft(input: {
       task: TaskRecord;
       userRequest: string;
       round: number;
       compressedContext?: CompressedLoopContextArtifact;  // NEW
     }): Promise<string>;
   }
   // ReviewerExecutor, FinalizerExecutor 동일 패턴
   ```

2. `src/orchestrator.ts` — executor 호출 시 압축 컨텍스트 전달:
   - `owner.createDraft({ ..., compressedContext })`
   - `reviewer.review({ ..., compressedContext })`
   - `finalizer.synthesize({ ..., compressedContext })`

3. `scripts/dry-run.ts` — fake executor에 압축 컨텍스트 수용 추가

**예상 감소:** 전체 루프 기준 45-55% (3라운드 누적 385 tokens → 150 tokens, 61%↓)

### Phase 1-C: isUsableModelOutput 강화

**목표:** 무의미한 모델 출력을 더 정확하게 감지.

**파일 변경:**

1. `src/orchestrator.ts` — `isUsableModelOutput` 함수 강화:
   - 최소 길이 임계값: 20자 미만이면 unusable
   - 무관 응답 패턴 필터: `/^(OK|ok|done|처리할 수 없습니다|알 수 없습니다|죄송합니다)[.!]*$/i`
   - 동일 문장 반복 감지: 같은 문장이 3회 이상 연속 등장하면 unusable
   - userRequest 단순 반복 검사 유지
   - 모든 조건을 OR로 결합 (하나라도 해당되면 unusable)

2. `tests/orchestrator.test.ts` — `isUsableModelOutput` 단위 테스트 추가:
   - 빈 문자열, 공백만 있는 경우
   - "OK", "done" 같은 짧은 응답
   - 동일 문장 3회 반복
   - 정상적인 긴 응답

### Phase 1-D: 실패 테스트 수정

**목표:** 4개 실패 테스트를 통과로 전환.

1. `docs/public-api-symbols.json` — `buildTaskGraph` 추가 (현재 `public.ts`에는 있으나 JSON에 누락)
2. `tests/command-entry-success-paths.test.ts` — 심볼 정렬 확인
3. `tests/mvp-completion-check.test.ts` — MVP completion gate 조정

---

## Phase 2: 유지보수성 개선 (P2, Week 2-3)

### Phase 2-A: 진단 모듈 분리

**목표:** 런타임 코드와 진단/메타 도구를 디렉토리로 분리.

**파일 이동:**

```
src/evaluation.ts                → src/diagnostic/evaluation.ts
src/inspection.ts                → src/diagnostic/inspection.ts
src/inventory-orchestration.ts   → src/diagnostic/inventory-orchestration.ts
src/decision-justification-report.ts → src/diagnostic/decision-justification-report.ts
src/artifact-check.ts            → src/diagnostic/artifact-check.ts
src/review-artifact-completeness.ts → src/diagnostic/review-artifact-completeness.ts
src/final-output-schema.ts       → src/diagnostic/final-output-schema.ts
src/capability-inventory.ts      → src/diagnostic/capability-inventory.ts
src/compression-verification.ts  → src/diagnostic/compression-verification.ts
src/context-storage.ts           → src/diagnostic/context-storage.ts
src/health-check.ts              → src/diagnostic/health-check.ts
src/loop-context-compression-policy.ts → src/diagnostic/loop-context-compression-policy.ts
src/meeting-transcript.ts        → src/diagnostic/meeting-transcript.ts
src/token-strategy-artifact.ts   → src/diagnostic/token-strategy-artifact.ts
src/verification-workflow-runner.ts → src/diagnostic/verification-workflow-runner.ts
src/output-normalization.ts      → src/diagnostic/output-normalization.ts
```

**src/index.ts 정리 후 남길 런타임 모듈:**
```
src/orchestrator.ts
src/db.ts
src/policies.ts
src/planning.ts
src/summarization.ts
src/role-routing.ts
src/types.ts
src/final-synthesis.ts
src/convergence-failure.ts
src/user-input-required.ts
src/token-baseline.ts
src/runtime-data.ts
src/public.ts
```

### Phase 2-B: Barrel File 자동화

**목표:** `src/index.ts` 수동 re-export → 빌드 스크립트 자동 생성.

**새 파일:** `scripts/generate-barrel.ts`
- `src/*.ts`에서 `export` 선언을 파싱하여 `src/index.ts` 생성
- `src/diagnostic/*.ts`도 포함
- `public-api-symbols.json`도 함께 갱신

### Phase 2-C: 체크 스크립트 공통화

**목표:** 30개 이상 `scripts/check-*.ts`의 중복 CLI 로직 제거.

**새 파일:**
```
scripts/_lib/cli-utils.ts       — arg parsing, JSON 출력, exit code 관리
scripts/_lib/artifact-utils.ts  — artifact 파일 읽기/쓰기/검증
```

각 체크 스크립트는 `_lib/` 유틸리티를 import하여 20줄 이내로 축소.

### Phase 2-D: 핵심 모듈 단위 테스트 추가

**목표:** 현재 간접 테스트로만 커버되는 모듈의 직접 단위 테스트 작성.

| 새 테스트 파일 | 대상 모듈 |
|----------------|-----------|
| `tests/meeting-transcript.test.ts` | `meeting-transcript.ts` |
| `tests/loop-context-compression-policy.test.ts` | `loop-context-compression-policy.ts` |
| `tests/context-storage.test.ts` | `context-storage.ts` |
| `tests/summarize-for-thread.test.ts` | `policies.ts` → `summarizeForThread` |

---

## Phase 3: Adapter 연동 준비 (P3, Week 4)

### Phase 3-A: 실제 Adapter 인터페이스 확정

**목표:** dry-run fake executor → 실제 OpenClaw/Hermes API 호출로 전환할 adapter 구조 확정.

**새 파일:**
```
src/adapters/
  openclaw-owner.ts       → OpenClaw API 호출, OwnerExecutor 구현
  hermes-reviewer.ts      → Hermes API 호출, ReviewerExecutor 구현
  openclaw-finalizer.ts   → OpenClaw API 호출, FinalizerExecutor 구현
  discord-delivery.ts     → Discord 게이트웨이, DiscordDelivery 구현 (기존 dry-run mock 교체)
```

각 adapter는 압축 컨텍스트를 기본 입력으로 받고, 전체 replay는 `audit: true` 옵션으로 제한.

### Phase 3-B: summarizeForThread 업그레이드 (선택적)

**목표:** 단순 truncation → 구조 인식 요약으로 전환.

구조 인식이 필요한 영역:
- `owner_draft`: 제목/핵심 제안/근거 구조 인식
- `review`: 평결/장점/문제점/리스크/수정안 구조 인식
- `final_synthesis`: 요청 분석/실행 결과/리뷰 결과/최종 응답 구조 인식

구조 인식이 불가능한 경우 현행 truncation으로 fallback.

---

## Phase 4-6: 260526 README 로드맵 정렬

260526 README의 Phase 2-B~4 요구사항은 MVP 이후 순차적으로 구현:

| README Phase | 요구사항 | 현황 | 우선순위 |
|-------------|----------|------|----------|
| Phase 2-B | 직무별 라우팅 (콘텐츠/아트/기술/마케팅/경영팀) | `role-routing.ts` 확장 필요 | MVP 이후 |
| Phase 2-C | Dual Persona Layer | `AgentRole` 타입 확장, 페르소나 전환 로직 | MVP 이후 |
| Phase 2-D | Retrieval & Verification Layer | 검색/검증 파이프라인 신규 | MVP 이후 |
| Phase 2-E | Human Approval & Escalation | escalation policy에 추가 패턴 | 부분 구현됨 |
| Phase 3 | Memory & Decision Log | SQLite persistence 확장 | MVP 이후 |
| Phase 4 | Notion/GitHub/Google Drive/n8n 연동 | 신규 adapter | MVP 이후 |

---

## 실행 일정

```
Week 1 (P0+P1):
  Day 1-2: Phase 1-A — buildReviewerRequestCompressed 구현 + 루프 배선
  Day 3-4: Phase 1-B — Owner/Reviewer/Finalizer 인터페이스 확장 + dry-run 연동
  Day 5:   Phase 1-C — isUsableModelOutput 강화 + 단위 테스트
  Day 5:   Phase 1-D — 실패 테스트 수정

Week 2 (P2):
  Day 1-3: Phase 2-A — 진단 모듈 → src/diagnostic/ 분리
  Day 4:   Phase 2-B — barrel file 자동화 스크립트
  Day 5:   Phase 2-C — 체크 스크립트 공통화

Week 3 (P2 계속):
  Day 1-3: Phase 2-D — 핵심 모듈 단위 테스트 추가
  Day 4-5: 통합 테스트, dry-run 검증, 토큰 감소율 측정

Week 4 (P3):
  Day 1-3: Phase 3-A — Adapter 구조 정의
  Day 4-5: Phase 3-B — summarizeForThread 업그레이드 (선택적)
```

## 검증 기준

각 Phase 완료 시:

```bash
npm test                    # 504+ 통과 (현재 504)
npm run typecheck           # 오류 없음
npm run dry-run -- --request "뮤직비디오 오프닝 아이디어를 회의해줘."  # 정상 출력
npm run dry-run -- --request ""   # exit 2 + error JSON
npm run check:token-reduction-savings-band  # 40-50% 감소 확인
npm run lint:all            # ruff + ESLint 통과
```

## 주의사항

- `installed dist` 직접 수정 금지 (개발 원칙)
- `node_modules` 직접 패치 금지
- 기존 504개 통과 테스트를 깨뜨리지 않을 것
- 진단 모듈 분리 시 import 경로 전체 업데이트
- 토큰 감소율 측정은 `deterministic-local-estimate-v1` 기준
