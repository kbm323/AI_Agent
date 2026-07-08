# Phase 33 Live Meeting Protocol Hardening Implementation Plan

> **For Hermes:** Execute task-by-task with strict TDD. Use subagent-driven-development if delegating implementation. Keep Phase 32's product decision intact: default Discord meeting threads post discussion only; summaries/reports/storage are explicit on-demand exports.

**Goal:** Fix the live Discord meeting UX so a default meeting behaves like a chaired meeting: representative first, agenda preserved, two rounds progress coherently, quality lead performs real validation, and Phase 32 no-auto-report behavior remains intact.

**Architecture:** Phase 33 is a protocol-hardening tranche on top of Runtime Architecture v2 / Phase 32. It should not add a new queue, gateway, or reporting system. It tightens the existing `gateway_bridge.py` → `multi_bot.py` → Discord projection path, then upgrades `phase32_live_audit.py` into a stricter live-thread protocol audit.

**Tech Stack:** Python dataclasses, Runtime Architecture v2 multi-bot pipeline, Discord projection, pytest, ruff, live Discord REST audit.

---

## Background Evidence

Last inspected live thread:

```text
Thread ID: 1521907985436381224
Thread name: Phase32 자동 보고 제거 검증
Message count: 12
Default final report/checkpoint markers: absent
Observed order: 콘텐츠 → 대표 → 아트 → 기술 → 마케팅 → 품질관리 → 콘텐츠 → 대표 → 아트 → 기술 → 마케팅 → 품질관리
Observed agenda drift: Phase32 검증 thread, but 대표 switched agenda to 신규 버추얼 아이돌 그룹의 데뷔 컨셉
Observed quality issue: 품질관리 Round 1 and Round 2 both said only 추가 검토가 필요합니다.
Current phase32 audit result: ok=False, last message is not validation/quality Round 2
```

Phase 33 must fix the protocol defects without regressing Phase 32's main win: no automatic final report/checkpoint in the default thread.

---

## Phase 33 Acceptance Criteria

| AC | Requirement | Verification |
|---|---|---|
| AC33-01 | Default thread still has exactly 12 visible meeting messages. | Deterministic test + live Discord audit. |
| AC33-02 | Message 1 is always 대표 opening/chair message. | `phase32_live_audit.py` order check. |
| AC33-03 | Round 1 order is 대표 → 콘텐츠 → 아트 → 기술 → 마케팅 → 품질관리. | Unit test on generated/projection messages. |
| AC33-04 | Message 7 is always 대표 Round 2 briefing / issue synthesis. | Unit test + live audit. |
| AC33-05 | Round 2 order is 대표 → 콘텐츠 → 아트 → 기술 → 마케팅 → 품질관리. | Unit test + live audit. |
| AC33-06 | 대표 must not invent or switch to a sample/default agenda. | Test with sentinel user agenda; reject unrelated generated agenda. |
| AC33-07 | User trigger text / meeting agenda is propagated into every role prompt. | Unit test inspects `WorkerTask.hermes_refs["prompt"]` or projected prompt source. |
| AC33-08 | Every visible bot message is meaningfully tied to the meeting agenda. | Deterministic audit with required agenda keywords / semantic proxy. |
| AC33-09 | Round 2 messages are not identical to Round 1 for the same role. | Audit helper compares normalized text per role. |
| AC33-10 | Round 2 messages explicitly reference at least one Round 1 point or cross-role issue. | Unit test with deterministic fixtures. |
| AC33-11 | 품질관리 Round 1 states risk/validation criteria, not generic filler. | Audit helper checks quality vocabulary and minimum specificity. |
| AC33-12 | 품질관리 Round 2 states approve / hold / revise-request conditions with remaining risks. | Audit helper checks decision vocabulary and specificity. |
| AC33-13 | Default thread contains no automatic final report/checkpoint markers. | Existing Phase 32 forbidden marker checks remain. |
| AC33-14 | On-demand exports remain explicit only. | `tests/test_runtime_architecture_v2_on_demand_exports.py` passes. |
| AC33-15 | Gateway summary does not claim final report/Notion/Second Brain storage happened automatically. | Gateway unit test. |
| AC33-16 | Official release-gate command is documented and excludes legacy backup tests. | Docs/runbook update + command passes or known live-only skips are documented. |
| AC33-17 | Ruff is clean for changed Phase 33 files. | `ruff check <changed files>`. |
| AC33-18 | One supervised live Discord smoke is run and pasted into the Phase 33 completion report with thread ID and audit summary. | Manual/live verification. |

