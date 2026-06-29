# Phase 30 GPT-Only Meeting E2E Implementation Plan

> **For Hermes:** Implement this plan with strict TDD. opencode-go is unavailable, so this phase builds the deterministic/orchestration layer first and leaves live worker providers as replaceable adapters.

**Goal:** Build a complete, testable MeetingRun E2E skeleton for the final Discord meeting system without requiring opencode-go usage.

**Architecture:** Add a deterministic Phase 30 meeting orchestrator that accepts a Discord/Hermes-style trigger, creates a MeetingRun, plans 7 Discord-facing roles, runs opinion/rebuttal rounds through an injected role-output provider, derives consensus/validation packets, writes final report/evidence/recovery artifacts, optionally projects into an injected Discord thread adapter, and writes Company Second Brain notes through the existing knowledge layer. Real opencode-go/GLM/Codex providers will later replace injected providers at the boundaries.

**Tech Stack:** Python dataclasses, existing `runtime_architecture_v2` schemas/store/projection/knowledge modules, pytest, ruff.

---

## Phase 30A — MeetingRun E2E Skeleton

**Objective:** Create a pure Python deterministic orchestrator that drives one request through all meeting stages without live worker calls.

**Files:**
- Create: `src/runtime_architecture_v2/meeting_e2e.py`
- Create: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: add a test that calls `run_phase30_meeting_e2e(root=tmp_path, trigger_text='...', role_output_provider=InjectedRoleOutputProvider(...), projection_adapter=FakeMeetingThreadProjectionAdapter())`.
2. Expected RED: import/module missing.
3. GREEN: implement minimal `Phase30MeetingE2EResult`, `run_phase30_meeting_e2e`, and artifact writing.
4. Verify: targeted pytest passes.

**Acceptance Criteria:**
- Result has `ok=True`.
- Result has `meeting_run_id`.
- MeetingRun reaches completed state.
- Stage sequence includes intake, thread, opinions, rebuttals, consensus, final_report, evidence, recovery.

## Phase 30B — 7-Bot Meeting Topology Policy

**Objective:** Represent final 7 Discord-facing roles without requiring seven live model calls.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: assert the default phase 30 plan includes exactly 7 roles: assistant_secretary, ceo_coordinator, content_lead, art_lead, tech_lead, marketing_lead, quality_lead.
2. RED: assert each role has an opinion entry and a rebuttal entry.
3. GREEN: implement `phase30_default_roles()` and meeting round generation.

**Acceptance Criteria:**
- 7 roles are represented in result/artifacts.
- Assistant role is explicit as secretary/intake/final-report assistant, not a hidden fake.

## Phase 30C — Consensus / Validation Packet Structure

**Objective:** Replace simple consensus optimism with a deterministic packet that can later be sent to GLM/Codex.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: inject one role output containing `BLOCKER:` and assert `consensus_reached=False`, `escalation_required=True`.
2. RED: inject all aligned role outputs and assert `consensus_reached=True`.
3. GREEN: implement `ConsensusPacket`, `ValidationPacket`, `derive_consensus_packet`.

**Acceptance Criteria:**
- Blockers and conflicts are recorded.
- Escalation flag is deterministic and explainable.
- Consensus packet is persisted.

## Phase 30D — Final Report + Evidence Writer

**Objective:** Persist final report, evidence, and recovery checkpoint per MeetingRun.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: assert files exist under `runtime/meeting_runs/<meeting_run_id>/phase30/`.
2. Expected files: `rounds.json`, `role_outputs.json`, `validation_packet.json`, `consensus.json`, `final_report.md`, `evidence.json`, `recovery_checkpoint.json`.
3. GREEN: write stable JSON/Markdown artifacts with sanitized content.

**Acceptance Criteria:**
- Artifacts contain meeting_run_id and stage sequence.
- Evidence includes thread ID and projected message IDs when present.

## Phase 30E — Company Second Brain Writer Decoupling

**Objective:** Allow knowledge writing from arbitrary completed Phase 30 sessions, not only Phase 14 dry-run.

