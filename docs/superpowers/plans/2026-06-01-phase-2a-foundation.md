# Phase 2-A Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 2-A foundation for same-thread OpenClaw/Hermes collaboration: routing, verdicts, storage, escalation, and resume-ready state.

**Architecture:** Keep the existing `CompanyOrchestrator` as the core state machine and add small focused modules for team routing, verdict parsing, approval records, and repeat-issue escalation. The standalone Discord runtime remains a development harness; production integration still targets an OpenClaw local plugin once it is found and copied into this repo.

**Tech Stack:** TypeScript on Node 24, `node:sqlite` `DatabaseSync`, `node:test`, Discord.js harness, OpenClaw/Hermes CLI adapters.

---

## File Structure

- Modify `src/types.ts`: add team route, new Hermes verdict enum, storage record types, and config fields.
- Create `src/routing.ts`: classify requests into `content`, `art`, `tech`, `marketing`, or `executive`.
- Modify `src/db.ts`: add route field and long-term tables `lore_entries`, `brand_decisions`, `approval_records`.
- Modify `src/executors/hermesCliExecutor.ts`: parse the new verdict enum while preserving legacy compatibility.
- Modify `src/orchestrator.ts`: store route, use new verdict names, track repeat unresolved issues, and keep thread resume requirements explicit.
- Modify `src/config.ts`: add `AI_AGENT_HERMES_TIMEOUT_SECONDS`, `AI_AGENT_DEBUG_MENTIONS`, and model routing env fields.
- Create `tests/routing.test.ts`: test deterministic route classification.
- Modify `tests/discordDelivery.test.ts`: add verdict parser compatibility tests.
- Modify `tests/orchestrator.test.ts`: add route storage, partial agreement, repeat escalation, and same-thread policy tests.
- Modify `docs/SESSION_HANDOFF.md`: record completed implementation progress after each task.

## Task 1: Team Routing

**Files:**
- Modify: `src/types.ts`
- Create: `src/routing.ts`
- Test: `tests/routing.test.ts`

- [ ] **Step 1: Add route type to `src/types.ts`**

Add:

```ts
export type TeamRoute = "content" | "art" | "tech" | "marketing" | "executive";
```

Add `teamRoute: TeamRoute;` to `TaskRecord`.

Add `teamRoute?: TeamRoute;` to `AiAgentDatabase.createTask` input in Task 2.

- [ ] **Step 2: Write failing route tests**

Create `tests/routing.test.ts`:

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { classifyTeamRoute } from "../src/routing.ts";

test("classifies content planning requests", () => {
  assert.equal(classifyTeamRoute("뮤직비디오 오프닝 장면 아이디어를 만들어줘"), "content");
});

test("classifies art direction requests", () => {
  assert.equal(classifyTeamRoute("캐릭터 비주얼 컨셉아트 색감과 의상 방향을 제안해줘"), "art");
});

test("classifies technical implementation requests", () => {
  assert.equal(classifyTeamRoute("Discord bot API와 Unreal VFX 구현 계획을 짜줘"), "tech");
});

test("classifies marketing requests", () => {
  assert.equal(classifyTeamRoute("쇼츠 제목 썸네일 SNS 카피를 제안해줘"), "marketing");
});

test("classifies executive risk requests", () => {
  assert.equal(classifyTeamRoute("예산 법무 IP 브랜드 리스크를 검토해줘"), "executive");
});

test("defaults ambiguous requests to content", () => {
  assert.equal(classifyTeamRoute("새 프로젝트 아이디어 회의하자"), "content");
});
```

- [ ] **Step 3: Run route tests to verify failure**

Run:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/routing.test.ts
```

Expected: FAIL because `src/routing.ts` does not exist.

- [ ] **Step 4: Implement `src/routing.ts`**

Create:

```ts
import type { TeamRoute } from "./types.ts";

const routePatterns: Array<{ route: TeamRoute; patterns: RegExp[] }> = [
  {
    route: "executive",
    patterns: [/예산|법무|계약|IP|저작권|상표|브랜드\s*리스크|외부\s*공개|승인|우선순위|리스크/i],
  },
  {
    route: "tech",
    patterns: [/코드|구현|API|CLI|서버|Discord|봇|Unreal|언리얼|VFX|자동화|n8n|Make|성능|보안/i],
  },
  {
    route: "art",
    patterns: [/캐릭터|비주얼|컨셉아트|색감|실루엣|의상|소품|공간\s*미술|아트|디자인/i],
  },
  {
    route: "marketing",
    patterns: [/마케팅|제목|썸네일|쇼츠|숏폼|SNS|카피|팬덤|클릭률|포지셔닝/i],
  },
  {
    route: "content",
    patterns: [/뮤직비디오|뮤비|영상|스토리|감정선|오프닝|엔딩|후킹|기획|시청자/i],
  },
];

export function classifyTeamRoute(userRequest: string): TeamRoute {
  for (const { route, patterns } of routePatterns) {
    if (patterns.some((pattern) => pattern.test(userRequest))) {
      return route;
    }
  }
  return "content";
}
```

- [ ] **Step 5: Run route tests to verify pass**

Run the same route test command.

Expected: PASS.

## Task 2: DB Schema For Route And Long-Term Tables

**Files:**
- Modify: `src/db.ts`
- Modify: `src/types.ts`
- Test: `tests/orchestrator.test.ts`

- [ ] **Step 1: Add failing DB assertions**

In `tests/orchestrator.test.ts`, add a test:

```ts
test("task stores team route and initializes long-term tables", () => {
  const db = new AiAgentDatabase();
  const task = db.createTask({
    id: "task-route-1",
    projectChannelId: "parent-1",
    threadId: "thread-1",
    userRequest: "기술 구현안을 만들어줘",
    teamRoute: "tech",
  });

  assert.equal(task.teamRoute, "tech");
  assert.equal(db.getTask("task-route-1")?.teamRoute, "tech");

  db.db.prepare("INSERT INTO lore_entries (id, key, value, created_at) VALUES (?, ?, ?, ?)").run(
    "lore-1",
    "character.main",
    "main character lore",
    "2026-06-01T00:00:00.000Z",
  );
  db.db.prepare("INSERT INTO brand_decisions (id, topic, decision, created_at) VALUES (?, ?, ?, ?)").run(
    "brand-1",
    "tone",
    "premium and playful",
    "2026-06-01T00:00:00.000Z",
  );
  db.db.prepare("INSERT INTO approval_records (id, task_id, approval_type, decision, created_at) VALUES (?, ?, ?, ?, ?)").run(
    "approval-1",
    task.id,
    "brand",
    "approved",
    "2026-06-01T00:00:00.000Z",
  );

  db.close();
});
```

- [ ] **Step 2: Run orchestrator tests to verify failure**