---

## Normal Meeting Protocol Target

```text
01 대표        Round 1 opening: user request, agenda, order, decision criteria
02 콘텐츠팀장  Round 1 content/editorial view
03 아트팀장    Round 1 visual/brand view
04 기술팀장    Round 1 implementation/automation view
05 마케팅팀장  Round 1 market/channel/growth view
06 품질관리팀장 Round 1 risks and validation criteria
07 대표        Round 2 briefing: synthesis of Round 1 and unresolved issues
08 콘텐츠팀장  Round 2 response/rebuttal/revision
09 아트팀장    Round 2 response/rebuttal/revision
10 기술팀장    Round 2 response/rebuttal/revision
11 마케팅팀장  Round 2 response/rebuttal/revision
12 품질관리팀장 Round 2 final validation: approve/hold/revise conditions
```

Non-goal: Do not auto-post final report, checkpoint, agreement, Notion export, or Second Brain export after message 12.

---

## Stage 1 — Freeze Protocol Fixtures and RED Tests

**Objective:** Capture the broken live-thread behavior as deterministic tests before changing implementation.

**Files:**
- Modify: `tests/test_runtime_architecture_v2_phase32_live_audit.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Optional fixture additions inside existing test files only; avoid new large fixture files unless necessary.

**Steps:**
1. Add a fixture that reproduces the bad live order: 콘텐츠 first, 대표 second.
2. Add a fixture that reproduces agenda drift: thread agenda says Phase32 but representative content says virtual idol debut.
3. Add a fixture that reproduces duplicated quality lead messages.
4. Add RED tests:
   - `test_phase33_rejects_content_before_chair_opening`
   - `test_phase33_rejects_representative_agenda_drift`
   - `test_phase33_rejects_duplicate_quality_rounds`
   - `test_phase33_accepts_protocol_order_without_final_report`
5. Run:

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase32_live_audit.py -q
```

Expected before implementation: new negative tests pass only if audit is added, or fail because audit does not yet detect the issue. Record the exact RED behavior.

**Acceptance:** The test suite names the three real live failures: order, agenda drift, quality repetition.

---

## Stage 2 — Strengthen `phase32_live_audit.py` into Phase 33 Protocol Audit

**Objective:** Make the audit helper catch the live UX failures before live smoke is considered successful.

**Files:**
- Modify: `src/runtime_architecture_v2/phase32_live_audit.py`
- Modify: `tests/test_runtime_architecture_v2_phase32_live_audit.py`

**Implementation Notes:**
- Keep existing Phase 32 checks:
  - count == 12
  - forbidden final-report/checkpoint markers absent
  - no automatic report markers
- Add Phase 33 checks:
  - exact author/role sequence
  - message 1 contains 대표/chair/opening language
  - message 7 contains Round 2/synthesis/쟁점 language
  - message 12 contains 품질관리/검증 and Round 2/final-validation language
  - same role Round 1 and Round 2 normalized text are not identical
  - quality lead Round 1 and Round 2 are not generic filler
  - optional `expected_agenda_terms` parameter checks agenda retention

**Suggested API shape:**

```python
def audit_phase33_default_meeting_protocol(
    messages: Sequence[Mapping[str, object]],
    *,
    expected_agenda_terms: Sequence[str] = (),
) -> Phase33MeetingProtocolAuditResult:
    ...
```

Keep `audit_phase32_default_thread()` for backward compatibility. It may call the stricter helper only when requested, or Phase 33 tests may call the new helper directly.

**Verification:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase32_live_audit.py -q
```

Expected after Stage 2: Phase 33 audit tests pass.

---

## Stage 3 — Fix Deterministic Bot Projection Order

**Objective:** Ensure default projected visible messages are generated in chair-led order.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Implementation Requirements:**
- Define one canonical visible role sequence for company meetings:

```python
ROUND1_VISIBLE_ROLE_ORDER = (
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "quality_lead",
)
ROUND2_VISIBLE_ROLE_ORDER = ROUND1_VISIBLE_ROLE_ORDER
```

Use existing role identifiers if names differ; do not introduce parallel role naming.

- Generate representative opening before team-lead Round 1 opinions.
- Generate representative Round 2 synthesis before Round 2 team responses.
- Preserve total visible messages at 12.
- Do not add final report/checkpoint message.

**Tests to add/update:**
- `test_phase33_default_visible_message_order_is_chair_led`
- `test_phase33_default_projection_count_remains_twelve`
- `test_phase33_no_automatic_final_report_after_order_fix`

**Verification:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py -q
```

