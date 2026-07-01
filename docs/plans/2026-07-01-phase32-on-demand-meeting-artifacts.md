# Phase 32 On-Demand Meeting Artifacts Plan

> **For Hermes:** Execute task-by-task with strict TDD. Do not continue patching automatic final-report content. The Phase 32 product decision is: meetings post only meeting discussion by default; summaries/reports/storage are explicit user-requested exports.

**Goal:** Remove automatic final report/checkpoint posting from the meeting flow and split all summaries, final reports, Notion exports, and Second Brain exports into on-demand actions.

**Architecture:** Runtime v2 still runs the Discord meeting, team-lead rounds, specialists, validation, and local evidence collection. The Discord thread receives only visible meeting messages by default. A later user request resolves the thread/meeting_run, loads local artifacts, and generates the requested summary/export through a separate on-demand path.

**Tech Stack:** Python dataclasses, Runtime Architecture v2 Phase 14 multi-bot pipeline, Discord projection, local runtime artifacts, pytest, live Discord body audit.

---

## Product Decision

Automatic meeting completion output is removed.

Default meeting flow:

```text
Discord meeting request
→ thread created
→ Round 1 team-lead messages
→ Round 2 team-lead messages
→ specialist/validation/evidence stored locally
→ no automatic final report
→ no automatic checkpoint
```

On-demand flow:

```text
User: 요약해줘 / 최종보고서로 정리해줘 / Notion에 저장해줘 / 세컨드브레인에 넣어줘
→ resolve latest thread/meeting_run
→ load local artifacts
→ generate requested artifact/export
→ post/save only because user explicitly requested it
```

## Non-Goals

- Do not auto-post a “회의 체크포인트”.
- Do not auto-post Final Report v2 or v3 after every meeting.
- Do not auto-save to Notion or Second Brain.
- Do not delete raw evidence or worker outputs needed for later exports.

---

## Phase 32 Acceptance Criteria

### Default meeting thread

A normal meeting must produce only visible meeting discussion messages:

```text
MSG01 대표 Round 1
MSG02 콘텐츠 Round 1
MSG03 아트 Round 1
MSG04 기술 Round 1
MSG05 마케팅 Round 1
MSG06 품질관리 Round 1
MSG07 대표 Round 2
MSG08 콘텐츠 Round 2
MSG09 아트 Round 2
MSG10 기술 Round 2
MSG11 마케팅 Round 2
MSG12 품질관리 Round 2
```

There must be no automatic:

```text
MSG13 대표 최종보고서
# 📋
## 🎯 결론
## ✅ 합의안
## 🚀 다음 액션
회의 체크포인트
```

### Local artifacts

Local artifacts must preserve enough source data for future on-demand exports:

```text
meeting_run.json
worker_outputs/
meeting transcript / session messages
specialist outputs
validation/model evidence
```

`final_report_v2.md` should not be treated as the default user-facing meeting output. It should either stop being generated automatically or be moved/renamed as a legacy debug artifact until v3 on-demand replaces it.

### Gateway summary

Gateway response may say the meeting completed and provide the thread id, but must not claim a final report was generated.

Allowed wording:

```text
회의가 완료되었습니다. thread에서 팀장 발언을 확인하세요.
필요하면 “요약해줘”, “최종보고서로 정리해줘”, “Notion에 저장해줘”라고 요청하세요.
```

Forbidden wording:

```text
최종보고서 생성 완료
합의서 생성 완료
Notion 저장 완료
Second Brain 저장 완료
```

---

## Stage 1 — Remove automatic Discord final report post

**Objective:** Stop creating/posting representative final report `BotMessage` at the end of Phase 14 meetings.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**TDD Steps:**
1. RED: update/add test asserting live Discord projection count is 12, not 13.
2. RED: assert no projected message contains `# 📋`, `## 🎯 결론`, `## ✅ 합의안`, or `## 🚀 다음 액션` during default meeting flow.
3. RED: assert last projected visible Discord message is the Round 2 validation/quality lead message.
4. GREEN: remove the automatic `final_report_msg = BotMessage(... msg_type="consensus" ...)` projection path.
5. Verify targeted pytest.

**Acceptance Criteria:**
- Default meeting thread has only team-lead round messages.
- No automatic final report/checkpoint in Discord.
- Existing team-lead round progression remains intact.

---

## Stage 2 — Stop treating `final_report_v2.md` as default meeting output

**Objective:** Ensure automatic meetings store source artifacts, not user-facing final reports.

**Files:**
- Modify: `src/runtime_architecture_v2/multi_bot.py`
- Modify: `tests/test_runtime_architecture_v2_phase14_multi_bot.py`

**TDD Steps:**
1. RED: assert default meeting output no longer requires `final_report_v2.md` as a primary artifact.
2. RED: assert source artifacts required for later export remain available.
3. GREEN: either stop writing `final_report_v2.md` automatically or move it under a clearly named legacy/debug path.
4. Verify targeted pytest.

**Acceptance Criteria:**
- Meeting source data remains reconstructable.
- User-facing report artifact is not auto-created as the default completion product.
- Legacy/debug compatibility is explicit if retained.

---

## Stage 3 — Adjust Gateway summary language

