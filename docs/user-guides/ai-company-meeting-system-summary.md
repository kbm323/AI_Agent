<!--
원본: C:/Users/KBM/Downloads/AI_Company_회의시스템_정리.md
용도: AI_Agent 회의 시스템 사용자용 빠른 요약본. 자세한 README는 docs/user-guides/ai-agent-meeting-readme.md 참고.
-->

# AI Company 회의 시스템 정리

## 1. 전체 흐름

```
@대표 요청 (Discord)
→ Hermes Gateway 수신
→ 트리거 판정 (회의/검토/분석/해줘/!)
→ 회의 파이프라인 실행
→ 스레드 생성 → 6팀장 2라운드 발언 → specialist 투입
→ 최종 보고 생성 → 스레드에 게시
```

---

## 2. 참여 봇 (Discord에 직접 발언하는 6명)

| 역할 | 기본 모델 |
|---|---|
| 대표 | deepseek-v4-pro |
| 콘텐츠 팀장 | qwen3.7-plus |
| 아트 팀장 | qwen3.7-plus |
| 기술 팀장 | deepseek-v4-pro |
| 마케팅 팀장 | qwen3.7-plus |
| 검증 팀장 | glm-5.1 |

> 29개 specialist는 Discord에 안 나오고 내부 worker로만 실행 → 최종 보고에 요약

---

## 3. 트리거 종류별 동작

| 요청 유형 | 예시 | 동작 |
|---|---|---|
| 일반 대화 | "안녕", "뭐해?" | 회의 안 열림, 그냥 응답 |
| 회의 요청 | "~회의하자 / 열어줘" | 6팀장 회의 시작 |
| 검토/리뷰 | "검토해줘 / 승인 / 확정" | 회의 + 기술·마케팅·검증 강화 |
| 분석/기획 | "분석해줘 / 기획해줘" | 회의 + specialist 키워드 매핑 투입 |
| "해줘" | 3단어 이상 의미있는 요청 | 회의 실행 (너무 짧으면 일반대화 처리) |
| 강제 실행(!) | "!이 안건으로 바로 회의" | 무조건 회의 실행 |

---

## 4. Specialist 키워드 매핑 (예시)

- 자동화 / 파이프라인 / API / 백엔드 → `backend-engineer`
- 보안 / 권한 / 토큰 → `security-engineer`
- 품질 / 검증 / QA → `quality-assurance`
- 쇼츠 / 유튜브 / 영상 / 편집 → `video-editor`
- 야구 / 성과 / 분석 → `data-analyst`
- BGM / 사운드 → `composer`, `sound-designer`
- 저작권 / 계약 / 법무 → `legal-reviewer`
- 썸네일 / UI / 레이아웃 → `ui-ux-designer`

---

## 5. 회의 내부 진행 (기본 2라운드)

- **1라운드**: 각 팀장 독립 의견 (전략/기획/디자인/구현/마케팅/리스크)
- **2라운드**: 반박·보완 (콘텐츠↔마케팅, 기술↔아트, 검증↔전체 지적)
- **Specialist**: 안건 키워드 따라 백그라운드 실행 후 보고에 요약

---

## 6. 실패/Fallback 처리

```
primary model 시도 → 실패 시 fallback → attempted_models 기록
→ 최종 보고에 fallback_used 표시
```

심하면 아래 명시적 에러로 처리됨:
- `worker_execution_failed`
- `live_discord_thread_blocked`
- `live_discord_publish_blocked`

---

## 7. Thread 운영 규칙 (한 회의 = 한 thread)

| 상황 | 규칙 |
|---|---|
| 같은 안건 후속 질문 | ✅ 같은 thread |
| 같은 프로젝트, 다른 산출물 | ⭐ 새 thread 권장 |
| 완전히 다른 주제 | 🆕 반드시 새 thread |
| 단순 확인/요약 | ✅ 같은 thread |
| 법무/보안/비용/출시 등 고위험 | 🆕 별도 thread 권장 |

**너무 긴 thread의 문제**
- 맥락 오염
- specialist 선택 오류
- 보고서 추적 어려움
- Discord 가독성 저하
- 나중에 검색 어려움

> **한 줄 정리**: 논의·후속 보완은 같은 thread, 안건/산출물이 바뀌면 새 thread

---

## 8. 회의록 저장 구조

### 담당

| 담당 | 역할 |
|---|---|
| 대표 / Gateway | 회의 시작, thread 생성, summary 반환 |
| 6팀장 봇 | Discord thread 안에서 실제 발언 |
| specialist worker | 안건별 백그라운드 분석 |
| Runtime v2 코드 | 전체 집계 → `_build_final_report()` |

### 저장 위치

```
runtime/meeting_runs/runtime/meeting_runs/<meeting_run_id>/
├── meeting_run.json          # 회의 메타데이터
├── decision_log.jsonl        # 의사결정/이벤트 로그
├── final_report.md           # 레거시 보고서
├── final_report_v2.md        # ⭐ 최종 정리본 (기준)
├── packets/*.json            # 각 봇/worker 입력 패킷
└── worker_outputs/*.json     # 각 봇/worker 실제 출력 (모델·fallback 포함)
```

### 저장 위치별 자동 저장 여부

| 위치 | 자동 저장 |
|---|---|
| Discord thread | ✅ |
| local runtime files | ✅ |
| final_report_v2.md | ✅ |
| worker_outputs JSON | ✅ |
| Notion | ❌ 기본 아님 |
| Obsidian / Second Brain | ❌ 기본 아님 |
| Git commit | ❌ 기본 아님 |

### 나중에 결과 다시 볼 때 우선순위

```
1. Discord thread        → 실제 회의 발언 (회의장)
2. final_report_v2.md    → 정리본/합의안 (회의록)
3. worker_outputs/*.json → 봇/전문가 원문·모델 evidence
4. meeting_run.json / decision_log.jsonl → 상태·이벤트 추적
```

---

## 핵심 요약

- **Discord thread** = 회의장
- **final_report_v2.md** = 회의록 / 최종 정리본
- **worker_outputs/** = 모든 발언·전문가 산출물 원본

---

## 현재 한계 / 알아둘 점

1. Discord에 말하는 건 6팀장 뿐, 29개 specialist는 뒤에서만 작동
2. 회의는 기본 2라운드 (3라운드는 설계상 가능하나 미적용)
3. slash command `/meeting`은 기본 경로 아님 → 자연어 `@대표 ~회의해줘`가 기본
4. Discord 메시지 2000자 제한으로 요약이 잘릴 수 있음 → 원본은 `final_report_v2.md`
5. 저장 경로가 `meeting_runs/runtime/meeting_runs`로 중첩되어 있음 (추후 정리 필요)