---

## Stage 4 — Guarantee Agenda / Trigger Text Propagation

**Objective:** Prevent representative or worker prompts from falling back to sample/default agendas.

**Files:**
- Modify: `src/runtime_architecture_v2/gateway_bridge.py`
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Modify or add focused gateway test if an existing file covers gateway summary/trigger behavior.

**Implementation Requirements:**
- `GatewayMeetingTrigger.text` must be passed into `run_phase14_multi_bot_pilot(trigger_text=...)` unchanged except safe sanitization.
- Thread title, meeting agenda, worker prompts, representative opening, and Round 2 synthesis must derive from the same canonical agenda string.
- Representative fallback text must say it cannot determine the agenda rather than inventing a sample agenda.
- Ban hardcoded sample agendas from default live meeting path, especially:
  - `신규 버추얼 아이돌 그룹의 데뷔 컨셉`
  - generic default meeting topics unrelated to user trigger

**Tests:**
- `test_phase33_trigger_text_reaches_all_role_prompts`
- `test_phase33_representative_does_not_replace_user_agenda`
- `test_phase33_gateway_summary_uses_thread_id_without_claiming_report`

**Verification:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py -q
```

If gateway tests are separate, run them too.

---

## Stage 5 — Strengthen Round 2 and Quality Lead Prompt Contracts

**Objective:** Make Round 2 a real rebuttal/decision-progress round and make quality lead user-facing.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`
- Potentially modify any helper that builds role-specific prompts or fallback text.

**Implementation Requirements:**
- Round 2 prompts must include:
  - Round 1 summary or previous role messages
  - required output sections: 동의, 보완/반박, 결정조건 or 실행조건
  - explicit instruction not to repeat Round 1 verbatim
- Quality Round 1 prompt must require:
  - 2+ risks
  - validation criteria
  - what evidence would make the meeting acceptable
- Quality Round 2 prompt must require:
  - approve / hold / revise-request decision
  - remaining risks
  - concrete next validation condition
- Fallback output for failed quality worker must be explicit degraded/fail-closed, not generic `추가 검토가 필요합니다` twice.

**Tests:**
- `test_phase33_round2_prompt_includes_round1_context`
- `test_phase33_quality_round1_has_risk_and_validation_criteria`
- `test_phase33_quality_round2_has_decision_and_remaining_risks`
- `test_phase33_quality_rounds_are_not_identical`

**Verification:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase32_live_audit.py -q
```

---

## Stage 6 — Preserve and Re-test On-Demand Export Boundaries

**Objective:** Ensure Phase 33 protocol fixes do not regress Phase 32's on-demand report/export model.

**Files:**
- Modify only if tests expose a regression:
  - `src/runtime_architecture_v2/on_demand_exports.py`
  - `src/runtime_architecture_v2/final_report_v3.py`
  - relevant tests

**Verification:**

```bash
PYTHONPATH=src python3 -m pytest \
  tests/test_runtime_architecture_v2_on_demand_exports.py \
  tests/test_runtime_architecture_v2_final_report_v3.py \
  tests/test_runtime_architecture_v2_phase32_live_audit.py -q
```

Expected: all pass. Default meeting still creates source evidence only; explicit request creates `final_report_v3.md` / `decision_summary.json`.

---

## Stage 7 — Define Official Release Gate and Clean Changed-File Ruff

**Objective:** Stop treating broken legacy backup test discovery as the release gate and keep Phase 33 files lint-clean.

**Files:**
- Modify: `docs/phase32-live-discord-audit-runbook.md` or create `docs/phase33-live-meeting-protocol-hardening.md`
- Optional: `pytest.ini` / `pyproject.toml` only if the project decides to exclude `backup/old_pipeline_*` from discovery globally.

**Required Release Gate:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase32_live_audit.py \
  tests/test_runtime_architecture_v2_on_demand_exports.py \
  tests/test_runtime_architecture_v2_final_report_v3.py -q

ruff check src/runtime_architecture_v2/multi_bot.py \
  src/runtime_architecture_v2/gateway_bridge.py \
  src/runtime_architecture_v2/phase32_live_audit.py \
  src/runtime_architecture_v2/on_demand_exports.py \
  src/runtime_architecture_v2/final_report_v3.py \
  tests/test_runtime_architecture_v2_phase14_multi_bot.py \
  tests/test_runtime_architecture_v2_phase32_live_audit.py

bash scripts/pre-commit-secret-scan.sh
```

