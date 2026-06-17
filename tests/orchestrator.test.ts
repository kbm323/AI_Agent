import test from "node:test";
import assert from "node:assert/strict";
import { AiAgentDatabase } from "../src/db.ts";
import * as orchestratorExports from "../src/orchestrator.ts";
import {
  buildEscalationMessage,
  buildReviewerRequest,
  buildThreadName,
  CompanyOrchestrator,
  serializeEscalationResult,
} from "../src/orchestrator.ts";
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

test("core meeting orchestration public exports have primary success-path coverage", async () => {
  assert.deepEqual(Object.keys(orchestratorExports).sort(), [
    "CompanyOrchestrator",
    "buildEscalationMessage",
    "buildReviewerRequest",
    "buildThreadName",
    "emitEscalationNotification",
    "serializeEscalationResult",
  ]);

  const reviewerRequest = buildReviewerRequest({
    userRequest: " 제작 회의 초안을 검토해줘. ",
    draft: " OpenClaw draft: 일정과 승인 기준을 정리했다. ",
    round: 1,
  });
  assert.match(reviewerRequest, /^Hermes reviewer request \(round 1\)/);
  assert.match(reviewerRequest, /Captured OpenClaw draft:\nOpenClaw draft: 일정과 승인 기준/);

  assert.equal(buildThreadName("  제작 회의 실행안을 만들어줘.  "), "Task: 제작 회의 실행안을 만들어줘.");
  assert.equal(
    buildEscalationMessage(["brand_or_public_release"]),
    ["User decision required", "", "Reasons:", "- brand_or_public_release"].join("\n"),
  );
  assert.deepEqual(
    JSON.parse(
      serializeEscalationResult({
        reasons: ["reviewer_requested_user_decision"],
        triggerType: "meeting_loop",
        nextRequiredAction: "Ask the user for the blocked production decision.",
      }),
    ),
    {
      schemaVersion: "escalation-result.v1",
      escalation: {
        required: true,
        reasons: ["reviewer_requested_user_decision"],
        triggerType: "meeting_loop",
        nextRequiredAction: "Ask the user for the blocked production decision.",
      },
    },
  );

  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner: {
      async createDraft() {
        return "Owner draft: agenda, assets, and delivery checklist.";
      },
    },
    reviewer: {
      async review() {
        return { verdict: "agree", content: "Hermes review: agree. Draft is ready." };
      },
    },
    finalizer: {
      async synthesize({ draft, review }) {
        return `Final synthesis: ${draft} ${review}`;
      },
    },
    idFactory: () => "core-public-export-task-1",
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-1", name: "production" },
      userRequest: "제작 회의를 진행하고 최종 실행안을 합성해줘.",
    });

    assert.equal(result.status, "finalized");
    assert.deepEqual(result.meetingHistory.map((turn) => turn.kind), [
      "request_analysis",
      "owner_draft",
      "review_request",
      "review",
      "final_synthesis",
    ]);
    assert.match(result.finalSynthesis ?? "", /^Final synthesis:/);
  } finally {
    db.close();
  }
});

test("buildReviewerRequest formats the Hermes review prompt with captured draft context", () => {
  const prompt = buildReviewerRequest({
    userRequest: "  출시 전 제작 회의 결과를 검토해줘.  ",
    draft: "  OpenClaw draft: 일정, 리스크, 승인 게이트를 정리했다.  ",
    round: 2,
  });

  assert.equal(
    prompt,
    [
      "Hermes reviewer request (round 2)",
      "",
      "User request:",
      "출시 전 제작 회의 결과를 검토해줘.",
      "",
      "Captured OpenClaw draft:",
      "OpenClaw draft: 일정, 리스크, 승인 게이트를 정리했다.",
      "",
      "Review task:",
      "OpenClaw draft를 기준으로 비판/보완/동의 여부를 판단하라.",
      "독립 제안을 새로 만들지 말고, draft의 장점/문제/리스크/수정안을 분리하라.",
      "",
      "Verdict must be one of: agree, agree_with_changes, disagree, needs_user_decision.",
    ].join("\n"),
  );
});

