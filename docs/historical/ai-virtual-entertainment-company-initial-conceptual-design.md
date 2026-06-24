# AI Virtual Entertainment Company — Final System Design
## Hermes-first Multi-Agent Meeting System

> **⚠️ STATUS: HISTORICAL / OUTDATED — 2026-06-25 기준**
>
> 이 문서는 2026년 초기 conceptual design artifact이며, 현재 canonical baseline이 아닙니다.
>
> **현재 canonical baseline:** [`docs/runtime-architecture-v2.md`](../runtime-architecture-v2.md)
> **현재 구현 상태:** Phase 23 완료 (Runtime v2 Alignment & Hardening)
>
> 이 문서와 현재 아키텍처 간 주요 불일치:
> - **OpenClaw**: 이 문서는 optional delegate로 기술하나, 현재 아키텍처에서 완전 제거됨 (Phase 23)
> - **Bot topology**: 이 문서는 29 role을 암시적 bot으로 기술하나, 실제 라이브 Discord bot은 7개 (비서 + 6팀장)
> - **보안/fail-closed**: 이 문서에 전무하나, Phase 23에서 fail-closed subphase status, warnings, secret sanitization 구현 완료
> - **Execution model**: 이 문서는 직접 모델 호출을 가정하나, 실제는 opencode-go CLI 기반 unified packet worker
>
> **보존 가치 — 현재도 유효한 부분:**
> - §2 시스템 철학 ("모델보다 직무 우선", "검증 없는 최종안 없다")
> - §5 29-role 조직도 (내부 org chart로 유효)
> - §8 10개 검증 기준
>
> ---
>
> # 아래는 원본 문서입니다 (historical reference)

> 목적: Discord를 중심으로 버추얼 엔터테인먼트 제작 회사를 AI 에이전트 조직으로 구현한다.  
> 핵심 구조는 **회의 → 합의 → 검증 → 최종 보고**이며, Hermes를 중심으로 운영하고 OpenClaw는 필요 시 실행 전문 외주 에이전트로 위임한다.

---

# 1. 최종 결론

## 선택 구조

```text
Hermes = 회사 운영 본체 / 기억 / 스킬 / 회의 진행
OpenClaw = 필요 시 호출하는 실행 전문 외주 에이전트
Codex GPT-5.5 = 최종 검증관 / 코드 감사 / 중요 승인
GLM-5.1 = 반론·리스크·논리 검증
NanoClaw/EJClaw = 원본 그대로 사용하지 않음. 구조 참고만 사용
```

## 선택 이유

- 사용자가 원하는 것은 단순 리뷰 파이프라인이 아니라 **회의형 AI 회사**다.
- NanoClaw/EJClaw식 `Worker → Reviewer → Final` 구조는 일방통행이라 부족하다.
- OpenClaw + Hermes 하이브리드는 강력하지만 설정 복잡도가 높다.
- Hermes는 memory, skills, profile, delegate_task, Discord gateway를 활용해 회의형 구조를 먼저 구현하기 좋다.
- OpenClaw는 완전히 제거하지 않고, 브라우저 컨트롤·외부 실행·복잡한 자동화가 필요할 때 위임 슬롯으로 남긴다.

---

# 2. 시스템 철학

## 핵심 원칙

```text
모델보다 직무가 우선이다.
검증 없는 최종안은 없다.
회의와 검증은 분리한다.
모든 작업은 기록되고 누적된다.
사용자는 대표이고, AI는 직원이다.
```

## 잘못된 방향

```text
단일 AI에게 전부 물어보기
모든 모델을 매번 호출하기
리뷰어가 단순히 초안만 검토하기
조직도를 그대로 1:1 상시 실행하기
```

## 올바른 방향

```text
질문
 ↓
라우팅
 ↓
관련 직무 에이전트 3~7명 회의
 ↓
합의안 생성
 ↓
검증 에이전트 검토
 ↓
필요 시 최종 승인
 ↓
사용자 보고
```

---

# 3. 최종 아키텍처

