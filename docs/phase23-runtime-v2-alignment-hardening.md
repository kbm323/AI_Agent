# Phase 23 Runtime v2 Alignment & Hardening Result

> Status: IN PROGRESS
> Started: 2026-06-25 KST
> Canonical baseline: `docs/runtime-architecture-v2.md`

## Decision

Phase 23 is a corrective alignment/hardening phase. It does not expand the AI Virtual Entertainment Company surface. It aligns active artifacts with Runtime Architecture v2 and hardens Phase 22 before any live-production claim.

## Task 1 — OpenClaw reference inventory

### Summary

OpenClaw references still exist in the repository, but they belong to distinct classes. Phase 23 must avoid destructive deletion of historical evidence while ensuring current active surfaces do not present OpenClaw as part of Runtime Architecture v2.

### Classification rules

```text
ACTIVE_SURFACE: current README/package scripts/current source/current tests that users or CI can execute.
LEGACY_EVIDENCE: old seeds, generated artifacts, old MVP/diagnosis docs, historical implementation remnants.
PRESERVE_WITH_LABEL: current Runtime v2 docs/plans that explicitly say OpenClaw is removed or legacy.
REMOVE_OR_RENAME: active command/script names that imply OpenClaw is a current workflow.
```

### Key findings

#### PRESERVE_WITH_LABEL

These are correct because they explicitly say OpenClaw is removed/legacy:

```text
docs/runtime-architecture-v2.md
docs/system-design-decisions.md
docs/phase23-runtime-v2-alignment-hardening-plan.md
```

#### ACTIVE_SURFACE — must clean or label

```text
README.md
package.json
scripts/check-openclaw-hermes-loop.ts
```

Findings:

```text
README.md
  - Mentions OpenClaw only as old MVP/legacy. Acceptable if wording remains explicitly historical.

package.json
  - Active script `check:openclaw-hermes-loop` still exposes OpenClaw as a current command.
  - Required action: rename to `check:legacy-openclaw-hermes-loop` or remove from active scripts.

scripts/check-openclaw-hermes-loop.ts
  - Script name and command contract present OpenClaw/Hermes loop as active.
  - Required action: label script as legacy and align command string if preserved.
```

#### LEGACY_EVIDENCE — preserve unless separately archived

```text
seeds/*.yaml
seeds/slim/packet_04_openclaw_controls.yaml
docs/refactoring-plan.md
docs/diagnosis-report.md
docs/token-reduction-strategy.md
docs/loop-context-compression-policy.md
docs/generated/*.json
```

These document earlier OpenClaw-era MVP/seed behavior and should not be silently deleted. They can remain if labelled as historical/legacy where human-facing.

#### Source/test remnants outside Runtime v2

The repository still contains older TypeScript/Python MVP modules and tests using OpenClaw names, for example:

```text
src/orchestrator.ts
src/final-synthesis.ts
src/meeting-transcript.ts
src/runtime_smoke_packet.py
src/runtime_smoke_cli.py
src/openclaw_approval.py
src/openclaw_execution_mode.py
src/openclaw_intervention.py
tests/*.test.ts
tests/test_openclaw_*.py
```

Phase 23 will not mass-delete these because they are part of older MVP/test history and broader non-Runtime-v2 surfaces. The immediate Phase 23 requirement is to prevent active README/package/script surfaces from advertising OpenClaw as the current Runtime v2 path.

### Task 1 verdict

```text
PASS: inventory complete enough for Phase 23 scope.
NEXT: Task 2 should rename/label package script and legacy check script, while preserving historical docs/seeds.
```

## Task 2 — Clean or label active OpenClaw surfaces

### Changes

```text
package.json
  - Renamed active script:
    check:openclaw-hermes-loop -> check:legacy-openclaw-hermes-loop

scripts/check-openclaw-hermes-loop.ts
  - Added LEGACY CHECK ONLY header.
  - Explicitly states OpenClaw is not part of current Runtime Architecture v2.
  - Updated reported command string to `ai-agent check:legacy-openclaw-hermes-loop`.
```

### Task 2 verdict

```text
PASS: active package/script surface no longer advertises OpenClaw as a current workflow.
PRESERVED: historical legacy check remains available for audit/regression.
```

## Task 3 — Phase 22 fail-closed RED tests

### Added regression tests

