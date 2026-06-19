# Runtime Architecture v2 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Implement the Hermes-first AI Virtual Entertainment Company runtime architecture around `MeetingRun`, Discord team-lead bot projections, opencode-go worker packets, dual validation, recovery, and simulation tests.

**Architecture:** Keep Hermes Core untouched. Add project-local schema, state, queue, packet, adapter, and simulation modules. Discord is a projection layer; `MeetingRun` storage is the source of truth.

**Tech Stack:** Python, pytest, JSON/YAML packets, SQLite queue/state, Discord adapter wrappers, opencode-go CLI wrapper, optional OpenClaw adapter.

---

## Phase 0: Guardrails

### Task 0.1: Preserve the Seed and Architecture Docs

**Objective:** Ensure the generated Seed and Runtime Architecture v2 document are committed as canonical design inputs.

**Files:**
- Existing: `seeds/seed_runtime_architecture_v2.yaml`
- Existing: `docs/runtime-architecture-v2.md`

**Steps:**
1. Validate YAML:
   ```bash
   python - <<'PY'
   import yaml
   yaml.safe_load(open('seeds/seed_runtime_architecture_v2.yaml'))
   print('ok')
   PY
   ```
2. Review doc path exists:
   ```bash
   test -f docs/runtime-architecture-v2.md
   ```
3. Commit when ready:
   ```bash
   git add seeds/seed_runtime_architecture_v2.yaml docs/runtime-architecture-v2.md
   git commit -m "docs: add runtime architecture v2 seed and design"
   ```

---

## Phase 1: Schema Layer

### Task 1.1: Create MeetingRun schema module

**Objective:** Define typed dataclasses or Pydantic-style plain validation for `MeetingRun` top-level state.

**Files:**
- Create: `src/runtime_architecture_v2/schemas.py`
- Test: `tests/test_runtime_architecture_v2_schemas.py`

**Required entities:**
- `MeetingRun`
- `TriggerRequest`
- `RoutingResult`
- `MeetingPhase`
- `WorkerTask`
- `ValidationCycle`
- `ReportPhase`
- `DiscordProjectionEvent`
- `RecoveryCheckpoint`

**Verification:**
```bash
pytest tests/test_runtime_architecture_v2_schemas.py -v
```

### Task 1.2: Enforce allowed top-level states

**Objective:** Reject invalid `MeetingRun.top_level_state` values.

**Allowed states:**
```text
created, classified, routed, queued, active, validating, reporting, completed, failed, cancelled, paused
```

**Test cases:**
- valid state accepted
- invalid state raises `ValueError`
- terminal states identified correctly

### Task 1.3: Add JSON packet serialization

**Objective:** Support deterministic JSON read/write for all packet schemas.

**Files:**
- Modify: `src/runtime_architecture_v2/schemas.py`
- Test: `tests/test_runtime_architecture_v2_packet_io.py`

**Verification:**
- round-trip JSON preserves `meeting_run_id`
- unknown state fails fast
- missing required fields fail fast

---

## Phase 2: Storage and State

### Task 2.1: Create MeetingRun file store

**Objective:** Store each MeetingRun under `runtime/meeting_runs/<meeting_run_id>/`.

**Files:**
- Create: `src/runtime_architecture_v2/store.py`
- Test: `tests/test_runtime_architecture_v2_store.py`

**Expected layout:**
```text
runtime/meeting_runs/mr_*/
  meeting_run.json
  packets/
  worker_outputs/
  validation/
  discord_projection/
  checkpoints/
  final_report.md
```

### Task 2.2: Add recovery checkpoint read/write

**Objective:** Persist and load `RecoveryCheckpoint` idempotently.

**Verification:**
- latest checkpoint can be loaded
- checkpoint update is atomic enough for local operation
- missing checkpoint returns a safe default

### Task 2.3: Add decision/audit log appenders

**Objective:** Append JSONL events with `meeting_run_id`.

**Files:**
- Modify: `src/runtime_architecture_v2/store.py`
- Test: `tests/test_runtime_architecture_v2_logs.py`

---

## Phase 3: Routing and Queue

### Task 3.1: Define routing adapter interface

**Objective:** Add an interface for Qwen router integration with a fake implementation for tests.

