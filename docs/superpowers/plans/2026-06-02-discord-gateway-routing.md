# Discord Gateway Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small, tested gateway routing layer that separates parent-channel task starts from same-thread user-decision resumes.

**Architecture:** Keep routing as a pure TypeScript function so the Discord runtime and the OpenClaw plugin integration can share the same behavior. The runtime calls the router before invoking `CompanyOrchestrator.runUserRequest(...)` or `CompanyOrchestrator.resumeFromUserDecision(...)`.

**Tech Stack:** TypeScript, Node test runner, discord.js runtime harness, SQLite-backed AI_Agent orchestrator.

---

### Task 1: Message Routing Policy

**Files:**
- Create: `src/discord/messageRouter.ts`
- Create: `tests/messageRouter.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
test("parent project channel starts a task", () => {
  const action = routeDiscordMessage({
    authorBot: false,
    channelId: "parent-1",
    content: "make a music video idea",
    isThread: false,
  }, { projectChannelIds: new Set(["parent-1"]) });

  assert.deepEqual(action, {
    kind: "start_task",
    parentChannelId: "parent-1",
    userRequest: "make a music video idea",
  });
});
```

- [ ] **Step 2: Run router tests and verify failure**

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/messageRouter.test.ts
```

Expected: import/module failure because `src/discord/messageRouter.ts` does not exist.

- [ ] **Step 3: Implement router**

```ts
export function routeDiscordMessage(...)
```

- [ ] **Step 4: Run router tests and verify pass**

Expected: all router tests pass.

### Task 2: Runtime Hook

**Files:**
- Modify: `src/runtime.ts`

- [ ] **Step 1: Replace inline message checks**

Use `routeDiscordMessage(...)` to select one of:

- `start_task`
- `resume_task`
- `ignore`

- [ ] **Step 2: Wire resume**

For `resume_task`, call:

```ts
await orchestrator.resumeFromUserDecision({
  threadId: action.threadId,
  userDecision: action.userDecision,
});
```

- [ ] **Step 3: Run full test suite**

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/*.test.ts
```

Expected: all tests pass.

### Task 3: Handoff And Git

**Files:**
- Modify: `docs/SESSION_HANDOFF.md`

- [ ] **Step 1: Update handoff**

Record:

- new routing policy file
- runtime resume hook
- test results
- next recommended step: connect the same policy into `openclaw-plugins/inter-agent-orchestration`

- [ ] **Step 2: Commit and push**

```powershell
git add src/discord/messageRouter.ts tests/messageRouter.test.ts src/runtime.ts docs/SESSION_HANDOFF.md docs/superpowers/plans/2026-06-02-discord-gateway-routing.md
git commit -m "feat: route discord thread resume events"
git push
```

