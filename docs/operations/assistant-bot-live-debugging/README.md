# Assistant Bot Live Debugging — 기록/백업

Created: 2026-06-28 KST
Scope: Discord AI-company bots, especially `aicompanyassistant`, `aicompanytech`, and `aicompanyquality`.

## 목적

Discord에서 비서 봇이나 다른 AI 회사 봇에게 일을 시켰는데 실제 결과가 틀렸을 때, 기술팀장/품질팀장이 단순 분석에 그치지 않고 다음 루틴으로 처리하도록 만든 운영 스킬이다.

```text
문제 신고
→ 로그/세션/외부 상태 직접 확인
→ 원인 분류
→ 수정 가능하면 스킬/config/helper 패치
→ gateway 재시작
→ 실제 상태 재검증
→ 기록/백업
→ 최종 보고만 사용자에게 출력
```

## 설치 위치

원본/배포본은 동일 내용이다.

```text
/home/kbm/.hermes/profiles/aicompanytech/skills/devops/assistant-bot-live-debugging/SKILL.md
/home/kbm/.hermes/profiles/aicompanyquality/skills/devops/assistant-bot-live-debugging/SKILL.md
/home/kbm/.hermes/profiles/aicompanyassistant/skills/devops/assistant-bot-live-debugging/SKILL.md
```

## 역할 분담

- 품질팀장: 실제 로그/API/Notion/cron/config 상태를 확인해서 bot answer와 external truth를 대조한다.
- 기술팀장: 원인 skill/config/helper를 최소 패치하고 재발 방지 규칙을 남긴다.
- 실행 agent: 로컬 WSL 파일 수정, gateway 재시작, 백업/검증을 수행한다.

권한이 없는 봇은 수정했다고 말하지 말고 handoff를 작성해야 한다.

## 주요 규칙

- 봇의 최종 답변은 증거가 아니다. 실제 Notion/cron/Discord/gateway 상태를 직접 조회한다.
- Notion records first: 노트/할 일/일정은 Notion이 원본 기록이다.
- Hermes cron은 반복 실행/알림 전달/모니터링용이다.
- config/skill/helper 변경 후에는 해당 gateway profile만 재시작한다.
- Discord 최종 보고에는 raw terminal/tool trace를 넣지 않는다.

## 관련 스킬/파일

```text
personal-notion-second-brain
personal-assistant-automation
notion-agent-operations
hermes-agent
systematic-debugging
```

관련 작업 백업:

```text
/home/kbm/F:ai-projects/10_PROJECTS/2026-06_AI_Agent/docs/operations/personal-notion-second-brain-assistant/
```

## 검증 명령

```bash
python3 - <<'PY'
import yaml
from pathlib import Path
for p in [
 Path('/home/kbm/.hermes/profiles/aicompanytech/skills/devops/assistant-bot-live-debugging/SKILL.md'),
 Path('/home/kbm/.hermes/profiles/aicompanyquality/skills/devops/assistant-bot-live-debugging/SKILL.md'),
 Path('/home/kbm/.hermes/profiles/aicompanyassistant/skills/devops/assistant-bot-live-debugging/SKILL.md'),
]:
    text=p.read_text(encoding='utf-8')
    assert text.startswith('---')
    end=text.find('\n---\n',3); assert end!=-1
    fm=yaml.safe_load(text[3:end])
    assert fm.get('name') == 'assistant-bot-live-debugging'
    assert fm.get('description') and len(fm['description']) <= 1024
print('all_skill_frontmatter_ok')
PY
```

## 복구 방법

1. 이 폴더의 `SKILL.md.backup`을 필요한 프로필에 복사한다.
2. 프로필별 경로:
   ```text
   /home/kbm/.hermes/profiles/<profile>/skills/devops/assistant-bot-live-debugging/SKILL.md
   ```
3. 해당 gateway를 재시작한다.
4. `hermes --profile <profile> skills list` 또는 Discord `/skill` autocomplete에서 확인한다.

## 현재 한계

- 기술팀장/품질팀장 봇이 항상 cross-profile write 권한을 가진다는 뜻은 아니다.
- 권한이 없는 경우에는 local executor용 handoff를 작성해야 한다.
- 실제 수정은 파일 write + gateway restart + fresh verification이 있어야 완료로 간주한다.