**Files:**
- Create: `src/runtime_architecture_v2/routing.py`
- Test: `tests/test_runtime_architecture_v2_routing.py`

**Initial fake routes:**
- fast Q&A
- creative meeting
- technical execution
- legal/risk
- mixed request

### Task 3.2: Create priority queue policy

**Objective:** Implement queue priority calculation and bounded concurrency policy.

**Files:**
- Create: `src/runtime_architecture_v2/queue_policy.py`
- Test: `tests/test_runtime_architecture_v2_queue_policy.py`

**Rules:**
- urgency influences priority
- critical beats normal
- aging prevents starvation
- Codex/OpenClaw have smaller concurrency limits

### Task 3.3: Add SQLite queue skeleton

**Objective:** Persist queued MeetingRuns with priority and status.

**Files:**
- Create: `src/runtime_architecture_v2/queue_store.py`
- Test: `tests/test_runtime_architecture_v2_queue_store.py`

---

## Phase 4: Worker Execution Layer

### Task 4.1: Define WorkerRunner interface

**Objective:** Create a generic runner interface for opencode-go, Hermes wrapper, and OpenClaw.

**Files:**
- Create: `src/runtime_architecture_v2/workers.py`
- Test: `tests/test_runtime_architecture_v2_workers.py`

**Interface:**
```python
class WorkerRunner:
    def dispatch(self, task: WorkerTask) -> WorkerTask: ...
    def collect(self, task: WorkerTask) -> WorkerTask: ...
```

### Task 4.2: Add FakeWorkerRunner

**Objective:** Enable simulation without external CLI or Discord.

**Verification:**
- fake worker writes output file
- completed state recorded
- failure and timeout can be simulated

### Task 4.3: Add opencode-go packet wrapper skeleton

**Objective:** Build command and packet layout without executing destructive actions.

**Files:**
- Modify: `src/runtime_architecture_v2/workers.py`
- Test: `tests/test_runtime_architecture_v2_opencode_wrapper.py`

**Constraint:**
- Do not require live opencode-go in unit tests.
- Use dry-run command construction tests first.

---

## Phase 5: Validation Layer

### Task 5.1: Define validation verdict schema and policy

**Objective:** Implement `pass`, `conditional_pass`, `revise`, `reject`, `escalate` verdict logic.

**Files:**
- Create: `src/runtime_architecture_v2/validation.py`
- Test: `tests/test_runtime_architecture_v2_validation.py`

### Task 5.2: Add GLM/Codex validator interfaces

**Objective:** Separate GLM risk review from Codex audit.

**Rules:**
- GLM default for contradiction/risk/legal/business concerns
- Codex only for code, critical, or final approval routes
- unavailable validator produces explicit degraded verdict, not silent pass

### Task 5.3: Implement correction loop decision

**Objective:** Convert blocking validation issues into a correction action.

**Verification:**
- `revise` creates follow-up worker/meeting action
- `reject` stops and reports
- `escalate` asks user

---

## Phase 6: Discord Projection Layer

### Task 6.1: Define bot topology config

**Objective:** Encode seven team-lead bots and their responsibilities.

**Files:**
- Create: `config/bot_topology.yaml`
- Create: `src/runtime_architecture_v2/discord_projection.py`
- Test: `tests/test_runtime_architecture_v2_discord_projection.py`

**Bots:**
- CEO/Coordinator
- Content Lead
- Art Lead
- Tech Lead
- Marketing Lead
- Business Support Lead
- Validation/Audit

### Task 6.2: Add projection event formatter

**Objective:** Convert internal events into Discord-safe messages.

**Rules:**
- no secrets
- no raw full worker dumps by default
- final report and validation verdict are visibly separated
- internal worker output can be summarized under team-lead persona

### Task 6.3: Add fake Discord projection sink

**Objective:** Test Discord UX without live Discord.

**Verification:**
- fake sink records intended bot persona and channel/thread target
- final report includes validation block
- failure alert targets master-control projection

---

## Phase 7: Orchestrator and Runtime Flows

### Task 7.1: Create RuntimeOrchestrator skeleton

**Objective:** Drive MeetingRun through top-level states with fake adapters.

**Files:**
- Create: `src/runtime_architecture_v2/orchestrator.py`
- Test: `tests/test_runtime_architecture_v2_orchestrator.py`

