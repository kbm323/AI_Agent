import test from "node:test";
import assert from "node:assert/strict";
import {
  assignDecomposedTasksToAgentRoles,
  decomposeUserRequest,
  deriveTaskRoutingAttributes,
  mapTaskAttributesToEligibleRoles,
  resolveTaskRoleEligibility,
  validateTaskRoleAssignments,
} from "../src/index.ts";

test("role routing module maps task attributes to eligible agent roles", () => {
  const taskAttributes = [
    deriveTaskRoutingAttributes({
      id: "custom-analysis-task",
      title: "요청 의도와 성공 기준 분석",
      rationale: "사용자 요청을 분해하고 회의 목표를 고정한다.",
    }),
    deriveTaskRoutingAttributes({
      id: "custom-execution-task",
      title: "OpenClaw 실행 초안 작성",
      rationale: "owner persona가 실행 가능한 draft를 작성한다.",
    }),
    deriveTaskRoutingAttributes({
      id: "custom-review-task",
      title: "Hermes 리뷰와 수렴 판단",
      rationale: "초안 검토, 리스크 확인, convergence 판단을 수행한다.",
    }),
    deriveTaskRoutingAttributes({
      id: "custom-final-task",
      title: "최종 합성 또는 escalation",
      rationale: "final synthesis를 만들거나 사용자 결정 필요성을 구조화한다.",
    }),
  ];

  assert.deepEqual(taskAttributes, [
    ["request_analysis"],
    ["execution_draft"],
    ["review_convergence"],
    ["final_synthesis", "escalation"],
  ]);
  assert.deepEqual(taskAttributes.map((attributes) => mapTaskAttributesToEligibleRoles(attributes)), [
    ["openclaw-owner"],
    ["openclaw-owner"],
    ["hermes-reviewer"],
    ["openclaw-finalizer"],
  ]);
});

test("role routing module returns deterministic primary route eligibility for canonical and custom tasks", () => {
  assert.deepEqual(resolveTaskRoleEligibility("task-003"), {
    taskId: "task-003",
    attributes: ["review_convergence"],
    eligibleRoles: ["hermes-reviewer"],
    primaryRole: "hermes-reviewer",
    responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
  });

  assert.deepEqual(
    resolveTaskRoleEligibility({
      id: "custom-final-task",
      title: "최종 합성 또는 escalation",
      rationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
    }),
    {
      taskId: "custom-final-task",
      attributes: ["final_synthesis", "escalation"],
      eligibleRoles: ["openclaw-finalizer"],
      primaryRole: "openclaw-finalizer",
      responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
    },
  );
});

test("role routing module rejects tasks with no routable attributes", () => {
  assert.throws(
    () =>
      resolveTaskRoleEligibility({
        id: "custom-unroutable-task",
        title: "색상 팔레트 메모",
        rationale: "회의 단계나 persona 책임 경계와 관련 없는 노트.",
      }),
    /No routing attributes could be derived for task id: custom-unroutable-task/,
  );
});

