# Phase 12.3 Discord Bot Permission Hardening Inventory

## Decision

Phase 12.3 is an inventory and hardening guide, not an automatic permission mutation step.

Current live Discord REST checks show all 7 bots are valid and can read their home channel and the system-log channel. However, every bot currently has risky guild-level permissions. The recommended posture removes all server-management and moderation permissions from bots by default.

## Verification Snapshot

- Date: 2026-06-23
- Guild ID: `1505600166676271244`
- System-log channel ID: `1507235209878442105`
- API checks used: Discord REST v10 with bot tokens from local Hermes profile `.env` files
- Token hygiene: token values were not printed or written
- Profiles checked: 7/7
- `/users/@me`: 7/7 OK
- Guild member lookup: 7/7 OK
- Guild roles lookup: OK
- Home-channel access: 7/7 OK
- System-log access: 7/7 OK
- Recommended posture contains Administrator: no

## Permission Model

This document distinguishes three things:

1. Current effective guild permissions observed through role membership and guild role permission integers.
2. Recommended OAuth invite permission integer for future re-invite / role hardening.
3. Manual action required in Discord UI or via a confirmed admin API path.

Do not claim permissions are hardened until the Discord server role state or OAuth re-invite has actually been changed and re-verified.

## Risk Flags

Risky permissions tracked in this inventory:

```text
ADMINISTRATOR
MANAGE_GUILD
MANAGE_CHANNELS
MANAGE_ROLES
MANAGE_WEBHOOKS
MANAGE_MESSAGES
MENTION_EVERYONE
BAN_MEMBERS
KICK_MEMBERS
MODERATE_MEMBERS
VIEW_AUDIT_LOG
```

Required base capabilities for the current bot workflow:

```text
VIEW_CHANNEL
SEND_MESSAGES
READ_MESSAGE_HISTORY
USE_APPLICATION_COMMANDS
SEND_MESSAGES_IN_THREADS
```

## Current Inventory

| Profile | Bot | Current guild permission integer | Risky permissions currently present | Home channel | System-log | Recommended integer | Manual hardening required |
|---|---|---:|---|---|---|---:|---|
| `aicompanyceo` | 버추얼컴퍼니-대표 | `2248490645712497` | `MANAGE_GUILD`, `MANAGE_CHANNELS`, `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `328565115968` | Yes |
| `aicompanyassistant` | 개인비서-Hermes | `2248490645712497` | `MANAGE_GUILD`, `MANAGE_CHANNELS`, `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `311385246784` | Yes |
| `aicompanycontent` | 버추얼컴퍼니-콘텐츠팀장 | `2248490645712497` | `MANAGE_GUILD`, `MANAGE_CHANNELS`, `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `311385246784` | Yes |
| `aicompanyart` | 버추얼컴퍼니-아트팀장 | `2248490645712497` | `MANAGE_GUILD`, `MANAGE_CHANNELS`, `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `311385246784` | Yes |
| `aicompanytech` | 버추얼컴퍼니-기술팀장 | `2248490645712449` | `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `311385246784` | Yes |
| `aicompanymarketing` | 버추얼컴퍼니-마케팅팀장 | `2248490645712449` | `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `311385246784` | Yes |
| `aicompanyquality` | 버추얼컴퍼니-품질관리팀장 | `2248490645712449` | `MANAGE_MESSAGES`, `MENTION_EVERYONE` | OK | OK | `328565115968` | Yes |

## Findings

### 1. Administrator is not present

No bot currently shows `ADMINISTRATOR` in the observed guild-level role permission set.

### 2. Four bots have server-management permissions

The following bots currently include both `MANAGE_GUILD` and `MANAGE_CHANNELS`:

```text
aicompanyceo
aicompanyassistant
aicompanycontent
aicompanyart
```

These permissions are not allowed by the Phase 12 posture. The assistant bot is the most important one to reduce because it should remain a low-privilege personal assistant, not a server administrator.

### 3. All bots can mention everyone

All 7 bots currently include `MENTION_EVERYONE`. This is unnecessary for the current mention-gated workflow and should be removed.

### 4. All bots have Manage Messages

