import assert from "node:assert/strict";
import { AiAgentDatabase, CompanyOrchestrator } from "../src/index.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor } from "../src/index.ts";

interface OpenClawHermesLoopCheckResult {
  command: "ai-agent check-openclaw-hermes-loop";
  status: "passed";
  scenario: "single_openclaw_step_followed_by_single_hermes_review";
  proof: {
    taskId: string;
    threadId: string;
    executionStep: { order: number; round: number; role: "openclaw-owner"; kind: "owner_draft"; calledOnce: true };
    reviewStep: { order: number; round: number; role: "hermes-reviewer"; kind: "review"; calledOnce: true };
    hermesReviewedOpenClawDraft: true;
    adjacentExecutionThenReview: true;
  };
}

export async function checkOpenClawHermesLoop(): Promise<OpenClawHermesLoopCheckResult> {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const ownerDraft = "OpenClaw execution step: produce a concrete internal plan with review gates.";
  const hermesReview = "Hermes review step: agree. The OpenClaw plan is concrete and reviewable.";
  const ownerCalls: Array<{ taskId: string; round: number }> = [];
  const reviewerCalls: Array<{ taskId: string; round: number; draft: string }> = [];
  const owner: OwnerExecutor = {
    async createDraft({ task, round }) {
      ownerCalls.push({ taskId: task.id, round });
      return ownerDraft;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ task, round, draft }) {
      reviewerCalls.push({ taskId: task.id, round, draft });
      return { verdict: "agree", content: hermesReview };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      return `Final synthesis from one loop iteration.\n\n${draft}\n\n${review}`;
    },
  };

  try {
    const orchestrator = new CompanyOrchestrator({
      db,
      discord,
      owner,
      reviewer,
      finalizer,
      idFactory: () => "task-openclaw-hermes-loop-1",
      config: { maxRounds: 1 },
    });

    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-openclaw-hermes-loop-1", name: "loop-proof" },
      userRequest: "내부 영상 제작 회의를 진행하고 최종 실행안을 합성해줘.",
    });
    const turns = db.getTurns(result.task.id);
    const ownerDraftIndex = turns.findIndex((turn) => turn.round === 1 && turn.role === "openclaw-owner" && turn.kind === "owner_draft");
    const hermesReviewIndex = turns.findIndex((turn) => turn.round === 1 && turn.role === "hermes-reviewer" && turn.kind === "review");

    assert.equal(result.status, "finalized");
    assert.equal(ownerCalls.length, 1);
    assert.equal(reviewerCalls.length, 1);
    assert.notEqual(ownerDraftIndex, -1);
    assert.notEqual(hermesReviewIndex, -1);
    assert.equal(hermesReviewIndex, ownerDraftIndex + 2, "Hermes review must follow the OpenClaw draft after the review request handoff");
    assert.deepEqual(ownerCalls[0], { taskId: result.task.id, round: 1 });
    assert.deepEqual(reviewerCalls[0], { taskId: result.task.id, round: 1, draft: ownerDraft });
    assert.equal(turns[ownerDraftIndex].content, ownerDraft);
    assert.equal(turns[hermesReviewIndex].content, hermesReview);

    return {
      command: "ai-agent check-openclaw-hermes-loop",
      status: "passed",
      scenario: "single_openclaw_step_followed_by_single_hermes_review",
      proof: {
        taskId: result.task.id,
        threadId: result.threadId,
        executionStep: {
          order: ownerDraftIndex + 1,
          round: 1,
          role: "openclaw-owner",
          kind: "owner_draft",
          calledOnce: true,
        },
        reviewStep: {
          order: hermesReviewIndex + 1,
          round: 1,
          role: "hermes-reviewer",
          kind: "review",
          calledOnce: true,
        },
        hermesReviewedOpenClawDraft: true,
        adjacentExecutionThenReview: true,
      },
    };
  } finally {
    db.close();
  }
}

export async function executeCheckOpenClawHermesLoopCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkOpenClawHermesLoop();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown OpenClaw/Hermes loop check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "openclaw_hermes_loop_check_failed", message }, null, 2)}\n`,
    };
  }
}

function createFakeDiscord(): DiscordDelivery {
  return {
    async createThread() {
      return { threadId: "thread-openclaw-hermes-loop-1", url: "https://discord.test/thread-openclaw-hermes-loop-1" };
    },
    async postParent() {},
    async postThread() {},
  };
}

const invokedAsScript = process.argv[1]?.endsWith("check-openclaw-hermes-loop.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckOpenClawHermesLoopCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