```text
tests/test_runtime_architecture_v2_phase22_autonomous_company.py
  - test_registered_roles_replaces_or_supplements_active_bots
  - test_live_mode_fails_closed_when_dispatch_results_fail
  - test_knowledge_exception_is_recorded_as_warning
  - test_command_route_exception_is_recorded
```

### RED result

```text
python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
→ 7 passed, 4 failed
```

Expected failures proved missing behavior:

```text
CompanyCycleResult.registered_roles missing
CompanyCycleResult.subphase_status missing
knowledge exception silently swallowed
command route exception silently swallowed
live dispatch failure did not fail closed
```

## Task 4 — Phase 22 fail-closed status model

### Changes

```text
src/runtime_architecture_v2/autonomous_company.py
  - Added CompanyCycleResult.warnings
  - Added CompanyCycleResult.subphase_status
  - Added CompanyCycleResult.registered_roles
  - Kept active_bots as backward-compatible alias for registered_roles
  - Sanitizes warnings/subphase_status/error through _sanitize_text
  - Replaced silent knowledge/command exception pass with visible warnings
  - Records health/daemon/daemon_dispatch/knowledge/commands subphase status
  - Live daemon dispatch failure sets ok=False with daemon_dispatch_failed
```

## Task 5 — 29-role terminology correction

### Changes

```text
CompanyCycleResult.registered_roles = len(DEFAULT_REGISTRY.profiles)
CompanyCycleResult.active_bots = registered_roles  # backward-compatible alias
cycle artifact includes both registered_roles and active_bots
```

### GREEN result

```text
python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
→ 11 passed
```

### Independent review fix

Independent review found one remaining live fail-open case: live mode with `created_runs > 0` and empty `dispatch_results` was still reported as `ok=True`. The fix added two more RED/GREEN regressions:

```text
- test_live_mode_fails_closed_when_dispatch_results_missing
- test_result_alias_normalizes_active_bots_to_registered_roles
```

Final targeted result after review fix:

```text
python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
→ 13 passed
```

## Task 6 — Live hardening boundary documentation

### Added

```text
docs/phase23-live-production-hardening-checklist.md
```

Checklist covers:

```text
- Discord interaction security: signature verification, replay protection, raw body handling
- Guild/channel allowlist
- Discord permission inventory with no Administrator by default
- Slash command registration and deferred response completion
- Kanban live client dependency and fail-closed policy
- opencode-go/GLM/Codex worker-validator-auditor live boundary
- quota/cost monitoring
- service supervision
- projection safety
```

### README status wording

README now distinguishes:

```text
Phase 13~22 planned implementation complete
Runtime v2 deterministic orchestration layer complete
Phase 23 fail-closed alignment/hardening in progress
Live production hardening remains
```

## Verification gate

### Automated checks

```text
Phase 22 targeted:
  python3 -m pytest tests/test_runtime_architecture_v2_phase22_autonomous_company.py -q
  → 13 passed

Runtime v2 targeted:
  python3 -m pytest tests/test_runtime_architecture_v2_*.py -q
  → 231 passed

Full test suite:
  python3 -m pytest tests/ -q
  → 5516 passed

Ruff:
  python3 -m ruff check src/runtime_architecture_v2/autonomous_company.py tests/test_runtime_architecture_v2_phase22_autonomous_company.py
  → All checks passed

TypeScript syntax:
  node --check scripts/check-openclaw-hermes-loop.ts
  → passed

Static checks:
  - `check:openclaw-hermes-loop` removed from package scripts
  - `check:legacy-openclaw-hermes-loop` present
  - assignment-style secret scan over changed files passed
```

### Independent review

```text
security_concerns: []
logic_errors: []
verdict: PASS
```

Important note from reviewer:

```text
Minor cosmetic: subphase_status['daemon'] key is absent when daemon_dispatch='failed'.
This does not affect ok semantics or operational correctness.
```

## Phase 23 verdict

```text
PASS: Runtime v2 remains Hermes-first.
PASS: OpenClaw active package/script surface is legacy-labeled.
PASS: Phase 22 top-level ok is fail-closed for live dispatch failures and missing dispatch dependency.
PASS: registered_roles is now explicit; active_bots remains a backward-compatible alias.
PASS: live production hardening boundary is documented, not implied complete.
```
