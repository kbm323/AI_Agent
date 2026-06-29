# Personal Notion Second Brain Assistant — 기록/백업

Created: 2026-06-28 01:29:59 KST
Scope: `aicompanyassistant` / 비서 봇이 Notion Second Brain에서 노트·할 일·스케줄·아이디어를 처리하도록 만든 전용 Hermes skill + helper script 기록이다.

## 원본 위치

```text
/home/kbm/.hermes/profiles/aicompanyassistant/skills/productivity/personal-notion-second-brain/SKILL.md
/home/kbm/.hermes/profiles/aicompanyassistant/skills/productivity/personal-notion-second-brain/scripts/notion_second_brain.py
```

## 백업 위치

```text
/home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent/docs/operations/personal-notion-second-brain-assistant/
├── README.md
├── SKILL.md.backup
├── scripts/notion_second_brain.py
├── notion-inventory-summary.json
├── manifest.json
└── backups/
    ├── personal-notion-second-brain-assistant_2026-06-28_0129KST.tar.gz
    └── personal-notion-second-brain-assistant_2026-06-28_0129KST.tar.gz.sha256
```

## 포함한 자료

- `SKILL.md.backup`: 비서 봇 전용 `personal-notion-second-brain` 스킬 원문 백업.
- `scripts/notion_second_brain.py`: Notion helper script 백업.
- `notion-inventory-summary.json`: 당시 확인한 Notion Second Brain DB 구조/카운트/속성/영역·자원 목록 요약. 토큰은 포함하지 않음.
- `manifest.json`: 파일 크기와 sha256 체크섬.
- `backups/*.tar.gz`: 위 핵심 자료 압축 백업.

## 의도

비서 봇에게 자연어로 말하면 다음 작업을 하도록 만든다.

- 명시적 노트/아이디어/자료 → `노트` DB에 바로 생성.
- 명시적 할 일/일정 → `할 일` DB에 바로 생성.
- 노트에서 파생되는 다음 행동 → 사용자 확인 후 `할 일` 생성.
- 새 영역/자원/프로젝트/목표 → 사용자 확인 후 생성.
- 삭제/완료/아카이브 → 후보를 보여주고 사용자 확인 후 처리.
- 웹서치 → 필요 여부를 노트에 남기고, 실제 검색은 사용자 확인 후 실행.
- 기존 넓은 영역/자원 재사용, 중복 연결 허용.

## 현재 확정된 Notion 해석

### 영역

- 3D
- 세컨드 프로젝트
- 재무관리
- 운동
- 유튜브
- 가족
- 나 매뉴얼

### 자원

- 자기 개발 · 계발
- 컴퓨터
- 노션
- 자동화
- 여행
- 건강
- 명언
- 음식
- 영화
- 타로 · 점술 · 운세
- 과학
- AI
- 영상

### 사용자 정의 의미

- `나 매뉴얼`: 개인 운영 매뉴얼 / 자기이해 / 비서가 참고할 본민님 기준.
- `세컨드 프로젝트`: 부업/사이드프로젝트 전체 바운더리.
- `AI_Agent`: 새 영역을 만들지 않고 `AI`, `자동화`, `컴퓨터` 중심으로 연결. Notion/Hermes 관련이면 `노션`, 콘텐츠화면 `유튜브`/`영상` 추가.

## helper 사용법

원본 script 기준:

```bash
SCRIPT=/home/kbm/.hermes/profiles/aicompanyassistant/skills/productivity/personal-notion-second-brain/scripts/notion_second_brain.py
python3 "$SCRIPT" inventory
python3 "$SCRIPT" propose-note --bot assistant --text "AI_Agent 회의 내용을 쇼츠로 만들면 좋겠다"
python3 "$SCRIPT" propose-task --text "내일 3시 미용실 예약" --date 2026-06-28
python3 "$SCRIPT" create-note --title "제목" --body "본문" --areas AI 자동화
python3 "$SCRIPT" create-note --title "제목" --body "본문" --areas AI 자동화 --confirm
python3 "$SCRIPT" create-task --title "할 일" --clarify 다음행동 --date 2026-06-28
python3 "$SCRIPT" create-task --title "할 일" --clarify 다음행동 --date 2026-06-28 --confirm
```

`create-note` / `create-task`는 기본 dry-run이다. 실제 Notion write는 `--confirm`을 붙였을 때만 실행된다.

## 검증 기록

구현 당시 확인한 사항:

- `inventory` 성공: `영역 · 자원`, `노트`, `할 일`, `프로젝트`, `목표` data source 모두 resolved.
- `python3 -m py_compile .../notion_second_brain.py` 통과.
- frontmatter validation 통과.
- `propose-note` 정상 동작: AI_Agent 쇼츠 예시에서 `AI`, `유튜브` 추천.
- `propose-task` 정상 동작: “내일 3시 미용실 예약” → `명료화=일정`.
- `create-note` dry-run 정상.
- `create-task` dry-run 정상.
- secret scan: `secret_pattern_hits=0`.
- `gw_aicompanyassistant` 재시작 후 Notion env presence 확인.
- Discord adapter log에서 `/skill` 등록 수가 114 → 115로 증가해 스킬 로드 반영 확인.

## 복구 절차

스킬이 깨졌거나 삭제되었을 때:

```bash
BACKUP=/home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent/docs/operations/personal-notion-second-brain-assistant
DEST=/home/kbm/.hermes/profiles/aicompanyassistant/skills/productivity/personal-notion-second-brain
mkdir -p "$DEST/scripts"
cp "$BACKUP/SKILL.md.backup" "$DEST/SKILL.md"
cp "$BACKUP/scripts/notion_second_brain.py" "$DEST/scripts/notion_second_brain.py"
chmod +x "$DEST/scripts/notion_second_brain.py"
```

그 다음 비서 gateway 재시작:

```bash
tmux kill-session -t gw_aicompanyassistant 2>/dev/null || true
tmux new-session -d -s gw_aicompanyassistant -x 120 -y 40 \
  "bash -lc 'set -a; source ~/.hermes/profiles/aicompanyassistant/.env; set +a; exec hermes --profile aicompanyassistant gateway run'"
```

## 주의

- 이 백업에는 Notion 토큰 값이나 `.env` 파일을 포함하지 않는다.
- Notion 실제 생성은 helper의 `--confirm` 사용 시에만 발생한다.
- 삭제/완료/새 taxonomy 생성은 사용자 확인 후 수행한다.

## 2026-06-28 incident note — 일정이 Notion 캘린더에 안 보인 문제

증상:
- 사용자 요청: `내일 3시 미용실 예약했어 일정기록해줘`
- 비서 응답은 완료라고 했지만 Notion 캘린더에는 일정이 보이지 않음.

확인 결과:
- 비서 세션 `20260627_221109_949a056f`에서 `personal-assistant-automation` 스킬이 로드됨.
- 실제 작업은 Notion write가 아니라 Hermes cron reminder 생성이었음.
- 생성된 cron: `3bfad159c1be`, `미용실 예약 리마인더`, 2026-06-29 14:00 KST.
- Notion `할 일` DB 최근 항목에는 새 미용실 일정이 없었음.

보정:
- Notion `할 일` DB에 `미용실 예약`을 생성함.
- 속성: `명료화=일정`, `날짜=2026-06-29T15:00:00+09:00`, `완료=false`.
- Notion page: `https://app.notion.com/p/38cc7162411f814f9425cf69126a8f10`

재발 방지:
- `personal-assistant-automation` 스킬에서 일정 기록/캘린더/예약 요청은 cron이 아니라 `personal-notion-second-brain` 우선이라고 패치함.
- `personal-notion-second-brain` 스킬에 `일정기록해줘`, `일정 추가해줘`, `캘린더에 넣어줘`, `예약했어` 트리거를 추가함.
- 알림/리마인더를 명시한 경우에만 Hermes cron을 추가로 만들도록 분리함.

## 2026-06-28 policy update — Notion 기록 vs Hermes cron 경계

사용자 확인:
- 노트 생성, 명시적 할 일 생성, 일정 기록은 Hermes cron이 먼저 처리하면 안 된다.
- Notion Second Brain write가 원본 기록이고, Hermes cron은 반복 실행/알림 전달용이다.

패치:
- `personal-assistant-automation` 설명과 본문에 Notion vs Hermes cron boundary를 추가했다.
- `personal-notion-second-brain` 우선순위를 일정뿐 아니라 노트/아이디어/할 일/상태 변경까지 확장했다.
- helper `notion_second_brain.py`에 `노션에 저장해줘:`, `일정기록해줘:` 같은 명령 접두어 제거를 추가했다.

검증:
- 잘못 생성된 `3bfad159c1be` 미용실 reminder cron은 삭제했고 `cron list`에서 미검출 확인.
- `propose-note`는 `노션에 저장해줘: AI 회의 내용을 쇼츠로 만들기 아이디어`를 제목 `AI 회의 내용을 쇼츠로 만들기 아이디어`로 정리함.
- `propose-task`는 `일정기록해줘: 내일 3시 미용실 예약`을 제목 `내일 3시 미용실 예약`, `명료화=일정`으로 정리함.
