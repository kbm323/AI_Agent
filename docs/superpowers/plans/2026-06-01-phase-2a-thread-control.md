# Phase 2-A Thread Control Plan

Generated: 2026-06-01

## Goal

Add the core thread-control behavior needed before wiring the Discord gateway:

```text
same Discord thread only -> wrong Hermes thread escalates
waiting task -> user reply resumes the same task -> final synthesis in same thread
```

## Scope

- Add a database lookup by Discord thread id.
- Add an orchestrator entrypoint for user-decision resume.
- Add an orchestrator entrypoint for Hermes wrong-thread detection.
- Keep all output in the original task thread.
- Update tests and handoff documentation.

## Out of Scope

- Full Discord Gateway polling implementation.
- Actual OpenClaw local plugin import.
- WSL plugin discovery, because the current Codex Desktop session cannot see a
  WSL distribution.

## Implementation Steps

1. Add regression tests in `tests/orchestrator.test.ts`.
   - A waiting task can resume from a user decision in the same thread.
   - A Hermes reply observed in a different thread marks the task as
     `waiting_for_user`.
2. Extend `AiAgentDatabase`.
   - Add `getTaskByThreadId(threadId)`.
3. Extend `CompanyOrchestrator`.
   - Add `resumeFromUserDecision({ threadId, userDecision })`.
   - Add `recordHermesThreadViolation({ taskId, observedThreadId })`.
   - Reuse stored owner draft and Hermes review for final synthesis.
4. Update `docs/SESSION_HANDOFF.md`.
5. Run the Node test suite.
6. Commit and push to `https://github.com/kbm323/AI_Agent`.

## Verification

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/*.test.ts
```

