# Plugin Verdict Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the imported OpenClaw plugin reviewer verdict values with AI_Agent Phase 2-A source of truth.

**Architecture:** Keep backward compatibility for existing plugin outputs that say `agree_with_changes`, but normalize new parsed verdicts to the Phase 2-A standard `partial_agree`. This keeps the OpenClaw plugin and AI_Agent core using the same enum.

**Tech Stack:** JavaScript OpenClaw plugin, Node test runner, AI_Agent TypeScript tests.

---

### Task 1: Plugin Verdict Parser Test

**Files:**
- Modify: `openclaw-plugins/inter-agent-orchestration/test/reviewer-mode.test.js`

- [ ] **Step 1: Add failing parser assertions**

Add a test asserting:

- `Verdict: partial_agree` parses as `partial_agree`
- legacy `Verdict: agree_with_changes` also parses as `partial_agree`
- ambiguous revision/recommendation text falls back to `partial_agree`

- [ ] **Step 2: Run selected plugin test and verify failure**

Expected before implementation: failure because `partial_agree` is not parsed and legacy values still return `agree_with_changes`.

### Task 2: Plugin Parser Implementation

**Files:**
- Modify: `openclaw-plugins/inter-agent-orchestration/index.js`

- [ ] **Step 1: Update `parseReviewerVerdict(...)`**

Return only:

- `agree`
- `partial_agree`
- `disagree`
- `needs_user_decision`

Map legacy `agree_with_changes` to `partial_agree`.

- [ ] **Step 2: Run selected plugin tests**

Expected: selected parser tests pass.

### Task 3: Handoff, Full Tests, Git

**Files:**
- Modify: `docs/SESSION_HANDOFF.md`

- [ ] **Step 1: Update handoff**

Record verdict alignment and verification.

- [ ] **Step 2: Run AI_Agent suite**

Expected: all tests pass.

- [ ] **Step 3: Commit and push**

Commit message:

```text
fix: align openclaw plugin verdict values
```