```text
사용자 / 대표
    ↓
Discord Gateway
    ↓
Hermes Meeting Coordinator
    ↓
Router Layer
    ↓
Round Table Meeting
    ↓
Consensus Builder
    ↓
Validation Layer
    ↓
Final Synthesis
    ↓
사용자 보고
```

## 컴포넌트 역할

| 계층 | 역할 | 담당 |
|---|---|---|
| Discord Gateway | 사용자 입력, Thread 운영 | Hermes |
| Meeting Coordinator | 회의 진행자, 라우팅 총괄 | Hermes |
| Router Layer | 관련 팀/직무 선정 | Hermes Skill |
| Round Table | 직무별 회의 | Hermes subagents |
| Consensus Builder | 합의안 작성 | Hermes |
| Validation Layer | 반론·리스크·누락 검증 | GLM-5.1 / Codex |
| Execution Delegate | 외부 작업 위임 | OpenClaw optional |
| Memory Layer | 결정·회의·사용자 선호 누적 | Hermes memory/skills |

---

# 4. 기본 워크플로우

## 표준 회의 흐름

```text
1. 사용자 질문 수신
2. Meeting Coordinator가 질문 유형 분석
3. Router가 관련 팀/직무 선정
4. 회의 에이전트 3~7명 소집
5. 각 에이전트가 독립 의견 제시
6. 에이전트 간 반론 및 수정
7. Consensus Builder가 합의안 작성
8. Validation Agent가 검증
9. 중요 사안은 Codex GPT-5.5 최종 승인
10. 최종 보고
11. 결정사항 memory/skill에 저장
```

## 회의 라운드 규칙

```text
Round 1: 각 직무 독립 의견
Round 2: 상호 반론 및 수정
Round 3: 합의안 도출
Validation: 검증 에이전트가 오류·리스크 탐지
Final: 최종 정리
```

## 무한 회의 방지

```text
기본 라운드: 3
합의 실패 시: 쟁점 요약 후 추가 라운드
중요 판단 필요 시: 사용자에게 에스컬레이션
```

---

# 5. 조직 구조

## 5.1 경영 레이어

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 대표이사 / CPO | 최종 승인, 전략 판단, 우선순위 결정 | Codex GPT-5.5 |
| 비즈그룹 | 프로젝트 조율, 팀 간 충돌 해결 | Qwen3.7 Max |
| 그룹장 | 회의 라우팅, 에스컬레이션 판단 | GLM-5.1 |

---

## 5.2 콘텐츠제작팀

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 팀장 | 콘텐츠 방향 총괄 | Kimi K2.6 |
| 콘텐츠 PD | 기획, 구성, 연출, 시청자 몰입 | Kimi K2.6 |
| 스크립트 작가 | 대본, 내레이션, 대사, 감정선 | Kimi K2.6 |
| 영상편집자 | 편집 흐름, 컷 구성, 시청 유지율 | Kimi K2.5 |
| 썸네일 디자이너 | 클릭률, 비주얼 훅, A/B 관점 | MiniMax M3 |
| 번역·자막 담당 | 다국어 자막, 문맥 보존 | Qwen3.6 Plus |

---

## 5.3 아트팀

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 팀장 | 전체 비주얼 방향 총괄 | MiniMax M3 |
| 컨셉 아티스트 | 세계관, 무드보드, 레퍼런스 | MiniMax M3 |
| 캐릭터 디자이너 | 캐릭터 원안, 개성, 상품성 | MiniMax M3 |
| 리거 | Live2D/3D 리깅, 구조, 제약 검토 | DeepSeek V4 Pro |
| 애니메이터 | 모션, 표정, 립싱크, 연기 | Kimi K2.5 |
| VFX 아티스트 | 이펙트, 파티클, 쉐이더 | DeepSeek V4 Pro |
| 배경·무대 아티스트 | 방송 배경, 무대, 공간 연출 | MiniMax M3 |
| 음향 엔지니어 | BGM, 효과음, 믹싱, 음성 품질 | Kimi K2.6 |

---

