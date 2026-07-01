# Meeting Round Progression and Quality-Language Fix Plan

## Goal
Make the visible Discord meeting read like a real two-round meeting instead of repeating the same statements.

## Acceptance Criteria

1. Round 2 live prompt includes a compact Round 1 transcript.
2. Round 2 prompt explicitly forbids repeating Round 1 and requires:
   - one agreement with another lead,
   - one supplement/rebuttal,
   - one final-agreement condition.
3. Quality lead prompt uses user-facing quality language and maps internal terms:
   - `worker_execution_failed` → `실패 상태로 표시`,
   - `placeholder output` → `임시/빈 응답`,
   - `regression test`/`회귀 테스트` → `재발 방지 검증`,
   - `evidence artifact` → `검증 기록`.
4. A smoke command must use round-specific responses, not one fixed role response for both rounds.
5. Discord verification must read the actual thread body and confirm Round 1 and Round 2 differ for the same role, especially 품질관리팀장.

## Implementation Steps

1. Add RED tests that capture live prompts for Round 1 and Round 2.
2. Add `previous_messages` plumbing from `run_meeting_phase()` → `_generate_bot_content()` → `_live_bot_content()`.
3. Extract prompt construction to a helper so it can be tested directly through captured command prompts.
4. Add a compact transcript formatter for previous messages.
5. Add quality-lead user-facing language rules to prompt construction.
6. Update README and meeting-trigger skill checklist.
7. Run targeted tests, broad tests, Markdown verification, and a live Discord smoke with round-specific outputs.
