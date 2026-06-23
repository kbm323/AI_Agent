# Phase 12 Live Operational Hardening Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Convert the Phase 11 Runtime Architecture v2 verified baseline into a safer live Discord operating posture without expanding scope into full e2e worker execution before quota and permission risks are controlled.

**Architecture:** Phase 12 is post-v2 operational hardening, not part of the original Phase 0-11 implementation plan. Keep Hermes Core untouched. Treat Discord as a projection/operations surface, use Discord REST only for narrow live smoke and permission/channel administration, and keep opencode-go live execution deferred or minimal while Go monthly quota is critical.

**Tech Stack:** Python, pytest, ruff, Discord REST API, Hermes profile `.env` files, tmux gateway scripts, GitHub MCP for remote verification.

---

## Current Context

Canonical Runtime Architecture v2 plan currently ends at Phase 11:

- `docs/runtime-architecture-v2-implementation-plan.md`
- `docs/runtime-architecture-v2-final-verification.md`

Phase 11 explicitly listed post-v2 operational items as outside the deterministic verification gate:

```text
1. Push local commit(s) to origin/main after GitHub credentials are fixed.
2. Optionally reset Discord/provider tokens that were ever exposed in chat or local history.
3. Optionally re-invite bots with narrower Discord permission integers.
4. Proceed to post-v2 live operational hardening / Phase 12 only after deciding scope.
```

Phase 12.1 has already been executed and documented:

- `docs/phase12-live-smoke.md`
- Discord REST projection smoke: PASS
- Message ID: `1518818346898821270`
- Commit: `903f41ab8541cd7f0d96bc52fd0396eff8674a96`

Current provider capacity:

```text
OpenCode Go: Monthly 96% used, critical
Codex: usable
Decision: avoid heavy opencode-go or Ouroboros loops
```

---

## Phase 12 Scope Decision

Phase 12 should be defined as:

```text
Live Operational Hardening
```

It should not silently become:

```text
Full live e2e production launch
```

### In Scope

- Document Phase 12 as an explicit post-v2 plan.
- Verify and harden Discord bot permission posture.
- Keep bots online through existing Hermes gateway profiles.
- Add or update operational documentation.
- Add deterministic tests where code changes are required.
- Use small live REST checks only where needed.
- Keep all token values hidden.

### Out of Scope

- Large opencode-go live worker execution while Go monthly quota is critical.
- Full Discord app interaction e2e unless separately planned.
- Custom queue/database/gateway replacement.
- Hermes Core modifications.
- Administrator permission for any bot.
- Automatic token reset without explicit execution plan.

---

## Phase 12 Acceptance Criteria

Phase 12 is complete when all accepted tasks below are either PASS or explicitly deferred with reason.

```text
AC-12.0 Plan exists and is committed.
AC-12.1 Discord REST projection live smoke is PASS. [already complete]
AC-12.3 Bot permission hardening plan is documented; permission mutation deferred by decision.
AC-12.4 Token reset decision is documented: do not rotate now; no tracked secrets introduced.
AC-12.5 Assistant UX/channel plan is implemented or documented as manual-only.
AC-12.6 Gateway status is verified after any Discord permission/channel changes.
AC-12.7 Focused tests and secret scans pass for changed files.
```

---

## Recommended Execution Order

### Task 12.0: Commit this Phase 12 plan

**Objective:** Make Phase 12 an explicit canonical plan before doing more live operations.

**Files:**

- Create: `docs/phase12-live-operational-hardening-plan.md`
- Source: `.hermes/plans/2026-06-23_123605-phase12-live-operational-hardening.md`

**Steps:**

1. Review this plan.
2. Promote it into `docs/` so project docs track it.
3. Commit the plan.

**Verification:**

```bash
git status --short
git diff -- docs/phase12-live-operational-hardening-plan.md
```

**Expected:** canonical Phase 12 plan exists in project docs.

---

### Task 12.3: Bot permission hardening inventory

**Objective:** Build an evidence-based permission inventory before changing Discord permissions.

**Files:**

- Read: `docs/discord-multibot-profiles.md`
- Create or update: `docs/phase12-discord-permission-hardening.md`

**Data to collect:**

```text
profile
application_id
current invite permission integer if documented
current role/channel access from Discord REST checks
recommended permission integer
risky permissions present/absent
manual re-invite required yes/no
```

**Recommended permission posture:**

```text
No bot gets Administrator.
Assistant gets lowest permission set.
Team-lead bots get channel/thread/message permissions only where needed.
CEO/Quality may keep Manage Threads only if live workflow proves it needs thread operations.
Manage Guild, Manage Channels, Manage Roles, Manage Webhooks are not allowed by default.
```

**Steps:**

1. Read existing profile mapping.
2. Query Discord `/users/@me` and target home/system channels using each bot token, with Discord-compatible User-Agent.
3. Do not print token values.
4. Produce a markdown inventory.
5. Classify changes into:
   - safe REST-verifiable state
   - manual Discord Developer Portal / OAuth re-invite action
   - deferred

**Verification:**

```bash
python3 <permission-inventory-script>
```

Expected structured output:

```text
7 profiles checked
0 tokens printed
system-log access: 7/7 readable
home-channel access: 7/7 readable
administrator: not granted / not requested in recommended posture
```

**Important:** If actual role permission changes require Discord UI or OAuth re-invite, do not pretend they are complete. Document the exact invite URLs/permission integers but mark execution as manual unless an authenticated API path is confirmed.

---

### Task 12.4: Token reset decision record

**Objective:** Decide whether to rotate exposed Discord tokens and document the choice.

