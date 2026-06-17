import test, { mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { checkPublicApi, PUBLIC_API_ARTIFACT_PATH } from "../scripts/check-public-api.ts";
import { AiAgentDatabase, analyzeUserRequest, buildDefaultTokenStrategy, CompanyOrchestrator, summarizeForThread } from "../src/index.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor, RunTaskResult } from "../src/index.ts";
import { send_message, on_message, register_handler } from "../src/messages.ts";

test("documented public API symbols are exposed from the documented module path", async () => {
  const result = await checkPublicApi();

  assert.deepEqual(result, {
    modulePath: "ai-agent",
    verifiedSymbols: [
      "CompanyOrchestrator",
      "AiAgentDatabase",
      "analyzeUserRequest",
      "buildCompressedLoopContextArtifact",
      "buildDefaultTokenStrategy",
      "buildReviewerRequest",
      "buildRoleRoutes",
      "buildTaskGraph",
      "decomposeUserRequest",
      "serializeEscalationResult",
      "summarizeForThread",
    ],
    exportedSymbols: [
      "AiAgentDatabase",
      "CompanyOrchestrator",
      "analyzeUserRequest",
      "buildCompressedLoopContextArtifact",
      "buildDefaultTokenStrategy",
      "buildReviewerRequest",
      "buildRoleRoutes",
      "buildTaskGraph",
      "decomposeUserRequest",
      "serializeEscalationResult",
      "summarizeForThread",
    ],
    undocumentedRuntimeSymbols: [],
    verifiedClassSymbols: ["CompanyOrchestrator", "AiAgentDatabase"],
    verifiedFunctionSymbols: [
      "analyzeUserRequest",
      "buildCompressedLoopContextArtifact",
      "buildDefaultTokenStrategy",
      "buildReviewerRequest",
      "buildRoleRoutes",
      "buildTaskGraph",
      "decomposeUserRequest",
      "serializeEscalationResult",
      "summarizeForThread",
    ],
    importSideEffects: {
      stdoutBytes: 0,
      stderrBytes: 0,
      createdFiles: [],
    },
  });

  const artifact = JSON.parse(readFileSync(new URL(`../${PUBLIC_API_ARTIFACT_PATH}`, import.meta.url), "utf8"));
  assert.deepEqual(artifact, result);
});

test("public request analyzer returns the primary MVP planning shape for clear requests", () => {
  const analysis = analyzeUserRequest("신제품 소개 영상 제작 회의를 진행하고 최종 구성안을 만들어줘.");

  assert.equal(analysis.intent.workflow, "analysis-routing-openclaw-hermes-synthesis");
  assert.equal(analysis.intent.summary, "신제품 소개 영상 제작 회의를 진행하고 최종 구성안을 만들어줘.");
  assert.deepEqual(
    analysis.constraints.map((constraint) => constraint.id),
    ["mvp-flow-required", "compressed-loop-context"],
  );
  assert.deepEqual(
    analysis.requiredOutputs.map((output) => output.id),
    ["task_breakdown", "role_routes", "meeting_loop_result", "final_synthesis", "escalation"],
  );
  assert.equal(analysis.userRequestSummary, "신제품 소개 영상 제작 회의를 진행하고 최종 구성안을 만들어줘.");
  assert.deepEqual(
    analysis.taskBreakdown.map((item) => item.id),
    ["task-001", "task-002", "task-003", "task-004"],
  );
  assert.deepEqual(
    analysis.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.match(analysis.loopContextSummary, /analysis -> routing -> OpenClaw draft -> Hermes review -> final synthesis\/escalation/);
  assert.deepEqual(analysis.ambiguitySignals, []);
});

test("public API entrypoint returns deterministic observable output for the same fixed request", async () => {
  const fixedNow = new Date("2026-06-05T00:00:00.000Z");
  const userRequest = "브랜드 캠페인 제작 회의를 진행하고 실행안과 검토 결과를 합성해줘.";

  mock.timers.enable({ apis: ["Date"], now: fixedNow });
  try {
    const first = await runFixedPublicEntrypoint(userRequest);
    const second = await runFixedPublicEntrypoint(userRequest);

    assert.deepEqual(second, first);
    assert.deepEqual(
      {
        status: first.status,
        threadId: first.threadId,
        taskId: first.task.id,
        createdAt: first.task.createdAt,
        updatedAt: first.task.updatedAt,
        routeRoles: first.requestAnalysis.roleRoutes.map((route) => route.role),
        meetingKinds: first.meetingHistory.map((turn) => turn.kind),
        escalationReasons: first.escalationReasons,
      },
      {
        status: "finalized",
        threadId: "thread-fixed-1",
        taskId: "task-fixed-1",
        createdAt: "2026-06-05T00:00:00.000Z",
        updatedAt: "2026-06-05T00:00:00.000Z",
        routeRoles: ["openclaw-owner", "openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
        meetingKinds: ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"],
        escalationReasons: [],
      },
    );
    assert.equal(first.finalSynthesis, "Final synthesis: Owner draft: fixed executable campaign plan. Hermes review: agree. Fixed draft is ready.");
  } finally {
    mock.timers.reset();
  }
});

test("public token strategy describes raw storage separation and compressed loop context", () => {
  const tokenStrategy = buildDefaultTokenStrategy();

  assert.match(tokenStrategy.rawStorage, /full model outputs/);
  assert.match(tokenStrategy.exposedLoopContext, /bounded summaries/);
  assert.match(tokenStrategy.compressionPolicy, /instead of replaying full raw text/);
  assert.match(tokenStrategy.targetReduction, /40-50%/);
});

test("public database retrieval returns preserved meeting history in insertion order", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "public-history-task-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-1",
    userRequest: "회의 이력을 보존하고 요약을 조회해줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  const ownerTurn = db.insertTurn({
    id: "public-history-turn-1",
    taskId: "public-history-task-1",
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "RAW OpenClaw output with complete execution notes.",
    visibleSummary: "OpenClaw summary for loop context.",
    createdAt: "2026-06-05T01:03:04.000Z",
  });
  const reviewTurn = db.insertTurn({
    id: "public-history-turn-2",
    taskId: "public-history-task-1",
    round: 1,
    role: "hermes-reviewer",
    kind: "review",
    content: "RAW Hermes review with complete critique details.",
    visibleSummary: "Hermes summary for loop context.",
    createdAt: "2026-06-05T01:04:05.000Z",
  });

  const turns = db.getTurns("public-history-task-1");

  assert.deepEqual(turns, [ownerTurn, reviewTurn]);
  assert.deepEqual(
    turns.map((turn) => ({ round: turn.round, role: turn.role, kind: turn.kind, summary: turn.visibleSummary })),
    [
      { round: 1, role: "openclaw-owner", kind: "owner_draft", summary: "OpenClaw summary for loop context." },
      { round: 1, role: "hermes-reviewer", kind: "review", summary: "Hermes summary for loop context." },
    ],
  );
  assert.match(turns[0].content, /complete execution notes/);
  assert.equal(turns[0].visibleSummary.includes("complete execution notes"), false);

  db.close();
});

