# Final Report v3 AI Summary + Schema Validation Implementation Plan

> **For Hermes:** Use test-driven-development for every code task. Do not patch the old heuristic report generator in place without RED tests. Implement task-by-task and verify with pytest plus live Discord body audit.

**Goal:** Provide an on-demand AI-assisted, schema-validated final-report export that turns meeting discussion into a user-facing conclusion, agreements, actions, and risks only when the user explicitly asks for a report/summary/export.

**Architecture:** Default meetings do not auto-post a final report or checkpoint. Team-lead messages and specialist outputs are stored as source artifacts. When the user explicitly asks for a report/export, an AI final-report summarizer produces a strict JSON decision schema. Code validators reject system/log wording, malformed actions, missing source concepts, and false “no risk” outputs. Discord renders a short user-facing requested report; local artifacts retain full evidence and model details.

**Tech Stack:** Python dataclasses, JSON parsing/validation, existing Hermes/OpenCode worker interfaces, pytest, Discord live smoke via existing Phase 14 projection path.

---

## Current Problem

The legacy automatic final report is structurally formatted but semantically wrong:

- `🎯 결론` often describes the reporting mechanism (`Discord에서 결론·합의안·다음 액션...`) instead of the meeting decision.
- `✅ 합의안` can contain system architecture statements (`Discord thread`, `runtime artifact`) instead of agenda-specific agreements.
- `🚀 다음 액션` can contain renderer/developer tasks (`bullet 요약`, `표 렌더링`) instead of owner-based meeting actions.
- `⚠️ 리스크` can say `리스크 없음` because system fallback succeeded, even when the meeting discussed product/legal/UX risks.
- Discord final reports expose too much model evidence and can end as an internal log rather than a decision document.
- Generic specialist outputs such as `composer는 정상 specialist 결과입니다` are not filtered out of user-facing reports.

The root cause is that final-report content is mostly assembled by deterministic helper heuristics rather than an AI summarizer with schema validation. Phase 32 changes the UX contract: this report must not be generated automatically after every meeting. It is an on-demand export.

---

## Target Report Model

Create a new module:

- `src/runtime_architecture_v2/final_report_v3.py`

Primary dataclass:

```python
@dataclass(frozen=True)
class FinalReportDecision:
    conclusion: str
    agreements: tuple[str, ...]
    actions: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_summary: str
    source_roles: tuple[str, ...]
```

Expected AI JSON shape:

```json
{
  "conclusion": "외계인 DJ 굿즈 쿠폰은 음성 사연 사용 동의, 쿠폰 중복 사용 차단, 사용 상태 UX 구분을 출시 조건으로 삼아 진행한다.",
  "agreements": [
    "청취자 음성 사연은 명시적 사용 동의가 있을 때만 굿즈 쿠폰에 연결한다.",
    "동의 기록이 없으면 쿠폰 생성을 차단한다.",
    "QR 쿠폰 화면은 사용 전/사용 완료 상태를 색상과 아이콘으로 구분한다."
  ],
  "actions": [
    "기술팀: 동의 기록이 없으면 쿠폰 생성이 차단되도록 구현한다.",
    "아트팀: QR 쿠폰 화면에 사용 전/사용 완료 상태를 색상과 아이콘으로 분리한다.",
    "법무/검증: 음성 사연 사용 동의와 쿠폰 약관 분리 고지를 검토한다."
  ],
  "risks": [
    "음성 사연 사용 동의가 누락되면 저작권/개인정보 리스크가 생긴다.",
    "쿠폰 중복 사용 방지가 없으면 운영 리스크가 생긴다."
  ],
  "evidence_summary": "검증 PASS, fallback 없음. 상세 모델 evidence는 local artifact에 보관한다.",
  "source_roles": ["대표", "콘텐츠 팀장", "아트 팀장", "기술 팀장", "마케팅 팀장", "검증 팀장", "legal-reviewer", "quality-assurance"]
}
```

---

## Validation Rules

### Forbidden wording in user-facing decision fields

`conclusion`, `agreements`, `actions`, and `risks` must not contain:

```text
Discord thread
runtime artifact
bullet 요약
표 렌더링
model evidence
fallback chain
deepseek
qwen
glm
worker_execution_failed
placeholder output
```

Allowed only in `evidence_summary` when phrased as a short evidence note:

```text
검증 PASS
fallback 없음
상세 evidence
local artifact
```

### Required structure

- `conclusion`: one sentence, roughly 30-180 Korean characters.
- `agreements`: 2-5 items.
- `actions`: 2-5 items, each matching `팀명: 할 일` or `역할/팀: 할 일`.
- `risks`: 1-4 items when meeting source contains risk concepts.
- `evidence_summary`: one short sentence.
- `source_roles`: at least one visible team lead role.

### Source concept checks

If source text contains important concepts, they must appear in at least one decision field:

- Source contains `동의` → output mentions `동의`.
- Source contains `쿠폰` → output mentions `쿠폰`.
- Source contains `법무` or `약관` → output mentions `법무`, `약관`, or `검증`.
- Source contains `리스크`, `저작권`, `개인정보`, `중복`, or `실패 상태` → risks must not be `리스크 없음`.

### Fallback safety

If AI summary generation, JSON parsing, or validation fails, do not reuse the old system-wording heuristic report.

Safe fallback report:

```text
# 📋 최종보고서

## 🎯 결론
AI 요약 생성에 실패했습니다. 아래 팀장 핵심 의견을 기준으로 사용자가 판단해야 합니다.

## ✅ 합의안
• 요약 실패 — 팀장 핵심 의견 참고

## 🚀 다음 액션
• 대표: 회의 결과를 재검토하고 합의안을 다시 생성한다.

## ⚠️ 리스크
• 자동 요약 실패로 인해 최종 결론이 확정되지 않았습니다.
```

---

## Discord vs Local Rendering

### Discord renderer

Discord final report is user-facing and short:

```text
# 📋 최종보고서: <안건>

## 🎯 결론
<agenda decision>

## ✅ 합의안
• <agreement 1>
• <agreement 2>
• <agreement 3>

## 🚀 다음 액션
• 기술팀: <task>
• 아트팀: <task>
• 법무/검증: <task>

## ⚠️ 리스크
• <risk 1>
• <risk 2>

## 🔍 검증
검증 PASS · fallback 없음 · 상세 evidence는 local artifact 보관
```

Discord constraints:

- Target length: <= 1600 chars.
- No Markdown tables.
- No model-by-model evidence lines.
- No raw JSON, status wrappers, Unicode escapes, or internal implementation terms.
- No generic specialist placeholder outputs.
- If truncation is unavoidable, truncate low-priority evidence summary first, not decision content.

### Local renderer

Local artifacts retain full traceability:

- `final_report_v3.md` — full decision report plus source summaries.
- `decision_summary.json` — validated `FinalReportDecision` JSON.
- `raw_meeting_messages.json` — all round messages.
- `specialist_outputs.json` — all specialist output payloads.
- `model_evidence.txt` — full validation/model/fallback evidence.
- Existing `final_report_v2.md` can remain temporarily for compatibility but must not be the Discord final report once v3 is enabled.

---

## Task 1: Add FinalReportDecision schema and parser

**Objective:** Add a strict decision schema without touching the live pipeline yet.

**Files:**
- Create: `src/runtime_architecture_v2/final_report_v3.py`
- Create: `tests/test_runtime_architecture_v2_final_report_v3.py`

**Step 1: Write RED tests**

Tests:

- Valid JSON parses into `FinalReportDecision`.
- Missing required keys fail closed.
- Non-list `agreements/actions/risks/source_roles` fail closed.
- Empty strings fail closed.