test("buildThreadName returns a compact deterministic task thread name", () => {
  assert.equal(
    buildThreadName("  첫 번째 장면을 정하고   두 번째 장면의 검토 기준도 만들어줘. ".repeat(2)),
    "Task: 첫 번째 장면을 정하고 두 번째 장면의 검토 기준도 만들어줘. 첫 번째 장면을 정하고 두 ",
  );
});

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
  assert.deepEqual(
    result.requestAnalysis.taskBreakdown.map((item) => item.id),
    ["task-001", "task-002", "task-003", "task-004"],
  );
  assert.deepEqual(
    result.requestAnalysis.roleRoutes.map((route) => route.role),
    ["openclaw-owner", "openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
  );
  assert.deepEqual(discord.parentPosts, ["Agent discussion started -> https://discord.test/thread-1"]);
  assert.equal(discord.threadPosts.every((post) => post.threadId === "thread-1"), true);
  assert.equal(discord.parentPosts.join("\n").includes("Owner draft"), false);

  const turns = db.getTurns("task-1");
  assert.deepEqual(turns.map((turn) => turn.kind), ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.match(turns[0].content, /taskBreakdown/);
  assert.match(turns[2].content, /Captured OpenClaw draft:\nOwner draft:/);
  assert.match(turns[3].content, /clear hook/);
  assert.match(turns[4].content, /Review used:/);
  assert.equal(result.meetingHistory.every((turn) => !turn.summary.includes("taskBreakdown")), true);
  db.close();
});

test("public runUserRequest synthesizes the final answer after Hermes agreement", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const ownerDraft = "Owner draft: define the agenda, produce the asset list, and prepare the delivery checklist.";
  const hermesReview = "Hermes review: agree. The draft covers agenda, assets, and delivery.";
  const owner: OwnerExecutor = {
    async createDraft() {
      return ownerDraft;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree",
        content: hermesReview,
      };
    },
  };
  const finalizerCalls: Parameters<FinalizerExecutor["synthesize"]>[0][] = [];
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      finalizerCalls.push(input);
      return `Final synthesis: ${input.draft} ${input.review} ${input.acceptedFeedback.join(" ")}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-final-synthesis-public-1",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "production" },
    userRequest: "제작 회의를 진행하고 최종 실행안을 합성해줘.",
  });

  assert.equal(result.status, "finalized");
  assert.equal(finalizerCalls.length, 1);
  assert.deepEqual(
    {
      userRequest: finalizerCalls[0].userRequest,
      draft: finalizerCalls[0].draft,
      review: finalizerCalls[0].review,
      reviewerVerdict: finalizerCalls[0].reviewerVerdict,
      acceptedFeedback: finalizerCalls[0].acceptedFeedback,
      rejectedFeedback: finalizerCalls[0].rejectedFeedback,
    },
    {
      userRequest: "제작 회의를 진행하고 최종 실행안을 합성해줘.",
      draft: ownerDraft,
      review: hermesReview,
      reviewerVerdict: "agree",
      acceptedFeedback: ["Hermes reviewer agreed with the draft."],
      rejectedFeedback: [],
    },
  );
  assert.match(result.finalSynthesis ?? "", /^Final synthesis:/);
  assert.match(result.finalSynthesis ?? "", /agenda, assets, and delivery/);

  const finalTurn = db.getTurns("task-final-synthesis-public-1").at(-1);
  assert.equal(finalTurn?.kind, "final_synthesis");
  assert.equal(finalTurn?.content, result.finalSynthesis);
  assert.equal(result.meetingHistory.at(-1)?.kind, "final_synthesis");
  db.close();
});

test("public runUserRequest synthesizes after Hermes agree_with_changes verdict", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const ownerDraft = "Owner draft: create the storyboard, identify risks, and keep a revision checkpoint.";
  const hermesReview = "Hermes review: agree with changes. Add one explicit owner to the revision checkpoint.";
  const owner: OwnerExecutor = {
    async createDraft() {
      return ownerDraft;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree_with_changes",
        content: hermesReview,
      };
    },
  };
  const finalizerCalls: Parameters<FinalizerExecutor["synthesize"]>[0][] = [];
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      finalizerCalls.push(input);
      return `Final synthesis with Hermes changes: ${input.acceptedFeedback.join(" ")}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-agree-with-changes-public-1",
    config: { maxRounds: 3 },
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "storyboard" },
    userRequest: "스토리보드 제작 회의를 진행하고 수정 조건을 반영해 최종안을 만들어줘.",
  });

  assert.equal(result.status, "finalized");
  assert.equal(finalizerCalls.length, 1);
  assert.deepEqual(
    {
      reviewerVerdict: finalizerCalls[0].reviewerVerdict,
      acceptedFeedback: finalizerCalls[0].acceptedFeedback,
      rejectedFeedback: finalizerCalls[0].rejectedFeedback,
    },
    {
      reviewerVerdict: "agree_with_changes",
      acceptedFeedback: ["Hermes reviewer agreed with changes for final synthesis."],
      rejectedFeedback: [],
    },
  );
  assert.match(result.finalSynthesis ?? "", /Hermes changes/);
  assert.deepEqual(
    result.meetingHistory.map((turn) => [turn.round, turn.role, turn.kind]),
    [
      [0, "openclaw-owner", "request_analysis"],
      [1, "openclaw-owner", "owner_draft"],
      [1, "openclaw-owner", "review_request"],
      [1, "hermes-reviewer", "review"],
      [4, "openclaw-finalizer", "final_synthesis"],
    ],
  );
  db.close();
});

