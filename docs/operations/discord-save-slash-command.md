# Discord `/archive` Hermes 운영 런북

## 목적과 경계

이 문서는 Hermes의 공식 **plugin-command + plugin-tool** 경로로 Discord
`/archive`를 배포하고 되돌리는 절차다. `ai-agent-commands` plugin이
`PluginContext.register_command()`로 Discord native `/archive` picker 항목을 제공하고,
같은 plugin의 비동기 `save_discord_thread_to_obsidian` tool이 모델 기반 호출 경로를
제공한다. 별도의 `save` skill은 설치하지 않는다.

Hermes Core 수정, standalone Discord interaction/webhook adapter, 수동 slash
command 등록, tool override grant는 사용하지 않는다. 특히 `--allow-tool`,
tool-use enforcement 완화, Administrator/권한 변경을 추가하지 않는다.

이 작업은 컨트롤러가 `aiagent`에서 수행한다. 하위 에이전트는 원격 설치,
gateway 재시작, Discord 호출을 수행하지 않는다.

## 회의 명령 신뢰성 기준

같은 `ai-agent-commands` plugin의 `/meeting-start`와 `/meeting-report`는
Runtime v2의 canonical 회의 산출물을 사용한다. 새 회의는 검증된 대표
`#회의실-전략결정` 부모 채널에서만 시작한다. 기존 스레드에서는 저장된
`MeetingRun` 연결로 부모 채널을 복원하며, 연결되지 않은 스레드는 provider
실행 전에 fail closed한다.

정상적인 여섯 팀장 회의의 모델 호출 기준은 다음과 같다.

```text
round 1: 팀장 발언 6회
round 2: 팀장 발언 6회
outcome: 검증 판정 1회
합계: 13회 + 안건에 따라 선택된 내부 specialist 호출
```

한 라운드에서는 최대 세 호출만 동시에 실행한다. 2라운드는 1라운드 전체가
끝나고 `meeting_session.json`이 저장된 뒤 시작하며, outcome 판정은 2라운드
저장 뒤 시작한다. 팀장별 `worker_outputs/` 파일은 저장된 2라운드 발언에서
만들고 같은 팀장에게 다시 모델을 호출하지 않는다. 내부 specialist만 별도
worker provider 호출을 유지한다.

운영 확인 대상은 다음 세 파일이다.

```text
runtime/meeting_runs/<meeting_run_id>/meeting_run.json
runtime/meeting_runs/<meeting_run_id>/meeting_session.json
runtime/meeting_runs/<meeting_run_id>/meeting_outcome.json
```

`meeting_session.json`에는 두 라운드와 각 발언의 `live`, `replacement`,
`failed` 상태가 있어야 한다. replacement는 실제 응답으로 승격하지 않는다.
`meeting_outcome.json`의 상태는 `agreed`, `partial_agreement`, `blocked`,
`needs_user_decision` 중 하나여야 한다. 보고서는 자동 생성하지 않고 사용자가
`/meeting-report`를 요청할 때 `reports/`에 생성한다.

배포 후 live smoke는 대표 회의실에서 한 회의만 감독 실행한다. 두 라운드
12개 발언, 위 세 canonical 파일, outcome 상태, Discord 스레드 연결을 확인한
뒤 `/meeting-report 브리핑해줘`를 한 번 실행한다. 실패가 있으면 추가 live
회의를 반복하지 않고 저장된 generation status와 error category를 먼저
검토한다. 이 검증을 위해 Discord 토큰을 교체하거나 출력하지 않는다.

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

### 확인된 호출 경계와 DM 제한

