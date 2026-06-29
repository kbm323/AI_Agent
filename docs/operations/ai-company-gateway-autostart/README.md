# AI Company Gateway Autostart — 기록/백업

Created: 2026-06-28 KST
Scope: Hermes default profile startup hook for 7 AI Company Discord gateways.

## 목적

사용자가 Hermes를 켜고 새 세션이 시작될 때, 7개 AI Company Discord gateway tmux 세션이 자동으로 살아있는지 확인하고 누락된 gateway만 시작한다.

대상 프로필:

```text
aicompanyassistant
aicompanyceo
aicompanycontent
aicompanyart
aicompanytech
aicompanymarketing
aicompanyquality
```

## 구현 방식

Hermes core를 수정하지 않고 공식 shell hook 기능을 사용한다.

설정 위치:

```text
/home/kbm/.hermes/config.yaml
```

추가된 hook:

```yaml
hooks:
  on_session_start:
    - command: /home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh
      timeout: 20
hooks_auto_accept: false
```

수동 allowlist:

```text
/home/kbm/.hermes/shell-hooks-allowlist.json
```

스크립트:

```text
/home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh
```

## 동작 원칙

- 기존 실행 중인 gateway는 건드리지 않는다.
- 누락된 `gw_<profile>` tmux session만 시작한다.
- profile-local `.env`를 source한 뒤 `hermes --profile <profile> gateway run`을 실행한다.
- 중복 실행 방지를 위해 flock + 60초 cooldown stamp를 사용한다.
- hook stdout은 `{}`만 출력해서 Hermes agent context에 불필요한 내용을 넣지 않는다.
- 로그는 `~/.hermes/logs/ai-company-gateway-autostart.log`에 남긴다.

## 검증 기록

실행한 검증:

```text
bash -n /home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh
hermes hooks doctor
hermes hooks test on_session_start
```

결과:

```text
bash_syntax_ok
All shell hooks look healthy.
Configured shell hooks (1 total):
  [on_session_start]
    - /home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh (timeout=20s, allowed)
```

실제 복구 테스트:

1. `gw_aicompanymarketing` tmux session을 의도적으로 종료.
2. autostart hook 직접 실행.
3. 7개 gateway 모두 alive 확인.

결과:

```text
before_hook_marketing_alive=false
aicompanyassistant alive=true
aicompanyceo alive=true
aicompanycontent alive=true
aicompanyart alive=true
aicompanytech alive=true
aicompanymarketing alive=true
aicompanyquality alive=true
```

로그:

```text
started=aicompanymarketing already=aicompanyassistant aicompanyceo aicompanycontent aicompanyart aicompanytech aicompanyquality failed=none
```

## 주의사항

- 이 설정은 OS 부팅 autostart가 아니다.
- “Hermes가 켜져 새 세션이 시작될 때” gateway를 보장한다.
- WSL 재부팅 직후 Hermes를 한 번 실행하면 7개 gateway가 올라와야 한다.
- 7개 gateway는 tmux session 이름 `gw_<profile>`로 관리된다.
- script edits are trusted by shell-hook allowlist, so 이 파일을 수정하면 다시 `hermes hooks doctor`로 확인한다.

## 복구 방법

1. 백업의 `start-ai-company-gateways.sh.backup`을 아래 위치에 복사:

```text
/home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh
```

2. 실행 권한 부여:

```bash
chmod 700 /home/kbm/.hermes/agent-hooks/start-ai-company-gateways.sh
```

3. `/home/kbm/.hermes/config.yaml`에 hook가 없으면 추가.
4. `/home/kbm/.hermes/shell-hooks-allowlist.json`에 `on_session_start` approval이 없으면 추가.
5. 검증:

```bash
hermes hooks list
hermes hooks doctor
```
