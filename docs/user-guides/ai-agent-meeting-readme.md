# AI_Agent 회의 사용 README

> 내가 Discord에서 `@대표`에게 명령을 던졌을 때, 어떤 상황에서 회의가 열리고, 누가 말하고, 회의록/정리본이 어디에 저장되는지 한눈에 보는 문서.

## 0. 빠르게 볼 파일

| 용도 | 파일 |
|---|---|
| 자세한 README / 기준 문서 | `docs/user-guides/ai-agent-meeting-readme.md` |
| 짧은 요약본 / 휴대용 정리 | `docs/user-guides/ai-company-meeting-system-summary.md` |

요약본은 사용자가 첨부한 `AI_Company_회의시스템_정리.md`를 프로젝트 안에 보관한 파일이다. 빠르게 구조만 볼 때는 요약본을 먼저 보고, 세부 운영 규칙이나 예시는 이 README를 본다.

## 1. 한 줄 요약

```text
@대표 회의/검토/분석/기획/해줘/! 요청
→ 새 Discord thread 생성
→ 6개 팀장 봇이 2라운드 회의
→ 안건별 내부 specialist worker 추가 분석
→ 검증/모델 evidence/fallback 포함 최종 보고 생성
→ Discord thread에 발언+최종 보고 게시, local runtime artifacts에도 저장
```

## 2. 언제 회의가 열리나?

대표 봇에게 온 메시지가 아래 조건에 걸리면 Runtime v2 회의 파이프라인이 실행된다.

| 상황 | 트리거 | 예시 | 결과 |
|---|---|---|---|
| 일반 대화 | 회의 키워드 없음 | `@대표 안녕` | 회의 안 열림. 대표/Gateway가 일반 응답 |
| 명시적 회의 | 회의, 미팅, 논의, 토론, 상의, 협의 | `@대표 야구 쇼츠 회의하자` | 새 thread 생성 후 6봇 회의 |
| 검토/리뷰 | 검토, 리뷰, 판단, 승인, 확정 | `@대표 이 기획안 검토해줘` | 검증/리스크 중심 회의 |
| 분석/전략/기획 | 분석, 전략, 기획, 평가, 진단 | `@대표 채널 성장 전략 분석해줘` | 분석 specialist 포함 가능 |
| 복합 작업 | `해줘` + 의미 있는 요청 | `@대표 자동화 파이프라인 설계해줘` | 회의 실행 |
| 강제 실행 | `!` 접두어 | `@대표 !이 안건 바로 회의 돌려` | 무조건 회의 실행 |

## 3. 한 회의 안에서 누가 참여하나?

### Discord에 직접 발언하는 6개 팀장 봇

| 역할 | 표시명 | 주 관점 | 기본 모델 |
|---|---|---|---|
| `ceo_coordinator` | 대표 | 최종 판단, 전략, 조율 | `deepseek-v4-pro` |
| `content_lead` | 콘텐츠 팀장 | 기획, 포맷, 스토리 | `qwen3.7-plus` |
| `art_lead` | 아트 팀장 | 비주얼, 디자인, 브랜딩 | `qwen3.7-plus` |
| `tech_lead` | 기술 팀장 | 구현, 자동화, 안정성 | `deepseek-v4-pro` |
| `marketing_lead` | 마케팅 팀장 | 성장, 시장, 채널 전략 | `qwen3.7-plus` |
| `validation_audit` | 검증 팀장 | 리스크, 정합성, 품질 | `glm-5.1` |

### 내부 specialist worker

내부 specialist는 Discord 봇으로 따로 말하지 않는다. 뒤에서 worker로 실행되고, 최종 보고서의 `내부 Specialist 투입` 섹션에 요약된다.

| 키워드/상황 | 투입 specialist 예시 |
|---|---|
| 야구, 스포츠, 성과, 지표, 분석, 데이터 | `data-analyst` |
| 자동화, 파이프라인, API, 백엔드, 연동, 수집 | `backend-engineer` |
| 쇼츠, 유튜브, 영상, 편집, 릴스, shorts | `video-editor` |
| 품질, 검증, 테스트, QA | `quality-assurance` |
| 음악, BGM, 사운드, 오디오 | `composer`, `sound-designer` |
| 보안, 권한, 토큰, secret | `security-engineer` |
| 법, 저작권, 계약, 컴플라이언스 | `legal-reviewer` |
| 디자인, UI, UX, 화면, 레이아웃 | `ui-ux-designer` |