고정한 Hermes revision의 `gateway.session_context`는
`HERMES_SESSION_MESSAGE_ID`를 제공하지만, Discord native slash event builder는
interaction ID를 이 변수에 넣지 않는다. `ai-agent-commands` plugin은 공식
`pre_gateway_dispatch` hook에서 raw Discord interaction ID를 먼저 고정한다. raw ID가
없을 때에는 같은 hook의 turn-start 시각을 Discord snowflake로 변환한 상한을 사용한다.
raw interaction/message ID와 `HERMES_SESSION_MESSAGE_ID`는 history 사용 전에 lower 22 bit를
0으로 만든 같은 millisecond의 timestamp-floor snowflake로 정규화한다. 따라서
서로 다른 snowflake generator가 만든 later message의 lower bit가 더 작아도 포함되지
않는다. 이 보수적 경계는 invocation과 같은 millisecond에 먼저 작성된 message도 생략할
수 있지만 invocation 뒤 message를 포함하지 않는다.

collection checkpoint는 Discord source와 명시적 DM session-start 경계별로 하나만
유지하며 payload에 immutable cutoff를 기록한다. 더 늦은 `/archive`는 같은 source/boundary의
이전 진행을 채택하고 새 cutoff까지의 interval만 먼저 수집한 뒤 dedupe한다. created,
updated, unchanged 중 하나가 Obsidian에 durable하게 반영되면 transcript checkpoint를
즉시 삭제하므로 성공한 반복 save가 full transcript를 누적하지 않는다.

checkpoint version 3은 채택한 이전 cutoff/cursor와 newer interval 진행을 별도로
기록한다. inherited message 수는 newer interval이 이전 cutoff에 도달하기 전 10,000건
상한을 충족한 것으로 계산하지 않는다. newer interval 자체가 10,000건이면 inherited
state를 버리고 newest contiguous 10,000건만 저장한다. 같은 source/DM boundary의
collection, summary, Obsidian durable save, conditional checkpoint cleanup 전체는
source-scoped process-local lock과 interprocess file lock을 함께 보유한다. 다른 source는
서로 다른 lock namespace를 사용하므로 전역 직렬화하지 않는다.