Run:

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_final_report_v3.py -q
```

Expected: FAIL because module does not exist.

**Step 2: Implement minimal dataclass + parser**

Add:

- `FinalReportDecision`
- `FinalReportDecisionError`
- `parse_final_report_decision_json(text: str) -> FinalReportDecision`

**Step 3: Verify GREEN**

Run same test; expected PASS.

---

## Task 2: Add decision validator

**Objective:** Reject AI summaries that look like system logs or malformed reports.

**Files:**
- Modify: `src/runtime_architecture_v2/final_report_v3.py`
- Modify: `tests/test_runtime_architecture_v2_final_report_v3.py`

**Step 1: RED tests**

Add tests that fail when:

- `conclusion` contains `Discord에서 결론·합의안·다음 액션`.
- `agreements` contain `Discord thread` or `runtime artifact`.
- `actions` contain `bullet 요약` or lack `팀명:` format.
- Source contains `동의/쿠폰/법무`, but decision omits them.
- Source contains risk keywords but risks say `리스크 없음`.

**Step 2: Implement validator**

Add:

```python
validate_final_report_decision(decision: FinalReportDecision, *, source_text: str) -> None
```

Fail closed with actionable error messages.

**Step 3: Verify**

Run:

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_final_report_v3.py -q
```

---

## Task 3: Build AI summarizer prompt and adapter

**Objective:** Generate structured decision JSON from team messages and specialist summaries.

**Files:**
- Modify: `src/runtime_architecture_v2/final_report_v3.py`
- Modify: `tests/test_runtime_architecture_v2_final_report_v3.py`

**Step 1: RED tests**

Verify prompt includes:

- Agenda.
- Round 1 and Round 2 team messages.
- Valid specialist outputs.
- JSON-only instruction.
- Forbidden wording list.
- Action owner format requirement.

Verify prompt excludes:

- Full model-by-model evidence.
- Raw worker payload JSON.
- Discord renderer instructions as decision content.

**Step 2: Implement**

Add:

- `build_final_report_decision_prompt(...) -> str`
- `generate_final_report_decision(..., command_runner=None) -> FinalReportDecision`

The generator should:

1. Call the summarizer boundary.
2. Parse JSON.
3. Validate decision.
4. Return safe fallback decision if parsing/validation fails.

**Step 3: Verify**

Use injected fake command runner. Do not call live model in unit tests.

---

## Task 4: Render v3 Discord and local reports

**Objective:** Separate user-facing Discord report from full local evidence report.

**Files:**
- Modify: `src/runtime_architecture_v2/final_report_v3.py`
- Modify: `tests/test_runtime_architecture_v2_final_report_v3.py`

**Step 1: RED tests**

Discord renderer must satisfy:

- `len(report) <= 1600`
- contains `## 🎯 결론`, `## ✅ 합의안`, `## 🚀 다음 액션`, `## ⚠️ 리스크`, `## 🔍 검증`
- no model IDs such as `deepseek`, `qwen`, `glm`
- no Markdown tables
- no raw JSON
- no `Discord thread`, `runtime artifact`, `bullet 요약`, `표 렌더링` in decision sections

Local renderer must include:

- validated decision summary
- full team summaries
- valid specialist summaries
- full validation/model evidence section

**Step 2: Implement**

Add:

- `render_final_report_v3_discord(decision, *, title, validation_passed, fallback_used) -> str`
- `render_final_report_v3_local(decision, *, title, team_messages, specialist_summaries, evidence_lines) -> str`

**Step 3: Verify**

Run v3 tests.

---

## Task 5: Filter generic specialist outputs before summarization

**Objective:** Prevent meaningless specialist outputs from entering reports.

**Files:**
- Modify: `src/runtime_architecture_v2/final_report_v3.py`
- Modify: `tests/test_runtime_architecture_v2_final_report_v3.py`
- Possibly reuse helpers from `src/runtime_architecture_v2/multi_bot.py`

**Step 1: RED tests**

Inputs:

```text
composer는 정상 specialist 결과입니다.
sound-designer는 정상 specialist 결과입니다.
legal-reviewer: 법무 검토자는 약관 분리 고지가 필요하다고 봅니다.
```

Expected:

- composer/sound-designer generic outputs excluded from user-facing decision context.
- legal-reviewer retained.

**Step 2: Implement filter**

Add:

```python
filter_user_facing_specialist_summaries(...)
```

**Step 3: Verify**

Run v3 tests.

---

## Task 6: Connect v3 as an on-demand export behind a feature flag

**Objective:** Generate v3 artifacts only when an explicit report/export request is made, without bringing back automatic meeting-completion reports.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Step 1: RED tests**