**Files:**
- Modify: `src/runtime_architecture_v2/knowledge.py` only if the existing `write_meeting_knowledge` cannot accept Phase 30 session shape.
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py` or `tests/test_runtime_architecture_v2_phase15_knowledge_loop.py`

**Steps:**
1. RED: assert Phase30 E2E with `write_knowledge=True` creates raw/wiki/index/log entries.
2. GREEN: adapt Phase30 result to the existing `write_meeting_knowledge` contract or add a small compatible session dataclass.

**Acceptance Criteria:**
- No opencode dependency.
- Knowledge output is sanitized and searchable by meeting topic.

## Phase 30F — Injected Discord Thread Projection Adapter

**Objective:** Test complete thread/message projection without live Discord mutation.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: assert fake adapter creates one thread, posts 14 role messages plus final report, and returns IDs.
2. GREEN: implement `FakeMeetingThreadProjectionAdapter` and adapter protocol.

**Acceptance Criteria:**
- Thread creation is represented once.
- Each role output is projected in the shared thread.
- Final report projection is represented.

## Phase 30G — Gateway Intake Contract

**Objective:** Define pure payload normalization from Hermes/Discord-like message into Phase30 trigger input.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: pass a payload fixture with user/channel/thread/profile fields and assert normalized trigger.
2. GREEN: implement `normalize_phase30_gateway_input`.

**Acceptance Criteria:**
- Mention/channel/profile validation data is carried into evidence.

## Phase 30H — Recovery / Resume Inspector

**Objective:** Provide status inspection from artifacts.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: run E2E, then call `inspect_phase30_meeting(root, meeting_run_id)` and assert status/stages/artifact paths.
2. GREEN: implement inspector.

**Acceptance Criteria:**
- Inspector does not require live services.
- Missing files are reported explicitly.

## Phase 30I — Dry-run CLI

**Objective:** Add an operator script to run Phase30 without opencode-go.

**Files:**
- Create: `scripts/run_phase30_meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`

**Steps:**
1. RED: subprocess the CLI with `--root tmp --trigger-text ... --dry-run` and parse JSON.
2. GREEN: implement CLI around `run_phase30_meeting_e2e`.

**Acceptance Criteria:**
- CLI returns JSON with `ok`, `meeting_run_id`, `thread_id`, `posted_count`, artifact paths.
- Default mode is dry-run/injected only.

## Deferred Until opencode-go Usage Returns

Updated: 2026-06-29 01:05 KST

This section is the durable handoff list for a future fresh-token session. The
current implementation intentionally stops at `opencode_used=false` deterministic
E2E. When opencode-go usage/quota resets, continue from here rather than
re-auditing the whole project.

### Current Baseline to Re-verify First

**Files already created:**
- `src/runtime_architecture_v2/meeting_e2e.py`
- `scripts/run_phase30_meeting_e2e.py`
- `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py`
- `docs/plans/2026-06-28-phase30-gpt-only-meeting-e2e.md`

**Baseline command:**

```bash
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py -q
ruff check \
  src/runtime_architecture_v2/meeting_e2e.py \
  scripts/run_phase30_meeting_e2e.py \
  tests/test_runtime_architecture_v2_phase30_meeting_e2e.py
python3 scripts/run_phase30_meeting_e2e.py \
  --root /tmp/ai_agent_phase30_baseline \
  --trigger-text 'baseline Phase 30 dry-run'
```

**Expected baseline:**
- pytest: `5 passed`
- ruff: `No issues found`
- CLI JSON has `ok=true`, `opencode_used=false`, `posted_count=15`,
  `state=completed`

### Phase 31A — OpenCodeGo Role Output Provider

**Objective:** Replace deterministic injected role output with real opencode-go
worker calls while preserving the Phase 30 E2E contract.

**Files:**
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Possibly use existing helpers in: `src/runtime_architecture_v2/workers.py`
- Test: `tests/test_runtime_architecture_v2_phase30_meeting_e2e.py` or create
  `tests/test_runtime_architecture_v2_phase31_opencode_provider.py`

**Implementation notes:**
- Add `OpenCodeGoRoleOutputProvider` implementing the existing
  `RoleOutputProvider` protocol.
- Generate a role-specific prompt/packet for each of the seven roles.
- Capture stdout, stderr, exit code, timeout flag, duration, runner name, and
  model/provider metadata.
- Do **not** fake-success on failure. If opencode-go fails, mark that role
  output as failed/degraded and let the meeting result fail or require
  escalation.
- Set `opencode_used=true` only when real worker calls were attempted.

**Acceptance Criteria:**
- Seven roles can run opinion and rebuttal rounds through opencode-go.
- `role_outputs.json` records provenance for each role output.
- `evidence.json` records `opencode_used=true` and per-role runner status.
- Failed worker calls are visible as degraded/failed, never silent fake output.

### Phase 31B — Actual 7-Role Deliberation Smoke

**Objective:** Prove that all seven Discord-facing roles produce real model
outputs for one supervised meeting run.

**Files:**
- Modify: `scripts/run_phase30_meeting_e2e.py`
- Test: add a marked/integration test only if it can be safely skipped without
  quota, e.g. `pytest.mark.integration`.

**Implementation notes:**
- Add explicit CLI option such as `--use-opencode-go`; default must remain
  dry-run/injected.
- Keep `--root` required/recommended for live smoke artifact isolation.
- Print a final JSON summary with meeting_run_id, state, posted_count,
  opencode_used, degraded roles, and artifact paths.

**Acceptance Criteria:**
- One supervised local run completes with all seven roles attempted.
- The run creates the same Phase 30 artifact set as dry-run.
- `final_report.md` is based on real role outputs.

### Phase 31C — GLM Live Validation Runner

**Objective:** Replace deterministic-only validation with a live GLM validation
step that consumes the Phase 30 consensus/validation packet.

**Files:**
- Create or modify: `src/runtime_architecture_v2/meeting_validation_live.py`
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase31_validation.py`

