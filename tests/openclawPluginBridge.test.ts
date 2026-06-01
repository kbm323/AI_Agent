import test from "node:test";
import assert from "node:assert/strict";
import { buildOpenClawCenteredTaskPlan, requestHermesHybridReview } from "../src/openclaw/pluginBridge.ts";
import type { TaskRecord } from "../src/types.ts";

const task: TaskRecord = {
  id: "task-1",
  projectChannelId: "parent-1",
  threadId: "thread-1",
  userRequest: "랜덤 테스트 요청",
  teamRoute: "content",
  status: "created",
  createdAt: "2026-05-28T00:00:00.000Z",
  updatedAt: "2026-05-28T00:00:00.000Z",
};

test("OpenClaw centered plan keeps parent as launcher and reviewer request includes captured draft", () => {
  const plan = buildOpenClawCenteredTaskPlan({
    parentChannelId: "parent-1",
    threadId: "thread-1",
    userRequest: "랜덤 테스트 요청",
    capturedOpenClawDraft: "실제 OpenClaw 초안: 후보 A, 후보 B, 후보 C",
    hermesRoute: "cli",
    round: 1,
  });

  assert.equal(plan.parentNotice, "Agent discussion started -> thread-1");
  assert.match(plan.openClawDraftPost, /실제 OpenClaw 초안/);
  assert.match(plan.hermesReviewerRequest, /Captured OpenClaw draft:\n실제 OpenClaw 초안/);
});

test("OpenClaw centered plan rejects missing or mirrored draft capture", () => {
  assert.throws(() => buildOpenClawCenteredTaskPlan({
    parentChannelId: "parent-1",
    threadId: "thread-1",
    userRequest: "랜덤 테스트 요청",
    capturedOpenClawDraft: "랜덤 테스트 요청",
    hermesRoute: "cli",
    round: 1,
  }), /OpenClaw draft capture failed/);
});

test("Hermes hybrid review falls back through route preference", async () => {
  const calls: string[] = [];
  const result = await requestHermesHybridReview({
    async reviewWithCli() {
      calls.push("cli");
      throw new Error("cli unavailable");
    },
    async reviewWithGatewayCommand() {
      calls.push("gateway-command");
      return {
        route: "gateway-command",
        verdict: "agree",
        content: "Hermes review via gateway-command",
      };
    },
  }, {
    task,
    userRequest: task.userRequest,
    capturedOpenClawDraft: "실제 OpenClaw 초안",
    reviewerRequest: "review this draft",
    round: 1,
    preferredRoutes: ["cli", "gateway-command", "discord-mention-polling"],
  });

  assert.deepEqual(calls, ["cli", "gateway-command"]);
  assert.equal(result.route, "gateway-command");
  assert.equal(result.verdict, "agree");
});
