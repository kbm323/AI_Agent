# Phase 31 Live Provider Completion Plan

> **For Hermes:** Execute task-by-task with strict TDD. Report only at stage boundaries with completed stage, verification, and remaining stages.

**Goal:** Finish the final meeting-system live-provider tranche now that OpenCode Go and GPT/Codex usage are available.

**Architecture:** Preserve the Phase 30 deterministic MeetingRun contract and replace only explicit boundaries: role-output provider, validation runner, audit runner, Discord projection adapter, and gateway wiring. Live side effects remain gated behind explicit CLI flags until injected tests and local live-provider smoke pass.

**Tech Stack:** Python dataclasses/protocols, subprocess runner injection, pytest, ruff, existing `runtime_architecture_v2` MeetingRun/store/knowledge modules, opencode-go/OpenCode CLI, GLM validation path, Codex/GPT audit path.

---

## Stage 0 — Baseline and Quota Gate

**Objective:** Confirm Phase 30 deterministic baseline and provider quota before writing live-provider code.

**Commands:**

```bash
bash scripts/check_all_quota.sh 2>&1 || true
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase30_meeting_e2e.py -q
ruff check src/runtime_architecture_v2/meeting_e2e.py scripts/run_phase30_meeting_e2e.py tests/test_runtime_architecture_v2_phase30_meeting_e2e.py
python3 scripts/run_phase30_meeting_e2e.py --root /tmp/ai_agent_phase31_baseline --trigger-text 'Phase 31 baseline dry-run'
```

**Acceptance Criteria:**
- OpenCode Go and Codex/GPT are not blocked.
- Phase 30 targeted tests pass.
- Ruff is clean.
- CLI dry-run has `ok=true`, `opencode_used=false`, `posted_count=15`, `state=completed`.

## Stage 1 — OpenCodeGoRoleOutputProvider Unit Boundary

**Objective:** Add a real-provider boundary without executing opencode-go during unit tests.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Modify: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**TDD Steps:**
1. RED: add a test with an injected subprocess runner returning JSON/text output and assert `OpenCodeGoRoleOutputProvider.generate()` returns sanitized role text.
2. RED: assert runner command includes opencode executable, model, role, round, and trigger context without leaking raw secrets.
3. RED: assert non-zero exit/timeout returns a structured `BLOCKER:` or failed/degraded text rather than fake success.
4. GREEN: implement `OpenCodeGoRoleOutputProvider`, runner protocol, command builder, result sanitizer.
5. Verify targeted pytest and ruff.

**Acceptance Criteria:**
- Unit tests do not call real opencode-go.
- Provider supports injected runner.
- Provider records/returns provenance for later artifact use or exposes a `last_results` structure.
- Failed calls cannot be mistaken for successful fake role output.

## Stage 2 — E2E Provenance and `opencode_used=true`

**Objective:** Thread provider provenance through `run_phase30_meeting_e2e()` artifacts.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Modify: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**TDD Steps:**
1. RED: run E2E with OpenCodeGo provider using injected successful runner.
2. Assert `evidence.json` has `opencode_used=true`.
3. Assert `role_outputs.json` includes per-role/round provenance: provider, model, exit_code, duration, status.
4. Assert `validation_packet.json` reflects live-provider mode.
5. GREEN: add optional provider metadata protocol and artifact serialization.

**Acceptance Criteria:**
- Deterministic injected provider still reports `opencode_used=false`.
- OpenCode provider reports `opencode_used=true` only when real/live provider boundary is selected.
- Artifact schema can distinguish live opencode outputs from deterministic fixtures.

## Stage 3 — CLI `--use-opencode-go` Local Smoke Mode

**Objective:** Add a gated local smoke path that can call opencode-go without Discord mutation.

**Files:**
- Modify: `scripts/run_phase30_meeting_e2e.py`
- Modify: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**TDD Steps:**
1. RED: CLI default remains dry-run and `opencode_used=false`.
2. RED: CLI with `--use-opencode-go --opencode-dry-run-runner-fixture` or injected test runner reports `opencode_used=true` without live process.
3. GREEN: add CLI flags `--use-opencode-go`, `--opencode-model`, `--opencode-timeout-sec`.
4. Manual smoke: after tests pass, run one bounded live opencode-go smoke under `/tmp`.

**Acceptance Criteria:**
- No live opencode call unless `--use-opencode-go` is explicit.
- Local opencode smoke creates the same artifact set as dry-run.
- Smoke output includes degraded roles if any.

**Status update — 2026-06-29 15:46 KST:**
- Implemented CLI flags: `--use-opencode-go`, `--opencode-model`,
  `--opencode-timeout-sec`, `--opencode-runner-fixture-output`.
- Fixture/injected opencode mode passes: `ok=true`, `opencode_used=true`,
  `opencode_result_count=14`, `posted_count=15`, `state=completed`.
- Real live smoke is blocked: a single direct `opencode-go --model glm-5.2
  --context-file ... --timeout-seconds 45 --prompt ... --format json` call did
  not finish within the outer 90s shell timeout. A full 14-call local smoke also
  exceeded the 600s shell timeout before writing Phase 30 artifacts.
- No lingering `opencode-go`/`opencode` process remained after timeout.
- Next session should diagnose the opencode-go shim/model latency before
  continuing to GLM/audit/live Discord. Do not retry the same 14-call smoke until
  a single-call smoke returns successfully.

## Stage 4 — GLM Live Validation Runner

