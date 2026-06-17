import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRoleRoutes,
  buildRoleRoutingMetadata,
  decomposeUserRequest,
  formatRoleRoute,
  matchRoleForTask,
} from "../src/index.ts";

test("agent routing public API maps the canonical meeting tasks to stable persona assignments", () => {
  const tasks = decomposeUserRequest("신제품 소개 영상 제작 회의를 열고 최종안을 합성해줘.");
  const routes = buildRoleRoutes(tasks);

  assert.equal(routes.length, tasks.length);
  assert.deepEqual(
    routes.map((route) => route.taskId),
    tasks.map((task) => task.id),
  );
  assert.deepEqual(routes, [
    {
      taskId: "task-001",
      role: "openclaw-owner",
      responsibility: "요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
    },
    {
      taskId: "task-002",
      role: "openclaw-owner",
      responsibility: "실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
    },
    {
      taskId: "task-003",
      role: "hermes-reviewer",
      responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
    },
    {
      taskId: "task-004",
      role: "openclaw-finalizer",
      responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
    },
  ]);
});

test("agent routing public API exposes stable single-task matching and display formatting", () => {
  const hermesRoute = matchRoleForTask("task-003");
  const finalizerRoute = matchRoleForTask({
    id: "task-004",
    title: "최종 합성 또는 escalation",
    rationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
  });

  assert.deepEqual(hermesRoute, {
    taskId: "task-003",
    role: "hermes-reviewer",
    responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
  });
  assert.equal(
    formatRoleRoute(finalizerRoute),
    "- task-004 -> openclaw-finalizer: 합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
  );
});

test("agent routing public API summarizes the primary meeting workflow metadata", () => {
  const routes = buildRoleRoutes(decomposeUserRequest("브랜드 캠페인 제작 회의로 실행안과 리뷰를 정리해줘."));

  assert.deepEqual(buildRoleRoutingMetadata(routes), {
    routeCount: 4,
    roles: ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
    workflowOrder: [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
    hasHermesReview: true,
    hasFinalizer: true,
  });
});