Run:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/orchestrator.test.ts
```

Expected: FAIL because `teamRoute` and the new tables do not exist.

- [ ] **Step 3: Implement DB changes**

Modify `TaskRecord` in `src/types.ts`:

```ts
export interface TaskRecord {
  id: string;
  projectChannelId: string;
  threadId: string;
  userRequest: string;
  teamRoute: TeamRoute;
  status: TaskStatus;
  createdAt: string;
  updatedAt: string;
}
```

Modify `createTask` input in `src/db.ts`:

```ts
createTask(input: {
  id: string;
  projectChannelId: string;
  threadId: string;
  userRequest: string;
  teamRoute?: TeamRoute;
  now?: string;
}): TaskRecord
```

Set:

```ts
teamRoute: input.teamRoute ?? "content",
```

Insert and select `team_route` in the `tasks` table. Migration should be idempotent:

```ts
try {
  this.db.exec("ALTER TABLE tasks ADD COLUMN team_route TEXT NOT NULL DEFAULT 'content'");
} catch (error) {
  if (!(error instanceof Error) || !/duplicate column/i.test(error.message)) {
    throw error;
  }
}
```

Add tables:

```sql
CREATE TABLE IF NOT EXISTS lore_entries (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brand_decisions (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  decision TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_records (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  approval_type TEXT NOT NULL,
  decision TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Run orchestrator tests to verify pass**

Expected: PASS.

## Task 3: New Hermes Verdicts

**Files:**
- Modify: `src/types.ts`
- Modify: `src/executors/hermesCliExecutor.ts`
- Modify: `src/orchestrator.ts`
- Test: `tests/discordDelivery.test.ts`

- [ ] **Step 1: Write failing parser tests**

Add to `tests/discordDelivery.test.ts`:

```ts
test("Hermes verdict parser supports Phase 2-A Korean and English verdicts", () => {
  assert.equal(parseVerdict("Verdict: agree"), "agree");
  assert.equal(parseVerdict("Verdict: partial_agree"), "partial_agree");
  assert.equal(parseVerdict("Verdict: disagree"), "disagree");
  assert.equal(parseVerdict("Verdict: needs_user_decision"), "needs_user_decision");
  assert.equal(parseVerdict("판정: 동의"), "agree");
  assert.equal(parseVerdict("판정: 부분동의"), "partial_agree");
  assert.equal(parseVerdict("판정: 비동의"), "disagree");
  assert.equal(parseVerdict("판정: 사용자결정필요"), "needs_user_decision");
});

test("legacy agree_with_changes maps to partial agreement", () => {
  assert.equal(parseVerdict("Verdict: agree_with_changes"), "partial_agree");
});
```

- [ ] **Step 2: Run parser tests to verify failure**

Run:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/discordDelivery.test.ts
```

Expected: FAIL because `partial_agree` is not yet supported.

- [ ] **Step 3: Update verdict type**

In `src/types.ts`, replace:

```ts
export type ReviewerVerdict = "agree" | "agree_with_changes" | "disagree" | "needs_user_decision";
```

with:

```ts
export type ReviewerVerdict = "agree" | "partial_agree" | "disagree" | "needs_user_decision";
```

- [ ] **Step 4: Update `parseVerdict`**

Use:

```ts
export function parseVerdict(content: string): ReviewerVerdict {
  const normalized = content.toLowerCase();
  const english = normalized.match(
    /\bverdict\s*:\s*(partial_agree|agree_with_changes|needs_user_decision|disagree|agree)\b/i,
  );
  if (english?.[1]) {
    return normalizeVerdict(english[1]);
  }

  const korean = content.match(/(?:판정|Verdict)\s*:\s*(사용자결정필요|부분동의|비동의|동의)/i);
  if (korean?.[1]) {
    return normalizeVerdict(korean[1]);
  }

  return "partial_agree";
}

function normalizeVerdict(value: string): ReviewerVerdict {
  switch (value.toLowerCase()) {
    case "agree":
    case "동의":
      return "agree";
    case "partial_agree":
    case "agree_with_changes":
    case "부분동의":
      return "partial_agree";
    case "disagree":
    case "비동의":
      return "disagree";
    case "needs_user_decision":
    case "사용자결정필요":
      return "needs_user_decision";
    default:
      return "partial_agree";
  }
}
```

- [ ] **Step 5: Update orchestrator branch**

In `src/orchestrator.ts`, replace `agree_with_changes` handling with `partial_agree`.

- [ ] **Step 6: Run all tests**

Run:

```powershell
& 'C:\Users\KBM\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests/*.test.ts
```

Expected: PASS.

## Task 4: Repeat-Issue Escalation

**Files:**
- Modify: `src/orchestrator.ts`
- Modify: `src/types.ts`
- Test: `tests/orchestrator.test.ts`

- [ ] **Step 1: Add failing repeat escalation test**

Add a test where the reviewer returns the same `disagree` review three times and owner keeps drafting different text. Expected final status: `waiting_for_user`, escalation includes `repeated_unresolved_issue`.

- [ ] **Step 2: Add config field**

In `OrchestratorConfig`, add:

```ts
repeatIssueThreshold: number;
```

In constructor default:

```ts
this.config = {
  maxRounds: deps.config?.maxRounds ?? 4,
  repeatIssueThreshold: deps.config?.repeatIssueThreshold ?? 3,
};
```

- [ ] **Step 3: Implement repeat signature tracking**

In `runUserRequest`, add:

```ts
const unresolvedIssueCounts = new Map<string, number>();
```

After review:

```ts
if (reviewerVerdict === "disagree" || reviewerVerdict === "needs_user_decision") {
  const signature = normalizeIssueSignature(review);
  const nextCount = (unresolvedIssueCounts.get(signature) ?? 0) + 1;
  unresolvedIssueCounts.set(signature, nextCount);
  if (nextCount >= this.config.repeatIssueThreshold) {
    escalationReasons.push("repeated_unresolved_issue");
  }
}
```

Create helper:

```ts
function normalizeIssueSignature(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim().slice(0, 240);
}
```

- [ ] **Step 4: Run orchestrator tests**

Expected: PASS.

## Task 5: Runtime Config For Timeout, Debug Mentions, And Models

**Files:**
- Modify: `src/config.ts`
- Modify: `.env.example`
- Test: add to existing config tests if present, otherwise add `tests/config.test.ts`

- [ ] **Step 1: Add failing config tests**

Create `tests/config.test.ts`:

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { loadRuntimeConfig } from "../src/config.ts";

test("loads Phase 2-A timeout and debug mention config", () => {
  const config = loadRuntimeConfig({
    DISCORD_BOT_TOKEN: "discord-token",
    AI_AGENT_PROJECT_CHANNEL_IDS: "parent-1",
    AI_AGENT_HERMES_TIMEOUT_SECONDS: "123",
    AI_AGENT_DEBUG_MENTIONS: "true",
  });

  assert.equal(config.hermesTimeoutSeconds, 123);
  assert.equal(config.debugMentions, true);
});

test("loads team model routing config", () => {
  const config = loadRuntimeConfig({
    DISCORD_BOT_TOKEN: "discord-token",
    AI_AGENT_PROJECT_CHANNEL_IDS: "parent-1",
    AI_AGENT_OPENCLAW_CONTENT_MODEL: "openclaw-content",
    AI_AGENT_HERMES_TECH_MODEL: "hermes-tech",
  });

  assert.equal(config.modelRouting.openclaw.content, "openclaw-content");
  assert.equal(config.modelRouting.hermes.tech, "hermes-tech");
});
```

- [ ] **Step 2: Implement config fields**

Add to `RuntimeConfig`:

```ts
hermesTimeoutSeconds: number;
debugMentions: boolean;
modelRouting: {
  openclaw: Partial<Record<TeamRoute, string>>;
  hermes: Partial<Record<TeamRoute, string>>;
};
```

Add boolean parser:

```ts
function parseBoolean(value: string | undefined, fallback: boolean): boolean {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return fallback;
  return ["1", "true", "yes", "on"].includes(normalized);
}
```

- [ ] **Step 3: Update `.env.example`**

Add:

```text
AI_AGENT_HERMES_TIMEOUT_SECONDS=600
AI_AGENT_DEBUG_MENTIONS=false
AI_AGENT_OPENCLAW_CONTENT_MODEL=
AI_AGENT_OPENCLAW_ART_MODEL=
AI_AGENT_OPENCLAW_TECH_MODEL=
AI_AGENT_OPENCLAW_MARKETING_MODEL=
AI_AGENT_OPENCLAW_EXECUTIVE_MODEL=
AI_AGENT_HERMES_CONTENT_MODEL=
AI_AGENT_HERMES_ART_MODEL=
AI_AGENT_HERMES_TECH_MODEL=
AI_AGENT_HERMES_MARKETING_MODEL=
AI_AGENT_HERMES_EXECUTIVE_MODEL=
```

- [ ] **Step 4: Run config tests and all tests**

Expected: PASS.

## Task 6: Session Handoff Update

**Files:**
- Modify: `docs/SESSION_HANDOFF.md`

- [ ] **Step 1: Add implementation progress section**

Add a section:

```md
## Phase 2-A Foundation Implementation Progress

- Team routing: complete/pending
- DB long-term tables: complete/pending
- New Hermes verdict enum: complete/pending
- Repeat issue escalation: complete/pending
- Config fields: complete/pending
```

- [ ] **Step 2: Record verification**

After all tests pass, update:

```md
Verification:
- Command: `<exact command>`
- Result: `tests N, pass N, fail 0`
```

## Self-Review

Spec coverage:

- Phase 2-A team routing: Tasks 1 and 5.
- Same-thread collaboration groundwork: Tasks 2, 3, and 4.
- Long-term table creation: Task 2.
- Timeout/debug/model config: Task 5.
- Handoff requirement: Task 6.

Known gaps intentionally deferred:

- Copying the actual OpenClaw local plugin is blocked until WSL is accessible.
- Full Discord polling implementation is deferred until plugin source is found.
- Full persona layer remains Phase 2-C.
- Full Research Layer remains Phase 2-D, though minimal search can be added later.
