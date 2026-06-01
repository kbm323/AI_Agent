import test from "node:test";
import assert from "node:assert/strict";
import { AiAgentDatabase } from "../src/db.ts";
import { CompanyOrchestrator } from "../src/orchestrator.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor } from "../src/types.ts";

function createFakeDiscord(): DiscordDelivery & { parentPosts: string[]; threadPosts: Array<{ threadId: string; content: string; fullContent?: string }> } {
  return {
    parentPosts: [],
    threadPosts: [],
    async createThread() {
      return { threadId: "thread-1", url: "https://discord.test/thread-1" };
    },
    async postParent(input) {
      this.parentPosts.push(input.content);
    },
    async postThread(input) {
      this.threadPosts.push(input);
    },
  };
}

test("MVP task runs in thread and stores full owner/reviewer/final outputs", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      return "Owner draft: build a three-scene music video concept with a clear hook.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree",
        content: "Hermes review: agree. The OpenClaw draft has a clear hook and feasible scene structure.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      return `Use this final plan.\n\nDraft used:\n${draft}\n\nReview used:\n${review}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-1",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "music-video" },
    userRequest: "뮤직비디오 오프닝 아이디어를 만들어줘.",
  });

  assert.equal(result.status, "finalized");
  assert.equal(result.task.teamRoute, "content");
  assert.deepEqual(discord.parentPosts, ["Agent discussion started -> https://discord.test/thread-1"]);
  assert.equal(discord.threadPosts.every((post) => post.threadId === "thread-1"), true);
  assert.equal(discord.parentPosts.join("\n").includes("Owner draft"), false);

  const turns = db.getTurns("task-1");
  assert.deepEqual(turns.map((turn) => turn.kind), ["owner_draft", "review_request", "review", "final_synthesis"]);
  assert.match(turns[1].content, /Captured OpenClaw draft:\nOwner draft:/);
  assert.match(turns[2].content, /clear hook/);
  assert.match(turns[3].content, /Review used:/);
  db.close();
});

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

test("missing owner draft stops before Hermes request", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft({ userRequest }) {
      return userRequest;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      throw new Error("Hermes should not be called without a usable draft");
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should not be called without a usable draft");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-2",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1" },
    userRequest: "랜덤 테스트 요청",
  });

  assert.equal(result.status, "failed");
  assert.deepEqual(db.getTurns("task-2").map((turn) => turn.kind), ["escalation"]);
  assert.match(discord.threadPosts[0].content, /OpenClaw draft capture failed/);
  db.close();
});

test("configurable escalation policy pauses the task before final synthesis", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      return "Owner draft: publish the final video to the public channel.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "needs_user_decision",
        content: "Hermes review: public release requires user approval.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should wait for user approval");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-3",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1" },
    userRequest: "완성본을 외부 공개해줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.ok(result.escalationReasons.includes("brand_or_public_release"));
  assert.ok(result.escalationReasons.includes("reviewer_requested_user_decision"));
  assert.deepEqual(db.getTurns("task-3").map((turn) => turn.kind), ["owner_draft", "review_request", "review", "escalation"]);
  db.close();
});