## 5.4 기술팀

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 팀장 | 기술 방향 총괄 | DeepSeek V4 Pro |
| R&D 파트 리드 | 기술 연구 방향, PoC 관리 | DeepSeek V4 Pro |
| Technical Artist | 아트↔기술 브릿지, 파이프라인 | DeepSeek V4 Pro |
| 파이프라인 R&D | 자동화, 워크플로우, 툴 제작 | DeepSeek V4 Pro |
| 신기술 프로토타이핑 | 실험, 빠른 검증 | DeepSeek V4 Flash |
| 모션캡처·트래킹 연구 | 트래킹, 모캡, 실시간 입력 | DeepSeek V4 Pro |
| 인프라·개발 파트 리드 | 시스템 설계, 개발 관리 | Codex GPT-5.5 |
| 스트리밍 엔지니어 | OBS, 송출, 방송 안정성 | DeepSeek V4 Pro |
| 웹·앱 개발자 | 웹, 앱, 내부 도구 개발 | Codex GPT-5.5 |
| 데이터 분석가 | 지표 분석, 수익/팬 데이터 | Qwen3.7 Max |

---

## 5.5 마케팅·커뮤니티팀

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 팀장 | 성장 전략 총괄 | Qwen3.7 Max |
| 마케터 | SNS, 광고, 캠페인, 바이럴 | Qwen3.7 Max |
| 커뮤니티 매니저 | 팬 반응, 커뮤니티 운영 | Kimi K2.6 |
| IP 협업 기획자 | 콜라보, 광고, 제휴 리스크 | GLM-5.1 |
| 굿즈 MD | 상품 기획, 수요 예측, 판매 전략 | Qwen3.7 Max |

---

## 5.6 경영지원팀

| 직무 | 역할 | 추천 모델 |
|---|---|---|
| 팀장 | 경영지원 총괄, 리스크 관리 | GLM-5.1 |
| 사업개발(BD) | 제휴, 플랫폼 계약, 글로벌 진출 | Qwen3.7 Max |
| 법무·계약 담당 | IP, 저작권, 계약 리스크 | GLM-5.1 |
| 재무·경리 | 예산, 비용, 수익성 검토 | GLM-5.1 |
| HR | 조직문화, 채용, 역할 설계 | Kimi K2.6 |

---

# 6. 모델 운영 원칙

## 6.1 역할별 기본 모델

| 역할군 | 기본 모델 |
|---|---|
| 콘텐츠·서사 | Kimi K2.6 |
| 영상 편집·모션 감각 | Kimi K2.5 |
| 아트·비주얼 발산 | MiniMax M3 |
| 기술·구현 가능성 | DeepSeek V4 Pro |
| 빠른 기술 실험 | DeepSeek V4 Flash |
| 전략·시장·데이터 | Qwen3.7 Max |
| 번역·보조 처리 | Qwen3.6 Plus |
| 반론·리스크 검증 | GLM-5.1 |
| 최종 승인·코드 감사 | Codex GPT-5.5 |

## 6.2 멀티모델 사용 기준

```text
멀티에이전트 = 기본값
멀티모델 = 제한 사용
```

### 항상 사용하는 검증

- 직무 간 반론
- 검증 에이전트
- 합의안 검토

### 멀티모델을 추가로 쓰는 경우

| 상황 | 추가 모델 |
|---|---|
| 코드/자동화/서버 | Codex GPT-5.5 |
| 법무·계약·리스크 | GLM-5.1 |
| 데이터·시장 판단 | Qwen3.7 Max |
| 기술 구현성 | DeepSeek V4 Pro |
| 최종 중요 결정 | Codex GPT-5.5 |

---

# 7. 라우팅 규칙

## 질문 유형별 라우팅

