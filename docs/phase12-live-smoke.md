# Phase 12 Live Operational Smoke

## 12.1 Discord live projection smoke

Status: PASS

Date: 2026-06-23 UTC
Target guild: `1505600166676271244`
Target channel: `1507235209878442105` (`시스템-로그`)
Bot role used: `ceo_coordinator`
Profile used: `aicompanyceo`

### Purpose

Verify that `LiveDiscordProjectionSink` can publish a safe Runtime Architecture v2 projection event to real Discord through the Discord REST API.

This smoke is a REST projection smoke only. It does not prove Discord app interaction trigger handling, slash command registration, deferred interaction completion, or live worker execution.

### Result

Published Discord message:

```text
message_id: 1518818346898821270
event_id: phase12_1_live_projection_smoke_2026-06-23T032153Z0000
publish_status: published
verified_get: true
author_bot: true
```

Safety checks:

```text
allowed_mentions: parse[]
synthetic secret redaction: PASS
@here literal blocking: PASS
raw Discord token output: none
```

### Finding during smoke

Initial live publish failed with:

```text
publish_status: failed
publish_error: discord_http_403
```

Root cause was not channel permission. `/users/@me` and channel reads also returned Cloudflare `error code: 1010` when using Python `urllib` default headers. Adding a Discord-compatible `User-Agent` made the same bot token and channel access succeed.

### Fix applied

`src/runtime_architecture_v2/projection.py` now sets a default Discord-compatible `User-Agent` in `_default_discord_http_post`:

```text
DiscordBot (https://github.com/kbm323/AI_Agent, phase12-live-smoke) Python/urllib
```

Regression test added:

```text
tests/test_runtime_architecture_v2_projection.py::test_default_discord_http_post_sends_discord_compatible_user_agent
```

### Verification commands

```bash
pytest tests/test_runtime_architecture_v2_projection.py -q
ruff check src/runtime_architecture_v2/projection.py tests/test_runtime_architecture_v2_projection.py
```

Observed result:

```text
17 passed
All checks passed
```

### Boundary classification

```text
Real deterministic core: existing Runtime Architecture v2 projection schemas/formatter
Live adapter smoke: PASS, Discord REST post + GET verification
Discord-app interaction smoke: not covered
Full e2e live: not covered
opencode-go worker live smoke: not covered, deferred because Go monthly quota is critical
```

### Next recommended Phase 12 work

1. Phase 12.3 bot permission hardening.
2. Phase 12.5 personal assistant UX/channel cleanup.
3. Phase 12.2 opencode-go worker live smoke only after quota risk is acceptable, or with a single minimal packet.
