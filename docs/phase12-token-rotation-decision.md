# Phase 12.4 Token Rotation Decision

## Decision

Do not rotate the Discord bot tokens at this time.

The current operational decision is to keep the existing tokens and avoid disrupting the live multi-bot gateway setup.

## Rationale

The current system is in controlled staging, not public production launch.

Current verified state:

```text
7/7 bot tokens valid
7/7 profiles have working Discord API access
7/7 home channels are readable
7/7 system-log access was verified in Phase 12.3
7/7 Hermes gateway tmux sessions are running after Phase 12.5
require_mention=true
thread_require_mention=true
free_response channels are empty
Administrator permission is absent
```

Given that the user explicitly decided token reset is unnecessary now, Phase 12.4 records the decision rather than performing credential rotation.

## Security Boundary

This decision does not mean token exposure is harmless. It means the accepted operational posture for this staging phase is:

```text
No token reset now.
Do not print tokens.
Do not commit tokens.
Keep tokens only in local Hermes profile .env files.
Keep mention-gating enabled.
Keep free-response disabled.
Revisit rotation only if a concrete incident, leak, or production-readiness gate requires it.
```

## Explicit Non-Actions

The following actions were not performed:

```text
No Discord Developer Portal token reset
No profile .env token replacement
No token values printed
No token values written to project docs
No gateway restart for token replacement
No OAuth re-invite or permission mutation
```

## Rotation Trigger Conditions

Token rotation should be revisited only if one of these conditions occurs:

```text
1. A token value appears in a committed file or shared external channel.
2. A bot shows unexpected activity or unauthorized access.
3. The system moves from controlled staging to public/production operation.
4. A Discord security warning or bot token compromise alert appears.
5. The user explicitly requests token reset later.
```

## If Rotation Is Needed Later

If a future rotation is approved, use this bounded procedure:

1. Reset each bot token in Discord Developer Portal.
2. Update only local profile `.env` files:

   ```text
   ~/.hermes/profiles/aicompanyceo/.env
   ~/.hermes/profiles/aicompanyassistant/.env
   ~/.hermes/profiles/aicompanycontent/.env
   ~/.hermes/profiles/aicompanyart/.env
   ~/.hermes/profiles/aicompanytech/.env
   ~/.hermes/profiles/aicompanymarketing/.env
   ~/.hermes/profiles/aicompanyquality/.env
   ```

3. Do not paste token values into chat, project docs, git commits, issue comments, or logs.
4. Restart gateways:

   ```bash
   bash scripts/stop_discord_multibot_gateways.sh
   bash scripts/start_discord_multibot_gateways.sh
   ```

5. Verify without printing token values:

   ```text
   /users/@me: 7/7 OK
   home-channel access: 7/7 OK
   system-log access: 7/7 OK
   gateway sessions: 7/7 running
   ```

## Phase 12.4 Status

```text
Token rotation decision: DO NOT ROTATE NOW
Credential mutation: NOT PERFORMED
Gateway restart for token replacement: NOT NEEDED
Tracked secrets introduced: NO
Status: PASS as decision record
```