현재 revision은 정확한 Discord DM 시작 message ID를 복원할
`HERMES_SESSION_START_MESSAGE_ID`를 제공하지 않는다. runtime reader도 지원되지 않는
process-global 환경변수를 session 경계로 소비하지 않으며, session `created_at`을
message 경계로 추측하지 않는다. 따라서 현재 공식 plugin/tool 경로의 DM `/archive`는
항상 `dm_boundary_unavailable`로 fail closed한다. 내부 command contract의 available
branch는 검증된 per-invocation session-start snowflake가 전달될 때만 그 시작점 이후와
invocation cutoff 이전을 저장하도록 테스트한다. private Discord thread와 DM의 저장
visibility는 모두 `private`이다. 이 future-only path는 guild/parent ID를 꾸며 내지 않고
명시적 private DM source identity로 collector부터 실제 Obsidian store까지 검증한다.

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
test -z "$(git status --porcelain --untracked-files=all)"
test -z "$(git ls-files --others --exclude-standard)"
while IFS= read -r ignored_path; do
  [ -z "$ignored_path" ] && continue
  case "$ignored_path" in
    runtime/*.py|runtime/*.pyc|runtime/*.sh|runtime/*.so) exit 1 ;;
    runtime/*) ;;
    *) exit 1 ;;
  esac
done < <(git ls-files --others --ignored --exclude-standard)
git diff --quiet
git diff --cached --quiet
export AI_AGENT_COMMIT="$(git rev-parse HEAD)"
export REVIEWED_BASE=c7d52c7fc6c3bb19ef048e16acd659a717dd6218
export PLUGIN_SOURCE=kbm323/AI_Agent/hermes_plugins/ai-agent-commands
export PLUGIN_NAME=ai-agent-commands

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

test "$(git rev-list --count "$REVIEWED_BASE..$AI_AGENT_COMMIT")" -gt 0
bash scripts/pre-commit-secret-scan.sh --range "$REVIEWED_BASE..$AI_AGENT_COMMIT"
```

`AI_AGENT_COMMIT`은 배포할 clean checkout의 실제 HEAD에서만 설정한다.
`hermes-version.txt`에는 전체 multi-line 출력, `hermes-agent-commit.txt`에는
검증된 설치 revision을 남긴다. 예를 들어 현재 첫 줄의 mutable 표시는
`Hermes Agent v0.18.2 (2026.7.7.2) · upstream bd740f20 · local 1d689e19 (+1 carried commit)`일
수 있으나, 배포 pin은 display의 upstream/local label이 아니라
`HERMES_TARGET_COMMIT`이다.

두 `git diff` 검사는 tracked file의 unstaged와 staged 변경을 모두 거부한다.
`git status --porcelain --untracked-files=all`과
`git ls-files --others --exclude-standard`은 commit 고정이나 설치 전에 모든
non-ignored untracked file도 거부한다. identity sync는 이 clean gate와
`AI_AGENT_COMMIT` 고정이 끝난 뒤에만 실행한다.
`runtime/discord_bot_identities.json`은 이 freeze 뒤 identity sync가 만드는
untracked/ignored runtime 산출물이므로 이 preflight에서 clean-checkout 위반으로
취급하지 않으며, commit하거나 deployment hash 입력으로 사용하지 않는다.
허용되는 ignored runtime 산출물은 `runtime/` 아래의 검증된 JSON/data뿐이다.
`src/`, `hermes_plugins/`, `hermes_skills/`, `scripts/`, `tests/` 또는 import 가능한
Python/shell 경로의 ignored 파일은 허용하지 않는다. runtime data는 code, import,
install, reviewed-range scan, source hash 입력 밖에 둔다.

설치 직전과 각 profile 설치 직전에 remote default branch를 확인한다.

```bash
assert_origin_main_matches_checkout() {
  git -C "$AI_AGENT_ROOT" fetch --quiet origin main
  test "$(git -C "$AI_AGENT_ROOT" rev-parse origin/main)" = "$AI_AGENT_COMMIT"
}

assert_origin_main_matches_checkout

EXPECTED_PLUGIN_YAML_SHA256="$(sha256sum "$AI_AGENT_ROOT/hermes_plugins/ai-agent-commands/plugin.yaml" | awk '{print $1}')"
EXPECTED_PLUGIN_INIT_SHA256="$(sha256sum "$AI_AGENT_ROOT/hermes_plugins/ai-agent-commands/__init__.py" | awk '{print $1}')"

printf '%s  plugin.yaml\n' "$EXPECTED_PLUGIN_YAML_SHA256" > "$DEPLOY_RECORD_DIR/expected-sha256.txt"
printf '%s  __init__.py\n' "$EXPECTED_PLUGIN_INIT_SHA256" >> "$DEPLOY_RECORD_DIR/expected-sha256.txt"
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

export ROLLBACK_STATE_DIR="$DEPLOY_RECORD_DIR/rollback-state"
mkdir -p "$ROLLBACK_STATE_DIR"

for profile in "${profiles[@]}"; do
  profile_root="$HOME/.hermes/profiles/$profile"
  state_root="$ROLLBACK_STATE_DIR/$profile"
  session="hermes-${profile}"
  mkdir -p "$state_root"
  if tmux has-session -t "$session" 2>/dev/null; then
    : > "$state_root/was-running"
  else
    : > "$state_root/was-stopped"
  fi
  hermes --profile "$profile" plugins list > "$DEPLOY_RECORD_DIR/$profile.plugins.before.txt"
  hermes --profile "$profile" skills list > "$DEPLOY_RECORD_DIR/$profile.skills.before.txt"
  if [ -f "$profile_root/config.yaml" ]; then
    cp -a "$profile_root/config.yaml" "$state_root/config.yaml"
  else
    : > "$state_root/config-was-absent"
  fi
  if [ -d "$profile_root/plugins/ai-agent-commands" ]; then
    cp -a "$profile_root/plugins/ai-agent-commands" "$state_root/plugin"
  fi
  test ! -e "$profile_root/plugins/ai-agent-commands"
  test ! -e "$profile_root/skills/save"
done
```

동명 plugin이 이미 있으면 이 배포는 중단한다. 별도 승인된 migration
절차 없이 기존 `/archive` surface를 덮어쓰지 않으므로, 정상 rollback에서는
branch-owned tool과 picker를 명확하게 제거하고 설치 전 profile config를
그대로 복원할 수 있다.

## profile별 비대화형 설치와 content 증명

GitHub subdirectory identifier는 공식 plugin 설치 surface다. 각 profile에는
동일한 identifier를 설치하되, 설치 시 plugin을 disabled로 유지하고 명시적으로
tool override를 거부한 뒤 enable한다.

```bash
set -euo pipefail

: > "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"

for profile in "${profiles[@]}"; do
  assert_origin_main_matches_checkout

  hermes --profile "$profile" plugins install "$PLUGIN_SOURCE" --force --no-enable
  hermes --profile "$profile" plugins enable "$PLUGIN_NAME" --no-allow-tool-override
  hermes --profile "$profile" plugins list | tee "$DEPLOY_RECORD_DIR/$profile.plugins.after.txt"

  installed_plugin_dir="$HOME/.hermes/profiles/$profile/plugins/$PLUGIN_NAME"
  test -f "$installed_plugin_dir/plugin.yaml"
  test -f "$installed_plugin_dir/__init__.py"

  plugin_yaml_sha256="$(sha256sum "$installed_plugin_dir/plugin.yaml" | awk '{print $1}')"
  plugin_init_sha256="$(sha256sum "$installed_plugin_dir/__init__.py" | awk '{print $1}')"
  test "$plugin_yaml_sha256" = "$EXPECTED_PLUGIN_YAML_SHA256"
  test "$plugin_init_sha256" = "$EXPECTED_PLUGIN_INIT_SHA256"

  printf '%s\t%s\t%s\n' \
    "$profile" "$plugin_yaml_sha256" "$plugin_init_sha256" \
    >> "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"
done

test "$(wc -l < "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv")" -eq 7
while IFS=$'\t' read -r profile plugin_yaml_sha256 plugin_init_sha256; do
  test "$plugin_yaml_sha256" = "$EXPECTED_PLUGIN_YAML_SHA256"
  test "$plugin_init_sha256" = "$EXPECTED_PLUGIN_INIT_SHA256"
done < "$DEPLOY_RECORD_DIR/profile-content-sha256.tsv"
```

이 검증은 일곱 profile 각각의 `plugin.yaml`, `__init__.py`가
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
: > "$ROLLBACK_STATE_DIR/aicompanyassistant/loaded-by-deployment"
```

assistant bot의 지정된 일반 test thread에서 아래를 순서대로 수행한다.

1. Discord native command picker에 `/archive`가 보이는지 확인한다. plugin command가
   picker entry를 제공한다.
2. `/archive`를 한 번 실행한다. `conversation` snapshot과 canonical page가
   생성되어야 한다.
3. 새 메시지 없이 `/archive`를 다시 실행한다. 결과는 `unchanged`이고 새
   snapshot은 생기지 않아야 한다.
4. test `MeetingRun`에 연결된 thread에서 `/archive`를 실행한다. 결과 `type`은
   `meeting`이고 MeetingRun evidence가 있어야 한다.
5. 일반 guild channel(Discord thread 아님)에서 `/archive`를 실행한다. 저장하지
   않고 thread-required 안전 응답을 반환해야 한다.
6. 생성된 파일만 대상으로 token, bearer, password, `@everyone`, `@here`를
   검사한다. 실제 credential 값을 화면이나 기록에 출력하지 않으며 기대
   결과는 0건이다.

이 smoke가 모두 통과하기 전에는 나머지 여섯 gateway를 재시작하지 않는다.
통과 후에만 기존 script로 누락된 gateway를 시작하고 상태를 확인한다.

```bash
# The assistant smoke above is the gate for every remaining profile reload.
for profile in "${profiles[@]}"; do
  test "$profile" = aicompanyassistant && continue
  state_root="$ROLLBACK_STATE_DIR/$profile"
  session="hermes-${profile}"
  if [ -f "$state_root/was-running" ]; then
    tmux kill-session -t "$session" 2>/dev/null || true
    tmux new-session -d -s "$session" -x 120 -y 40 \
      "HERMES_ACCEPT_HOOKS=1 hermes --profile $profile gateway run"
    : > "$state_root/loaded-by-deployment"
  fi
done

# Preserve the assistant's original stopped state after it completes the smoke.
if [ -f "$ROLLBACK_STATE_DIR/aicompanyassistant/was-stopped" ]; then
  tmux kill-session -t hermes-aicompanyassistant 2>/dev/null || true
fi
bash scripts/status_discord_multibot_gateways.sh
```

## 롤백

smoke가 실패하거나 `/archive` picker, late-bound thread context, 저장 결과 중
하나라도 기대와 다르면 추가 gateway를 재시작하지 않는다. 다음은 profile별
plugin과 skill을 제거하고 prior config를 복구하는 rollback이다. 실행 전
`rollback-state`가 완전한지 확인한다.

```bash
set -euo pipefail

test -d "$ROLLBACK_STATE_DIR"
HERMES_PROFILE_ROOT="$HOME/.hermes/profiles" \
  bash scripts/rollback_discord_save_profiles.sh prepare
```

`prepare`는 `loaded-by-deployment` marker를 신뢰하지 않는다. assistant smoke 이전이나
나머지 profile reload 도중 실패했더라도 먼저 일곱 tmux session을 모두 idempotent하게
중지한 뒤 disk/config를 복구하고, restored state로 일곱 gateway를 모두 새로 시작한다.
따라서 기존 session 이름 충돌이 rollback을 중단시킬 수 없다. `was-running`과
`was-stopped` 기록은 이 단계에서 gateway 선택에 사용하지 않고 마지막 상태 복구에만
사용한다.

gateway가 restored config로 시작되면 Discord에서 assistant의 `/tools`를 실행해
`save_discord_thread_to_obsidian` tool이 없음을 확인하고, native command picker에서
`/archive`가 제거되었음을 확인한다. 두 absence 증거를 deployment record에 남기기
전에는 rollback success를 선언하지 않는다. assistant가 원래 실행 중이 아니었다면
resync와 absence 검증 직후 이 임시 gateway를 다시 중지한다. 원래 실행 중이었다면
restored prior config로 계속 실행한다. Hermes Core를 편집하거나 standalone adapter를
켜서 우회하지 않는다.

위 absence 검증은 assistant 하나가 아니라 일곱 bot 각각에서 수행한다. 각 profile의
`/tools`에 `save_discord_thread_to_obsidian`가 없고 native picker에 `/archive`가 없다는
화면 증거를 `$DEPLOY_RECORD_DIR/<profile>.rollback-absence.txt`에 기록한 뒤에만 아래
상태 복구 gate를 실행한다.

```bash
set -euo pipefail
bash scripts/rollback_discord_save_profiles.sh finalize
```

## 검증 기록과 Ubuntu gate

Windows 로컬에서 `.venv`와 `PYTHONUTF8=1`으로 실행한 focused Runtime v2
suite는 `190 passed`이며 operational shell guard suite는 `8 passed`다. Controller는
동일 interpreter로 feature head와
baseline `c7d52c7`에서 required regression suite의 같은 세 실패를 재현했다.
이는 이 branch가 만든 실패가 아닌 pre-existing baseline failure다. 다만
Phase 14 fixture가 명시 token mapping을 넘기면서 projection path는 profile
token loader를 다시 사용하는 contract conflict가 있으므로, 이를 단순히
environment-only failure라고 분류하지 않는다. 실제 profile env가 있는
server에서의 전체 regression 검증은 배포 성공 전에 여전히 필요하다.

Windows에서 수동 확장한 TypeScript `node --check` 검증은 `BAD=0`이었다.
Repository-wide Ruff는 기존 `1330` finding이 있다. package script의 glob,
mypy, PATH 차이 때문에 Windows에서 `npm run typecheck`와 `npm run lint:ruff`를
required gate로 주장할 수 없다. secret scanner의 staged/range shell tests는 Git
Bash에서 실행하지만, 실제 reviewed commit range gate는 Ubuntu에서 다시 실행한다.
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
  tests/test_runtime_architecture_v2_phase25_command_surface.py \
  tests/test_runtime_architecture_v2_save_skill.py -q

python -m pytest tests/test_discord_save_operational_guards.py -q

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
bash scripts/pre-commit-secret-scan.sh --range "$REVIEWED_BASE..$AI_AGENT_COMMIT"
```

위 Python regression, Ubuntu static gate, identity sync, 일곱 profile hash
증명, assistant smoke 중 하나라도 실패하면 controller는 deployment success를
선언하지 않는다.
# LLM Wiki/QMD rollout addendum (2026-07-20)

This section extends the existing `/archive` procedure for
`/llmwiki-ingest`, `/llmwiki-note`, and `/llmwiki-find`. It does not replace
the existing vault folders or create one QMD collection per Hermes profile.
The commands and versions below were checked against the official QMD GitHub
repository and the ArchiveBox-owned PyPI package before this revision.

## Pinned prerequisites and ARM64 gate

Run this once on `aiagent` before restarting any Gateway. Stop immediately if
the architecture, runtime version, package version, or plugin inventory does
not match the reviewed values.

```bash
set -euo pipefail
cd /home/ubuntu/hermes-workspace/AI_Agent

uname -m | tee "$DEPLOY_RECORD_DIR/machine-architecture.txt"
test "$(uname -m)" = "aarch64"

node --version | tee "$DEPLOY_RECORD_DIR/node-version.txt"
node -e 'const major=Number(process.versions.node.split(".")[0]); process.exit(major >= 22 ? 0 : 1)'
npm install -g @tobilu/qmd@2.5.3
qmd --version | tee "$DEPLOY_RECORD_DIR/qmd-version.txt"

uv tool install abx-dl==1.11.235
abx-dl version | tee "$DEPLOY_RECORD_DIR/abx-dl-version.txt"
abx-dl plugins | tee "$DEPLOY_RECORD_DIR/abx-dl-plugins.txt"
```

`abx-dl plugins` is the authoritative dependency inventory for this pinned
release. Install every reviewed text/metadata extractor dependency it reports
before continuing. Runtime commands always pass `--no-install`; a Gateway is
never allowed to install packages in response to Discord input. Keep browser
cookies, authenticated personas, and private-source credentials outside the
first rollout.

## One whole-vault QMD collection

The collection is shared by all seven profiles and covers the existing vault
without moving `raw/` or `wiki/`.

```bash
set -euo pipefail
export QMD_EMBED_MODEL="hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"
export QMD_FORCE_CPU=1
export QMD_LLAMA_GPU=false

if ! qmd collection list | grep -q '^obsidian'; then
  qmd collection add /home/ubuntu/Obsidian --name obsidian --mask "**/*.md"
