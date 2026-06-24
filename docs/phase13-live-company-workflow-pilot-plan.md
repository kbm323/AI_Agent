# Phase 13 Live Company Workflow Pilot Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Prove one complete, bounded live-company workflow from a user-facing company request into a MeetingRun, role routing, one real opencode-go worker task, validation/audit policy handling, and Discord-safe reporting without turning the system into full production autonomy.

**Architecture:** Phase 13 builds on the completed Runtime Architecture v2 and Phase 12 live operational hardening. Keep Hermes Core untouched. Use existing `MeetingRun` schemas, store, routing, scheduling, worker, validation, projection, and policy modules; add only the thin pilot orchestration and documentation needed to connect a single live workflow end-to-end under quota and safety gates.

**Tech Stack:** Python, pytest, Runtime Architecture v2 modules, ignored `runtime/` artifacts, Discord REST projection sink, opencode-go bounded live worker smoke boundary, Hermes gateway profiles, GitHub MCP for remote verification.

---

## Current Context

Completed baseline:

```text
Phase 0-11: Runtime Architecture v2 implementation and deterministic verification complete
Phase 12.1: Discord REST live projection smoke PASS
Phase 12.2: opencode-go worker live smoke PASS
Phase 12.3: bot permission inventory documented; permission mutation deferred
Phase 12.4: token rotation decision documented; do not rotate now
Phase 12.5: assistant UX/channel decision documented; #일일-브리핑 retained
Gateway: 7/7 hermes-aicompany tmux sessions running
Quota at plan time: Go OK, Codex OK
Git: clean main...origin/main
```

Verified but not yet claimed:

```text
full Discord app interaction e2e
full MeetingRun live company workflow
multi-bot operational protocol
persistent Second Brain knowledge loop
autonomous scheduling/Kanban operations
production readiness
```

Phase 13 should close the first of these gaps only: one live company workflow pilot.

---

## Phase 13 Scope Decision

Phase 13 is:

```text
Live Company Workflow Pilot
```

Phase 13 is not:

```text
Full production launch
Always-on autonomous company operation
Unbounded multi-agent fanout
Ouroboros/Ralph loop
Custom Discord slash-command platform rebuild
Custom queue/database replacement
```

### In Scope

- Add a canonical Phase 13 plan document under `docs/` after this plan is accepted.
- Define one pilot request scenario that exercises the full company workflow at small scale.
- Use existing Runtime Architecture v2 modules where possible.
- Add a small pilot runner or script if needed.
- Create one real `MeetingRun` artifact under ignored `runtime/`.
- Route the request to a limited set of roles.
- Execute at most one live opencode-go worker task by default.
- Use fake/injected workers for any extra roles unless explicitly promoted later.
- Record validation/audit handling through existing policy structures.
- Post or prepare one Discord-safe summary projection.
- Add focused tests for new pilot orchestration code.
- Keep all tokens hidden and all runtime output out of git.

### Out of Scope

- Discord slash command implementation unless Hermes Gateway support is verified separately.
- Multiple simultaneous live workers.
- Long-running autonomous loops.
- Production monitoring stack.
- Bot permission mutation.
- Token rotation.
- `#개인-비서` channel creation.
- Persistent Second Brain write-back beyond a documentation note.

---

## Phase 13 Acceptance Criteria

Phase 13 is complete when all accepted criteria below are PASS or explicitly deferred with reason.

```text
AC-13.0 Canonical Phase 13 plan exists and is committed.
AC-13.1 A single pilot scenario is defined with exact input, expected route, worker role, validation behavior, and report shape.
AC-13.2 Pilot orchestration creates a real MeetingRun artifact under ignored runtime/ using existing store/schema modules.
AC-13.3 Pilot uses existing routing/scheduling/validation/projection boundaries, not new platform replacements.
AC-13.4 At most one opencode-go live worker task is executed by default, gated by quota and explicit pilot flag.
AC-13.5 A Discord-safe report is produced and either posted through the live projection sink or documented as dry-run if live posting is not selected.
AC-13.6 Focused tests pass for any new code.
AC-13.7 Secret scans show 0 tracked secret findings.
AC-13.8 Gateway status is checked after pilot execution.
AC-13.9 Final documentation records what is proven and what remains unproven.
```

