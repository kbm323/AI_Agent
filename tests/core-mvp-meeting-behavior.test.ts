import test from "node:test";
import assert from "node:assert/strict";
import { AiAgentDatabase } from "../src/db.ts";
import { CompanyOrchestrator } from "../src/orchestrator.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor } from "../src/types.ts";

function createFakeDiscord(): DiscordDelivery & {
  parentPosts: string[];
  threadPosts: Array<{ threadId: string; content: string; fullContent?: string }>;
} {
  return {
    parentPosts: [],
    threadPosts: [],
    async createThread() {
      return { threadId: "thread-core-mvp-1", url: "https://discord.test/thread-core-mvp-1" };
    },
    async postParent(input) {
      this.parentPosts.push(input.content);
    },
    async postThread(input) {
      this.threadPosts.push(input);
    },
  };
}

test("core MVP meeting flow returns a completed OpenClaw/Hermes discussion result with final route state", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const rawContextSentinel = "RAW_FULL_TEXT_SENTINEL_ONLY_IN_STORAGE";
  const ownerDrafts = [
    `OpenClaw draft round 1: campaign meeting plan needs stronger acceptance gates. ${"x".repeat(1400)} ${rawContextSentinel}`,
    "OpenClaw draft round 2: campaign meeting plan includes scenes, owners, acceptance gates, and delivery checklist.",
  ];
  const ownerCalls: Array<{ userRequest: string; round: number }> = [];
  const reviewerCalls: Array<{ draft: string; round: number }> = [];
  const finalizerCalls: Parameters<FinalizerExecutor["synthesize"]>[0][] = [];
  const owner: OwnerExecutor = {
    async createDraft({ userRequest, round }) {
      ownerCalls.push({ userRequest, round });
      return ownerDrafts[round - 1] ?? ownerDrafts.at(-1) ?? "";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ draft, round }) {
      reviewerCalls.push({ draft, round });
      if (round === 1) {
        return {
          verdict: "disagree",
          content: "Hermes review round 1: disagree. Add explicit acceptance gates before final synthesis.",
        };
      }
      return {
        verdict: "agree_with_changes",
        content: "Hermes review round 2: agree with changes. Keep acceptance gates in the final synthesis.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      finalizerCalls.push(input);
      return [
        "Final synthesis: run the virtual company production meeting.",
        input.draft,
        input.review,
        input.acceptedFeedback.join("\n"),
      ].join("\n\n");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-core-mvp-meeting-1",
    config: { maxRounds: 3 },
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-core-mvp", name: "campaign" },
    userRequest: "캠페인 제작 회의를 열고 역할별 검토를 거쳐 최종 실행안을 합성해줘.",
  });

  assert.equal(result.status, "finalized");
  assert.deepEqual(
    result.requestAnalysis.taskBreakdown.map((task) => task.id),
    ["task-001", "task-002", "task-003", "task-004"],
  );
  assert.deepEqual(
    result.requestAnalysis.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.deepEqual(ownerCalls.map((call) => call.round), [1, 2]);
  assert.deepEqual(reviewerCalls.map((call) => call.round), [1, 2]);
  assert.equal(reviewerCalls[0].draft, ownerDrafts[0]);
  assert.equal(reviewerCalls[1].draft, ownerDrafts[1]);
  assert.equal(finalizerCalls.length, 1);
  assert.equal(finalizerCalls[0].reviewerVerdict, "agree_with_changes");
  assert.deepEqual(finalizerCalls[0].acceptedFeedback, [
    "Round 1 Hermes feedback queued for OpenClaw revision.",
    "Hermes reviewer agreed with changes for final synthesis.",
  ]);
  assert.deepEqual(
    result.intermediateDecisions.map((decision) => ({
      round: decision.round,
      role: decision.role,
      decision: decision.decision,
      reviewerVerdict: decision.reviewerVerdict,
      reasons: decision.reasons,
      sourceTurnKinds: decision.sourceTurnKinds,
    })),
    [
      {
        round: 1,
        role: "hermes-reviewer",
        decision: "draft_rejected_for_revision",
        reviewerVerdict: "disagree",
        reasons: ["round_1_revision_required"],
        sourceTurnKinds: ["owner_draft", "review"],
      },
      {
        round: 2,
        role: "hermes-reviewer",
        decision: "draft_accepted_with_changes",
        reviewerVerdict: "agree_with_changes",
        reasons: [],
        sourceTurnKinds: ["owner_draft", "review"],
      },
    ],
  );
  assert.match(result.intermediateDecisions[0].summary, /rejected round 1/i);
  assert.match(result.intermediateDecisions[1].summary, /accepted the OpenClaw draft with changes/i);
  assert.match(result.finalSynthesis ?? "", /Final synthesis:/);
  assert.equal(result.discussionResult.status, "completed");
  assert.deepEqual(result.discussionResult.finalRouteState, {
    taskId: "task-core-mvp-meeting-1",
    threadId: "thread-core-mvp-1",
    taskStatus: "finalized",
    finalRound: 4,
    routeSequence: [
      { taskId: "task-001", role: "openclaw-owner", title: "요청 의도와 성공 기준 정리" },
      { taskId: "task-002", role: "openclaw-owner", title: "OpenClaw 실행 초안 작성" },
      { taskId: "task-003", role: "hermes-reviewer", title: "Hermes 리뷰와 수렴 판단" },
      { taskId: "task-004", role: "openclaw-finalizer", title: "최종 합성 또는 escalation" },
    ],
    finalTurn: { round: 4, role: "openclaw-finalizer", kind: "final_synthesis" },
    converged: true,
    escalationReasons: [],
  });
  assert.deepEqual(result.discussionResult.recordedDecisions, result.intermediateDecisions);
  assert.equal(result.discussionResult.finalSynthesis, result.finalSynthesis);

  assert.deepEqual(discord.parentPosts, ["Agent discussion started -> https://discord.test/thread-core-mvp-1"]);
  assert.equal(discord.parentPosts.join("\n").includes("OpenClaw draft"), false);

  const turns = db.getTurns("task-core-mvp-meeting-1");
  assert.deepEqual(
    turns.map((turn) => [turn.round, turn.role, turn.kind]),
    [
      [0, "openclaw-owner", "request_analysis"],
      [1, "openclaw-owner", "owner_draft"],
      [1, "openclaw-owner", "review_request"],
      [1, "hermes-reviewer", "review"],
      [2, "openclaw-owner", "owner_draft"],
      [2, "openclaw-owner", "review_request"],
      [2, "hermes-reviewer", "review"],
      [4, "openclaw-finalizer", "final_synthesis"],
    ],
  );
  assert.equal(turns[1].content.includes(rawContextSentinel), true);
  assert.equal(turns[1].visibleSummary.includes(rawContextSentinel), false);
  assert.equal(result.meetingHistory.some((turn) => turn.summary.includes(rawContextSentinel)), false);
  assert.equal(discord.threadPosts.some((post) => post.content.includes(rawContextSentinel)), false);
  assert.equal(discord.threadPosts.some((post) => post.fullContent?.includes(rawContextSentinel)), true);
  db.close();
});

test("core MVP meeting flow escalates ambiguous requests before OpenClaw execution", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      throw new Error("OpenClaw should not draft before ambiguous request escalation");
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      throw new Error("Hermes should not review before ambiguous request escalation");
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Final synthesis should not run before ambiguous request escalation");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-core-mvp-escalation-1",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-core-mvp", name: "campaign" },
    userRequest: "대충 좋은 후보 여러 개를 추천만 해줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.deepEqual(result.escalationReasons, ["underspecified_preference", "unclear_success_criteria"]);
  assert.equal(result.finalSynthesis, undefined);
  assert.deepEqual(
    result.meetingHistory.map((turn) => [turn.round, turn.role, turn.kind]),
    [
      [0, "openclaw-owner", "request_analysis"],
      [0, "openclaw-finalizer", "escalation"],
    ],
  );
  assert.match(result.meetingHistory.at(-1)?.summary ?? "", /User decision required/);
  assert.match(db.getTurns("task-core-mvp-escalation-1").at(-1)?.content ?? "", /underspecified_preference/);
  db.close();
});