fi
qmd update -c obsidian
qmd embed -f -c obsidian
qmd query "회의 결정" --json -c obsidian -n 5 \
  | tee "$DEPLOY_RECORD_DIR/qmd-korean-query.json"
test -s "$DEPLOY_RECORD_DIR/qmd-korean-query.json"
```

Do not create profile-specific collections. QMD configuration, indexes,
models, locks, and `runtime/qmd/dirty.json` stay on local server storage, not
inside the Google Drive-mounted vault.

## `abx-dl --no-install` source probes

Set four accessible public test URLs. These are deployment inputs, not saved
configuration. Do not use private, paid, login-only, or sensitive sources.

```bash
export ABX_PROBE_ARTICLE_URL='https://example.com/'
export ABX_PROBE_YOUTUBE_URL='<public YouTube video URL>'
export ABX_PROBE_INSTAGRAM_URL='<public Instagram post URL>'
export ABX_PROBE_THREADS_URL='<public Threads post URL>'

probe_abx_text() {
  label="$1"
  url="$2"
  output="$DEPLOY_RECORD_DIR/abx-probe-$label"
  mkdir -p "$output"
  timeout 150 abx-dl --no-install --dir="$output" "$url"
  test -s "$output/index.jsonl"
  find "$output" -type f \
    \( -name '*.md' -o -name '*.txt' -o -name '*.json' \
       -o -name '*.vtt' -o -name '*.srt' -o -name '*.html' \) \
    -size +0c -print -quit | grep -q .
}