## 4. 회의는 어떤 순서로 진행되나?

```text
1. Gateway가 회의 의도 감지
2. Runtime v2가 meeting_run_id 생성
3. Discord thread 생성
4. Round 1: 6개 팀장 독립 의견
5. Round 2: 6개 팀장 반박/보완
6. 안건별 internal specialist worker 실행
7. 검증 verdict 생성
8. fallback/model evidence 수집
9. final_report_v2.md 생성
10. 같은 Discord thread에 최종 보고 메시지 게시
11. Gateway summary 반환
```

### Round 1 — 독립 의견

각 팀장이 자기 관점으로 먼저 말한다.

```text
대표: 전략/최종 판단
콘텐츠: 기획/포맷/스토리
아트: 시각/브랜딩/디자인
기술: 구현/자동화/안정성
마케팅: 성장/시장/채널
검증: 리스크/정합성/품질
```

### Round 2 — 반박/보완

Round 2는 Round 1 회의록을 읽고 진행한다. 같은 말을 다시 쓰는 단계가 아니라, 다른 팀장 의견에 대한 동의·보완·최종 합의 조건을 추가하는 단계다.

```text
콘텐츠 ↔ 마케팅
기술 ↔ 아트
대표 ↔ 전체 조율
검증 ↔ 위험/누락 지적
```

Round 2 prompt는 다음을 강제한다.

```text
- 1라운드 의견 반복 금지
- 동의하는 다른 팀장 의견 1개
- 보완/반박할 다른 팀장 의견 1개
- 최종 합의에 넣을 조건 1개
```

검증/품질관리 역할은 내부 구현어를 사용자-facing 품질 언어로 바꿔 말한다.

```text
worker_execution_failed → 실패 상태로 표시
placeholder output → 임시/빈 응답
회귀 테스트 / regression test → 재발 방지 검증
evidence artifact → 검증 기록
```

## 5. 상황별 추천 명령어

| 목적 | 추천 명령 |
|---|---|
| 기본 회의 | `@대표 야구 정보 쇼츠 유튜브 콘텐츠 회의하자` |
| 분석 중심 | `@대표 야구 쇼츠 채널 성과 분석하고 개선 전략 회의해줘` |
| 기술 중심 | `@대표 콘텐츠 자동화 파이프라인 안정성 검토해줘` |
| 최종 의사결정 | `@대표 이 콘텐츠 방향으로 확정해도 되는지 검토 회의해줘` |
| 강제 실행 | `@대표 !이 안건으로 바로 회의 돌려` |
| 법무/리스크 | `@대표 선수 영상 클립 사용 저작권 리스크 검토해줘` |
| 영상/콘텐츠 | `@대표 쇼츠 편집 자동화와 영상 포맷 개선안 회의하자` |
| 음악/오디오 | `@대표 BGM과 사운드 디자인 방향 회의하자` |
| 디자인/UI | `@대표 썸네일 디자인과 화면 레이아웃 개선 회의하자` |

## 6. 한 thread 안에서 계속 이야기해도 되나?

기본 원칙:

```text
한 안건 / 한 회의 / 한 결과물 = 한 Discord thread
```

| 상황 | 같은 thread? | 이유 |
|---|---|---|
| 같은 안건의 후속 질문 | OK | 기존 회의 맥락 유지 |
| 같은 결과물 보완 | OK | 합의안/액션을 이어서 다듬기 좋음 |
| 단순 확인/요약 요청 | OK | thread 맥락 그대로 사용 |
| 같은 프로젝트지만 새 산출물 | 새 thread 권장 | 결과물 추적이 쉬움 |
| 완전히 다른 주제 | 새 thread 필수 | 맥락 오염 방지 |
| 법무/보안/비용/출시 등 고위험 검토 | 새 thread 권장 | 감사/evidence 분리 |