---

## Pilot Scenario

Use one bounded company request:

```text
“AI virtual entertainment company의 다음 콘텐츠 아이디어 하나를 회의하고, 실행 가능성/마케팅 포인트/검증 리스크를 한 페이지로 정리해줘.”
```

Expected high-level route:

```text
request_type: creative_meeting
primary_role: content_lead
supporting_roles: marketing_lead, quality_lead
live_worker_role: content_lead only by default
validator: policy/fake or one bounded validator only if quota remains safe and explicitly enabled
report_target: Discord-safe summary
```

Default execution posture:

```text
content_lead: one live opencode-go worker
marketing_lead: fake/injected output
quality_lead: fake/injected validation note
Discord projection: live only if explicit --live-discord flag is used; otherwise dry-run report file
```

This keeps Phase 13 as a workflow pilot, not a fanout launch.

---

## Proposed Implementation Approach

Add the smallest project-local layer needed for the pilot:

```text
src/runtime_architecture_v2/pilot.py
scripts/run_phase13_company_workflow_pilot.py
tests/test_runtime_architecture_v2_phase13_pilot.py
docs/phase13-live-company-workflow-pilot-plan.md
docs/phase13-live-company-workflow-pilot.md
```

Use existing modules:

```text
schemas.py
store.py
routing.py
scheduling_policy.py
workers.py
validation.py
projection.py
policies.py
orchestrator.py where useful
```

Avoid adding:

```text
new queue database
new Discord gateway adapter
new bot permission model
new token handling
new global daemon
```

---

## Recommended Execution Order

### Task 13.0: Promote this plan into canonical docs

**Objective:** Make Phase 13 explicit before implementation.

**Files:**
- Create: `docs/phase13-live-company-workflow-pilot-plan.md`
- Source: `.hermes/plans/2026-06-24_161348-phase13-live-company-workflow-pilot.md`
- Modify: `README.md`

**Steps:**
1. Copy this plan into `docs/phase13-live-company-workflow-pilot-plan.md`.
2. Add it to README repository layout.
3. Add Phase 13 as the current next phase.
4. Run doc validation and secret scan.
5. Commit and push.

**Verification:**

```bash
git diff --check
python3 <local staged secret scan>
```

Expected:

```text
0 high-risk secret findings
Phase 13 plan visible in docs and README
```

---

### Task 13.1: Define pilot scenario fixture

**Objective:** Make the pilot input deterministic and testable.

**Files:**
- Create: `src/runtime_architecture_v2/pilot.py`
- Create: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**Implementation target:**

Add a function such as:

```python
def build_phase13_pilot_request() -> dict[str, object]:
    return {
        "pilot_id": "phase13_live_company_workflow_pilot",
        "trigger_text": "AI virtual entertainment company의 다음 콘텐츠 아이디어 하나를 회의하고, 실행 가능성/마케팅 포인트/검증 리스크를 한 페이지로 정리해줘.",
        "user_id": "phase13-user",
        "channel_id": "phase13-channel",
        "live_worker_roles": ["content_lead"],
        "fake_support_roles": ["marketing_lead", "quality_lead"],
    }
```

**Test cases:**

```text
pilot request has stable pilot_id
trigger_text is non-empty
only one default live worker role
support roles are fake by default
```

**Verification:**

```bash
pytest tests/test_runtime_architecture_v2_phase13_pilot.py -q
```

Expected:

```text
new tests pass
```

---

### Task 13.2: Add MeetingRun creation for pilot

**Objective:** Create a real MeetingRun artifact under ignored `runtime/` using existing schemas/store.

**Files:**
- Modify: `src/runtime_architecture_v2/pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**Implementation target:**

Add a function such as:

```python
def create_phase13_meeting_run(root: Path, request: dict[str, object]) -> MeetingRun:
    ...
