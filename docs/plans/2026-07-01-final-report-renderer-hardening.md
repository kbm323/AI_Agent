# Final Report Renderer Hardening Implementation Plan

> **For Hermes:** Implement directly with strict TDD: RED test first, smallest GREEN change, then targeted/full verification.

**Goal:** Make AI_Agent meeting final reports trustworthy and readable by fixing specialist output accuracy, separating Discord/local rendering, unifying evidence formatting, and making the conclusion decision-specific.

**Architecture:** Keep `final_report_v2.md` as the full local artifact with Markdown tables. Add a Discord-specific final-report renderer for the thread's last message using bullet lists and a single compact evidence code block because Discord does not render Markdown tables. Build both renderers from the same role/task/validation inputs so data stays consistent.

**Tech Stack:** Python, pytest, Runtime Architecture v2 `multi_bot.py`, worker adapter in `workers.py`, docs in `docs/user-guides/`.

---

## Task 1: Add RED tests for the reported failures

**Objective:** Prove the current implementation still fails the new acceptance criteria before changing production code.

**Files:**
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**Steps:**
1. Extend the final-report test to include `ui-ux-designer` in the trigger and fake worker outputs.
2. Assert `ui-ux-designer` appears with its own UX output and never reuses validation/audit phrasing.
3. Extend the live Discord projection test to assert the last posted final report contains no Markdown table rows like `| 팀장 |` or `| specialist |`.
4. Assert Discord evidence uses one code block containing both validation verdict and model lines.
5. Run the two focused tests and confirm they fail on current production code.

## Task 2: Implement shared report data + local artifact renderer

**Objective:** Preserve `final_report_v2.md` as the full table-based artifact while making specialist rows use the correct role output.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`

**Steps:**
1. Extract helper data builders for role summaries, specialist summaries, validation lines, and model evidence.
2. Keep local artifact tables for `팀장 핵심 의견` and `Specialist 투입`.
3. Keep `final_report` return value as the local artifact content for CLI/debug compatibility.
4. Run focused final-report test and confirm pass.

## Task 3: Add Discord-specific final-report renderer

**Objective:** Post a Discord-friendly report without Markdown tables and with compact evidence.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`

**Steps:**
1. Add `_build_discord_final_report(...)` from the same source data.
2. Render team/specialist summaries as bullets, not tables.
3. Render validation + model evidence inside one `text` code block.
4. Use this renderer only for the final Discord `BotMessage`; keep `final_report_v2.md` unchanged.
5. Run live projection focused test and confirm pass.

## Task 4: Strengthen worker prompt/output handling

**Objective:** Prevent specialist roles from collapsing into generic validation/audit text.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `src/runtime_architecture_v2/workers.py` if prompt delivery regressions appear

**Steps:**
1. Ensure each `WorkerTask.hermes_refs["prompt"]` includes the exact specialist role and role-specific instruction.
2. Ensure injected/legacy command runners receive the prompt so tests can verify role-specific outputs.
3. Run `tests/test_runtime_architecture_v2_phase14_multi_bot.py::test_phase14_final_report_summarizes_evidence_and_fallbacks`.

## Task 5: Docs and verification

**Objective:** Keep the user guide aligned with actual Discord/local behavior.

**Files:**
- Modify: `docs/user-guides/ai-agent-meeting-readme.md`

**Steps:**
1. Document that Discord uses bullet/code-block summary while local artifact keeps tables.
2. Run Markdown verification for code fence balance and required section names.
3. Run targeted pytest: `PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase13_pilot.py tests/test_runtime_architecture_v2_workers.py -q`.
4. Run broad suite excluding live smoke/e2e tests.
5. Run a real Discord REST smoke and read the last thread message content.
6. Commit and push.
