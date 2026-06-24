# Phase 23 Runtime v2 Alignment & Hardening Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Resolve the post-Phase 13~22 plan-compliance issues without shrinking the final AI Virtual Entertainment Company goal: remove/label OpenClaw legacy drift, make Phase 22 fail-closed, clarify 29-role terminology, and prepare live hardening boundaries.

**Architecture:** Keep Runtime Architecture v2 Hermes-first. Treat AI_Agent as the MeetingRun domain runtime and keep Hermes as the platform layer. Do not reintroduce OpenClaw. Do not mutate live Discord permissions/tokens/channels in this phase.

**Tech Stack:** Python runtime modules under `src/runtime_architecture_v2/`, pytest, ruff, markdown docs, existing GitHub workflow.

---

## Decision

Phase 23 is a corrective alignment/hardening phase, not a new feature expansion.

It fixes four issues discovered after Phase 13~22:

1. OpenClaw is legacy and must not be treated as a missing optional delegate.
2. Phase 22 top-level `ok=true` must be fail-closed across integrated subphases.
3. “29 bots” wording must be clarified as “29-role org chart”; the actual Discord-facing topology is `버추얼컴퍼니-Hermes` personal assistant + 6 company team-lead bots.
4. Live production hardening must be documented as the next boundary, not implied complete.

## Acceptance Criteria

### AC1 — Final baseline document and OpenClaw legacy cleanup / labeling

- `docs/system-design-decisions.md` is the current canonical final-system decision baseline unless superseded by a later explicitly named v2 document.
- The canonical baseline must include the latest Discord topology:
  - `버추얼컴퍼니-Hermes` personal assistant/secretary bot.
  - Six company team-lead bots: `대표`, `콘텐츠 팀장`, `아트 팀장`, `기술 팀장`, `마케팅 팀장`, `검증 팀장`.
  - 29-role org chart is internal and does not mean 29 Discord bot accounts.
  - Business Support/legal/finance/HR are internal roles by default, not separate live bots.
- Runtime Architecture v2 docs must state OpenClaw is removed from the current architecture.
- Historical seed/docs may retain OpenClaw only if explicitly labeled legacy/historical.
- Active README/package/script surfaces must not present OpenClaw as a current workflow.
- No active Phase 13~22 plan/result doc should describe OpenClaw as required current functionality.
- If old OpenClaw check scripts remain for historical tests, they must be moved/labeled as legacy or removed only after reference checks pass.

### AC2 — Phase 22 fail-closed integration

- `AutonomousCompany.run()` must record per-subphase status/warnings/errors.
- No broad `except Exception: pass` may silently hide knowledge or command failures.
- `CompanyCycleResult.ok` must be false when any blocking subphase fails.
- Live mode must fail closed when required live dependencies are missing or dispatch results fail.
- Dry-run mode may allow non-fatal warnings, but warnings must be visible in the artifact.
- Add regression tests for live dependency missing, dispatch failure, command exception, and knowledge exception.

### AC3 — 29-role terminology correction

- Replace ambiguous product-facing wording such as `29-Bot Discord Registry` with `29-role Org Chart Registry` where appropriate.
- Rename or supplement `active_bots` with `registered_roles` in Phase 22 outputs, preserving backward compatibility if needed.
- README and docs must clearly state:
  - 29 entries = internal org chart / role registry.
  - 7 Discord-facing bots = `버추얼컴퍼니-Hermes` personal assistant/secretary + 6 company team-lead bots (`대표`, `콘텐츠 팀장`, `아트 팀장`, `기술 팀장`, `마케팅 팀장`, `검증 팀장`).
  - Personal assistant is a user-support/intake layer, not a company department role in the 29-role org chart.
  - Internal workers are dispatched behind team leads, not separate Discord bot accounts.

### AC4 — Live hardening boundary documentation

- Add a live hardening checklist document covering:
  - Discord signature verification and replay protection.
  - Guild/channel allowlist.
  - Slash command registration.
  - No Administrator permission by default.
  - Kanban live client dependency and failure policy.
  - GLM/Codex/opencode-go live validation boundary.
  - Quota/cost monitoring.
  - Service supervision for actual always-on operation.
- README must distinguish:
  - Phase 13~22 planned implementation complete.
  - Deterministic orchestration layer complete.
  - Live production hardening remains.

### AC5 — Verification gate

- Phase 23 targeted tests pass.
- Runtime v2 targeted tests pass.
- Ruff passes.
- Secret/static scan over changed files is clean.
- Independent review returns `security_concerns=[]` and `logic_errors=[]`.
- Commit and push to `origin/main`; verify local/remote HEAD match.

---

## Task 1: Inventory OpenClaw references and classify active vs legacy

**Objective:** Produce a precise list of OpenClaw references that should be removed, relabeled, or preserved as historical evidence.