**Implementation notes:**
- Keep deterministic validator as fallback/test mode.
- Add a boundary/protocol for validation runner so live GLM can be injected.
- Persist raw validator packet and sanitized validator result.
- Treat contradiction/risk/blocker findings as escalation triggers.

**Acceptance Criteria:**
- `validation_packet.json` includes live validator metadata when enabled.
- GLM result is reflected in `consensus.json`, `evidence.json`, and
  `final_report.md`.
- Live validator failure does not become a fake pass.

### Phase 31D — Codex/GPT Final Audit Escalation

**Objective:** Add high-risk/final-audit escalation after consensus and GLM
validation.

**Files:**
- Create or modify: `src/runtime_architecture_v2/meeting_audit_live.py`
- Modify: `src/runtime_architecture_v2/meeting_e2e.py`
- Test: `tests/test_runtime_architecture_v2_phase31_audit.py`

**Implementation notes:**
- Trigger on high-risk criteria: unresolved blocker, security/legal/finance
  terms, external publication, failed GLM confidence, or quality_lead blocker.
- Persist audit request/result separately from normal role outputs.
- Record `audit_escalated=true/false` in evidence.

**Acceptance Criteria:**
- Escalation-required meetings call the audit runner when enabled.
- Final report includes audit verdict and required corrections.
- Audit failure is explicit degraded/failed state, not hidden.

### Phase 31E — Live Discord Thread Projection Adapter

**Objective:** Replace `FakeMeetingThreadProjectionAdapter` with a live Discord
thread/message adapter for supervised smoke only.

**Files:**
- Modify or create: `src/runtime_architecture_v2/meeting_discord_live.py`
- Modify: `scripts/run_phase30_meeting_e2e.py`
- Test: injected HTTP/unit tests plus manual supervised smoke record.

**Implementation notes:**
- Do not mutate live Discord by default.
- Require an explicit flag such as `--execute-live-discord`.
- Use injected HTTP/client tests first; only then run a supervised live smoke.
- Store real thread ID and message IDs in `evidence.json`.

**Acceptance Criteria:**
- One shared meeting thread is created.
- 14 role messages plus final report are posted or explicitly skipped with
  reason.
- Evidence records actual Discord IDs.

### Phase 31F — Hermes Gateway → Phase 30 Runner Wiring

**Objective:** Connect real Discord/Hermes mention intake to the Phase 30 E2E
runner.

**Files:**
- Inspect existing gateway hooks/adapters before editing.
- Likely modify existing Hermes/Discord command surface adapter, not core Hermes.
- Test with payload fixtures and one supervised Discord mention.

**Implementation notes:**
- Hermes-native mention/natural-language intake remains preferred.
- Standalone slash command is optional, not core.
- Preserve mention gating, channel allowlist, and safety boundaries.

**Acceptance Criteria:**
- User Discord request creates a MeetingRun.
- MeetingRun creates/uses a thread projection path.
- Resulting final report and evidence are linked back to the request.

### Phase 31G — Company Second Brain Live Result Verification

**Objective:** Prove that live/opencode-backed MeetingRun output is written to
the company knowledge base, not just dry-run output.

**Files:**
- `src/runtime_architecture_v2/knowledge.py`
- Phase 30/31 tests as needed.

**Acceptance Criteria:**
- Live/opencode-backed run writes raw + wiki + index + log entries.
- Knowledge entry contains meeting topic, final decision, roles, validation, and
  artifact links.
- Secrets and Discord mass mentions are sanitized.

### Phase 31H — Supervised Live Smoke Evidence Record

**Objective:** Produce the first end-to-end evidence bundle for the final meeting
system with real providers enabled.

**Evidence required:**
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

**Acceptance Criteria:**
- Evidence bundle is committed or archived under an operations/evidence path.
- Report states exactly which providers were live and which were injected.

### Phase 31I — 24h / Unattended Pilot

**Objective:** Validate long-running operations after the supervised smoke passes.

**Do not start this before:**
- opencode-go quota is available and monitored.
- Discord live projection smoke passes.
- Gateway stability is confirmed.
- Recovery/checkpoint behavior is proven.

**Acceptance Criteria:**
- 24h runbook executed.
- No silent fake fallback.
- All failures have evidence and recovery path.
- Final status report distinguishes production readiness from smoke success.

### Quick Next-Session Prompt

Use this exact prompt after opencode-go usage resets:

> Continue AI_Agent Phase 31 from
> `docs/plans/2026-06-28-phase30-gpt-only-meeting-e2e.md`, section
> “Deferred Until opencode-go Usage Returns”. First run the baseline commands,
> then implement Phase 31A OpenCodeGoRoleOutputProvider with TDD. Do not run live
> Discord mutation until injected tests and local opencode smoke pass.

Search anchor: Do not run live Discord mutation until injected tests and local opencode smoke pass.