probe_abx_text generic-article "$ABX_PROBE_ARTICLE_URL"       # generic article
probe_abx_text youtube-video "$ABX_PROBE_YOUTUBE_URL"         # YouTube video
probe_abx_text instagram-post "$ABX_PROBE_INSTAGRAM_URL"      # public Instagram post
probe_abx_text threads-post "$ABX_PROBE_THREADS_URL"          # public Threads post
```

An inaccessible source or a source with no bounded textual artifact is an
unsupported case. Do not add a Runtime v2 site-specific fallback to make a
failed probe pass.

## Shared five-minute reconciliation timer

Install exactly one service and one timer for the whole server. Do not install
copies under the seven profile directories.

```bash
python -m scripts.run_qmd_reconcile --root /home/ubuntu/hermes-workspace/AI_Agent

sudo tee /etc/systemd/system/ai-agent-qmd-reconcile.service >/dev/null <<'EOF'
[Unit]
Description=Reconcile the AI Agent Obsidian QMD index
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
TimeoutStartSec=30min
WorkingDirectory=/home/ubuntu/hermes-workspace/AI_Agent
Environment=HOME=/home/ubuntu
Environment=PYTHONUTF8=1
Environment=PATH=/home/ubuntu/.local/bin:/home/ubuntu/.hermes/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=QMD_EMBED_MODEL=hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf
Environment=QMD_FORCE_CPU=1
Environment=QMD_LLAMA_GPU=false
ExecStart=/usr/bin/python3 -m scripts.run_qmd_reconcile --root /home/ubuntu/hermes-workspace/AI_Agent
EOF