**Files:**
- Inspect: `README.md`
- Inspect: `package.json`
- Inspect: `scripts/*openclaw*`
- Inspect: `scripts/check-meeting-loop-routing.ts`
- Inspect: `docs/*.md`
- Inspect: `seeds/*.yaml`

**Steps:**

1. Run reference search:

```bash
python3 - <<'PY'
from pathlib import Path
for p in Path('.').rglob('*'):
    if p.is_dir() or '.git' in p.parts or 'node_modules' in p.parts:
        continue
    if p.suffix.lower() not in {'.md','.yaml','.yml','.json','.ts','.py'}:
        continue
    text = p.read_text(encoding='utf-8', errors='ignore')
    if 'OpenClaw' in text or 'openclaw' in text:
        print(p)
PY
```

2. Classify each hit:

```text
ACTIVE_SURFACE: README, package scripts, current docs, current tests
LEGACY_EVIDENCE: old seeds, old historical docs, old generated artifacts
REMOVE_OR_RENAME: active commands/scripts that imply current OpenClaw workflow
PRESERVE_WITH_LABEL: documents needed for audit/history
```

3. Save classification in the Phase 23 result doc later.

**Expected result:** No code changes yet; only an inventory.

---

## Task 2: Clean or label active OpenClaw surfaces

**Objective:** Make current project surfaces unambiguous: OpenClaw is removed/legacy.

**Files:**
- Modify: `README.md`
- Modify: `package.json` if active scripts still expose OpenClaw checks
- Modify or move: `scripts/check-openclaw-hermes-loop.ts` if still active
- Modify: any current docs that imply OpenClaw is current

**Rules:**

- Do not delete historical seed files unless they are clearly unused.
- Prefer labeling legacy docs over destructive deletion.
- If package scripts reference old OpenClaw checks, either:
  - remove the script from active npm commands, or
  - rename it to `check:legacy-openclaw-hermes-loop` and mark legacy.

**Verification:**

```bash
python3 - <<'PY'
from pathlib import Path
active = ['README.md', 'package.json']
for f in active:
    text = Path(f).read_text(encoding='utf-8')
    print(f, 'OpenClaw' in text or 'openclaw' in text)
PY
```

Expected: README may mention OpenClaw only as legacy; package active scripts should not imply current OpenClaw workflow.

---

## Task 3: Add Phase 22 fail-closed regression tests

**Objective:** Lock the correct behavior before changing `autonomous_company.py`.

**Files:**
- Modify: `tests/test_runtime_architecture_v2_phase22_autonomous_company.py`

**Test cases to add:**

1. `test_live_mode_fails_closed_when_dispatch_results_fail`
   - Arrange daemon/dispatch failure via monkeypatch or fake result.
   - Assert `ok is False` and `error` includes sanitized dispatch failure code.

2. `test_knowledge_exception_is_recorded_as_warning_or_error`
   - Monkeypatch `retrieve_knowledge_context` to raise.
   - Assert cycle artifact exposes warning/error and does not silently pass.

3. `test_command_route_exception_is_recorded`
   - Monkeypatch `DiscordCommandRouter.route` to raise for one command.
   - Assert result records command failure count/warning.

4. `test_registered_roles_replaces_or_supplements_active_bots`
   - Assert `registered_roles == 29`.
   - If `active_bots` remains for compatibility, assert it mirrors `registered_roles` and is documented as deprecated/alias.

**Run expected RED:**

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
```

Expected: new tests fail before implementation.

---

## Task 4: Implement Phase 22 fail-closed status model

**Objective:** Make `CompanyCycleResult.ok` reflect integrated runtime success, not just health-scan exceptions.

**Files:**
- Modify: `src/runtime_architecture_v2/autonomous_company.py`

**Implementation shape:**

Add fields to `CompanyCycleResult`:

```python
warnings: tuple[str, ...] = ()
subphase_status: dict[str, str] = field(default_factory=dict)
registered_roles: int = 0
```

Keep backward compatibility if needed:

```python
active_bots: int = 0  # deprecated alias for registered_roles
```

Replace silent passes:

```python
except (OSError, ValueError, RuntimeError) as exc:
    warnings.append('knowledge_context_failed')
    subphase_status['knowledge'] = 'warning'
```

For command route exceptions:

```python
except (OSError, ValueError, RuntimeError) as exc:
    warnings.append(f'command_route_failed:{cmd.name}')
    subphase_status['commands'] = 'warning'
```

For live mode blocking:

```python
blocking_errors = []
if not self.dry_run:
    if daemon_tick.dispatch_results and any(not dr.get('ok') for dr in daemon_tick.dispatch_results):
        blocking_errors.append('dispatch_failed')
    if daemon_tick.created_runs and not daemon_tick.dispatch_results:
        blocking_errors.append('live_dispatch_dependency_missing')
