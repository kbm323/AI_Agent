# Live Verification

## Required `.env`

```text
DISCORD_BOT_TOKEN=<AI_Agent discord bot token>
HERMES_DISCORD_BOT_TOKEN=<optional Hermes discord bot token for reviewer posts>
AI_AGENT_PROJECT_CHANNEL_IDS=1505600167221526621
AI_AGENT_DB_PATH=./data/ai_agent.sqlite
AI_AGENT_MAX_ROUNDS=4
AI_AGENT_THREAD_AUTO_ARCHIVE_MINUTES=10080
AI_AGENT_OPENCLAW_COMMAND=openclaw
AI_AGENT_OPENCLAW_AGENT_ID=main
AI_AGENT_OPENCLAW_TIMEOUT_SECONDS=600
AI_AGENT_HERMES_COMMAND=hermes
```

Do not put tokens in commits or screenshots.

## OpenClaw Plugin Runtime

Current live verification uses the installed OpenClaw plugin:

```text
/home/kbm/.openclaw/local-plugins/inter-agent-orchestration
```

Restart after plugin edits:

```bash
systemctl --user restart openclaw-gateway.service
journalctl --user -u openclaw-gateway.service --since -30s --no-pager
```

Expected plugin log prefix:

```text
[IAO-LIVE]
```

## Dev Harness Run

```bash
npm run live:start
```

`live:start` temporarily stops `openclaw-gateway.service` and `hermes-gateway.service` so their Discord tokens do not conflict with AI_Agent's OpenClaw/Hermes bot posters. Press `Ctrl+C` to restore both gateway services.

Status and stop helpers:

```bash
npm run live:status
npm run live:stop
```

Then post a neutral test message in parent channel `1505600167221526621`, for example:

```text
랜덤 테스트 요청: 후보를 만들고 리뷰해서 최종안을 정리해줘
```

## Expected Parent Channel

Allowed:

```text
Agent discussion started -> <thread>
```

Not allowed:

- OpenClaw draft
- Hermes reviewer request
- Hermes review
- Final synthesis

## Expected Thread

- User request
- OpenClaw draft
- compact Hermes reviewer request timeline entry
- Hermes review
- Final synthesis, unless escalation pauses the task

The full captured OpenClaw draft must be included in the reviewer prompt and persisted in SQLite as `review_request`, but it should not be repeated verbatim in the Discord reviewer request message.

## Expected Logs

```text
[IAO-LIVE] parent channel request detected
[IAO-LIVE] auto thread created
[IAO-LIVE] orchestration target switched
[IAO-LIVE] OpenClaw parent reply intercepted
[IAO-LIVE] parent reply suppressed
[IAO-LIVE] OpenClaw draft captured
[IAO-LIVE] OpenClaw draft posted threadId=<createdThreadId>
[IAO-LIVE] reviewer request includes captured draft
[IAO-LIVE] Hermes reply detected threadId=<createdThreadId>
[IAO-LIVE] Final synthesis posted threadId=<createdThreadId>
```

For escalation:

```text
[IAO-LIVE] User decision required threadId=<createdThreadId>
```

For resume:

```text
[IAO-LIVE] User decision received threadId=<createdThreadId>
[IAO-LIVE] Final synthesis posted threadId=<createdThreadId> stopReason="user_decision_received" resumed=true
[IAO-LIVE] orchestration resumed from user decision
```

## Inspect DB

```bash
npm run inspect:latest
```

The latest task should contain full turn content in SQLite:

- `owner_draft`
- `review_request`
- `review`
- `final_synthesis`

Escalation/resume tasks should contain:

- `owner_draft`
- `review_request`
- `review`
- `escalation`
- `user_decision`
- `final_synthesis`

If the task fails before Hermes, inspect whether `owner_draft` is missing or equal to the user request.