| 질문 유형 | 소집 대상 |
|---|---|
| 뮤비 기획 | 콘텐츠PD, 스크립트 작가, 아트디렉터, VFX, 마케팅 |
| 캐릭터 디자인 | 컨셉아티스트, 캐릭터디자이너, 리거, 애니메이터, 마케팅 |
| Unreal/VFX | Technical Artist, VFX, 파이프라인 R&D, Codex, GLM |
| 영상 편집 | 영상편집자, 콘텐츠PD, 썸네일디자이너, 마케팅 |
| SNS/마케팅 | 마케터, 커뮤니티매니저, IP기획자, 데이터분석가 |
| 사업/계약 | BD, 법무, 재무, 그룹장 |
| 예산/우선순위 | 그룹장, 재무, 팀장, Codex |
| 법무/저작권 | 법무, IP기획자, GLM, Codex |
| 자동화/개발 | 웹·앱개발자, 파이프라인 R&D, Codex, GLM |

---

# 8. 검증 기준

## 모든 분야 공통 검증

검증 에이전트는 다음 항목을 반드시 확인한다.

```text
1. 논리 오류
2. 누락된 관점
3. 실행 가능성
4. 비용/시간 리스크
5. 브랜드 일관성
6. 사용자 목표와의 일치
7. 환각 가능성
8. 근거 부족
9. 과도한 낙관
10. 에스컬레이션 필요 여부
```

## 분야별 검증

| 분야 | 검증 기준 |
|---|---|
| 콘텐츠 | 재미, 몰입, 구조, 감정선, 시청 지속성 |
| 아트 | 비주얼 일관성, 구현 가능성, 캐릭터성, 차별성 |
| 기술 | 실제 구현 가능성, 유지보수, 성능, 자동화 가능성 |
| 마케팅 | 클릭률, 팬 반응, 브랜드 리스크, 확산성 |
| 법무 | 저작권, 계약 리스크, IP 충돌 |
| 재무 | 예산, 수익성, 비용 대비 효과 |
| 코드 | 동작, 테스트, 보안, 유지보수, 확장성 |

---

# 9. OpenClaw 위임 슬롯

OpenClaw는 기본 운영 본체가 아니다.  
필요할 때 호출하는 실행 전문 외주 에이전트다.

## OpenClaw를 호출하는 경우

```text
브라우저 자동화
외부 웹 조작
복잡한 Discord 운영
여러 컴퓨터 노드 제어
장시간 실행 작업
별도 실행 환경이 필요한 작업
```

## 위임 흐름

```text
Hermes Meeting Coordinator
    ↓
필요 작업 감지
    ↓
OpenClaw에 실행 위임
    ↓
OpenClaw 결과 반환
    ↓
Hermes가 검토/합성
    ↓
사용자 보고
```

---

# 10. NanoClaw/EJClaw 처리 방침

## 원본 그대로 사용하지 않는 이유

```text
NanoClaw/EJClaw 구조:
Worker → Reviewer → Final

사용자가 원하는 구조:
회의 에이전트 3~7명
↕
상호 반론
↕
합의
↓
검증
↓
최종
```

NanoClaw/EJClaw는 리뷰 파이프라인으로는 좋지만, 회의형 AI 회사에는 부족하다.

## 차용할 요소

```text
작업 Thread 중심 구조
Main/Review/Final 단계 구분
검토자 분리 원칙
Discord 기반 운영 방식
```

---

# 11. Discord 서버 구조

```text
🏢 AI Virtual Entertainment Company
│
├── 📋 경영
│   ├── #전략-회의실
│   └── #일일-브리핑
│
├── 🎬 콘텐츠제작팀
│   ├── #기획-스크립트
│   └── #편집-썸네일
│
├── 🎨 아트팀
│   ├── #캐릭터-디자인
│   ├── #리깅-애니메이션
│   └── #vfx-음향
│
├── ⚙️ 기술팀
│   ├── #r&d-파이프라인
│   └── #인프라-데이터
│
├── 📣 마케팅팀
│   ├── #sns-캠페인
│   └── #커뮤니티-ip
│
├── 🏢 경영지원팀
│   ├── #사업-계약
│   └── #재무-hr
│
└── 🔀 크로스팀
    ├── #전체-회의
    ├── #전체-리뷰
    └── #마스터-컨트롤
```

---

# 12. 파일/스킬 구조