**Objective:** Add live validation boundary after role rounds.

**Files:**
- Create/modify: `src/runtime_architecture_v2/meeting_validation_live.py`
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Create/modify: `tests/test_runtime_architecture_v2_phase31_validation.py`

**Acceptance Criteria:**
- Deterministic validator remains default.
- Live GLM result is persisted and reflected in consensus/evidence/final report.
- GLM failure is degraded/failed, not fake pass.

**Status update — 2026-06-29 16:27 KST:**
- Implemented injected validation boundary: `MeetingValidationRunner` and
  `MeetingValidationResult`.
- `run_phase30_meeting_e2e(..., validation_runner=...)` persists validator
  output into `validation_packet.json` and `evidence.json`.
- Final report includes sanitized validation provider/model/verdict/summary.
- Blocking validation verdicts fail closed as `validation_blocked` and force
  MeetingRun state to `failed` without pretending consensus is production-ready.
- Live GLM process/network call is not yet enabled; current proof is injected
  runner TDD. Real GLM live smoke belongs before or during Stage 5.

## Stage 5 — Codex/GPT Audit Escalation Runner

**Objective:** Add final-audit boundary for high-risk or unresolved meetings.

**Files:**
- Create/modify: `src/runtime_architecture_v2/meeting_audit_live.py`
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Create/modify: `tests/test_runtime_architecture_v2_phase31_audit.py`

**Acceptance Criteria:**
- Audit is only invoked for configured high-risk conditions.
- Audit result is persisted and included in final report.
- Audit failure is explicit; no silent pass.

**Status update — 2026-06-29 16:31 KST:**
- Implemented injected audit boundary: `MeetingAuditRunner` and
  `MeetingAuditResult`.
- Audit runner is invoked only when consensus/validation requires escalation.
- Audit result is persisted into `evidence.json`, exposed on
  `Phase30MeetingE2EResult.audit_result`, and summarized in the final report.
- Audit runner exceptions fail closed into a sanitized `requires_changes` audit
  result. No raw audit output is persisted in public artifacts.
- Live Codex/GPT process call is not yet enabled; current proof is injected
  runner TDD.

## Stage 6 — Live Discord Thread Projection Adapter

**Objective:** Add live Discord adapter behind explicit `--execute-live-discord` gate.

**Files:**
- Create/modify: `src/runtime_architecture_v2/meeting_discord_live.py`
- Modify: `scripts/run_phase30_meeting_e2e.py`
- Tests: injected HTTP tests first.

**Acceptance Criteria:**
- Injected HTTP tests verify thread create and message post payloads.
- Live flag is required for any Discord mutation.
- Message IDs and thread ID are persisted in evidence.

**Status update — 2026-06-29 16:35 KST:**
- Implemented injected Discord REST projection boundary:
  `DiscordRestProjectionAdapter` and `DiscordHttpClient`.
- Adapter sends Discord-compatible `Authorization`, `Content-Type`, and
  `User-Agent` headers.
- Adapter sanitizes content and disables uncontrolled mentions with
  `allowed_mentions: {parse: []}`.
- Non-2xx REST responses fail closed with adapter `last_error` instead of
  pretending projection succeeded.
- Actual live Discord mutation remains gated/deferred; only injected HTTP tests
  were executed in this stage.

## Stage 7 — Hermes Gateway Wiring

**Objective:** Connect real Discord/Hermes mention intake to Phase 30/31 runner.

**Files:**
- Inspect existing Hermes/gateway integration before editing.
- Modify only domain adapter/wiring, not core Hermes.

**Acceptance Criteria:**
- Discord user request creates MeetingRun.
- Resulting thread/final report/evidence links back to request.

**Status update — 2026-06-29 16:52 KST:**
- Gateway intake chain validated: Hermes/Discord-like payload →
  `normalize_phase30_gateway_input()` → `run_phase30_meeting_e2e()`.
- Gateway `session_id`, `user_id`, `channel_id`, `guild_id` preserved in
  `MeetingRun.trigger` and artifacts.
- Chain was already wired in earlier phases; Phase 31F added the explicit test
  proving intake produces a complete meeting E2E result with all 7 artifacts.

## Stage 8 — Supervised Live Smoke Evidence Bundle

**Objective:** Produce final evidence for real provider-enabled meeting system.

**Required Evidence:**
- User request message ID
- MeetingRun ID
- Discord thread ID
- 7 role output provenance records
- 14 role message IDs
- final report message ID
- GLM validation result if enabled
- Codex/GPT audit result if escalated
- `final_report.md`
- `evidence.json`
- Company Second Brain wiki path

**Status update — 2026-06-29 16:52 KST:**
- Second Brain live result integration verified: `write_knowledge=True` with
  opencode/injected provider produces knowledge wiki, raw, and index artifacts.
- Supervised smoke evidence bundle test proves all 7 artifact files exist for a
  full negative (blocked) flow: `evidence.json` carries `opencode_results`,
  `validation_result`, `audit_result`, and final report includes boundary
  summaries (sanitized).
- Live operator-supervised smoke (real opencode + real Discord posting) is
  deferred until the opencode-go single-call timeout blocker is resolved.

## Stage 9 — 24h Unattended Pilot

**Objective:** Validate bounded long-run operation only after supervised smoke passes.

**Acceptance Criteria:**
- Quota checked before/after.
- No silent fake fallback.
- Failures have recovery/evidence.
- Report separates smoke success from production readiness.