```

Top-level ok:

```python
ok = health.ok and not blocking_errors and not error_parts
```

**Verification:**

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
python3 scripts/run_phase22_company_cycle.py
```

Expected: tests pass; dry-run still returns ok=true with visible warnings only if non-blocking.

---

## Task 5: Correct 29-role terminology in code/docs

**Objective:** Remove ambiguity between org chart roles and actual Discord bot accounts.

**Files:**
- Modify: `src/runtime_architecture_v2/autonomous_company.py`
- Modify: `src/runtime_architecture_v2/bot_registry.py` docstrings if needed
- Modify: `docs/phase20-29-bot-discord-deployment.md`
- Modify: `docs/phase20-29-bot-discord-deployment-plan.md`
- Modify: `docs/phase22-always-on-autonomous-company.md`
- Modify: `README.md`

**Required wording:**

```text
29-role Org Chart Registry
7 Discord-facing bots
22 internal worker roles
Org chart roles are not Discord bot accounts
```

**Compatibility rule:**

If renaming CLI output is breaking, keep `active_bots` temporarily but add `registered_roles` and document `active_bots` as legacy alias.

**Verification:**

```bash
python3 scripts/run_phase20_bot_registry.py
python3 scripts/run_phase22_company_cycle.py
python3 -m pytest tests/test_runtime_architecture_v2_phase20_bot_registry.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
```

---

## Task 6: Add live production hardening checklist

**Objective:** Prevent overclaiming that Phase 13~22 equals full live production deployment.

**Files:**
- Create: `docs/phase23-live-production-hardening-checklist.md`
- Modify: `README.md`

**Checklist sections:**

```text
1. Discord webhook security
2. Discord bot profile/gateway operations
3. Slash command registration
4. Kanban live client dependency
5. Live worker/model validation
6. Quota/cost monitoring
7. Always-on service supervision
8. Rollback/recovery
9. Smoke test order
10. Definition of production-ready
```

**Required README wording:**

```text
Phase 13~22 planned implementation complete.
Runtime Architecture v2 deterministic orchestration layer complete.
Live production hardening remains; see docs/phase23-live-production-hardening-checklist.md.
```

---

## Task 7: Write Phase 23 result document

**Objective:** Record what changed and why.

**Files:**
- Create: `docs/phase23-runtime-v2-alignment-hardening.md`

**Must include:**

```text
Decision
Reasoning
Constraints
Out of Scope
Design Principle
OpenClaw legacy classification
Phase 22 fail-closed changes
29-role terminology changes
Verification results
Remaining live hardening items
```

---

## Task 8: Verification gate

**Objective:** Prove no regression and preserve the established release standard.

**Commands:**

```bash
python3 -m pytest tests/test_runtime_architecture_v2_phase20_bot_registry.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
python3 -m pytest tests/test_runtime_architecture_v2_phase13_pilot.py tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_phase16_kanban_operations.py tests/test_runtime_architecture_v2_phase17_production.py tests/test_runtime_architecture_v2_phase18_dispatch_loop.py tests/test_runtime_architecture_v2_phase19_daemon.py tests/test_runtime_architecture_v2_phase20_bot_registry.py tests/test_runtime_architecture_v2_phase21_discord_webhook.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
python3 -m ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_phase13_pilot.py tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase15_knowledge_loop.py tests/test_runtime_architecture_v2_phase16_kanban_operations.py tests/test_runtime_architecture_v2_phase17_production.py tests/test_runtime_architecture_v2_phase18_dispatch_loop.py tests/test_runtime_architecture_v2_phase19_daemon.py tests/test_runtime_architecture_v2_phase20_bot_registry.py tests/test_runtime_architecture_v2_phase21_discord_webhook.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py
```

Then run full suite if time/quota allows:

```bash
python3 -m pytest tests/
```

Run independent review with fail-closed rule:

```text
security_concerns must be []
logic_errors must be []
```

---

## Task 9: Commit / push / remote verification

**Objective:** Land the alignment phase cleanly.

**Commands:**

```bash
git status --short
git add README.md docs/ src/runtime_architecture_v2/autonomous_company.py src/runtime_architecture_v2/bot_registry.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py package.json scripts/
git commit -m "fix: align Runtime v2 terminology and fail-closed company cycle"
git push origin main
git rev-parse @
git rev-parse @{u}
```

Expected: local and upstream SHAs match.

---

## Out of Scope

- No Discord permission mutation.
- No token reset.
- No live channel/thread changes.
- No new Discord bot accounts.
- No OpenClaw reintroduction.
- No migration to Obsidian.
- No custom queue database unless a verified Hermes gap appears.

## Design Principle

```text
Runtime Architecture v2 remains Hermes-first.
OpenClaw is legacy, not a missing dependency.
29 roles are company org-chart roles, not 29 Discord bots.
Phase 22 must fail closed before any live production claim.
Live external boundaries are hardened after deterministic orchestration is correct.
```
