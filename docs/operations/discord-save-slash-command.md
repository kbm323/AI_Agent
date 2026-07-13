# Discord `/save` Hermes 운영 런북

## 목적과 경계

이 문서는 Hermes의 공식 **skill-command + plugin-tool** 경로로 Discord
`/save`를 배포하고 되돌리는 절차다. 설치된 `save` skill이 Discord native
`/save` picker 항목을 제공하고, `ai-agent-commands` plugin은 모델이 호출하는
비동기 `save_discord_thread_to_obsidian` tool만 제공한다. plugin은 command를
등록하지 않는다.

Hermes Core 수정, standalone Discord interaction/webhook adapter, 수동 slash
command 등록, tool override grant는 사용하지 않는다. 특히 `--allow-tool`,
tool-use enforcement 완화, Administrator/권한 변경을 추가하지 않는다.

이 작업은 컨트롤러가 `aiagent`에서 수행한다. 하위 에이전트는 원격 설치,
gateway 재시작, Discord 호출을 수행하지 않는다.

## 대상과 fail-closed 원칙

대상 profile은 정확히 다음 일곱 개다.

```bash
set -euo pipefail

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

이 문서의 다중 profile loop는 모두 `set -euo pipefail` 아래에서 실행한다.
한 profile의 install, enable, hash 검증, identity sync, 또는 경로 검사가
실패하면 즉시 중단한다. 일곱 profile 모두가 검증되기 전에는 어떤 gateway도
재시작하지 않는다.

## Hermes 버전과 배포 revision 고정

이 절차의 검증 대상 Hermes revision은
`1d689e19203281228878ac6770d4a6700d4ae385`다. 이 값은
`/home/ubuntu/.hermes/hermes-agent`의 검증된 설치 Git HEAD다. display용
`hermes --version`은 여러 줄이며 upstream/local 표시는 설치 상태에 따라
바뀔 수 있으므로, 전체 문자열을 고정값과 비교하지 않는다. 첫 줄이
`Hermes Agent v0.18.2 (2026.7.7.2)`로 시작하는지만 확인하고, 정확한 revision은
설치 Git HEAD로 비교한다. 둘 중 하나라도 다르면 중단하고 다른 Hermes
버전의 CLI/installer 동작을 추측하지 않는다.

Hermes v0.18.2의 plugin installer는 GitHub subdirectory를 선택할 때 default
branch를 shallow clone하며 browser tree ref/commit 선택을 보존하지 않는다.
따라서 plugin identifier에 commit을 붙여 installer가 checkout한다고 주장하면
안 된다. 이 런북은 installer 직전 `origin/main`이 승인된 checkout commit과
같음을 확인하고, 설치 뒤 각 profile의 plugin/skill 파일 hash를 checkout의
기대 hash와 비교해 같은 revision임을 증명한다. remote가 중간에 이동하면
hash 검증이 실패하고 재시작 전에 중단한다.

```bash
export AI_AGENT_ROOT=/home/ubuntu/hermes-workspace/AI_Agent
export OBSIDIAN_VAULT_PATH=/home/ubuntu/Obsidian
export HERMES_AGENT_ROOT=/home/ubuntu/.hermes/hermes-agent
export HERMES_TARGET_COMMIT=1d689e19203281228878ac6770d4a6700d4ae385
export HERMES_VERSION_PREFIX='Hermes Agent v0.18.2 (2026.7.7.2)'
export DEPLOY_RECORD_DIR=/home/ubuntu/hermes-workspace/deploy-records/discord-save-$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$DEPLOY_RECORD_DIR"
chmod 700 "$DEPLOY_RECORD_DIR"

cd "$AI_AGENT_ROOT"
git diff --quiet
export AI_AGENT_COMMIT="$(git rev-parse HEAD)"
export PLUGIN_SOURCE=kbm323/AI_Agent/hermes_plugins/ai-agent-commands
export PLUGIN_NAME=ai-agent-commands
export SKILL_NAME=save
export SKILL_SOURCE="https://raw.githubusercontent.com/kbm323/AI_Agent/$AI_AGENT_COMMIT/hermes_skills/save/SKILL.md"