```text
ai-agent-company/
│
├── README.md
├── SYSTEM_DESIGN.md
│
├── docs/
│   ├── organization.md
│   ├── meeting-protocol.md
│   ├── routing-rules.md
│   ├── validation-rules.md
│   └── model-map.md
│
├── skills/
│   ├── meeting-coordinator.md
│   ├── round-table.md
│   ├── consensus-builder.md
│   ├── validation-agent.md
│   ├── escalation-rule.md
│   └── openclaw-delegate.md
│
├── personas/
│   ├── executive/
│   ├── content/
│   ├── art/
│   ├── tech/
│   ├── marketing/
│   └── business-support/
│
├── memory/
│   ├── company-memory.md
│   ├── decision-log.md
│   ├── project-history.md
│   └── user-preferences.md
│
└── tests/
    ├── meeting-simulation.md
    ├── routing-test.md
    ├── validation-test.md
    └── regression-checklist.md
```

---

# 13. 핵심 스킬 정의

## meeting-coordinator

```text
역할:
- 사용자 질문 분석
- 회의 필요 여부 판단
- 관련 팀/직무 선정
- 회의 라운드 진행
- 합의안 요청
- 검증 요청
- 최종 보고
```

## round-table

```text
역할:
- 각 에이전트 독립 의견 수집
- 상호 반론 유도
- 충돌점 정리
- 추가 라운드 필요 여부 판단
```

## consensus-builder

```text
역할:
- 공통 의견 추출
- 충돌 의견 정리
- 채택/보류/기각 분류
- 실행 가능한 합의안 작성
```

## validation-agent

```text
역할:
- 논리 오류 검출
- 환각 가능성 탐지
- 리스크 분석
- 빠진 관점 지적
- 사용자 승인 필요 여부 판단
```

## openclaw-delegate

```text
역할:
- Hermes가 직접 처리하기 어려운 외부 실행 작업 식별
- OpenClaw에 위임할 작업 명세 작성
- 반환 결과를 회의 시스템으로 재투입
```

---

# 14. 최종 운영 예시

## 예시 1: 뮤비 첫 장면 기획

```text
사용자:
"신카이 마코토풍 버추얼 아이돌 뮤비 첫 장면 기획해줘"

회의 소집:
- 콘텐츠PD / Kimi K2.6
- 컨셉아티스트 / MiniMax M3
- VFX아티스트 / DeepSeek V4 Pro
- 마케터 / Qwen3.7 Max

검증:
- GLM-5.1

최종:
- Hermes synthesis
```

## 예시 2: Unreal 자동 렌더링 스크립트

```text
사용자:
"언리얼에서 자동 렌더링 파이프라인 만들고 싶어"

회의 소집:
- Technical Artist / DeepSeek V4 Pro
- 파이프라인 R&D / DeepSeek V4 Pro
- 웹·앱개발자 / Codex GPT-5.5

검증:
- GLM-5.1
- Codex GPT-5.5 코드 감사

필요 시:
- OpenClaw 외부 실행 위임
```

## 예시 3: 굿즈 기획

```text
사용자:
"캐릭터 데뷔 굿즈 구성 추천해줘"

회의 소집:
- 캐릭터디자이너 / MiniMax M3
- 굿즈 MD / Qwen3.7 Max
- 마케터 / Qwen3.7 Max
- 재무 / GLM-5.1

검증:
- GLM-5.1

최종:
- 수익성/브랜드 리스크 포함 보고
```

---

# 15. 최종 요약

```text
이 시스템은 Hermes를 중심으로 한 회의형 AI 회사다.

Hermes는 기억, 스킬, 회의 진행, Discord 운영을 담당한다.
직무 에이전트는 질문에 따라 3~7명만 소집된다.
모든 결과는 검증 에이전트를 통과한다.
중요 사안과 코드는 Codex GPT-5.5가 최종 검증한다.
OpenClaw는 기본 본체가 아니라 실행 전문 위임 슬롯이다.
NanoClaw/EJClaw는 참고 구조만 차용한다.
```

## 최종 공식

```text
Hermes-first
+ Multi-agent meeting
+ Role-based personas
+ Controlled multi-model validation
+ Optional OpenClaw delegation
= AI Virtual Entertainment Company
```