**Note:** Do not claim full `pytest tests -q` is clean until `backup/old_pipeline_20260629_222134` is excluded or fixed. That cleanup can be a separate release-quality tranche if it touches broad legacy files.

---

## Stage 8 — Supervised Live Discord Reverification

**Objective:** Prove Phase 33 against an actual Discord thread body, not only local tests.

**Prerequisites:**
- 7 company bot tokens valid.
- Create/send thread permissions valid in `#회의실-전략결정`.
- Provider quota checked before fan-out.
- Single provider call smoke passes before full fan-out if provider path changed.

**Live Smoke Prompt:**
Use a representative, user-style prompt that does not contain audit sentinel strings directly:

```text
Phase33 회의 진행 품질 검증 회의 열어줘. 회의 진행 순서, 안건 유지, 2라운드 반론, 품질관리 최종 검증 조건을 점검해줘.
```

**Live Audit Steps:**
1. Trigger meeting through the real Gateway/Discord path.
2. Fetch the created thread messages in chronological order via Discord REST.
3. Run:

```python
from src.runtime_architecture_v2.phase32_live_audit import (
    audit_phase33_default_meeting_protocol,
)

result = audit_phase33_default_meeting_protocol(
    messages,
    expected_agenda_terms=("Phase33", "회의 진행 품질", "안건 유지"),
)
assert result.ok, result
```

4. Verify manually:
   - Message 1 = 대표 opening
   - Message 7 = 대표 Round 2 briefing
   - Message 12 = 품질관리 final validation
   - no final report/checkpoint markers
   - no unrelated sample agenda
   - quality Round 1/2 are different and concrete

**Completion Report Must Include:**
- thread ID
- thread name
- message count
- audit result
- forbidden marker count
- role order
- remaining issues, if any

---

## Stage 9 — Phase 33 Completion Documentation

**Objective:** Record what was fixed, what was proven, and what remains before final-system completion.

**Files:**
- Create: `docs/phase33-live-meeting-protocol-hardening.md`
- Modify: `README.md` only if status wording needs to change.
- Modify: `docs/user-guides/ai-agent-meeting-readme.md` if visible meeting flow changed.

**Required Sections:**
- Scope
- Live thread defect evidence from pre-fix thread
- Acceptance criteria table AC33-01~AC33-18
- Local verification commands and outputs
- Live Discord verification evidence
- Remaining phases after Phase 33:
  1. On-demand export expansion: summary/agreement/action-items/Notion/Second Brain
  2. Full release gate cleanup: legacy backup pytest discovery + Ruff backlog
  3. 24h unattended operation proof

---

## Implementation Order Summary

1. Stage 1 — RED tests for observed live failures.
2. Stage 2 — Protocol audit helper.
3. Stage 3 — Deterministic message order fix.
4. Stage 4 — Agenda/trigger propagation fix.
5. Stage 5 — Round 2 and quality lead prompt contracts.
6. Stage 6 — On-demand export regression guard.
7. Stage 7 — Release gate and changed-file Ruff.
8. Stage 8 — Supervised live Discord reverification.
9. Stage 9 — Completion documentation.

---

## Risk / Difficulty Estimate

| Workstream | Difficulty | Risk |
|---|---:|---|
| Protocol order tests/audit | 4/10 | Low |
| Multi-bot message ordering | 5/10 | Medium: existing tests may encode old order |
| Agenda propagation | 7/10 | Medium-high: crosses gateway, multi_bot, worker prompt, fallback path |
| Quality lead prompt/fallback | 6/10 | Medium: model output variability |
| On-demand regression guard | 3/10 | Low |
| Live Discord reverification | 7/10 | Medium-high: tokens, permissions, provider quota |
| Release gate/Ruff cleanup for changed files | 5/10 | Medium |

Overall Phase 33 difficulty: **6.5/10**.

Expected duration:
- Deterministic TDD and docs: 3~5 hours
- Live Discord verification included: 4~7 hours
- If provider/permission issues appear: add separate blocker report, do not claim live pass

---

## Stop Conditions

Stop and report a blocker if:
- Live Discord thread creation/posting is blocked by permissions or token failure.
- Provider completion path cannot produce one single valid role response.
- The system can only pass by using static fake messages in live mode.
- The representative still replaces the user agenda after Stage 4.

Do not lower the quality bar by accepting a thread that only has 12 messages but fails order, agenda, or quality checks.