test("meeting loop persists Hermes review persona turns across revision rounds", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const ownerDrafts = [
    "Owner draft round 1: campaign structure exists but success criteria are not explicit.",
    "Owner draft round 2: campaign structure includes explicit success criteria and review gates.",
  ];
  const hermesReviews = [
    {
      verdict: "disagree" as const,
      content: "Hermes review round 1: disagree. Add measurable success criteria before synthesis.",
    },
    {
      verdict: "agree" as const,
      content: "Hermes review round 2: agree. Success criteria and review gates are now explicit.",
    },
  ];
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      return ownerDrafts[round - 1] ?? ownerDrafts.at(-1) ?? "";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      return hermesReviews[round - 1] ?? hermesReviews.at(-1) ?? hermesReviews[0];
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ review, acceptedFeedback }) {
      return `Final synthesis after Hermes convergence.\n\n${review}\n\n${acceptedFeedback.join("\n")}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-hermes-orchestrator-1",
    config: { maxRounds: 3 },
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "campaign" },
    userRequest: "캠페인 제작 회의를 진행하고 검토 후 최종안을 만들어줘.",
  });

  assert.equal(result.status, "finalized");
  const hermesTurns = db.getTurns("task-hermes-orchestrator-1").filter((turn) => turn.role === "hermes-reviewer");
  assert.deepEqual(
    hermesTurns.map((turn) => [turn.round, turn.kind]),
    [
      [1, "review"],
      [2, "review"],
    ],
  );
  assert.match(hermesTurns[0].content, /Add measurable success criteria/);
  assert.match(hermesTurns[1].content, /review gates are now explicit/);
  assert.deepEqual(
    result.meetingHistory.filter((turn) => turn.role === "hermes-reviewer").map((turn) => [turn.round, turn.kind]),
    [
      [1, "review"],
      [2, "review"],
    ],
  );
  assert.equal(result.meetingHistory.some((turn) => turn.summary.includes("Add measurable success criteria before synthesis.")), true);
  assert.equal(result.finalSynthesis?.includes("Hermes convergence"), true);
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
  assert.deepEqual(db.getTurns("task-2").map((turn) => turn.kind), ["request_analysis", "escalation"]);
  assert.match(discord.threadPosts.at(-1)?.content ?? "", /OpenClaw draft capture failed/);
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
  assert.deepEqual(db.getTurns("task-3").map((turn) => turn.kind), ["request_analysis", "owner_draft", "review_request", "review", "escalation"]);
  db.close();
});

test("Hermes user-decision verdict emits escalation artifact without request keyword triggers", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      return "Owner draft: compare two implementation paths and state that both are viable.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "needs_user_decision",
        content: "Hermes review: the alternatives are both viable, so the requester must choose the product direction.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should wait when Hermes requires user input");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-reviewer-user-input-1",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "direction-check" },
    userRequest: "기획 회의를 진행하고 두 구현 경로의 장단점을 정리해줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.deepEqual(result.escalationReasons, ["reviewer_requested_user_decision"]);
  assert.equal(result.finalSynthesis, undefined);
  assert.deepEqual(
    result.meetingHistory.map((turn) => [turn.round, turn.role, turn.kind]),
    [
      [0, "openclaw-owner", "request_analysis"],
      [1, "openclaw-owner", "owner_draft"],
      [1, "openclaw-owner", "review_request"],
      [1, "hermes-reviewer", "review"],
      [1, "openclaw-finalizer", "escalation"],
    ],
  );
  assert.match(result.meetingHistory.at(-1)?.summary ?? "", /reviewer_requested_user_decision/);
  db.close();
});

test("ambiguous request produces escalation artifact inside normal task output", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      throw new Error("OpenClaw should wait when the request is ambiguous");
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      throw new Error("Hermes should wait when the request is ambiguous");
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should not run for ambiguous input");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-4",
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1" },
    userRequest: "대충 좋은 후보 여러 개 추천만 해줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.deepEqual(result.escalationReasons, ["underspecified_preference", "unclear_success_criteria"]);
  assert.deepEqual(db.getTurns("task-4").map((turn) => turn.kind), ["request_analysis", "escalation"]);
  assert.match(result.meetingHistory.at(-1)?.summary ?? "", /User decision required/);
  db.close();
});

test("configured convergence failure threshold emits escalation artifact", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      return `Owner draft round ${round}: create a concrete creative plan with scenes, owners, and review gates.`;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      return {
        verdict: "disagree",
        content: `Hermes review round ${round}: disagree. The draft still lacks enough convergence evidence.`,
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      throw new Error("Finalizer should not run when convergence failure reaches the threshold");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-convergence-threshold-1",
    config: { maxRounds: 2 },
  });

  const result = await orchestrator.runUserRequest({
    project: { channelId: "parent-1", name: "creative-plan" },
    userRequest: "창작 회의 실행안을 장면, 담당자, 검토 게이트 중심으로 구체화해줘.",
  });

  assert.equal(result.status, "waiting_for_user");
  assert.deepEqual(result.escalationReasons, ["max_rounds_without_agreement"]);
  assert.deepEqual(
    db.getTurns("task-convergence-threshold-1").map((turn) => [turn.round, turn.role, turn.kind]),
    [
      [0, "openclaw-owner", "request_analysis"],
      [1, "openclaw-owner", "owner_draft"],
      [1, "openclaw-owner", "review_request"],
      [1, "hermes-reviewer", "review"],
      [2, "openclaw-owner", "owner_draft"],
      [2, "openclaw-owner", "review_request"],
      [2, "hermes-reviewer", "review"],
      [2, "openclaw-finalizer", "escalation"],
    ],
  );
  assert.match(result.meetingHistory.at(-1)?.summary ?? "", /max_rounds_without_agreement/);
  assert.equal(result.finalSynthesis, undefined);
  db.close();
});
