# Phase 24 Live Boundary Inventory & Allowlist Foundation Result

> Status: COMPLETE
> Date: 2026-06-25 KST
> Canonical baseline: `docs/runtime-architecture-v2.md`
> Previous boundary checklist: `docs/phase23-live-production-hardening-checklist.md`

## Decision

Phase 24 implements the first live-production hardening gate after Phase 23: explicit live Discord boundary allowlisting.

It does not expand the Discord command surface and does not mutate live Discord permissions, tokens, channels, or service processes.

```text
PASS: Hermes-first command surface preserved.
PASS: standalone slash commands are not default requirements.
PASS: current bot permission posture is preserved.
PASS: live Discord projection can now fail closed on guild/profile/channel mismatch before HTTP.
```

## Implemented Changes

### Code

```text
src/runtime_architecture_v2/projection.py
```

Added:

```text
DiscordBoundaryDecision
DiscordLiveBoundaryPolicy
```

The current verified policy records:

```text
Guild: Entertainment / 1505600166676271244
Profiles: 7
- aicompanyassistant / #일일-브리핑
- aicompanyceo / #회의실-전략결정
- aicompanycontent / #콘텐츠-메인
- aicompanyart / #아트-메인
- aicompanytech / #기술-메인
- aicompanymarketing / #마케팅-메인
- aicompanyquality / #전체-리뷰
```

Safety posture encoded in policy:

```text
permission_mutation_allowed = False
administrator_allowed = False
require_mention = True
thread_require_mention = True
free_response_channels = ()
```

`LiveDiscordProjectionSink` now accepts optional:

```text
boundary_policy
profile
guild_id
```

When a boundary policy is provided, the sink evaluates the parent channel before token lookup and before making the Discord REST HTTP call. A blocked decision returns a structured result and does not call the injected HTTP client.

Phase 13 and Phase 14 live Discord projection paths now inject `DiscordLiveBoundaryPolicy.current_verified()` by default. Because the current verified allowlist is symbolic until a token-safe real channel-ID inventory is run, arbitrary live Discord channels fail closed instead of posting.

### Tests

```text
tests/test_runtime_architecture_v2_projection.py
```

Added TDD regressions:

```text
test_phase24_live_boundary_policy_preserves_current_bot_permissions
test_phase24_live_boundary_policy_fails_closed_for_unknown_guild_or_channel
test_live_discord_projection_sink_blocks_disallowed_boundary_before_http
test_live_discord_projection_sink_allows_thread_post_when_parent_channel_allowed
test_phase13_live_discord_boundary_blocks_unknown_channel_before_http
test_route_bot_projection_live_discord_unknown_channel_blocks_before_http
```

RED result:

```text
ImportError: cannot import name 'DiscordLiveBoundaryPolicy'
```

GREEN result:

```text
python3 -m pytest tests/test_runtime_architecture_v2_projection.py -q
→ 21 passed

python3 -m pytest tests/test_runtime_architecture_v2_phase13_pilot.py::test_phase13_live_discord_boundary_blocks_unknown_channel_before_http -q
→ 1 passed

python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_route_bot_projection_live_discord_unknown_channel_blocks_before_http -q
→ 1 passed
```

### Documentation

Added:

```text
docs/phase24-live-boundary-inventory-allowlist-plan.md
docs/phase24-live-boundary-inventory-allowlist.md
```

## Constraints Preserved

```text
No Discord permission mutation.
No Administrator permission introduction.
No global free-response channel introduction.
No token rotation.
No channel creation/deletion.
No standalone slash command expansion.
No separate Discord interaction endpoint.
```

## Important Note on Channel IDs

Current Hermes profile config records mention-gating and free-response posture, but does not expose profile-local home channel IDs in `.env` or `config.yaml`.

Therefore Phase 24 encodes the current verified home-channel allowlist as symbolic profile/home-channel entries:

```text
home:<profile>:#<channel-name>
```

This is enough to enforce fail-closed behavior in deterministic tests. A later live verification phase can replace or augment these symbolic entries with real Discord channel IDs after a token-safe inventory run, without changing the policy shape.

## Production Readiness Wording

Use this wording after Phase 24:

```text
Phase 24 live boundary allowlist foundation complete.
Runtime v2 deterministic orchestration layer remains complete.
Live production hardening still remains for live worker/validator/auditor smoke, service supervision, full closed-loop live pilot, and 24h production runbook. Hermes Gateway surface verification was completed in Phase 25.
```

Do not claim fully live production readiness yet.

## Remaining Phases

| Phase | Content |
|---|---|
| Phase 26 | Live Worker / Validator / Auditor Boundary Smoke — quota-gated opencode-go, GLM validator, Codex auditor boundary smoke. |
| Phase 27 | Always-on Service Supervision Pilot — start/stop/status/heartbeat/log bounds for the 7 Hermes profiles without permission expansion. |
| Phase 28 | Full Live Closed-loop Pilot — Hermes Gateway input to MeetingRun to workers/validation/projection in controlled smoke channel. |
| Phase 29 | 24h Live Pilot & Production Runbook — bounded operations proof, recovery, quota/cost evidence, final production readiness verdict. |