### Task 7.2: Implement fast Q&A flow

**Objective:** Verify request can complete without meeting rounds.

**Expected sequence:**
```text
created -> classified -> routed -> active -> validating? -> reporting -> completed
```

### Task 7.3: Implement meeting request flow

**Objective:** Simulate agenda, participant selection, round 1, round 2, consensus, validation, report.

### Task 7.4: Implement worker execution flow

**Objective:** Simulate WorkerTask dispatch/output/validation/report.

### Task 7.5: Implement validation failure correction loop

**Objective:** Simulate `revise` verdict and re-run of relevant worker/meeting phase.

### Task 7.6: Implement crash recovery resume

**Objective:** Load checkpoint and resume the next idempotent action.

### Task 7.7: Implement timeout/worker failure flow

**Objective:** Mark failed/timed_out, retry if allowed, otherwise report failure.

---

## Phase 8: Operations Policies

### Task 8.1: Add security level policy

**Objective:** Encode L0-L5 action levels and approval requirements.

**Files:**
- Create: `src/runtime_architecture_v2/security_policy.py`
- Test: `tests/test_runtime_architecture_v2_security_policy.py`

### Task 8.2: Add quota/model policy

**Objective:** Encode Qwen/GLM/Codex/opencode-go/OpenClaw use rules and fallback behavior.

**Files:**
- Create: `src/runtime_architecture_v2/model_policy.py`
- Test: `tests/test_runtime_architecture_v2_model_policy.py`

### Task 8.3: Add observability event definitions

**Objective:** Standardize audit events and metrics names.

**Files:**
- Create: `src/runtime_architecture_v2/observability.py`
- Test: `tests/test_runtime_architecture_v2_observability.py`

---

## Phase 9: End-to-End Simulation

### Task 9.1: Add simulation CLI

**Objective:** Run MeetingRun scenarios without Discord or external models.

**Files:**
- Create: `scripts/simulate_runtime_architecture_v2.py`
- Test: `tests/test_runtime_architecture_v2_simulation_cli.py`

**Example:**
```bash
python scripts/simulate_runtime_architecture_v2.py --scenario fast_qa
python scripts/simulate_runtime_architecture_v2.py --scenario meeting
python scripts/simulate_runtime_architecture_v2.py --scenario worker_failure
```

### Task 9.2: Add smoke test for required scenarios

**Objective:** Verify all acceptance scenarios run with fake adapters.

**Scenarios:**
- fast Q&A
- meeting request
- worker execution
- dual validation pass
- validation correction loop
- crash recovery
- timeout/worker failure

---

## Phase 10: Live Adapter Wiring

### Task 10.1: Wire live Discord adapter behind projection interface

**Objective:** Keep fake sink tests while adding live Discord support.

**Constraint:**
- Do not put tokens in docs or code.
- Environment variables only.

### Task 10.2: Wire live opencode-go runner behind WorkerRunner

**Objective:** Execute packet-based worker calls through opencode-go CLI.

**Constraint:**
- Start with dry-run and command construction.
- Then one controlled smoke packet.

### Task 10.3: Wire optional OpenClaw executor

**Objective:** Add OpenClaw only for browser/external tool-use tasks.

### Task 10.4: Wire GLM/Codex validators

**Objective:** Add live validators with quota policy guard.

---

## Phase 11: Final Verification

### Task 11.1: Run unit tests

```bash
pytest tests/test_runtime_architecture_v2_*.py -v
```

### Task 11.2: Run simulation smoke

```bash
python scripts/simulate_runtime_architecture_v2.py --scenario all
```

### Task 11.3: Run existing project tests

```bash
pytest -q
```

### Task 11.4: Validate acceptance criteria against Seed

Checklist:
- [ ] schema completeness
- [ ] runtime flow coverage
- [ ] implementation readiness
- [ ] operations readiness
- [ ] Discord UX fidelity
- [ ] quota/model policy
- [ ] testability

---

## Notes

This plan intentionally starts with schema, fake adapters, storage, and simulation before live Discord/opencode-go/OpenClaw wiring.
That is not MVP reduction; it is the safest path to the final architecture because it proves the source-of-truth runtime before attaching external systems.