All 7 bots currently include `MANAGE_MESSAGES`. This may be useful for moderation bots, but these bots are operational/team agents, not moderation agents. Remove by default unless a future workflow explicitly needs cleanup/moderation.

### 5. Current channel access is sufficient

Every bot can currently read its home channel and `#시스템-로그`. Reducing guild-level management permissions should preserve basic channel access if the recommended permission set is used and channel overwrites do not explicitly deny the bot roles.

## Recommended Permission Integers

### Team-lead and assistant bots

Use for:

```text
aicompanyassistant
aicompanycontent
aicompanyart
aicompanytech
aicompanymarketing
```

Recommended permission integer:

```text
311385246784
```

Risky permissions included: none from the tracked risk list.

### CEO and quality bots

Use for:

```text
aicompanyceo
aicompanyquality
```

Recommended permission integer:

```text
328565115968
```

Risky permissions included: none from the tracked risk list.

This keeps CEO/Quality slightly above the team-lead posture for thread-oriented workflow, while still excluding server-management/moderation permissions.

## Recommended OAuth Re-Invite URLs

Use these only if choosing the OAuth re-invite path. They include `bot` and `applications.commands` scopes.

```text
대표:
https://discord.com/oauth2/authorize?client_id=1518627210930421831&permissions=328565115968&scope=bot%20applications.commands

비서:
https://discord.com/oauth2/authorize?client_id=1505920161956499649&permissions=311385246784&scope=bot%20applications.commands

콘텐츠팀장:
https://discord.com/oauth2/authorize?client_id=1518653758953885808&permissions=311385246784&scope=bot%20applications.commands

아트팀장:
https://discord.com/oauth2/authorize?client_id=1518654338136801371&permissions=311385246784&scope=bot%20applications.commands

기술팀장:
https://discord.com/oauth2/authorize?client_id=1518654736608399380&permissions=311385246784&scope=bot%20applications.commands

마케팅팀장:
https://discord.com/oauth2/authorize?client_id=1518655166599925832&permissions=311385246784&scope=bot%20applications.commands

품질관리팀장:
https://discord.com/oauth2/authorize?client_id=1518656763484704919&permissions=328565115968&scope=bot%20applications.commands
```

## Manual Hardening Procedure

Preferred safe path:

1. In Discord server settings, inspect each bot role.
2. Remove these permissions from every bot role:
   - `Manage Server`
   - `Manage Channels`
   - `Manage Messages`
   - `Mention @everyone, @here, and All Roles`
   - `Manage Roles`
   - `Manage Webhooks`
   - `Kick Members`
   - `Ban Members`
   - `Moderate Members`
   - `View Audit Log`
3. Preserve the required workflow capabilities:
   - View Channels
   - Send Messages
   - Read Message History
   - Use Application Commands
   - Send Messages in Threads
   - Create/Use Threads only where needed
4. If role editing is ambiguous, re-invite each bot with the recommended OAuth URL above.
5. Restart or status-check the gateways only after permission changes are complete.
6. Re-run the inventory check and confirm:
   - 7/7 tokens valid
   - 7/7 home channels readable
   - 7/7 system-log readable
   - 0 tracked risky permissions present

## Verification Command Used

The inventory was collected with a local one-shot Python script that:

- loads `DISCORD_BOT_TOKEN` from each local Hermes profile `.env`
- calls `/users/@me`
- calls `/guilds/{guild_id}/members/{bot_user_id}`
- calls `/guilds/{guild_id}/roles`
- calls `/channels/{home_channel_id}`
- calls `/channels/{system_log_channel_id}`
- prints only IDs, status codes, permission integers, and permission names
- never prints token values

Expected summary:

```text
profiles_checked: 7
roles_fetch_ok: true
/users/@me: 7/7 200
guild member: 7/7 200
home-channel access: 7/7 200
system-log access: 7/7 200
tracked risky permissions after current inventory: present
tracked risky permissions in recommended posture: none
```

## Phase 12.3 Status

Current status:

```text
Inventory: PASS
Documentation: PASS
Actual Discord permission mutation: NOT PERFORMED
Hardening complete: NO, manual action required
```

Phase 12.3 can be considered complete only as an inventory/documentation step. The operational permission hardening itself remains manual until the Discord server role state is changed and re-verified.