actual_hermes_version="$(hermes --version)"
actual_hermes_version_first_line="$(printf '%s\n' "$actual_hermes_version" | sed -n '1p')"
case "$actual_hermes_version_first_line" in
  "$HERMES_VERSION_PREFIX"*) ;;
  *) exit 1 ;;
esac
actual_hermes_commit="$(git -C "$HERMES_AGENT_ROOT" rev-parse HEAD)"
test "$actual_hermes_commit" = "$HERMES_TARGET_COMMIT"
printf '%s\n' "$actual_hermes_version" > "$DEPLOY_RECORD_DIR/hermes-version.txt"
printf '%s\n' "$actual_hermes_commit" > "$DEPLOY_RECORD_DIR/hermes-agent-commit.txt"
printf '%s\n' "$AI_AGENT_COMMIT" > "$DEPLOY_RECORD_DIR/ai-agent-commit.txt"
printf '%s\n' "$PLUGIN_SOURCE" > "$DEPLOY_RECORD_DIR/plugin-source.txt"
printf '%s\n' "$SKILL_SOURCE" > "$DEPLOY_RECORD_DIR/skill-source.txt"
```

`AI_AGENT_COMMIT`은 배포할 clean checkout의 실제 HEAD에서만 설정한다. raw
skill URL은 반드시 이 40자리 commit을 포함해야 하며 `main` URL을 사용하지
않는다. `hermes-version.txt`에는 전체 multi-line 출력, `hermes-agent-commit.txt`에는
검증된 설치 revision을 남긴다. 예를 들어 현재 첫 줄의 mutable 표시는
`Hermes Agent v0.18.2 (2026.7.7.2) · upstream bd740f20 · local 1d689e19 (+1 carried commit)`일
수 있으나, 배포 pin은 display의 upstream/local label이 아니라
`HERMES_TARGET_COMMIT`이다.

설치 직전과 각 profile 설치 직전에 remote default branch를 확인한다.

```bash
assert_origin_main_matches_checkout() {
  git -C "$AI_AGENT_ROOT" fetch --quiet origin main
  test "$(git -C "$AI_AGENT_ROOT" rev-parse origin/main)" = "$AI_AGENT_COMMIT"
}

assert_origin_main_matches_checkout

EXPECTED_PLUGIN_YAML_SHA256="$(sha256sum "$AI_AGENT_ROOT/hermes_plugins/ai-agent-commands/plugin.yaml" | awk '{print $1}')"
EXPECTED_PLUGIN_INIT_SHA256="$(sha256sum "$AI_AGENT_ROOT/hermes_plugins/ai-agent-commands/__init__.py" | awk '{print $1}')"
EXPECTED_SKILL_SHA256="$(sha256sum "$AI_AGENT_ROOT/hermes_skills/save/SKILL.md" | awk '{print $1}')"

printf '%s  plugin.yaml\n' "$EXPECTED_PLUGIN_YAML_SHA256" > "$DEPLOY_RECORD_DIR/expected-sha256.txt"
printf '%s  __init__.py\n' "$EXPECTED_PLUGIN_INIT_SHA256" >> "$DEPLOY_RECORD_DIR/expected-sha256.txt"
printf '%s  SKILL.md\n' "$EXPECTED_SKILL_SHA256" >> "$DEPLOY_RECORD_DIR/expected-sha256.txt"
```

배포 기록에는 token, `.env` 내용, Discord API 응답 본문을 저장하거나 출력하지
않는다.

## 사전 점검과 identity 동기화

`DISCORD_BOT_TOKEN`은 각 profile의
`~/.hermes/profiles/<profile>/.env`에만 있어야 한다. 이 터미널에서 token을
export하거나 출력하지 않는다.

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

python scripts/sync_discord_bot_identities.py
test -s runtime/discord_bot_identities.json
python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("runtime/discord_bot_identities.json").read_text(encoding="utf-8"))
assert len(payload) == 7
assert all("hermes_profile" in value and "role" in value for value in payload.values())
assert {value["hermes_profile"] for value in payload.values()} == {
    "aicompanyassistant", "aicompanyceo", "aicompanycontent", "aicompanyart",
    "aicompanytech", "aicompanymarketing", "aicompanyquality",
}
PY
```