Run `run_phase14_multi_bot_pilot` with injected summarizer output and assert:

- default meeting completion does not create/post `final_report_v3.md`.
- explicit on-demand report request creates `final_report_v3.md`.
- explicit on-demand report request creates `decision_summary.json`.
- Discord uses v3 renderer only for the requested report/export.
- Existing `final_report_v2.md` may still exist for compatibility.

**Step 2: Implement feature flag**

Add parameter/config:

```python
use_final_report_v3: bool = True
```

Keep `False` path for emergency rollback during transition.

**Step 3: Verify**

Run Phase 14 tests.

---

## Task 7: Make v3 the requested Discord report/export

**Objective:** Stop automatic v2 model-evidence-heavy final reports and use v3 only when the user explicitly requests a report/export.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Step 1: RED tests**

With a meeting source containing audio consent, coupon, legal, and UX risks, assert the default Discord meeting thread:

- has no automatic final message
- has no `# 📋`, `## 🎯 결론`, `## ✅ 합의안`

Then assert an explicit on-demand report request produces a Discord report that:

- conclusion is agenda-specific
- agreements mention consent/coupon/UX
- actions include owner prefixes such as `기술팀:` and `법무/검증:`
- risks do not say `리스크 없음`
- no model-by-model evidence lines
- <= 1600 chars

**Step 2: Implement switch**

Use `render_final_report_v3_discord()` only for explicit report/export requests, not for the default meeting completion path.

**Step 3: Verify**

Run targeted Phase 14 tests.

---

## Task 8: Update documentation and run live Discord audit

**Objective:** Document the new report contract and prove it in Discord.

**Files:**
- Modify: `docs/user-guides/ai-agent-meeting-readme.md`
- Optional: update `gateway/meeting-trigger` skill references if reusable checklist changes.

**Step 1: Update docs**

Document:

- AI summarizer + schema validator.
- Discord report is short decision document.
- Local artifact keeps full evidence.
- `final_report_v3.md` and `decision_summary.json` locations.

**Step 2: Run verification**

Commands:

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_final_report_v3.py tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase13_pilot.py tests/test_runtime_architecture_v2_workers.py -q
PYTHONPATH=src python3 -m pytest tests/ -q --ignore=tests/test_runtime_architecture_v2_opencode_live_smoke.py --ignore=tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py --ignore=tests/test_runtime_architecture_v2_phase30_meeting_e2e.py --ignore=tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py --ignore=tests/test_runtime_smoke_packet.py
```

Then run a live Discord smoke and read the actual thread body.

Live audit must confirm:

- default meeting has no automatic final report/checkpoint
- explicit on-demand report feels like a decision report, not a system log
- conclusion is agenda-specific
- agreements/actions/risks are derived from meeting content
- no `Discord thread`, `runtime artifact`, `bullet 요약`, `표 렌더링` in decision sections
- evidence is summarized in one line
- message length <= 1600 chars

---

## Rollback Strategy

Keep v2 local artifact generation during rollout. If v3 summarizer fails in live use:

1. Post safe fallback v3 decision report to Discord.
2. Keep v2 full report local-only for debugging.
3. Do not post v2 model-evidence-heavy report as user-facing final message.

---

## Completion Criteria

Done only when all are true:

- `FinalReportDecision` schema/parser/validator covered by tests.
- AI summarizer prompt and injected fake summarizer covered by tests.
- Discord renderer produces <= 1600-char user-facing decision report.
- Local renderer stores full evidence.
- Phase 14 pipeline generates `final_report_v3.md` and `decision_summary.json`.
- Default meeting completion does not post any automatic final message. On-demand report request produces v3 report.
- Live Discord audit passes by reading the actual thread body.
- Broad pytest suite passes.
- Docs updated.

Remaining stages after this plan is accepted:

1. Implement Task 1-2 — schema, parser, validator.
2. Implement Task 3 — AI summarizer adapter.
3. Implement Task 4-5 — v3 renderers and specialist filtering.
4. Implement Task 6-7 — on-demand export integration and requested report rendering.
5. Implement Task 8 — docs, broad tests, live Discord audit, commit/push.