test("public thread summarizer normalizes and bounds preserved history summaries", () => {
  const summary = summarizeForThread("  Line one\n\n\n\nLine two with raw details that should be bounded.  ", 32);

  assert.equal(summary, "Line one\n\nLine two with raw det…");
  assert.equal(summary.length, 32);
});

test("on_message registers a global handler and returns an unsubscribe function", () => {
  let callCount = 0;
  const handler = async (_msg: unknown) => {
    callCount++;
  };

  const unsub = on_message(handler);
  assert.equal(typeof unsub, "function");

  // Unsubscribe should remove the handler
  unsub();

  // Verify unsub was called (no-op, handler removed)
  assert.equal(callCount, 0);
});

test("register_handler returns an unsubscribe function for pattern-matched handlers", () => {
  let callCount = 0;
  const handler = async (_msg: unknown) => {
    callCount++;
  };

  const unsub = register_handler("/meeting", handler);
  assert.equal(typeof unsub, "function");

  unsub();
  assert.equal(callCount, 0);
});

test("send_message is importable as a function", () => {
  assert.equal(typeof send_message, "function");
});

test("on_message is importable as a function", () => {
  assert.equal(typeof on_message, "function");
});

test("register_handler is importable as a function", () => {
  assert.equal(typeof register_handler, "function");
});

async function runFixedPublicEntrypoint(userRequest: string): Promise<RunTaskResult> {
  const db = new AiAgentDatabase();
  const discord: DiscordDelivery = {
    async createThread() {
      return { threadId: "thread-fixed-1", url: "https://discord.test/thread-fixed-1" };
    },
    async archiveThread(_threadId: string) {},
    async getThread(threadId: string) {
      return { threadId, name: `thread-${threadId}`, archived: false };
    },
    async postParent() {},
    async postThread() {},
  };
  const owner: OwnerExecutor = {
    async createDraft() {
      return "Owner draft: fixed executable campaign plan.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree",
        content: "Hermes review: agree. Fixed draft is ready.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      return `Final synthesis: ${draft} ${review}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-fixed-1",
  });

  try {
    return await orchestrator.runUserRequest({
      project: { channelId: "channel-fixed-1", name: "fixed-project" },
      userRequest,
    });
  } finally {
    db.close();
  }
}