`sync_discord_bot_identities.py`에는 CLI parser가 없다. 따라서 반드시 인자
없이 실행하며, default 산출물
`runtime/discord_bot_identities.json`을 뒤에서 검사한다. 이 runtime 파일은
secret이 아닌 ID/역할 매핑이지만 commit하지 않는다.

## 설치 전 기록

각 profile의 기존 plugin/skill 상태를 복구 기록으로 남긴다. 기록 디렉터리는
공유하거나 commit하지 않는다.

```bash
set -euo pipefail

for profile in "${profiles[@]}"; do
  hermes --profile "$profile" plugins list > "$DEPLOY_RECORD_DIR/$profile.plugins.before.txt"
  hermes --profile "$profile" skills list > "$DEPLOY_RECORD_DIR/$profile.skills.before.txt"
done
```

## profile별 비대화형 설치와 content 증명

GitHub subdirectory identifier는 공식 plugin 설치 surface다. 각 profile에는
동일한 identifier를 설치하되, 설치 시 plugin을 disabled로 유지하고 명시적으로
tool override를 거부한 뒤 enable한다. skill 설치도 force/yes로 비대화형으로
수행한다.

```bash
set -euo pipefail

: > "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"

for profile in "${profiles[@]}"; do
  assert_origin_main_matches_checkout

  hermes --profile "$profile" plugins install "$PLUGIN_SOURCE" --force --no-enable
  hermes --profile "$profile" plugins enable "$PLUGIN_NAME" --no-allow-tool-override
  hermes --profile "$profile" skills install "$SKILL_SOURCE" --force --yes

  hermes --profile "$profile" plugins list | tee "$DEPLOY_RECORD_DIR/$profile.plugins.after.txt"
  hermes --profile "$profile" skills list | tee "$DEPLOY_RECORD_DIR/$profile.skills.after.txt"

  installed_plugin_dir="$HOME/.hermes/profiles/$profile/plugins/$PLUGIN_NAME"
  installed_skill_path="$HOME/.hermes/profiles/$profile/skills/$SKILL_NAME/SKILL.md"
  test -f "$installed_plugin_dir/plugin.yaml"
  test -f "$installed_plugin_dir/__init__.py"
  test -f "$installed_skill_path"

  plugin_yaml_sha256="$(sha256sum "$installed_plugin_dir/plugin.yaml" | awk '{print $1}')"
  plugin_init_sha256="$(sha256sum "$installed_plugin_dir/__init__.py" | awk '{print $1}')"
  skill_sha256="$(sha256sum "$installed_skill_path" | awk '{print $1}')"
  test "$plugin_yaml_sha256" = "$EXPECTED_PLUGIN_YAML_SHA256"
  test "$plugin_init_sha256" = "$EXPECTED_PLUGIN_INIT_SHA256"
  test "$skill_sha256" = "$EXPECTED_SKILL_SHA256"

  printf '%s\t%s\t%s\t%s\n' \
    "$profile" "$plugin_yaml_sha256" "$plugin_init_sha256" "$skill_sha256" \
    >> "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"
done

test "$(wc -l < "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv")" -eq 7
while IFS=$'\t' read -r profile plugin_yaml_sha256 plugin_init_sha256 skill_sha256; do
  test "$plugin_yaml_sha256" = "$EXPECTED_PLUGIN_YAML_SHA256"
  test "$plugin_init_sha256" = "$EXPECTED_PLUGIN_INIT_SHA256"
  test "$skill_sha256" = "$EXPECTED_SKILL_SHA256"
done < "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"
```

이 검증은 일곱 profile 각각의 `plugin.yaml`, `__init__.py`, `SKILL.md`가
동일한 approved checkout content인지 증명하고 hash를 남긴다. `origin/main`이
checkout commit과 다르거나, installer가 다른 content를 가져오거나, profile
plugin 경로가 예상과 다르면 중단한다. local-path plugin install은 지원된
fallback가 아니므로 사용하지 않는다.