sudo tee /etc/systemd/system/ai-agent-qmd-reconcile.timer >/dev/null <<'EOF'
[Unit]
Description=Run QMD reconciliation every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
Unit=ai-agent-qmd-reconcile.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ai-agent-qmd-reconcile.timer
sudo systemctl start ai-agent-qmd-reconcile.service
systemctl --no-pager --full status ai-agent-qmd-reconcile.service
systemctl --no-pager --full status ai-agent-qmd-reconcile.timer
```

A nonzero service result leaves `runtime/qmd/dirty.json` in place for the next
timer run. Service output contains only stable status codes and must not include
source text, tokens, stderr, or absolute credential paths.

## Assistant-first command smoke and rollout

Deploy the reviewed plugin hash to `aicompanyassistant` first. In a designated
test thread, verify these commands in order:

1. `/llmwiki-note deployment smoke note`
2. `/llmwiki-ingest summarize this https://example.com/`
3. `/llmwiki-find deployment smoke`
4. `/archive`
5. Repeat `/archive` without new messages and confirm `unchanged`.

Confirm the new raw record is under `raw/notes/` or `raw/sources/`, the
canonical page is under `wiki/`, `wiki/log.md` contains one idempotency marker,
and the Korean QMD query returns only vault-relative paths. Record no message
body or credential in deployment evidence.

