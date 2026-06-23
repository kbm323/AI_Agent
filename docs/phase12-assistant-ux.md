# Phase 12.5 Personal Assistant UX / Channel Cleanup

## Decision

Do not mutate Discord channel structure automatically in Phase 12.5.

The assistant bot remains mapped to `#일일-브리핑` for now. A dedicated `#개인-비서` channel is recommended as the final UX target, but it should be created manually or in a separately approved live Discord administration step.

## Reasoning

The assistant bot is a personal/operations assistant, not a server administrator.

Current safe posture is already acceptable for controlled staging:

```text
DISCORD_REQUIRE_MENTION=true
DISCORD_THREAD_REQUIRE_MENTION=true
DISCORD_FREE_RESPONSE_CHANNELS=
```

This means the assistant does not free-respond globally and should only respond when mentioned.

Creating channels or changing Discord server structure is a live administrative mutation. Since Phase 12.3 intentionally deferred permission changes and the current role posture still includes broad permissions, it is safer not to use those permissions just because they exist.

## Current State

- Profile: `aicompanyassistant`
- Bot: `개인비서-Hermes`
- Current home channel: `#일일-브리핑`
- Current home channel ID: `1507063720025522267`
- Dedicated `#개인-비서` channel: not present in the current guild channel list
- Guild text channels checked: 11
- Assistant home channel REST check: OK
- Mention-gating: enabled
- Thread mention-gating: enabled
- Free-response channels: empty
- Token values printed or written: no

## Target UX

Final target behavior:

```text
Assistant is a personal/operations assistant.
Assistant does not administer the Discord server.
Assistant responds only when mentioned.
Assistant has a clearly named home channel.
Assistant does not free-respond globally.
Assistant does not need Administrator or server-management permissions.
```

Recommended dedicated channel:

```text
#개인-비서
```

Recommended channel usage:

```text
- 개인 브리핑
- 사용자의 직접 지시
- 일정/작업 요약
- 회사 운영 상태 질의
- 다른 팀장 봇에게 전달할 요청 초안
```

Out of scope for the assistant:

```text
- 서버 관리
- 역할/권한 변경
- 채널 생성/삭제
- 메시지 moderation
- @everyone 호출
- 글로벌 free response
```

## Current Mapping

| Profile | Bot | Current Home | Current Channel ID | Target Home | Status |
|---|---|---|---|---|---|
| `aicompanyassistant` | 개인비서-Hermes | `#일일-브리핑` | `1507063720025522267` | `#개인-비서` | Deferred until channel exists |

## Recommended Manual Procedure

When ready to separate the assistant UX:

1. Human Discord server admin creates text channel:

   ```text
   #개인-비서
   ```

2. Keep the channel visible only to the intended user/admin group if privacy is desired.

3. Preserve minimal assistant workflow permissions in that channel:

   ```text
   View Channel
   Send Messages
   Read Message History
   Use Application Commands
   Send Messages in Threads, if threads are used
   ```

4. Do not grant the assistant:

   ```text
   Administrator
   Manage Server
   Manage Channels
   Manage Roles
   Manage Webhooks
   Manage Messages
   Mention Everyone
   ```

5. After the channel exists, update only the assistant profile `.env`:

   ```text
   ~/.hermes/profiles/aicompanyassistant/.env
   DISCORD_HOME_CHANNEL=<new 개인-비서 channel id>
   ```

6. Restart only the assistant gateway if gateways are running:

   ```bash
   tmux kill-session -t hermes-aicompanyassistant
   tmux new-session -d -s hermes-aicompanyassistant -x 120 -y 40 \
     'HERMES_ACCEPT_HOOKS=1 hermes --profile aicompanyassistant gateway run'
   ```

7. Verify:

   ```text
   /users/@me returns 200
   /channels/<new channel id> returns 200
   DISCORD_REQUIRE_MENTION=true
   DISCORD_THREAD_REQUIRE_MENTION=true
   DISCORD_FREE_RESPONSE_CHANNELS is empty
   assistant gateway is running, if live gateway is intended
   ```

## Phase 12.5 Verification Snapshot

Performed checks:

```text
Discord guild channels listed through REST: PASS
Dedicated #개인-비서 channel found: NO
Assistant current home channel exists: PASS
Assistant current home channel name: #일일-브리핑
Assistant require_mention: true
Assistant thread_require_mention: true
Assistant free-response channels: empty
Token values printed: no
```

Gateway status at the time of this check:

```text
Initially no hermes-aicompany tmux sessions were running.
Existing gateway start script was then used to restart all 7 profiles.
7/7 hermes-aicompany tmux sessions were present after restart.
```

This does not require profile/channel mutation. If a future `#개인-비서` channel migration is performed, restart only the assistant gateway after updating `DISCORD_HOME_CHANNEL`.

## Phase 12.5 Status

```text
Assistant UX inventory: PASS
Assistant safety posture: PASS
Dedicated channel creation: DEFERRED
Assistant profile mutation: DEFERRED
Gateway restart: 7/7 existing profile gateways restarted with current configuration
```

Phase 12.5 is complete as a safe UX/channel cleanup decision record. The final `#개인-비서` channel migration is deferred until the channel is manually created or a separate Discord administration step is explicitly accepted.