```

It should:

```text
create meeting_run_id with phase13 prefix
write meeting_run.json through existing store helper
set trigger text and top-level state consistently
append decision/audit log if existing helpers support it
```

**Test cases:**

```text
meeting_run.json is written
meeting_run_id starts with phase13
trigger text is preserved
runtime path is caller-provided and can be under tmp_path
```

**Verification:**

```bash
pytest tests/test_runtime_architecture_v2_phase13_pilot.py -q
```

---

### Task 13.3: Route pilot to company roles

**Objective:** Reuse routing policy to produce a small company role plan.

**Files:**
- Modify: `src/runtime_architecture_v2/pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**Implementation target:**

Add a compact route object or dictionary:

```text
primary_role: content_lead
supporting_roles: marketing_lead, quality_lead
live_worker_roles: content_lead
fake_worker_roles: marketing_lead, quality_lead
```

Do not add a full new routing engine. If existing fake Qwen router can express the route, wrap it. If not, use a deterministic pilot-only mapping and document it as pilot-only.

**Test cases:**

```text
route includes content/marketing/quality roles
route has exactly one live worker by default
route does not include CEO/assistant as live worker by default
```

---

### Task 13.4: Add worker task builder

**Objective:** Build one opencode-go WorkerTask for the content role, with fake support tasks for the rest.

**Files:**
- Modify: `src/runtime_architecture_v2/pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**Implementation target:**

Create functions like:

```python
def build_phase13_worker_tasks(meeting_run: MeetingRun, route: dict[str, object], root: Path) -> list[WorkerTask]:
    ...
```

Rules:

```text
one OPENCODE_GO task for content_lead
fake/injected tasks for marketing_lead and quality_lead
packet/output paths under runtime/phase13...
model_policy preferred glm-5.1 for the live worker unless existing policy says otherwise
```

**Test cases:**

```text
exactly one OPENCODE_GO task by default
task paths are under provided root
worker_task_ids are deterministic enough for tests
```

---

### Task 13.5: Add dry-run pilot runner CLI

**Objective:** Provide a script that exercises the workflow with fake workers only by default.

**Files:**
- Create: `scripts/run_phase13_company_workflow_pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py` or CLI smoke test if existing project pattern supports it

**CLI behavior:**

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode dry-run
```

Expected output:

```json
{
  "pilot_id": "phase13_live_company_workflow_pilot",
  "mode": "dry-run",
  "meeting_run_id": "...",
  "top_level_state": "completed",
  "live_worker_count": 0,
  "fake_worker_count": 3,
  "report_path": "runtime/.../final_report.md",
  "ok": true
}
```

Rules:

```text
dry-run never calls live Discord
 dry-run never calls opencode-go
all artifacts go under ignored runtime/
```

---

### Task 13.6: Add explicit live-worker flag

**Objective:** Permit exactly one live opencode-go worker only when explicitly requested.

**Files:**
- Modify: `scripts/run_phase13_company_workflow_pilot.py`
- Modify: `src/runtime_architecture_v2/pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**CLI behavior:**

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1
```

Rules:

```text
require --max-live-workers 1
fail closed if max-live-workers > 1
check quota before live execution if a reusable quota function exists; otherwise require operator pre-check and document it
use OpenCodeGoSmokeRunner or OpenCodeGoWorkerRunner boundary
expected output/report shape must be structured
```

**Test cases:**

```text
live-worker mode rejects max-live-workers > 1
live-worker mode can use injected command runner in tests
nonzero/timeout results become structured failure
```

---

### Task 13.7: Add report generation

**Objective:** Produce one Discord-safe final report from the pilot workflow.

**Files:**
- Modify: `src/runtime_architecture_v2/pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**Report shape:**

```markdown
# Phase 13 Pilot Report

## Request
...

## Route
- Content Lead: ...
- Marketing Lead: ...
- Quality Lead: ...

## Output
...

## Validation
...

## Boundaries
...
```

Rules:

```text
no raw secrets
no token values
no uncontrolled mentions
safe for Discord projection
```

---

### Task 13.8: Optional live Discord projection flag

**Objective:** Post the final report only when explicitly requested.

**Files:**
- Modify: `scripts/run_phase13_company_workflow_pilot.py`
- Test: `tests/test_runtime_architecture_v2_phase13_pilot.py`

**CLI behavior:**

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1 --live-discord
```