**Objective:** Make gateway response match the new UX: meeting completed, no automatic report.

**Files:**
- Modify: `src/runtime_architecture_v2/gateway_bridge.py`
- Modify: relevant gateway/phase tests.

**TDD Steps:**
1. RED: gateway meeting result summary must not contain `최종보고서 생성 완료` or equivalent.
2. RED: summary should mention thread id and optional follow-up commands.
3. GREEN: update summary builder.
4. Verify targeted tests.

**Acceptance Criteria:**
- Gateway does not claim a final report/checkpoint exists.
- User gets a concise next-step hint outside the meeting thread.

---

## Stage 4 — Update Final Report v3 plan to on-demand export

**Objective:** Align `Final Report v3` with Phase 32: v3 is not automatic meeting completion; it is an explicit export action.

**Files:**
- Modify: `docs/plans/2026-07-01-final-report-v3-ai-summary-schema.md`

**Required edits:**
- Replace “Make v3 the Discord final report” with “Make v3 the on-demand final report/export”.
- Replace “Discord final message uses v3 report” with “Default meeting thread has no automatic final message; v3 is generated only after explicit user request”.
- Replace live audit criteria to include both:
  1. default meeting has no auto report
  2. explicit on-demand report request generates v3

**Acceptance Criteria:**
- No plan text implies final report auto-post after every meeting.
- v3 generation is explicitly tied to user request.

---

## Stage 5 — Update user docs and meeting-trigger skill

**Objective:** Document the new default UX.

**Files:**
- Modify: `docs/user-guides/ai-agent-meeting-readme.md`
- Optional: patch `gateway/meeting-trigger` skill/reference after code is verified.

**TDD/Verification Steps:**
1. Update docs to state: meetings auto-post only team-lead discussion.
2. Add examples:
   - `요약해줘`
   - `최종보고서로 정리해줘`
   - `Notion에 저장해줘`
   - `세컨드브레인에 넣어줘`
3. Remove “final report automatically posted” language.
4. Verify Markdown fences and key phrases.

**Acceptance Criteria:**
- Docs match implementation.
- User can understand that report/storage are explicit commands.

---

## Stage 6 — Live Discord audit

**Objective:** Prove the default meeting thread is discussion-only.

**Steps:**
1. Run a live Discord smoke with a fresh, non-final-report prompt.
2. Fetch thread messages through Discord API.
3. Assert:
   - message count is 12 for six visible bots × two rounds
   - no `# 📋` / `## 🎯 결론` / `## ✅ 합의안`
   - last message is quality/validation Round 2
   - Round 1 and Round 2 differ
   - no checkpoint/final-report message appears
4. Run targeted and broad pytest.

**Acceptance Criteria:**
- Live Discord body audit confirms no automatic report/checkpoint.
- Local artifacts still exist for later on-demand report generation.

---

## Stage 7 — On-demand exports follow-up plan

**Objective:** Start the next implementation tranche only after Phase 32 removes automatic reports.

Follow-up features:

```text
요약해줘 → short summary
최종보고서로 정리해줘 → Final Report v3
합의서로 정리해줘 → agreement/action document
Notion에 저장해줘 → Notion export
세컨드브레인에 넣어줘 → Second Brain note
할 일로 만들어줘 → action item extraction
```

This is a separate implementation after the default meeting flow is clean.

---

## Verification Commands

Targeted:

```bash
PYTHONPATH=src python3 -m pytest tests/test_runtime_architecture_v2_phase14_multi_bot.py tests/test_runtime_architecture_v2_phase13_pilot.py tests/test_runtime_architecture_v2_workers.py -q
```

Broad non-live/e2e:

```bash
PYTHONPATH=src python3 -m pytest tests/ -q \
  --ignore=tests/test_runtime_architecture_v2_opencode_live_smoke.py \
  --ignore=tests/test_runtime_architecture_v2_phase26_worker_boundary_smoke.py \
  --ignore=tests/test_runtime_architecture_v2_phase30_meeting_e2e.py \
  --ignore=tests/test_runtime_architecture_v2_phase29_live_pilot_runbook.py \
  --ignore=tests/test_runtime_smoke_packet.py
```

Markdown:

```bash
python3 - <<'PY'
from pathlib import Path
paths = [
    Path('docs/plans/2026-07-01-phase32-on-demand-meeting-artifacts.md'),
    Path('docs/plans/2026-07-01-final-report-v3-ai-summary-schema.md'),
    Path('docs/user-guides/ai-agent-meeting-readme.md'),
]
for p in paths:
    if p.exists():
        t = p.read_text(encoding='utf-8')
        assert t.strip(), p
        assert t.count(chr(96) * 3) % 2 == 0, p
print('markdown verification passed')
PY
```

---

## Remaining Stages

1. Stage 1 — remove automatic Discord final report post.
2. Stage 2 — stop treating `final_report_v2.md` as default output.
3. Stage 3 — adjust Gateway summary language.
4. Stage 4 — update Final Report v3 plan to on-demand export.
5. Stage 5 — update user docs and meeting-trigger guidance.
6. Stage 6 — live Discord audit: exactly 12 meeting messages, no report/checkpoint.
7. Stage 7 — plan/implement on-demand exports after default flow is clean.