### 좋은 사용 방식

```text
새 회의 시작:
@대표 야구 정보 쇼츠 자동화 파이프라인 회의하자

같은 thread 후속:
기술팀 의견 기준으로 구현 순서 더 구체화해줘
마케팅팀 관점에서 제목/썸네일 A/B 테스트안 추가해줘
검증팀 기준으로 리스크만 다시 정리해줘

별도 안건:
@대표 야구 쇼츠 저작권 리스크 별도 검토 회의 열어줘
```

### 한 thread에 너무 많이 넣으면 생기는 문제

| 문제 | 설명 |
|---|---|
| 맥락 오염 | 이전 안건 결론이 새 안건에 섞임 |
| specialist 선택 오류 | 누적 키워드 때문에 엉뚱한 specialist가 투입될 수 있음 |
| 보고서 추적 어려움 | 어떤 결론이 어느 회의 결과인지 흐려짐 |
| Discord 가독성 저하 | 6봇×2라운드 발언이 계속 쌓임 |
| 검색 어려움 | thread 제목과 실제 내용이 달라짐 |

## 7. 회의록/정리본은 누가 만들고 어디에 저장되나?

회의록은 별도 “서기 봇”이 수동으로 쓰는 것이 아니다. Runtime v2가 자동으로 집계한다.

```text
6팀장 발언
+ internal specialist worker output
+ validation verdict
+ attempted_models/fallback evidence
→ Runtime v2 final_report builder
→ final_report_v2.md 생성
→ Gateway summary로 반환
```

실제 정리 로직:

```text
src/runtime_architecture_v2/multi_bot.py
  → _build_final_report()
```

Gateway summary 연결:

```text
src/runtime_architecture_v2/gateway_bridge.py
  → result.final_report를 summary로 반환
```

## 8. 저장 위치

회의마다 `meeting_run_id`가 생긴다.

예:

```text
phase14_multi_bot_operational_pilot_20260630113845600686
```

저장 위치:

```text
/home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent/runtime/meeting_runs/runtime/meeting_runs/<meeting_run_id>/
```

주의: 현재는 경로가 `runtime/meeting_runs/runtime/meeting_runs`처럼 중첩되어 저장된다. 기능상 동작하지만, 나중에 정리 대상이다.

## 9. 저장 파일 구조

```text
runtime/meeting_runs/runtime/meeting_runs/<meeting_run_id>/
├── meeting_run.json
├── decision_log.jsonl
├── final_report.md
├── final_report_v2.md
├── packets/
│   ├── msg_<id>_1_ceo_coordinator_opinion.json
│   ├── msg_<id>_1_content_lead_opinion.json
│   ├── msg_<id>_2_tech_lead_rebuttal.json
│   ├── wt_<id>_7_data-analyst.json
│   ├── wt_<id>_8_backend-engineer.json
│   └── ...
└── worker_outputs/
    ├── msg_<id>_1_ceo_coordinator_opinion.json
    ├── msg_<id>_1_content_lead_opinion.json
    ├── msg_<id>_2_tech_lead_rebuttal.json
    ├── wt_<id>_7_data-analyst.json
    ├── wt_<id>_8_backend-engineer.json
    └── ...
```

## 10. 각 파일의 의미

| 파일/폴더 | 의미 | 우선순위 |
|---|---|---|
| `final_report_v2.md` | 최종 회의 정리본. 합의안/검증/evidence/fallback 포함 | 1순위 |
| `final_report.md` | 기존 Phase13/14 호환용 보고서 | 참고용 |
| `meeting_run.json` | 회의 상태, trigger, routing, projection id 등 메타데이터 | 디버깅용 |
| `decision_log.jsonl` | 회의 중 decision/event 로그 | 감사/추적용 |
| `packets/*.json` | 각 봇/worker에게 전달된 입력 packet | 입력 추적용 |
| `worker_outputs/*.json` | 각 봇/worker의 실제 출력, 모델, fallback, 에러 | 원문/evidence |

## 11. 최종 보고서 구성

