# Discord `/save` Hermes 운영 런북

## 목적과 경계

이 문서는 Hermes의 공식 **skill-command + plugin-tool** 경로로 Discord
`/save`를 배포하고 되돌리는 절차다. `/save`의 native picker 항목은 설치된
`save` skill이 제공하며, `ai-agent-commands` 플러그인은 모델이 호출하는
비동기 `save_discord_thread_to_obsidian` tool만 제공한다. 플러그인은 명령을
등록하지 않으므로 skill을 가리거나 Hermes가 session context를 묶기 전에
실행되지 않는다.

Hermes Core 수정, standalone Discord interaction/webhook adapter, 수동 slash
command 등록, tool override grant는 사용하지 않는다. 특히 `--allow-tool`,
tool-use enforcement 완화, Administrator/권한 변경을 추가하지 않는다.

이 작업은 컨트롤러가 `aiagent`에서 수행한다. 하위 에이전트는 원격 설치,
재시작, Discord 호출을 수행하지 않는다.

## 대상과 고정 기록

대상 profile은 정확히 다음 일곱 개다.

```bash
profiles=(
  aicompanyassistant
  aicompanyceo
  aicompanycontent
  aicompanyart
  aicompanytech
  aicompanymarketing
  aicompanyquality
)
```

공식 설치 식별자는 다음 두 값이다. 플러그인은 GitHub subdirectory
identifier를 사용하고, 단일 파일 skill은 raw `SKILL.md` URL을 사용한다.

```bash
PLUGIN_SOURCE=kbm323/AI_Agent/hermes_plugins/ai-agent-commands
SKILL_SOURCE=https://raw.githubusercontent.com/kbm323/AI_Agent/main/hermes_skills/save/SKILL.md
PLUGIN_NAME=ai-agent-commands
SKILL_NAME=save
```

배포 전에 승인된 AI_Agent checkout, Hermes CLI 버전, plugin/skill source와
설치 후 목록을 한 디렉터리에 남긴다. `main` URL은 가변이므로 이 기록과
skill SHA-256이 실제 배포본의 pin이다. 다음 명령에서 `DEPLOY_RECORD_DIR`은
접근 제한된 운영 기록 위치로 바꾼다.

```bash
export DEPLOY_RECORD_DIR=/home/ubuntu/hermes-workspace/deploy-records/discord-save-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DEPLOY_RECORD_DIR"
chmod 700 "$DEPLOY_RECORD_DIR"

hermes --version | tee "$DEPLOY_RECORD_DIR/hermes-version.txt"
git -C /home/ubuntu/hermes-workspace/AI_Agent rev-parse HEAD | tee "$DEPLOY_RECORD_DIR/ai-agent-commit.txt"
printf '%s\n' "$PLUGIN_SOURCE" > "$DEPLOY_RECORD_DIR/plugin-source.txt"
printf '%s\n' "$SKILL_SOURCE" > "$DEPLOY_RECORD_DIR/skill-source.txt"
```

배포 기록에는 토큰, `.env` 내용, Discord API 응답 본문을 저장하거나 출력하지
않는다.

## 사전 점검과 identity 동기화

`aiagent`에서 승인된 commit을 checkout한 뒤 다음 정확한 환경 입력을 설정한다.
`DISCORD_BOT_TOKEN`은 각 profile의 `~/.hermes/profiles/<profile>/.env`에만
있어야 하며, 이 터미널에서 export하거나 출력하지 않는다.

```bash
export AI_AGENT_ROOT=/home/ubuntu/hermes-workspace/AI_Agent
export OBSIDIAN_VAULT_PATH=/home/ubuntu/Obsidian

cd "$AI_AGENT_ROOT"

python scripts/sync_discord_bot_identities.py \
  --output runtime/discord_bot_identities.json
```

위 identity 동기화는 일곱 profile의 bot identity를
`runtime/discord_bot_identities.json`에 생성한다. 이 파일은 secret이 아닌
ID/역할 매핑이지만 runtime 산출물이므로 commit하지 않는다. 스크립트가 현재
배포된 Hermes profile 경로를 읽도록, 아래 검사에서 일곱 profile 모두가
token 값을 **표시하지 않고** 존재함을 확인한 뒤 실행한다.