Only after the Assistant smoke passes, install the same plugin file hashes and
restart the remaining six profiles sequentially. Stop on the first hash,
Gateway, command, or timer failure.

Rollback disables `ai-agent-qmd-reconcile.timer`, restores the previous plugin
revision, and restarts only profiles that were running before deployment:

```bash
sudo systemctl disable --now ai-agent-qmd-reconcile.timer
sudo systemctl stop ai-agent-qmd-reconcile.service
bash scripts/rollback_discord_save_profiles.sh prepare
# Verify plugin/tool/command absence for all profiles, then:
bash scripts/rollback_discord_save_profiles.sh finalize
```

Rollback leaves the Obsidian vault, immutable raw records, and the local QMD
cache intact. It must not delete user knowledge to remove the command surface.

## Meeting command extension (`ai-agent-commands 0.3.0`)

Version `0.3.0` adds `/meeting-start` and `/meeting-report` to the same reviewed
plugin. `/meeting-start` calls the existing Runtime v2 Gateway bridge and
persists the resolved Discord thread as `MeetingRun.metadata.discord_thread_id`.
`/meeting-report` resolves only that persisted linkage and uses the existing
on-demand export service. Neither command writes to Obsidian automatically;
the user keeps using `/archive` when the thread should be retained.

Apply the same source-hash, enable-without-tool-override, and assistant-first
rules from this runbook. Before starting any provider-backed meeting, use the
assistant profile for a bounded registration smoke:

1. Confirm `/meeting-start` and `/meeting-report` appear in the native picker.
2. Run blank `/meeting-start` and confirm that it requests a meeting topic
   without creating a thread or MeetingRun.
3. Run outside-thread `/meeting-report` and confirm that it requests a linked
   meeting thread without creating a report.
4. Confirm `/archive` and all three `/llmwiki-*` commands remain present.
5. Confirm the installed `plugin.yaml` and `__init__.py` hashes match the
   reviewed checkout and that the Gateway stays connected.

Only after this assistant smoke passes, install the same `0.3.0` hashes and
restart the remaining six profiles sequentially. After the first supervised
content-level meeting, verify the created `meeting_run.json` contains the exact
`discord_thread_id`, then run `/meeting-report 브리핑해줘` inside that thread.
Keep the first provider-backed content smoke supervised; registration rollout
must not create a live meeting by itself.

Rollback restores the prior plugin revision and restarts the affected profiles.
It does not delete MeetingRun artifacts, archived conversations, the Obsidian
vault, or the QMD index.

## KakaoTalk read-only extension (`ai-agent-commands 0.4.0`)

Version `0.4.0` adds two internal tools and the `kakao-collect` Hermes skill.
The skill appears as `/kakao-collect` in Discord, lists at most 10 recent rooms
from the JSON object in `KAKAO_ALLOWED_ROOMS`, and uses Hermes `clarify`
buttons for selection. Keys are numeric KakaoTalk chat IDs and values are safe
display names. An empty, malformed, or unmatched allowlist fails closed.

The extension calls only Iris `http://127.0.0.1:3000/query`. It contains no
KakaoTalk send or reply tool. Raw messages are written to
`raw/chat-logs/kakaotalk/<chat_id>/`; cursors are written to
`runtime/kakaotalk/cursors/` only after persistence. On the first request for a
room, `initial_baseline=current` records the current cursor without importing
old history.

Deploy the plugin and `hermes_skills/kakao-collect/SKILL.md` from the same
reviewed commit. Restart profiles sequentially, confirm the native command is
registered, and verify tool inventory contains only
`list_recent_kakaotalk_rooms` and `collect_kakaotalk_room_readonly` for Kakao.
Do not expose Iris port 3000 or Android ADB port 5555 beyond loopback.