`final_report_v2.md`와 Discord 마지막 대표 보고는 판단에 필요한 순서대로 아래 섹션을 가진다.

```text
# 📋 [안건명]
**상태:** ✅ 완료 · 검증 PASS · fallback 없음

## 🎯 결론
## ✅ 합의안
## 🚀 다음 액션
## ⚠️ 리스크 / 이견
## 👥 팀장 핵심 의견
## 🧑‍💻 Specialist 투입
## 🔍 검증 상세 / 모델 Evidence
```

결론/합의안/액션은 위쪽에 두고, 모델 evidence처럼 확인만 하면 되는 정보는 아래쪽 참고 섹션으로 압축한다. 6팀장과 specialist 결과는 문단 나열이 아니라 표 형태로 한 줄씩 스캔할 수 있게 정리한다.

Discord 마지막 대표 보고와 local artifact는 같은 데이터를 쓰지만 렌더링이 다르다.

| 위치 | 렌더링 |
|---|---|
| Discord thread 마지막 보고 | Discord가 표를 렌더링하지 않으므로 bullet list + 단일 evidence code block 사용 |
| `final_report_v2.md` | GitHub/Markdown에서 보기 좋은 table 유지 |

Specialist 결과는 각 `WorkerTask.role`의 `worker_outputs/*.json`에서 직접 읽어오며, 특정 specialist가 검증팀장/다른 role 발언을 재사용하지 않도록 role별 prompt와 회귀 테스트로 고정한다.

`합의안`과 `다음 액션`은 고정 템플릿 문구만 쓰지 않는다. 팀장 발언과 specialist 결과에서 `회귀 테스트`, `evidence`, `자동화`, `UI/UX`, `fallback` 같은 실행 신호를 추출해 실제 후속 작업으로 승격한다. Discord에서는 다음 액션도 다른 섹션과 동일하게 `•` bullet 형식으로 통일한다.

합의안은 결론 문장을 다시 인용하지 않는다. 결론은 최종 판단 1문장이고, 합의안은 그 판단을 뒷받침하는 세부 결정이다. 예를 들어 `legal-reviewer placeholder` 문제가 감지되면 합의안은 `legal-reviewer placeholder는 worker_execution_failed로 표시하고 evidence 상태와 동기화한다`처럼 role/대상/처리 방식을 보존한다. 다음 액션도 generic 문구보다 `legal-reviewer placeholder output을 worker_execution_failed로 처리하는 회귀 테스트`처럼 구체 대상이 있는 문장을 우선한다.

Specialist output이 `<role> specialist output` 같은 placeholder로 들어오면 성공 산출물로 보여주지 않는다. 최종 보고에는 `worker_execution_failed: placeholder output for <role>`로 표시하고, 모델 evidence도 `⚠️ ... worker_execution_failed=placeholder_output`로 맞춘다.

### 특히 중요한 섹션

| 섹션 | 보면 좋은 경우 |
|---|---|
| `🎯 결론` | 대표 판단을 한두 문장으로 먼저 볼 때 |
| `✅ 합의안` | 회의 결론만 빠르게 보고 싶을 때 |
| `🚀 다음 액션` | 후속 작업을 뽑을 때 |
| `⚠️ 리스크 / 이견` | 막을 이슈가 있는지 확인할 때 |
| `👥 팀장 핵심 의견` | 팀장별 입장을 표로 비교할 때 |
| `🧑‍💻 Specialist 투입` | 어떤 전문가 worker가 추가 분석했는지 볼 때 |
| `🔍 검증 상세 / 모델 Evidence` | 검증팀 판단, fallback, 모델 경로를 확인할 때 |

## 12. Discord에는 무엇이 남나?

Discord thread에는 기본적으로 다음이 남는다.

```text
Round 1: 6개 팀장 의견
Round 2: 6개 팀장 반박/보완
Final: 대표가 AI_Agent 회의 최종 보고 게시
총 13개 메시지(6봇×2라운드 + 최종 보고 1개)
```

내부 specialist는 Discord에 별도 발언하지 않는다.