```bash
for profile in "${profiles[@]}"; do
  env_file="$HOME/.hermes/profiles/$profile/.env"
  test -r "$env_file"
  grep -qE '^[[:space:]]*(export[[:space:]]+)?DISCORD_BOT_TOKEN=[^[:space:]]+' "$env_file"
done

for path in \
  "$OBSIDIAN_VAULT_PATH" \
  "$OBSIDIAN_VAULT_PATH/raw/chat-logs" \
  "$OBSIDIAN_VAULT_PATH/wiki"; do
  test -d "$path"
  test -w "$path"
done
```

`sync_discord_bot_identities.py`가 `--output`을 지원하는 승인된 checkout인지
먼저 확인한다. 인자 오류가 나면 다른 경로나 수동 JSON을 만들지 말고,
승인 commit의 스크립트/API 불일치를 컨트롤러에게 blocker로 보고한다.

## 설치 전 backup

각 profile의 현재 Hermes 상태와 기존 `save` skill 파일이 있으면 backup을
남긴다. backup은 권한을 700/600으로 제한하고 기록 디렉터리를 공유하거나
commit하지 않는다.

```bash
for profile in "${profiles[@]}"; do
  hermes --profile "$profile" plugins list \
    > "$DEPLOY_RECORD_DIR/$profile.plugins.before.txt"
  hermes --profile "$profile" skills list \
    > "$DEPLOY_RECORD_DIR/$profile.skills.before.txt"

  skill_path="$HOME/.hermes/profiles/$profile/skills/$SKILL_NAME/SKILL.md"
  if test -f "$skill_path"; then
    mkdir -p "$DEPLOY_RECORD_DIR/$profile.skill.before"
    cp "$skill_path" "$DEPLOY_RECORD_DIR/$profile.skill.before/SKILL.md"
    chmod 600 "$DEPLOY_RECORD_DIR/$profile.skill.before/SKILL.md"
  fi
done
```

## 프로필별 공식 설치 및 활성화

모든 gateway process가 **동일한** plugin source를 load해야 한다. 설치는
profile별로 수행한다. `--enable`은 plugin allow-list에 활성 상태로 설치한다.
`save` skill은 Hermes에서 설치 즉시 slash command로 사용 가능하므로 별도
명령 등록이나 tool override grant가 없다.

```bash
for profile in "${profiles[@]}"; do
  hermes --profile "$profile" plugins install "$PLUGIN_SOURCE" --enable
  hermes --profile "$profile" plugins enable "$PLUGIN_NAME"
  hermes --profile "$profile" skills install "$SKILL_SOURCE"

  hermes --profile "$profile" plugins list \
    | tee "$DEPLOY_RECORD_DIR/$profile.plugins.after.txt"
  hermes --profile "$profile" skills list \
    | tee "$DEPLOY_RECORD_DIR/$profile.skills.after.txt"

  sha256sum "$HOME/.hermes/profiles/$profile/skills/$SKILL_NAME/SKILL.md" \
    | tee "$DEPLOY_RECORD_DIR/$profile.skill.sha256"
done
```

각 `plugins list`에는 `ai-agent-commands`가 enabled로, 각 `skills list`에는
`save`가 있어야 한다. CLI 출력 형식이나 profile skill 경로가 설치된 Hermes
버전에서 다르면 추측해서 복사하지 말고 `hermes --help`, `hermes plugins --help`,
`hermes skills --help`와 앞 단계의 Hermes version 기록을 컨트롤러에게
전달한다. 이 경우 assistant gateway를 재시작하지 않는다.

### 기존 local-path 명령의 위치

다음은 Task 9 brief의 원래 명령이며, 승인된 `$AI_AGENT_ROOT` checkout을
직접 검사하거나 GitHub source를 사용할 수 없는 복구 상황에서는 여전히
유효하다. 이것이 기본 배포 방식은 아니다. 기본 방식은 위의
`kbm323/AI_Agent/hermes_plugins/ai-agent-commands` GitHub subdirectory
identifier다.

```bash
hermes plugins install \
  /home/ubuntu/hermes-workspace/AI_Agent/hermes_plugins/ai-agent-commands

hermes plugins list
```

## assistant 우선 재시작과 Discord smoke

초기에는 assistant gateway만 재시작한다. 기존 운영 방식은 tmux session
`hermes-aicompanyassistant`와 `HERMES_ACCEPT_HOOKS=1 hermes --profile
aicompanyassistant gateway run`이다. 나머지 여섯 gateway는 이 단계에서
재시작하지 않는다.

```bash
tmux kill-session -t hermes-aicompanyassistant 2>/dev/null || true
tmux new-session -d -s hermes-aicompanyassistant -x 120 -y 40 \
  "HERMES_ACCEPT_HOOKS=1 hermes --profile aicompanyassistant gateway run"
```

