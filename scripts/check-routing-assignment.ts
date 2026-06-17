import assert from "node:assert/strict";
import {
  assignDecomposedTasksToAgentRoles,
  buildRoleRoutes,
  buildRoleRoutingMetadata,
  decomposeUserRequest,
  validateTaskRoleAssignments,
} from "../src/index.ts";
import type { AgentRole } from "../src/index.ts";
import type { TaskRoleAssignmentValidationReport } from "../src/index.ts";

interface RoutingAssignment {
  taskId: string;
  taskTitle: string;
  taskRationale: string;
  assignedRole: AgentRole;
  responsibility: string;
}

interface RoutingAssignmentCheckResult {
  command: "ai-agent check-routing-assignment";
  status: "passed";
  scenario: "representative_decomposed_task_set";
  deterministic: boolean;
  executionResponsibilityProof: {
    executionTaskIds: string[];
    openclawExecutionRole: "openclaw-owner";
    allExecutionTasksAssignedToOpenClaw: boolean;
    responsibilities: string[];
  };
  reviewResponsibilityProof: {
    reviewTaskIds: string[];
    hermesReviewRole: "hermes-reviewer";
    allReviewTasksAssignedToHermes: boolean;
    reviewSignals: string[];
    responsibilities: string[];
  };
  assignmentValidationProof: TaskRoleAssignmentValidationReport;
  deterministicInputRuns: Array<{
    runId: "first" | "second";
    assignments: RoutingAssignment[];
  }>;
  artifact: {
    userRequest: string;
    decomposedTaskInput: Array<{
      taskId: string;
      taskTitle: string;
      taskRationale: string;
    }>;
    inputTaskCount: number;
    assignments: RoutingAssignment[];
    routeCount: number;
    metadata: ReturnType<typeof buildRoleRoutingMetadata>;
  };
}

const representativeRequest = "브랜드 영상 제작 회의를 진행하고 최종 산출물을 합성해줘.";