```text
data-analyst
backend-engineer
video-editor
quality-assurance
...
```

이런 specialist 결과는 `final_report_v2.md`와 `worker_outputs/*.json`에 저장된다.

## 13. Discord summary와 local artifact 차이

| 위치 | 내용 | 한계 |
|---|---|---|
| Discord thread | 실제 6봇 발언 + 대표의 최종 보고 메시지 | 표 대신 bullet/code block 요약. specialist 원문 전체는 직접 안 보임. 최종 보고는 2000자 제한으로 요약될 수 있음 |
| Gateway summary | final_report 기반 요약 | Discord/Gateway 표시 환경에 따라 잘릴 수 있음 |
| `final_report_v2.md` | 전체 최종 정리본 | 로컬 파일 확인 필요 |
| `worker_outputs/*.json` | 모든 발언/worker 원문과 모델 evidence | 사람이 읽기엔 JSON이라 다소 불편 |

따라서 나중에 다시 볼 때 우선순위는:

```text
1. Discord thread — 실제 회의 발언과 마지막 최종 보고 보기
2. final_report_v2.md — 회의록/최종 정리본 보기
3. worker_outputs/*.json — 원문/evidence/모델/fallback 확인
4. meeting_run.json / decision_log.jsonl — 상태/이벤트 디버깅
```

## 14. Notion / Second Brain 저장 여부

현재 live meeting path 기준 기본 저장은:

```text
Discord thread
+ local runtime artifacts
```

Notion/Second Brain 자동 저장은 기본값이 아니다.

| 저장 위치 | 자동 저장 여부 |
|---|---|
| Discord thread | 예 |
| local runtime files | 예 |
| `final_report_v2.md` | 예 |
| `worker_outputs/*.json` | 예 |
| Notion | 기본 아님 |
| Obsidian / Second Brain | 기본 아님 |
| Git commit | 기본 아님 |

Notion 저장까지 원하면 별도 knowledge/write pipeline을 붙여야 한다.

## 15. 모델 fallback은 어떻게 기록되나?

worker는 primary model을 먼저 시도하고, 실패하면 fallback chain을 순서대로 시도한다.

```text
primary model
→ 실패 시 fallback model
→ attempted_models 기록
→ final_report_v2.md의 Fallback/Evidence 섹션에 표시
```

예:

```text
content_lead: qwen3.7-plus -> deepseek-v4-pro
fallback_used=true
```

문제가 생기면 다음처럼 명시적으로 실패한다.

```text
worker_execution_failed
live_discord_thread_blocked
live_discord_publish_blocked
```

## 16. 내 사용 규칙 요약

```text
1. 새 안건은 @대표로 새 회의 요청
2. 같은 안건 후속 질문은 같은 thread에서 계속
3. 새 산출물/고위험 검토는 새 thread 권장
4. 실제 발언은 Discord thread에서 확인
5. 최종 정리본은 final_report_v2.md 확인
6. 원문/evidence는 worker_outputs/*.json 확인
7. Notion 저장은 자동이 아니므로 필요하면 별도 요청
```

## 17. 빠른 예시

### 회의 시작

```text
@대표 야구 정보 쇼츠 유튜브 콘텐츠 자동화 파이프라인과 성과 분석 회의하자
```

예상 동작:

```text
새 thread 생성
6팀장 2라운드 발언
specialist: data-analyst, backend-engineer, video-editor 등 투입
final_report_v2.md 생성
```

### 같은 thread 후속

```text
기술팀 의견 기준으로 구현 순서만 다시 정리해줘
```

예상 동작:

```text
같은 thread 맥락 유지
기술/검증 중심으로 후속 정리
```

### 새 thread 권장

```text
@대표 야구 쇼츠 저작권 리스크 별도 검토 회의 열어줘
```

예상 동작:

```text
새 thread 생성
legal-reviewer / validation 중심 검토
```

## 18. 한 줄 기억법

```text
Discord thread = 회의장
final_report_v2.md = 회의록/최종 정리본
worker_outputs = 모든 발언과 specialist 산출물 원본/evidence
meeting_run.json = 회의 상태/메타데이터
```
