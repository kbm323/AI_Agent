# Plugin Same-Thread Violation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenClaw plugin entrypoint for Hermes same-thread violations and align it with AI_Agent's `waiting_for_user` policy.

**Architecture:** Keep the plugin self-contained: when a gateway signal says Hermes replied in the wrong thread, the plugin posts an escalation in the expected task thread, persists the task as `waiting_for_user`, and stores `failure_reason='hermes_wrong_thread'`. Resume then allows user-decision final synthesis even if no valid same-thread Hermes review exists.

**Tech Stack:** JavaScript OpenClaw plugin, SQLite via `node:sqlite`, Node test runner, AI_Agent TypeScript test suite.

---

### Task 1: Same-Thread Violation Test

**Files:**
- Modify: `openclaw-plugins/inter-agent-orchestration/test/reviewer-mode.test.js`

- [ ] **Step 1: Add failing test**

Assert that `recordHermesThreadViolation(...)`:

- posts only to the expected thread
- returns `waiting_for_user`
- stores task `status='waiting_for_user'`
- stores `failure_reason='hermes_wrong_thread'`
- records `owner_draft` and `escalation` turns

- [ ] **Step 2: Verify failure**

Run selected test and expect export/function-not-found failure.

### Task 2: Resume After Violation

**Files:**
- Modify: `openclaw-plugins/inter-agent-orchestration/test/reviewer-mode.test.js`
- Modify: `openclaw-plugins/inter-agent-orchestration/index.js`

- [ ] **Step 1: Extend test**

After recording a violation, call `resumeWaitingOrchestrationFromUserDecision(...)`.
Assert final synthesis is posted in the expected thread and task status becomes
`completed`.

- [ ] **Step 2: Implement minimal resume relaxation**

Allow resume when:

```text
task.failure_reason === "hermes_wrong_thread"
```

and an OpenClaw draft exists, even when same-thread reviews are empty.

### Task 3: Handoff And Git

**Files:**
- Modify: `docs/SESSION_HANDOFF.md`

- [ ] **Step 1: Update handoff**

Record same-thread violation entrypoint, test results, and current phase.

- [ ] **Step 2: Run tests**

Run:

```bash
node --test --test-name-pattern "Hermes same-thread violation" test/reviewer-mode.test.js
```

and:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/*.test.ts
```

- [ ] **Step 3: Commit and push**

Commit:

```text
feat: record openclaw plugin same-thread violations
```