export function checkRoutingAssignment(): RoutingAssignmentCheckResult {
  const first = buildRoutingAssignmentArtifact(representativeRequest);
  const second = buildRoutingAssignmentArtifact(representativeRequest);
  const firstInputRun = buildAssignmentsFromDecomposedTasks(first.decomposedTaskInput, "first");
  const secondInputRun = buildAssignmentsFromDecomposedTasks(first.decomposedTaskInput.map((task) => ({ ...task })), "second");
  const executionResponsibilityProof = buildExecutionResponsibilityProof(first.assignments);
  const reviewResponsibilityProof = buildReviewResponsibilityProof(first.assignments);
  const assignmentValidationProof = validateTaskRoleAssignments(
    first.decomposedTaskInput.map((task) => ({
      id: task.taskId,
      title: task.taskTitle,
      rationale: task.taskRationale,
    })),
    first.assignments,
  );

  assert.deepEqual(first, second, "routing assignment payload must be deterministic");
  assert.deepEqual(
    firstInputRun.assignments,
    secondInputRun.assignments,
    "identical decomposed task inputs must produce identical role assignments",
  );
  assert.deepEqual(
    firstInputRun.assignments,
    first.assignments,
    "routing assignment payload must match assignments built from decomposed task input",
  );
  assert.equal(first.assignments.length, first.decomposedTaskInput.length, "routing must return one assignment per input task");
  assert.equal(first.routeCount, first.inputTaskCount, "routing metadata must preserve one route per input task");
  assert.deepEqual(assignmentValidationProof.failures, [], "routing assignment validation must not report failures");
  assert.equal(assignmentValidationProof.valid, true, "routing assignment validation must pass");
  assert.deepEqual(first.assignments, [
    {
      taskId: "task-001",
      taskTitle: "요청 의도와 성공 기준 정리",
      taskRationale: "회의 루프가 같은 목표를 검토하도록 사용자 요청을 짧은 기준으로 고정한다.",
      assignedRole: "openclaw-owner",
      responsibility: "요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
    },
    {
      taskId: "task-002",
      taskTitle: "OpenClaw 실행 초안 작성",
      taskRationale: "owner persona가 실행 가능한 1차 산출물을 만든다.",
      assignedRole: "openclaw-owner",
      responsibility: "실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
    },
    {
      taskId: "task-003",
      taskTitle: "Hermes 리뷰와 수렴 판단",
      taskRationale: "reviewer persona가 초안의 오류, 누락, 사용자 결정 필요성을 판정한다.",
      assignedRole: "hermes-reviewer",
      responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
    },
    {
      taskId: "task-004",
      taskTitle: "최종 합성 또는 escalation",
      taskRationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
      assignedRole: "openclaw-finalizer",
      responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
    },
  ]);
  assert.deepEqual(first.metadata, {
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
  assert.deepEqual(executionResponsibilityProof, {
    executionTaskIds: ["task-002"],
    openclawExecutionRole: "openclaw-owner",
    allExecutionTasksAssignedToOpenClaw: true,
    responsibilities: ["실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다."],
  });
  assert.deepEqual(reviewResponsibilityProof, {
    reviewTaskIds: ["task-003"],
    hermesReviewRole: "hermes-reviewer",
    allReviewTasksAssignedToHermes: true,
    reviewSignals: ["리뷰", "검토", "리스크", "수렴", "판정"],
    responsibilities: ["초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다."],
  });

  return {
    command: "ai-agent check-routing-assignment",
    status: "passed",
    scenario: "representative_decomposed_task_set",
    deterministic: true,
    executionResponsibilityProof,
    reviewResponsibilityProof,
    assignmentValidationProof,
    deterministicInputRuns: [firstInputRun, secondInputRun],
    artifact: first,
  };
}

export function executeCheckRoutingAssignmentCommand(): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkRoutingAssignment();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown routing assignment check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "routing_assignment_check_failed", message }, null, 2)}\n`,
    };
  }
}

function buildRoutingAssignmentArtifact(userRequest: string): RoutingAssignmentCheckResult["artifact"] {
  const tasks = decomposeUserRequest(userRequest);
  const routes = buildRoleRoutes(tasks);
  const assignmentReport = assignDecomposedTasksToAgentRoles(tasks);
  assert.equal(assignmentReport.oneAssignmentPerTask, true, "role routing module must assign exactly one role per decomposed task");
  const assignments = assignmentReport.assignments;

  return {
    userRequest,
    decomposedTaskInput: tasks.map((task) => ({
      taskId: task.id,
      taskTitle: task.title,
      taskRationale: task.rationale,
    })),
    inputTaskCount: tasks.length,
    assignments,
    routeCount: routes.length,
    metadata: buildRoleRoutingMetadata(routes),
  };
}

function buildAssignmentsFromDecomposedTasks(
  decomposedTasks: RoutingAssignmentCheckResult["artifact"]["decomposedTaskInput"],
  runId: "first" | "second",
): RoutingAssignmentCheckResult["deterministicInputRuns"][number] {
  const tasks = decomposedTasks.map((task) => ({
    id: task.taskId,
    title: task.taskTitle,
    rationale: task.taskRationale,
  }));
  const assignmentReport = assignDecomposedTasksToAgentRoles(tasks);
  assert.equal(assignmentReport.oneAssignmentPerTask, true, "role routing module must assign exactly one role per decomposed task");

  return {
    runId,
    assignments: assignmentReport.assignments.map((assignment, index) => {
      const task = decomposedTasks[index];
      return {
        taskId: task.taskId,
        taskTitle: task.taskTitle,
        taskRationale: task.taskRationale,
        assignedRole: assignment.assignedRole,
        responsibility: assignment.responsibility,
      };
    }),
  };
}

function buildExecutionResponsibilityProof(
  assignments: RoutingAssignment[],
): RoutingAssignmentCheckResult["executionResponsibilityProof"] {
  const executionAssignments = assignments.filter((assignment) =>
    /OpenClaw 실행|owner persona가 실행/i.test(`${assignment.taskTitle} ${assignment.taskRationale}`),
  );

  return {
    executionTaskIds: executionAssignments.map((assignment) => assignment.taskId),
    openclawExecutionRole: "openclaw-owner",
    allExecutionTasksAssignedToOpenClaw: executionAssignments.every((assignment) => assignment.assignedRole === "openclaw-owner"),
    responsibilities: executionAssignments.map((assignment) => assignment.responsibility),
  };
}

function buildReviewResponsibilityProof(
  assignments: RoutingAssignment[],
): RoutingAssignmentCheckResult["reviewResponsibilityProof"] {
  const reviewSignals = ["리뷰", "검토", "리스크", "수렴", "판정"];
  const reviewAssignments = assignments.filter((assignment) =>
    /Hermes 리뷰|reviewer persona|초안의 오류|누락|리스크 식별|수렴 여부|판정/.test(
      `${assignment.taskTitle} ${assignment.taskRationale} ${assignment.responsibility}`,
    ),
  );

  return {
    reviewTaskIds: reviewAssignments.map((assignment) => assignment.taskId),
    hermesReviewRole: "hermes-reviewer",
    allReviewTasksAssignedToHermes: reviewAssignments.every((assignment) => assignment.assignedRole === "hermes-reviewer"),
    reviewSignals,
    responsibilities: reviewAssignments.map((assignment) => assignment.responsibility),
  };
}

const invokedAsScript = process.argv[1]?.endsWith("check-routing-assignment.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckRoutingAssignmentCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
