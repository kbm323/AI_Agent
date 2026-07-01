# Phase 32 Live Discord Audit Runbook

Stage 6 freezes the live verification contract for the Phase 32 meeting UX.

## Goal

Prove with the actual Discord thread body that:

1. A default meeting posts only the 12 team-lead discussion messages.
2. No automatic final report/checkpoint appears in the default thread.
3. An explicitly requested Final Report v3 can be generated and audited separately.
4. The requested report is user-facing, <= 1600 chars, and contains no internal model/log markers.

## Default meeting audit

After running a live meeting, fetch the thread messages in chronological order and pass them to:

```python
from src.runtime_architecture_v2.phase32_live_audit import audit_phase32_default_thread

result = audit_phase32_default_thread(messages)
assert result.ok, result
```

Required pass conditions:

| Check | Expected |
|---|---|
| message count | 12 |
| default final report | absent |
| checkpoint | absent |
| final-report markers | absent |
| last message | 검증/품질관리 Round 2 |

Forbidden default-thread markers:

```text
# 📋
## 🎯 결론
## ✅ 합의안
## 🚀 다음 액션
회의 체크포인트
```

## On-demand Final Report v3 audit

After the user explicitly asks `최종보고서로 정리해줘`, generate the report with:

```python
from src.runtime_architecture_v2.on_demand_exports import (
    OnDemandExportType,
    run_on_demand_export,
)

export = run_on_demand_export(root, meeting_run_id, OnDemandExportType.FINAL_REPORT)
```

Before or after posting the report body to Discord, audit it with:

```python
from src.runtime_architecture_v2.phase32_live_audit import audit_phase32_on_demand_report

report_audit = audit_phase32_on_demand_report(export.content)
assert report_audit.ok, report_audit
```

Required sections:

```text
# 📋 최종보고서
## 🎯 결론
## ✅ 합의안
## 🚀 다음 액션
## ⚠️ 리스크
## 🔍 검증
```

Forbidden requested-report markers:

```text
model evidence
deepseek
qwen
glm
runtime artifact
worker_execution_failed
placeholder output
Discord thread
```

## Local artifacts

A requested v3 report must create:

```text
runtime/meeting_runs/<meeting_run_id>/final_report_v3.md
runtime/meeting_runs/<meeting_run_id>/decision_summary.json
```

A default meeting must not create those files until the explicit on-demand request runs.

## JSON summary

Use `phase32_audit_summary()` to make CI/log-friendly audit output:

```python
from src.runtime_architecture_v2.phase32_live_audit import phase32_audit_summary

summary = phase32_audit_summary(
    default_thread=default_audit,
    on_demand_report=report_audit,
)
```

The summary is JSON-serializable and has top-level `ok`.

## Current blocker pattern

If live execution returns:

```json
{"ok": false, "error": "live_discord_thread_blocked"}
```

then do not claim Discord body verification passed. Report this as a live-boundary blocker and rely only on deterministic tests until the policy/config allows live thread creation/posting.

## Verification commands

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase32_live_audit.py -q
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_final_report_v3.py tests/test_runtime_architecture_v2_on_demand_exports.py tests/test_runtime_architecture_v2_phase32_live_audit.py -q
```