Rules:

```text
without --live-discord, write report file only
with --live-discord, use existing LiveDiscordProjectionSink
allowed_mentions remains safe
report target defaults to system-log or explicit env/channel
```

This task may be deferred if the dry-run and live-worker report file are enough for Phase 13.

---

### Task 13.9: Execute the pilot once

**Objective:** Run the accepted Phase 13 pilot with the smallest live surface.

**Pre-checks:**

```bash
git status --short --branch
bash scripts/check_all_quota.sh
bash scripts/status_discord_multibot_gateways.sh
```

Recommended run:

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1
```

Optional live Discord projection only if selected:

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1 --live-discord
```

Expected:

```text
ok=true
meeting_run artifact created under runtime/
exactly one live opencode-go worker attempted
report produced
no tracked secrets
```

---

### Task 13.10: Final documentation and verification

**Objective:** Record what Phase 13 proved and what remains.

**Files:**
- Create: `docs/phase13-live-company-workflow-pilot.md`
- Modify: `README.md`

**Verification commands:**

```bash
pytest tests/test_runtime_architecture_v2_phase13_pilot.py -q
pytest tests/test_runtime_architecture_v2_*.py -q
python3 scripts/run_phase13_company_workflow_pilot.py --mode dry-run
ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_phase13_pilot.py scripts/run_phase13_company_workflow_pilot.py
python3 <local high-risk secret scan>
git diff --check
```

If live-worker was executed:

```bash
bash scripts/check_all_quota.sh
bash scripts/status_discord_multibot_gateways.sh
```

Expected final documentation:

```text
Phase 13 pilot PASS/FAIL
live worker attempted yes/no
live Discord projection attempted yes/no
exact files/artifacts generated
remaining gaps before Phase 14
```

---

## Phase 13 Risks and Guardrails

### Risk: Accidentally becoming production launch

Mitigation:

```text
Only one pilot scenario.
Only one live worker by default.
No fanout.
No long-running loop.
No production claim.
```

### Risk: Quota exhaustion

Mitigation:

```text
Require quota snapshot before live-worker run.
Keep dry-run default.
Require --max-live-workers 1.
No Ouroboros/Ralph loop.
```

### Risk: Discord overclaiming

Mitigation:

```text
Separate report file generation from live Discord projection.
Use --live-discord explicitly.
Document whether posting was actually attempted.
```

### Risk: Rebuilding Hermes

Mitigation:

```text
Use existing Hermes gateway/profile/process setup.
Use Runtime Architecture v2 domain layer only.
Do not add a separate Discord gateway or queue DB.
```

### Risk: Secret leakage

Mitigation:

```text
Do not read or print token values.
Runtime output stays under ignored runtime/.
Run staged secret scan before commit.
```

---

## Recommended Phase 13 Completion Boundary

Phase 13 should end when this is true:

```text
One company request can be processed through a real MeetingRun pilot.
The system can route roles, run at most one live worker, generate a report, and optionally project the report safely.
The result is documented with exact boundaries.
```

Phase 13 should not wait for:

```text
multi-bot protocol maturity
Second Brain automatic knowledge loop
autonomous scheduling/Kanban
production monitoring
full Discord interaction surface
```

Those belong to later phases.

---

## Suggested Later Phases

```text
Phase 14: Multi-bot Operational Protocol
Phase 15: Persistent Second Brain / Knowledge Loop
Phase 16: Autonomous Scheduling / Kanban Operations
Phase 17: Production Readiness / Monitoring / Recovery
```

---

## Final Recommendation

Proceed in this order:

```text
13.0 promote plan to docs
13.1 define pilot scenario
13.2 create MeetingRun artifact path
13.3 deterministic route
13.4 worker task builder
13.5 dry-run pilot CLI
13.6 live-worker flag with max-live-workers=1
13.7 report generation
13.8 optional live Discord projection flag
13.9 execute one pilot
13.10 document outcome
```

Do not implement Phase 13 code until this plan is accepted or promoted into canonical docs.