test("partial_agree goes to final synthesis without wasting max rounds", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  let ownerCalls = 0;
  const owner: OwnerExecutor = {
    async createDraft() {
      ownerCalls += 1;
      return "Owner draft: 후보 A, 후보 B, 후보 C.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "partial_agree",
        content: "Hermes review: 후보 A/B/C를 언급했고, 후보 B를 보완하면 좋다.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ review }) {
      return `Final synthesis using review: ${review}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    config: { maxRounds: 4 },
    idFactory: () => "task-4",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1" },
    userRequest: "랜덤 테스트 요청",
  });

  assert.equal(result.status, "finalized");
  assert.equal(ownerCalls, 1);
  assert.deepEqual(db.getTurns("task-4").map((turn) => turn.kind), ["owner_draft", "review_request", "review", "final_synthesis"]);
  db.close();
});

test("repeated unresolved reviewer issue escalates to user decision", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  let ownerCalls = 0;
  const owner: OwnerExecutor = {
    async createDraft() {
      ownerCalls += 1;
      return `Owner draft revision ${ownerCalls}: keep the risky plan.`;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "disagree",
        content: "Hermes review: same unresolved feasibility issue remains.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should wait after repeated unresolved issues");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    config: { maxRounds: 5, repeatIssueThreshold: 3 },
    idFactory: () => "task-repeat-1",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1" },
    userRequest: "기술 구현안을 만들어줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.equal(ownerCalls, 3);
  assert.ok(result.escalationReasons.includes("repeated_unresolved_issue"));
  assert.deepEqual(db.getTurns("task-repeat-1").map((turn) => turn.kind), [
    "owner_draft",
    "review_request",
    "review",
    "owner_draft",
    "review_request",
    "review",
    "owner_draft",
    "review_request",
    "review",
    "escalation",
  ]);
  db.close();
});

test("Hermes reply in a different thread escalates to user decision", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner: {
      async createDraft() {
        throw new Error("Owner should not be called for thread violation recording");
      },
    },
    reviewer: {
      async review() {
        throw new Error("Reviewer should not be called for thread violation recording");
      },
    },
    finalizer: {
      async synthesize() {
        throw new Error("Finalizer should not be called for thread violation recording");
      },
    },
  });
  db.createTask({
    id: "task-thread-violation-1",
    projectChannelId: "parent-1",
    threadId: "thread-expected",
    userRequest: "Build a same-thread workflow.",
  });

  const result = await orchestrator.recordHermesThreadViolation({
    taskId: "task-thread-violation-1",
    observedThreadId: "thread-wrong",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.deepEqual(result.escalationReasons, ["hermes_wrong_thread"]);
  assert.deepEqual(discord.threadPosts.map((post) => post.threadId), ["thread-expected"]);
  assert.match(discord.threadPosts[0].content, /Hermes replied outside the task thread/);
  assert.deepEqual(db.getTurns("task-thread-violation-1").map((turn) => turn.kind), ["escalation"]);
  db.close();
});

test("waiting task resumes from user decision in the same thread", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const task = db.createTask({
    id: "task-resume-1",
    projectChannelId: "parent-1",
    threadId: "thread-resume",
    userRequest: "Choose the final direction.",
  });
  db.insertTurn({
    taskId: task.id,
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "Owner draft: option A and option B.",
    visibleSummary: "Owner draft summary",
  });
  db.insertTurn({
    taskId: task.id,
    round: 1,
    role: "hermes-reviewer",
    kind: "review",
    content: "Hermes review: option B is stronger, but needs user choice.",
    visibleSummary: "Hermes review summary",
  });
  db.insertTurn({
    taskId: task.id,
    round: 1,
    role: "openclaw-finalizer",
    kind: "escalation",
    content: "User decision required",
    visibleSummary: "User decision required",
  });
  db.updateTaskStatus(task.id, "waiting_for_user");

  let finalizerInput: Parameters<FinalizerExecutor["synthesize"]>[0] | undefined;
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner: {
      async createDraft() {
        throw new Error("Owner should not be called during resume");
      },
    },
    reviewer: {
      async review() {
        throw new Error("Reviewer should not be called during resume");
      },
    },
    finalizer: {
      async synthesize(input) {
        finalizerInput = input;
        return `Final after decision: ${input.acceptedFeedback.join(" ")}`;
      },
    },
  });

  const result = await orchestrator.resumeFromUserDecision({
    threadId: "thread-resume",
    userDecision: "Use option B.",
  });

  assert.equal(result.status, "finalized");
  assert.equal(result.threadId, "thread-resume");
  assert.equal(finalizerInput?.draft, "Owner draft: option A and option B.");
  assert.equal(finalizerInput?.review, "Hermes review: option B is stronger, but needs user choice.");
  assert.deepEqual(finalizerInput?.acceptedFeedback, ["User decision: Use option B."]);
  assert.deepEqual(db.getTurns("task-resume-1").map((turn) => turn.kind), [
    "owner_draft",
    "review",
    "escalation",
    "user_decision",
    "final_synthesis",
  ]);
  assert.deepEqual(discord.threadPosts.map((post) => post.threadId), ["thread-resume"]);
  assert.match(discord.threadPosts[0].content, /Final after decision/);
  db.close();
});