## assistant 우선 재시작과 Discord smoke

앞 절의 일곱 profile content 검증이 모두 완료된 뒤에만 assistant gateway를
처음 재시작한다. 나머지 여섯 gateway는 이 단계에서 재시작하지 않는다.

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

이 smoke가 모두 통과하기 전에는 나머지 여섯 gateway를 재시작하지 않는다.
통과 후에만 기존 script로 누락된 gateway를 시작하고 상태를 확인한다.

```bash
bash scripts/start_discord_multibot_gateways.sh
bash scripts/status_discord_multibot_gateways.sh
```

## 롤백

smoke가 실패하거나 `/save` picker, late-bound thread context, 저장 결과 중
하나라도 기대와 다르면 추가 gateway를 재시작하지 않는다. 다음은 profile별
plugin과 skill을 제거하는 rollback이다.

```bash
set -euo pipefail

for profile in "${profiles[@]}"; do
  hermes --profile "$profile" plugins disable ai-agent-commands
  hermes --profile "$profile" plugins list
  hermes --profile "$profile" skills uninstall save
  hermes --profile "$profile" skills list
done
```

이전 plugin/skill이 있었으면 `$DEPLOY_RECORD_DIR/*.before.txt`에 기록한
공식 identifier/version을 컨트롤러가 검토한 뒤 각 profile에 재설치한다.
Hermes Core를 편집하거나 standalone adapter를 켜서 우회하지 않는다.

## 검증 기록과 Ubuntu gate

Windows 로컬에서 `.venv`와 `PYTHONUTF8=1`으로 실행한 focused Runtime v2
suite는 `161 passed`다. Controller는 동일 interpreter로 feature head와
baseline `c7d52c7`에서 required regression suite의 같은 세 실패를 재현했다.
이는 이 branch가 만든 실패가 아닌 pre-existing baseline failure다. 다만
Phase 14 fixture가 명시 token mapping을 넘기면서 projection path는 profile
token loader를 다시 사용하는 contract conflict가 있으므로, 이를 단순히
environment-only failure라고 분류하지 않는다. 실제 profile env가 있는
server에서의 전체 regression 검증은 배포 성공 전에 여전히 필요하다.

Windows에서 수동 확장한 TypeScript `node --check` 검증은 `BAD=0`이었다.
Repository-wide Ruff는 기존 `1330` finding이 있다. package script의 glob,
mypy, PATH 차이 때문에 Windows에서 `npm run typecheck`, `npm run lint:ruff`,
`bash scripts/pre-commit-secret-scan.sh`를 required gate로 주장할 수 없다.
다음 exact gate는 Ubuntu `aiagent`에서 실행해 통과 결과를 deployment record에
남겨야 한다. 이 런북은 해당 static gate가 이미 통과했다고 주장하지 않는다.

```bash
set -euo pipefail

python -m pytest \
  tests/test_runtime_architecture_v2_hermes_command_context.py \
  tests/test_runtime_architecture_v2_discord_history.py \
  tests/test_runtime_architecture_v2_conversation_summary.py \
  tests/test_runtime_architecture_v2_obsidian_conversations.py \
  tests/test_runtime_architecture_v2_save_command.py \
  tests/test_runtime_architecture_v2_ai_agent_plugin.py \
  tests/test_runtime_architecture_v2_store.py \
  tests/test_runtime_architecture_v2_phase15_knowledge_loop.py \
  tests/test_runtime_architecture_v2_phase25_command_surface.py -q

python -m pytest \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase21_discord_webhook.py \
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py \
  tests/test_runtime_architecture_v2_phase32_live_audit.py \
  tests/test_runtime_architecture_v2_on_demand_exports.py \
  tests/test_runtime_smoke_packet.py -q

npm run typecheck
npm run lint:ruff
git diff --check
git diff --cached --check
bash scripts/pre-commit-secret-scan.sh
```

위 Python regression, Ubuntu static gate, identity sync, 일곱 profile hash
증명, assistant smoke 중 하나라도 실패하면 controller는 deployment success를
선언하지 않는다.