assistant bot의 지정된 일반 test thread에서 아래를 순서대로 수행한다.

1. Discord native command picker에 `/save`가 보이는지 확인한다. skill이
   picker entry를 제공하며 plugin은 command를 등록하지 않는다.
2. `/save`를 한 번 실행한다. `conversation` snapshot과 canonical page가
   생성되어야 한다.
3. 새 메시지 없이 `/save`를 다시 실행한다. 결과는 `unchanged`이고 새
   snapshot은 생기지 않아야 한다.
4. test `MeetingRun`에 연결된 thread에서 `/save`를 실행한다. 결과 `type`은
   `meeting`이고 MeetingRun evidence가 있어야 한다.
5. 일반 guild channel(Discord thread 아님)에서 `/save`를 실행한다. 저장하지
   않고 thread-required 안전 응답을 반환해야 한다.
6. 생성된 파일만 대상으로 token, bearer, password, `@everyone`, `@here`를
   검사한다. 실제 credential 값을 화면이나 기록에 출력하지 않으며 기대
   결과는 0건이다.

다섯 항목이 모두 통과하기 전에는 나머지 gateway를 재시작하지 않는다. 통과
후에만 기존 start script로 이미 실행 중인 session을 보존하면서 누락된 여섯
profile을 시작하고 상태를 확인한다.

```bash
bash scripts/start_discord_multibot_gateways.sh
bash scripts/status_discord_multibot_gateways.sh
```

## 롤백

smoke가 실패하거나 `/save` picker, late-bound thread context, 저장 결과 중
하나라도 기대와 다르면 추가 gateway를 재시작하지 않고 다음을 실행한다.

```bash
for profile in "${profiles[@]}"; do
  hermes --profile "$profile" plugins disable ai-agent-commands
  hermes --profile "$profile" plugins list
  hermes --profile "$profile" skills uninstall save
  hermes --profile "$profile" skills list
done
```

그 뒤 assistant gateway만 다시 시작해 기존 plugin/skill 없는 상태를 먼저
확인한다. 배포 전 이미 `save` skill 또는 이전 plugin이 있었다면,
`$DEPLOY_RECORD_DIR/*.before.txt`에 기록한 정확한 이전 identifier/version으로
각 profile에 다시 설치하고 enable한다. backup한 `SKILL.md`는 이전 등록 source가
복구 불가능할 때에만 운영자 검토 후 profile-local skill 위치로 복원한다.
Hermes Core를 편집하거나 standalone adapter를 켜서 우회하지 않는다.

## 로컬 검증

Windows에서는 `PYTHONUTF8=1`과 repository `.venv`를 사용한다. 아래 검증은
배포 전 checkout에서 수행하며, `PASS` 결과와 failure count를 deployment
record에 남긴다.

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest `
  tests/test_runtime_architecture_v2_hermes_command_context.py `
  tests/test_runtime_architecture_v2_discord_history.py `
  tests/test_runtime_architecture_v2_conversation_summary.py `
  tests/test_runtime_architecture_v2_obsidian_conversations.py `
  tests/test_runtime_architecture_v2_save_command.py `
  tests/test_runtime_architecture_v2_ai_agent_plugin.py `
  tests/test_runtime_architecture_v2_store.py `
  tests/test_runtime_architecture_v2_phase15_knowledge_loop.py `
  tests/test_runtime_architecture_v2_phase25_command_surface.py -q

.\.venv\Scripts\python.exe -m pytest `
  tests/test_runtime_architecture_v2_phase14_multi_bot.py `
  tests/test_runtime_architecture_v2_phase21_discord_webhook.py `
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py `
  tests/test_runtime_architecture_v2_phase32_live_audit.py `
  tests/test_runtime_architecture_v2_on_demand_exports.py `
  tests/test_runtime_smoke_packet.py -q

npm run typecheck
npm run lint:ruff
git diff --check
git diff --cached --check
```

마지막으로 staged added-line secret scan은 repository 표준 script를 사용한다.
아직 staged file이 없다면 실행 전 runbook만 stage한 뒤 수행하며, 예상 결과는
0 finding이다.

```bash
bash scripts/pre-commit-secret-scan.sh
```

이 script는 staged added line의 secret-assignment pattern을 검사한다. Task 9의
문서 변경은 `docs` 범위지만, 구현 변경이 함께 들어온 배포 commit이면 `src`,
`tests`, `scripts`, `hermes_plugins`, `docs` 전체를 동일한 staged scan 대상으로
유지한다.
