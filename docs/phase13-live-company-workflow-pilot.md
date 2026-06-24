# Phase 13 Live Company Workflow Pilot Result

## Status

```text
PASS
```

Phase 13 implemented and executed one bounded live company workflow pilot.

## What was implemented

```text
src/runtime_architecture_v2/pilot.py
scripts/run_phase13_company_workflow_pilot.py
tests/test_runtime_architecture_v2_phase13_pilot.py
docs/phase13-live-company-workflow-pilot-plan.md
docs/phase13-live-company-workflow-pilot.md
```

README was updated to list Phase 13 as completed and to document the new pilot module/script.

## Pilot scenario

```text
AI virtual entertainment company의 다음 콘텐츠 아이디어 하나를 회의하고,
실행 가능성/마케팅 포인트/검증 리스크를 한 페이지로 정리해줘.
```

## Route proven

```text
request_type: creative_meeting
primary_role: content_lead
supporting_roles: marketing_lead, quality_lead
live_worker_roles: content_lead only
fake_support_roles: marketing_lead, quality_lead
validator: quality_lead policy/fake validation
projection: Discord-safe summary through fake sink by default
```

## Live execution result

Command:

```bash
python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1
```

Result:

```text
ok: true
meeting_run_id: phase13_live_company_workflow_pilot_20260624075627889091
top_level_state: completed
live_worker_count: 1
fake_worker_count: 2
projection_status: published through fake sink
live_discord: not attempted
```

Runtime artifact:

```text
runtime/meeting_runs/phase13_live_company_workflow_pilot_20260624075627889091/
```

Report artifact:

```text
runtime/meeting_runs/phase13_live_company_workflow_pilot_20260624075627889091/final_report.md
```

## Verification

```text
pytest tests/test_runtime_architecture_v2_phase13_pilot.py -q
=> 12 passed

pytest tests/test_runtime_architecture_v2_*.py -q
=> 101 passed

ruff check src/runtime_architecture_v2 tests/test_runtime_architecture_v2_phase13_pilot.py scripts/run_phase13_company_workflow_pilot.py
=> No issues found

python3 scripts/run_phase13_company_workflow_pilot.py --mode dry-run
=> ok=true, live_worker_count=0, fake_worker_count=3

python3 scripts/run_phase13_company_workflow_pilot.py --mode live-worker --max-live-workers 1
=> ok=true, live_worker_count=1, fake_worker_count=2
```

Post-run operational snapshot:

```text
Go quota: available
Codex quota: available
Hermes AI company gateway sections: 7
Gateway warnings: existing skill command collision / interaction endpoint warnings only
```

## What is proven

```text
A single company request can create a real MeetingRun artifact.
The pilot can route content/marketing/quality roles deterministically.
Dry-run mode executes all roles through fake workers and writes a report.
Live-worker mode executes exactly one opencode-go worker task by default.
Support roles remain fake/injected by default.
Validation policy produces a pass/reject verdict from worker task state.
Final report is Discord-safe and omits raw worker dumps.
Projection is separated from live Discord posting and defaults to fake sink.
```

## What remains unproven

```text
Full Discord app interaction e2e.
Live Discord projection for this Phase 13 pilot.
Multiple simultaneous live workers.
Always-on autonomous company operation.
Persistent Second Brain write-back loop.
Production monitoring/recovery beyond current runtime artifacts.
Multi-bot operational protocol maturity.
```

## Guardrails retained

```text
Hermes Core untouched.
No custom queue/database replacement.
No new Discord gateway adapter.
No token values committed.
Runtime artifacts remain under ignored runtime/.
Live worker fanout is fail-closed above one worker.
Live worker execution remains bounded by a 600s subprocess timeout.
```