test("role routing module assigns each decomposed task to exactly one appropriate agent role", () => {
  const tasks = decomposeUserRequest("가상 회사형 제작 회의로 요청 분석, OpenClaw 실행, Hermes 리뷰, 최종 합성을 진행해줘.");
  const report = assignDecomposedTasksToAgentRoles(tasks);

  assert.equal(report.inputTaskCount, tasks.length);
  assert.equal(report.assignmentCount, tasks.length);
  assert.equal(report.oneAssignmentPerTask, true);
  assert.deepEqual(
    report.assignments.map((assignment) => assignment.taskId),
    tasks.map((task) => task.id),
  );
  assert.deepEqual(
    report.assignments.map((assignment) => ({
      taskId: assignment.taskId,
      taskTitle: assignment.taskTitle,
      taskRationale: assignment.taskRationale,
      assignedRole: assignment.assignedRole,
      hasSingleAssignedRole:
        typeof assignment.assignedRole === "string" &&
        ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"].includes(assignment.assignedRole),
      hasResponsibility: assignment.responsibility.length > 0,
    })),
    [
      {
        taskId: "task-001",
        taskTitle: "요청 의도와 성공 기준 정리",
        taskRationale: "회의 루프가 같은 목표를 검토하도록 사용자 요청을 짧은 기준으로 고정한다.",
        assignedRole: "openclaw-owner",
        hasSingleAssignedRole: true,
        hasResponsibility: true,
      },
      {
        taskId: "task-002",
        taskTitle: "OpenClaw 실행 초안 작성",
        taskRationale: "owner persona가 실행 가능한 1차 산출물을 만든다.",
        assignedRole: "openclaw-owner",
        hasSingleAssignedRole: true,
        hasResponsibility: true,
      },
      {
        taskId: "task-003",
        taskTitle: "Hermes 리뷰와 수렴 판단",
        taskRationale: "reviewer persona가 초안의 오류, 누락, 사용자 결정 필요성을 판정한다.",
        assignedRole: "hermes-reviewer",
        hasSingleAssignedRole: true,
        hasResponsibility: true,
      },
      {
        taskId: "task-004",
        taskTitle: "최종 합성 또는 escalation",
        taskRationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
        assignedRole: "openclaw-finalizer",
        hasSingleAssignedRole: true,
        hasResponsibility: true,
      },
    ],
  );
});

test("role routing module validates generated routing decisions as concrete assignment evidence", () => {
  const tasks = decomposeUserRequest("가상 회사형 제작 회의로 요청 분석, OpenClaw 실행, Hermes 리뷰, 최종 합성을 진행해줘.");
  const report = assignDecomposedTasksToAgentRoles(tasks);

  assert.deepEqual(validateTaskRoleAssignments(tasks, report.assignments), {
    valid: true,
    inputTaskCount: 4,
    assignmentCount: 4,
    checkedTaskIds: ["task-001", "task-002", "task-003", "task-004"],
    failures: [],
  });
});

test("role routing module reports invalid and unsupported assignment decisions", () => {
  const tasks = decomposeUserRequest("가상 회사형 제작 회의로 요청 분석, OpenClaw 실행, Hermes 리뷰, 최종 합성을 진행해줘.");
  const report = assignDecomposedTasksToAgentRoles(tasks);
  const invalidAssignments = [
    report.assignments[0],
    {
      ...report.assignments[1],
      assignedRole: "hermes-reviewer",
    },
    {
      ...report.assignments[2],
      assignedRole: "finance-agent",
    },
    {
      ...report.assignments[2],
      assignedRole: "hermes-reviewer",
    },
  ];

  const validation = validateTaskRoleAssignments(tasks, invalidAssignments);

  assert.equal(validation.valid, false);
  assert.equal(validation.inputTaskCount, 4);
  assert.equal(validation.assignmentCount, 4);
  assert.deepEqual(
    validation.failures.map((failure) => ({
      kind: failure.kind,
      taskId: failure.taskId,
      assignmentIndex: failure.assignmentIndex,
      assignedRole: failure.assignedRole,
      expectedRoles: failure.expectedRoles,
    })),
    [
      {
        kind: "role_mismatch",
        taskId: "task-002",
        assignmentIndex: 1,
        assignedRole: "hermes-reviewer",
        expectedRoles: ["openclaw-owner"],
      },
      {
        kind: "unsupported_role",
        taskId: "task-003",
        assignmentIndex: 2,
        assignedRole: "finance-agent",
        expectedRoles: ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
      },
      {
        kind: "duplicate_assignment",
        taskId: "task-003",
        assignmentIndex: 3,
        assignedRole: undefined,
        expectedRoles: undefined,
      },
      {
        kind: "missing_assignment",
        taskId: "task-004",
        assignmentIndex: undefined,
        assignedRole: undefined,
        expectedRoles: ["openclaw-finalizer"],
      },
    ],
  );
  assert.match(validation.failures.map((failure) => failure.message).join("\n"), /unsupported role: finance-agent/);
});