**Files:**

- Create or update: `docs/phase12-token-rotation-decision.md`

**Context:** Some Discord bot tokens were previously pasted in chat. Security best practice is to reset them in Discord Developer Portal and update profile `.env` files.

**Decision options:**

```text
Option A: Rotate now
  Pros: best security posture
  Cons: requires updating 7 Hermes profile .env files and restarting gateways

Option B: Defer temporarily
  Pros: avoids disrupting current smoke/hardening session
  Cons: exposed tokens remain risky
```

**Steps if rotating:**

1. User resets each bot token in Discord Developer Portal.
2. Update only local profile `.env` files:
   - `~/.hermes/profiles/aicompanyceo/.env`
   - `~/.hermes/profiles/aicompanyassistant/.env`
   - `~/.hermes/profiles/aicompanycontent/.env`
   - `~/.hermes/profiles/aicompanyart/.env`
   - `~/.hermes/profiles/aicompanytech/.env`
   - `~/.hermes/profiles/aicompanymarketing/.env`
   - `~/.hermes/profiles/aicompanyquality/.env`
3. Restart gateways with existing scripts.
4. Re-run `/users/@me` token validity checks without printing token values.

**Verification:**

```bash
bash scripts/status_discord_multibot_gateways.sh
python3 <token-validity-check-script>
```

Expected:

```text
7/7 tokens valid
7/7 gateways running
0 token values printed
```

---

### Task 12.5: Personal assistant UX/channel cleanup

**Objective:** Give the assistant bot a clear home without granting server admin permissions.

**Files:**

- Update: `docs/discord-multibot-profiles.md`
- Create or update: `docs/phase12-assistant-ux.md`
- Possibly update profile env: `~/.hermes/profiles/aicompanyassistant/.env`

**Target behavior:**

```text
Assistant is a personal/operations assistant.
Assistant does not administer the Discord server.
Assistant responds only when mentioned or in explicitly allowed private/home channel flow.
Assistant does not free-respond globally.
```

**Steps:**

1. Inspect current assistant home channel.
2. Decide whether to create/use `개인-비서` channel.
3. If channel creation is required, first verify whether a bot has safe permission to create it.
4. Prefer manual channel creation if Discord role permissions are unclear.
5. Update `DISCORD_HOME_CHANNEL` for `aicompanyassistant` only after channel exists.
6. Restart assistant gateway.
7. Verify gateway status and channel access.

**Verification:**

```bash
bash scripts/status_discord_multibot_gateways.sh
python3 <assistant-home-channel-check-script>
```

Expected:

```text
aicompanyassistant running
assistant home_channel points to intended channel
require_mention=true
free_response disabled or narrow-scoped
```

---

### Task 12.2: opencode-go worker live smoke, deferred/minimal

**Objective:** Validate one minimal live worker packet only when quota risk is acceptable.

**Current decision:** Defer by default because OpenCode Go monthly usage is critical at 96%.

**Preconditions:**

```text
OpenCode Go monthly below agreed threshold, or user explicitly accepts risk.
Hourly usage safe.
One minimal packet only.
No Ouroboros loop.
No multi-agent fanout.
```

**Files:**

- Existing: `tests/test_runtime_architecture_v2_opencode_live_smoke.py`
- Existing: `src/runtime_architecture_v2/workers.py`
- Update if needed: `docs/phase12-live-smoke.md`

**Verification:**

```bash
bash scripts/check_all_quota.sh
pytest tests/test_runtime_architecture_v2_opencode_live_smoke.py -q
```

Expected:

```text
No large model loop started
One bounded live smoke result recorded
Structured failure if quota/provider unavailable
```

---

## Global Verification Before Any Commit

For documentation-only tasks:

```bash
git diff --check
python3 - <<'PY'
from pathlib import Path
for p in Path('docs').glob('phase12*.md'):
    print(p, p.exists(), p.stat().st_size)
PY
```

For code changes:

```bash
pytest tests/test_runtime_architecture_v2_*.py -q
pytest tests/test_quota_scripts_no_hardcoded_secrets.py -q
pytest -q
ruff check <changed python files>
```

Secret hygiene:

```bash
python3 <local-high-risk-secret-scan-script>
```

Expected:

```text
0 high-risk secret findings
no token values printed
```

Remote verification after push:

```text
Use GitHub MCP get_commit for the pushed commit.
```

---

## Risks and Guardrails

### Risk: Treating Phase 12 as already planned

Mitigation: This file is the Phase 12 plan. Do not execute 12.3+ until this plan is reviewed or accepted.

### Risk: Discord permission changes are not fully automatable

Mitigation: Separate inventory, recommended integers, and manual re-invite steps. Do not mark as done until verified through Discord REST or user confirmation.

### Risk: Token exposure

Mitigation: Never print tokens. Keep `.env` local. Add secret scans before committing.

### Risk: Quota exhaustion

Mitigation: Avoid heavy opencode-go work. Keep 12.2 deferred/minimal.

### Risk: Overclaiming live readiness

Mitigation: Use boundary labels:

```text
REST projection smoke
Discord-app interaction smoke
Full e2e live
Worker live smoke
```

Only claim the layer actually verified.

---

## Final Recommendation

Proceed in this order:

```text
12.0 Plan approval / commit
12.3 Permission hardening inventory and documentation
12.5 Assistant UX/channel cleanup
12.4 Token rotation decision
12.2 Minimal opencode-go live smoke only after quota improves or explicit approval
```

Do not start Phase 12.3 implementation until this Phase 12 plan is accepted.
