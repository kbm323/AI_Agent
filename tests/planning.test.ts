import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  analyzeUserRequest,
  assessTaskDecompositionOverlap,
  buildRoleRoutes,
  buildRoleRoutingMetadata,
  buildStructuredAnalysisArtifact,
  buildDefaultTokenStrategy,
  decomposeUserRequest,
  formatRoleRoute,
  matchRoleForTask,
  parseTaskBreakdownFromAnalysisArtifact,
  taskDecompositionRequiredFields,
  validateTaskDecompositionOutput,
  type StructuredAnalysisArtifact,
} from "../src/index.ts";
import { checkRequestAnalysis, executeCheckRequestAnalysisCommand } from "../scripts/check-request-analysis.ts";
import {
  checkTaskDecompositionStability,
  executeCheckTaskDecompositionStabilityCommand,
} from "../scripts/check-task-decomposition-stability.ts";
import { checkRoutingAssignment, executeCheckRoutingAssignmentCommand } from "../scripts/check-routing-assignment.ts";
import { checkTaskOverlap, executeCheckTaskOverlapCommand } from "../scripts/check-task-overlap.ts";

test("public task decomposition returns the MVP meeting workflow tasks", () => {
  const tasks = decomposeUserRequest("가상 회사형 멀티 에이전트 회의로 영상 제작안을 만들어줘.");

  assert.deepEqual(
    tasks.map((task) => [task.id, task.title]),
    [
      ["task-001", "요청 의도와 성공 기준 정리"],
      ["task-002", "OpenClaw 실행 초안 작성"],
      ["task-003", "Hermes 리뷰와 수렴 판단"],
      ["task-004", "최종 합성 또는 escalation"],
    ],
  );
  assert.equal(tasks.every((task) => task.rationale.length > 0), true);
});

test("task decomposition module emits schema-valid structured output", () => {
  const tasks = decomposeUserRequest("브랜드 영상 제작 회의로 요청 분석, 실행, 리뷰, 최종 합성을 진행해줘.");
  const validation = validateTaskDecompositionOutput(tasks);

  assert.deepEqual(validation, {
    valid: true,
    errors: [],
    taskCount: 4,
    requiredFields: ["id", "title", "rationale"],
  });
  assert.deepEqual(
    tasks.map((task) => Object.keys(task).sort()),
    [
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
    ],
  );
  assert.deepEqual(
    tasks.map((task) => task.dependsOn),
    [[], ["task-001"], ["task-002"], ["task-003"]],
  );
});

test("public task decomposition required fields expose the schema contract used by validation", () => {
  const tasks = decomposeUserRequest("제품 소개 영상 제작 회의를 열어줘.");
  const validation = validateTaskDecompositionOutput(tasks);

  assert.deepEqual(taskDecompositionRequiredFields, ["id", "title", "rationale"]);
  assert.deepEqual(validation.requiredFields, taskDecompositionRequiredFields);
  assert.equal(
    tasks.every((task) =>
      taskDecompositionRequiredFields.every((field) => typeof task[field] === "string" && task[field].trim().length > 0),
    ),
    true,
  );
});

test("emitted task decomposition units are non-overlapping by module rules", () => {
  const tasks = decomposeUserRequest("가상 회사형 멀티 에이전트 회의로 영상 제작안을 만들어줘.");
  const report = assessTaskDecompositionOverlap(tasks);

  assert.deepEqual(report, {
    nonOverlapping: true,
    checkedRules: ["duplicate_task_id", "duplicate_title_fingerprint", "duplicate_workflow_scope", "unknown_workflow_scope"],
    taskCount: 4,
    overlaps: [],
  });
});

test("task overlap detector reports duplicate decomposition units", () => {
  const [first, second, ...rest] = decomposeUserRequest("가상 회사형 멀티 에이전트 회의로 영상 제작안을 만들어줘.");
  const report = assessTaskDecompositionOverlap([
    first,
    { ...second, id: first.id, title: first.title },
    ...rest,
  ]);

  assert.equal(report.nonOverlapping, false);
  assert.deepEqual(
    report.overlaps.map((overlap) => overlap.rule),
    ["duplicate_task_id", "duplicate_title_fingerprint", "duplicate_workflow_scope"],
  );
});

test("task decomposition validation rejects overlapping generated sibling tasks", () => {
  const [first, second, ...rest] = decomposeUserRequest("가상 회사형 멀티 에이전트 회의로 영상 제작안을 만들어줘.");
  const overlappingTasks = [
    first,
    { ...second, id: first.id, title: first.title },
    ...rest,
  ];
  const validation = validateTaskDecompositionOutput(overlappingTasks);

  assert.equal(validation.valid, false);
  assert.equal(validation.taskCount, 4);
  assert.deepEqual(validation.requiredFields, taskDecompositionRequiredFields);
  assert.deepEqual(validation.errors, [
    "taskBreakdown sibling overlap duplicate_task_id for task-001, task-001 using value task-001",
    "taskBreakdown sibling overlap duplicate_title_fingerprint for task-001, task-001 using value 요청 의도와 성공 기준 정리",
    "taskBreakdown sibling overlap duplicate_workflow_scope for task-001, task-001 using value request_analysis_and_success_criteria",
    "taskBreakdown dependency task-001 must not reference itself",
    "taskBreakdown dependency task-003 references unknown task id task-002",
    "taskBreakdown dependency task-003 must reference an earlier task id: task-002",
  ]);
  assert.throws(
    () => buildRoleRoutes(overlappingTasks),
    /task decomposition must contain non-overlapping sibling tasks: taskBreakdown sibling overlap duplicate_task_id/,
  );
});

test("public request analysis uses the same decomposition and role routing success path", () => {
  const userRequest = "  제품 소개   회의를 진행하고\n최종안을 만들어줘.  ";
  const analysis = analyzeUserRequest(userRequest);

  assert.deepEqual(analysis.intent, {
    summary: "제품 소개 회의를 진행하고 최종안을 만들어줘.",
    primaryGoal: "Run the requested work through the MVP virtual-company multi-agent meeting flow.",
    meetingSystem: "virtual-company-multi-agent-meeting",
    workflow: "analysis-routing-openclaw-hermes-synthesis",
  });
  assert.deepEqual(
    analysis.constraints.map((constraint) => `${constraint.source}:${constraint.id}`),
    ["mvp-default:mvp-flow-required", "mvp-default:compressed-loop-context"],
  );
  assert.deepEqual(
    analysis.requiredOutputs.map((output) => `${output.id}:${output.producedBy}`),
    [
      "task_breakdown:openclaw-owner",
      "role_routes:system",
      "meeting_loop_result:hermes-reviewer",
      "final_synthesis:openclaw-finalizer",
      "escalation:openclaw-finalizer",
    ],
  );
  assert.deepEqual(analysis.taskBreakdown, decomposeUserRequest(userRequest));
  assert.deepEqual(
    analysis.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.equal(analysis.userRequestSummary, "제품 소개 회의를 진행하고 최종안을 만들어줘.");
});

test("public role matcher maps each MVP meeting task to its primary role", () => {
  assert.deepEqual(
    ["task-001", "task-002", "task-003", "task-004"].map((taskId) => matchRoleForTask(taskId)),
    [
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
    ],
  );
});

test("public role matcher accepts task objects from the decomposition output", () => {
  const tasks = decomposeUserRequest("서비스 런칭 제작 회의를 진행해줘.");

  assert.deepEqual(matchRoleForTask(tasks[2]), {
    taskId: "task-003",
    role: "hermes-reviewer",
    responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
  });
});

test("public route builder matches decomposed tasks in workflow order", () => {
  const tasks = decomposeUserRequest("신제품 제작 회의 루프를 실행해줘.");

  assert.deepEqual(buildRoleRoutes(tasks), tasks.map((task) => matchRoleForTask(task)));
  assert.deepEqual(
    buildRoleRoutes(tasks).map((route) => route.role),
    ["openclaw-owner", "openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
  );
});

test("module routing rules assign every decomposed meeting task to the required persona route", () => {
  const expectedRoutingRules = [
    {
      taskId: "task-001",
      taskTitle: "요청 의도와 성공 기준 정리",
      requiredRole: "openclaw-owner",
      requiredResponsibilityEvidence: "회의 목표",
    },
    {
      taskId: "task-002",
      taskTitle: "OpenClaw 실행 초안 작성",
      requiredRole: "openclaw-owner",
      requiredResponsibilityEvidence: "Hermes",
    },
    {
      taskId: "task-003",
      taskTitle: "Hermes 리뷰와 수렴 판단",
      requiredRole: "hermes-reviewer",
      requiredResponsibilityEvidence: "수렴 여부",
    },
    {
      taskId: "task-004",
      taskTitle: "최종 합성 또는 escalation",
      requiredRole: "openclaw-finalizer",
      requiredResponsibilityEvidence: "escalation",
    },
  ] as const;

  const tasks = decomposeUserRequest("가상 회사형 제작 회의로 요청을 분석하고 최종안을 합성해줘.");
  const routes = buildRoleRoutes(tasks);

  assert.equal(routes.length, expectedRoutingRules.length);
  assert.deepEqual(
    expectedRoutingRules.map((rule, index) => {
      const task = tasks[index];
      const route = routes[index];

      return {
        taskId: task.id,
        taskTitle: task.title,
        routedTaskId: route.taskId,
        role: route.role,
        responsibilityMatchesRule: route.responsibility.includes(rule.requiredResponsibilityEvidence),
      };
    }),
    expectedRoutingRules.map((rule) => ({
      taskId: rule.taskId,
      taskTitle: rule.taskTitle,
      routedTaskId: rule.taskId,
      role: rule.requiredRole,
      responsibilityMatchesRule: true,
    })),
  );
});

test("role routing accepts parsed decomposed task structures without schema loss", () => {
  const artifact = buildStructuredAnalysisArtifact("고객 온보딩 영상 제작 회의로 실행안과 리뷰를 정리해줘.");
  const tasks = parseTaskBreakdownFromAnalysisArtifact(artifact);
  const routes = buildRoleRoutes(tasks);

  assert.deepEqual(
    tasks.map((task) => Object.keys(task).sort()),
    [
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
    ],
  );
  assert.deepEqual(
    routes.map((route, index) => ({
      taskId: route.taskId,
      sourceTaskId: tasks[index].id,
      sourceTitle: tasks[index].title,
      sourceRationale: tasks[index].rationale,
      sourceDependencies: tasks[index].dependsOn,
      role: route.role,
      responsibility: route.responsibility,
    })),
    [
      {
        taskId: "task-001",
        sourceTaskId: "task-001",
        sourceTitle: "요청 의도와 성공 기준 정리",
        sourceRationale: "회의 루프가 같은 목표를 검토하도록 사용자 요청을 짧은 기준으로 고정한다.",
        sourceDependencies: [],
        role: "openclaw-owner",
        responsibility: "요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
      },
      {
        taskId: "task-002",
        sourceTaskId: "task-002",
        sourceTitle: "OpenClaw 실행 초안 작성",
        sourceRationale: "owner persona가 실행 가능한 1차 산출물을 만든다.",
        sourceDependencies: ["task-001"],
        role: "openclaw-owner",
        responsibility: "실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
      },
      {
        taskId: "task-003",
        sourceTaskId: "task-003",
        sourceTitle: "Hermes 리뷰와 수렴 판단",
        sourceRationale: "reviewer persona가 초안의 오류, 누락, 사용자 결정 필요성을 판정한다.",
        sourceDependencies: ["task-002"],
        role: "hermes-reviewer",
        responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
      },
      {
        taskId: "task-004",
        sourceTaskId: "task-004",
        sourceTitle: "최종 합성 또는 escalation",
        sourceRationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
        sourceDependencies: ["task-003"],
        role: "openclaw-finalizer",
        responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
      },
    ],
  );
});

test("task decomposition maps every structured analysis item into a required-field task object", () => {
  const artifact = buildStructuredAnalysisArtifact("신규 서비스 출시 회의로 요청 분석부터 최종 합성까지 진행해줘.");
  const structuredItems = [
    {
      id: "task-001",
      title: "구조화 항목 1: 요청 분석",
      rationale: "structured analysis item 1 must become the first task rationale.",
      dependsOn: [],
    },
    {
      id: "task-002",
      title: "구조화 항목 2: OpenClaw 실행",
      rationale: "structured analysis item 2 must become the second task rationale.",
      dependsOn: ["task-001"],
    },
    {
      id: "task-003",
      title: "구조화 항목 3: Hermes 검토",
      rationale: "structured analysis item 3 must become the third task rationale.",
      dependsOn: ["task-002"],
    },
    {
      id: "task-004",
      title: "구조화 항목 4: 최종 합성",
      rationale: "structured analysis item 4 must become the fourth task rationale.",
      dependsOn: ["task-003"],
    },
  ];
  const parsedTasks = parseTaskBreakdownFromAnalysisArtifact({
    ...artifact,
    taskBreakdown: structuredItems,
  });
  const validation = validateTaskDecompositionOutput(parsedTasks);

  assert.equal(parsedTasks.length, structuredItems.length);
  assert.deepEqual(parsedTasks, structuredItems);
  assert.deepEqual(validation, {
    valid: true,
    errors: [],
    taskCount: structuredItems.length,
    requiredFields: taskDecompositionRequiredFields,
  });
  assert.deepEqual(
    parsedTasks.map((task) =>
      Object.fromEntries(taskDecompositionRequiredFields.map((field) => [field, typeof task[field] === "string" && task[field].length > 0])),
    ),
    structuredItems.map(() => ({ id: true, title: true, rationale: true })),
  );
});

test("structured analysis parsing rejects overlapping sibling task artifacts", () => {
  const artifact = buildStructuredAnalysisArtifact("브랜드 영상 제작 회의로 요청 분석, 실행, 리뷰, 합성을 진행해줘.");

  assert.throws(
    () =>
      parseTaskBreakdownFromAnalysisArtifact({
        ...artifact,
        taskBreakdown: [
          artifact.taskBreakdown[0],
          { ...artifact.taskBreakdown[1], id: "task-001", title: artifact.taskBreakdown[0].title },
          ...artifact.taskBreakdown.slice(2),
        ],
        roleRoutes: [
          artifact.roleRoutes[0],
          { ...artifact.roleRoutes[1], taskId: "task-001" },
          ...artifact.roleRoutes.slice(2),
        ],
      }),
    /task decomposition must contain non-overlapping sibling tasks: taskBreakdown sibling overlap duplicate_task_id/,
  );
});

test("task decomposition derives and attaches dependencies from structured analysis results", () => {
  const artifact = buildStructuredAnalysisArtifact("브랜드 영상 제작 회의로 실행과 검토, 최종 합성을 진행해줘.");
  const parsedTasks = parseTaskBreakdownFromAnalysisArtifact({
    ...artifact,
    taskBreakdown: [
      { ...artifact.taskBreakdown[0], dependsOn: [] },
      { ...artifact.taskBreakdown[1], dependsOn: ["task-001"] },
      { ...artifact.taskBreakdown[2], dependsOn: ["task-001", "task-002"] },
      { ...artifact.taskBreakdown[3], dependsOn: ["task-003"] },
    ],
  });

  assert.deepEqual(
    parsedTasks.map((task) => ({ id: task.id, dependsOn: task.dependsOn })),
    [
      { id: "task-001", dependsOn: [] },
      { id: "task-002", dependsOn: ["task-001"] },
      { id: "task-003", dependsOn: ["task-001", "task-002"] },
      { id: "task-004", dependsOn: ["task-003"] },
    ],
  );
  assert.deepEqual(validateTaskDecompositionOutput(parsedTasks).errors, []);
});

test("task decomposition rejects structured analysis dependencies that are missing or out of order", () => {
  const artifact = buildStructuredAnalysisArtifact("브랜드 영상 제작 회의로 실행과 검토, 최종 합성을 진행해줘.");

  assert.throws(
    () =>
      parseTaskBreakdownFromAnalysisArtifact({
        ...artifact,
        taskBreakdown: [
          { ...artifact.taskBreakdown[0], dependsOn: ["task-404"] },
          { ...artifact.taskBreakdown[1], dependsOn: ["task-003"] },
          ...artifact.taskBreakdown.slice(2),
        ],
      }),
    /taskBreakdown dependency task-001 references unknown task id task-404/,
  );
});

test("public multi-role routing delegates every meeting phase to the expected persona", () => {
  const tasks = decomposeUserRequest("브랜드 영상 제작 회의를 열고 최종 산출물을 합성해줘.");
  const routes = buildRoleRoutes(tasks);

  assert.deepEqual(
    routes.map((route) => ({
      taskId: route.taskId,
      role: route.role,
      hasResponsibility: route.responsibility.length > 0,
    })),
    [
      { taskId: "task-001", role: "openclaw-owner", hasResponsibility: true },
      { taskId: "task-002", role: "openclaw-owner", hasResponsibility: true },
      { taskId: "task-003", role: "hermes-reviewer", hasResponsibility: true },
      { taskId: "task-004", role: "openclaw-finalizer", hasResponsibility: true },
    ],
  );
  assert.equal(routes[1].responsibility.includes("Hermes"), true);
  assert.equal(routes[2].responsibility.includes("수렴 여부"), true);
  assert.equal(routes[3].responsibility.includes("escalation"), true);
});

test("public route formatter returns stable meeting routing lines", () => {
  const routes = buildRoleRoutes(decomposeUserRequest("앱 출시 제작 회의 결과를 정리해줘."));

  assert.deepEqual(routes.map((route) => formatRoleRoute(route)), [
    "- task-001 -> openclaw-owner: 요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
    "- task-002 -> openclaw-owner: 실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
    "- task-003 -> hermes-reviewer: 초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
    "- task-004 -> openclaw-finalizer: 합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
  ]);
});

test("public route metadata summarizes the primary routing success path", () => {
  const routes = buildRoleRoutes(decomposeUserRequest("브랜드 캠페인 제작 회의를 진행해줘."));

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

test("public default token strategy preserves raw storage and compressed loop context boundaries", () => {
  const tokenStrategy = buildDefaultTokenStrategy();

  assert.deepEqual(Object.keys(tokenStrategy).sort(), [
    "compressionPolicy",
    "exposedLoopContext",
    "rawStorage",
    "targetReduction",
  ]);
  assert.match(tokenStrategy.rawStorage, /SQLite/);
  assert.match(tokenStrategy.exposedLoopContext, /summaries/);
  assert.match(tokenStrategy.compressionPolicy, /full raw text/);
  assert.match(tokenStrategy.targetReduction, /40-50%/);
});

test("request analysis check emits a stable minimum scenario artifact", () => {
  const result = checkRequestAnalysis();

  assert.equal(result.command, "ai-agent check-request-analysis");
  assert.equal(result.status, "passed");
  assert.equal(result.scenario, "minimum");
  assert.equal(result.deterministic, true);
  assert.equal(result.artifact.schemaVersion, "request-analysis.v1");
  assert.equal(result.artifact.intent.workflow, "analysis-routing-openclaw-hermes-synthesis");
  assert.deepEqual(
    result.artifact.constraints.map((constraint) => constraint.id),
    ["mvp-flow-required", "compressed-loop-context"],
  );
  assert.deepEqual(
    result.artifact.requiredOutputs.map((output) => output.id),
    ["task_breakdown", "role_routes", "meeting_loop_result", "final_synthesis", "escalation"],
  );
  assert.deepEqual(
    result.artifact.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.match(result.artifact.tokenStrategy.targetReduction, /40-50%/);
});

test("request analysis command returns parseable JSON on success", () => {
  const result = executeCheckRequestAnalysisCommand();
  const output = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(output.command, "ai-agent check-request-analysis");
  assert.equal(output.artifact.userRequestSummary, "브랜드 영상 제작 회의를 진행하고 최종 산출물을 합성해줘.");
});

test("task overlap check emits stable proof that decomposition units do not overlap", () => {
  const result = checkTaskOverlap();

  assert.equal(result.command, "ai-agent check-task-overlap");
  assert.equal(result.status, "passed");
  assert.equal(result.deterministic, true);
  assert.equal(result.artifact.nonOverlapping, true);
  assert.equal(result.artifact.taskCount, 4);
  assert.deepEqual(result.artifact.overlaps, []);
});

test("task overlap command returns parseable JSON on success", () => {
  const result = executeCheckTaskOverlapCommand();
  const output = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(output.command, "ai-agent check-task-overlap");
  assert.equal(output.artifact.nonOverlapping, true);
  assert.deepEqual(output.artifact.overlaps, []);
});

test("routing assignment check emits the expected assignment payload", () => {
  const result = checkRoutingAssignment();

  assert.equal(result.command, "ai-agent check-routing-assignment");
  assert.equal(result.status, "passed");
  assert.equal(result.scenario, "representative_decomposed_task_set");
  assert.equal(result.deterministic, true);
  assert.deepEqual(result.executionResponsibilityProof, {
    executionTaskIds: ["task-002"],
    openclawExecutionRole: "openclaw-owner",
    allExecutionTasksAssignedToOpenClaw: true,
    responsibilities: ["실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다."],
  });
  assert.equal(result.artifact.inputTaskCount, 4);
  assert.equal(result.artifact.routeCount, result.artifact.inputTaskCount);
  assert.deepEqual(
    result.artifact.decomposedTaskInput.map((task) => task.taskId),
    result.artifact.assignments.map((assignment) => assignment.taskId),
  );
  assert.deepEqual(
    result.artifact.assignments.map((assignment) => ({
      taskId: assignment.taskId,
      taskTitle: assignment.taskTitle,
      assignedRole: assignment.assignedRole,
      hasResponsibility: assignment.responsibility.length > 0,
    })),
    [
      {
        taskId: "task-001",
        taskTitle: "요청 의도와 성공 기준 정리",
        assignedRole: "openclaw-owner",
        hasResponsibility: true,
      },
      {
        taskId: "task-002",
        taskTitle: "OpenClaw 실행 초안 작성",
        assignedRole: "openclaw-owner",
        hasResponsibility: true,
      },
      {
        taskId: "task-003",
        taskTitle: "Hermes 리뷰와 수렴 판단",
        assignedRole: "hermes-reviewer",
        hasResponsibility: true,
      },
      {
        taskId: "task-004",
        taskTitle: "최종 합성 또는 escalation",
        assignedRole: "openclaw-finalizer",
        hasResponsibility: true,
      },
    ],
  );
  assert.deepEqual(result.artifact.metadata.workflowOrder, [
    "task-001:openclaw-owner",
    "task-002:openclaw-owner",
    "task-003:hermes-reviewer",
    "task-004:openclaw-finalizer",
  ]);
  assert.equal(result.artifact.metadata.hasHermesReview, true);
  assert.equal(result.artifact.metadata.hasFinalizer, true);
});

test("routing assignment command returns parseable JSON on success", () => {
  const result = executeCheckRoutingAssignmentCommand();
  const output = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(output.command, "ai-agent check-routing-assignment");
  assert.deepEqual(output.executionResponsibilityProof, {
    executionTaskIds: ["task-002"],
    openclawExecutionRole: "openclaw-owner",
    allExecutionTasksAssignedToOpenClaw: true,
    responsibilities: ["실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다."],
  });
  assert.equal(output.artifact.inputTaskCount, 4);
  assert.equal(output.artifact.assignments.length, 4);
  assert.equal(output.artifact.routeCount, output.artifact.inputTaskCount);
  assert.deepEqual(
    output.artifact.decomposedTaskInput.map((task: { taskId: string }) => task.taskId),
    output.artifact.assignments.map((assignment: { taskId: string }) => assignment.taskId),
  );
  assert.deepEqual(
    output.artifact.assignments.map((assignment: { taskId: string; assignedRole: string }) => `${assignment.taskId}->${assignment.assignedRole}`),
    [
      "task-001->openclaw-owner",
      "task-002->openclaw-owner",
      "task-003->hermes-reviewer",
      "task-004->openclaw-finalizer",
    ],
  );
});

test("task decomposition accepts the structured request analysis artifact contract", () => {
  const { artifact } = checkRequestAnalysis();
  const tasks = parseTaskBreakdownFromAnalysisArtifact(artifact);

  assert.deepEqual(tasks, decomposeUserRequest(artifact.userRequestSummary));
  assert.deepEqual(
    tasks.map((task) => `${task.id}:${task.title}`),
    [
      "task-001:요청 의도와 성공 기준 정리",
      "task-002:OpenClaw 실행 초안 작성",
      "task-003:Hermes 리뷰와 수렴 판단",
      "task-004:최종 합성 또는 escalation",
    ],
  );
  assert.equal(tasks.every((task) => task.rationale.length > 0), true);
});

test("task decomposition accepts a valid request analysis output fixture as input", () => {
  const fixtureUrl = new URL("./fixtures/request-analysis-output.json", import.meta.url);
  const artifact = JSON.parse(readFileSync(fixtureUrl, "utf8")) as StructuredAnalysisArtifact;
  const tasks = parseTaskBreakdownFromAnalysisArtifact(artifact);

  assert.deepEqual(
    tasks.map((task) => ({ id: task.id, dependsOn: task.dependsOn })),
    [
      { id: "task-001", dependsOn: [] },
      { id: "task-002", dependsOn: ["task-001"] },
      { id: "task-003", dependsOn: ["task-002"] },
      { id: "task-004", dependsOn: ["task-003"] },
    ],
  );
  assert.deepEqual(
    tasks.map((task) => `${task.id}:${task.title}`),
    [
      "task-001:요청 의도와 성공 기준 정리",
      "task-002:OpenClaw 실행 초안 작성",
      "task-003:Hermes 리뷰와 수렴 판단",
      "task-004:최종 합성 또는 escalation",
    ],
  );
  assert.deepEqual(
    buildRoleRoutes(tasks).map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
});

test("task decomposition is deterministic across repeated structured analysis artifact execution", () => {
  const artifact = buildStructuredAnalysisArtifact("브랜드 영상 제작 회의로 콘셉트와 실행안을 정리해줘.");
  const serializedArtifact = JSON.stringify(artifact);

  const first = parseTaskBreakdownFromAnalysisArtifact(artifact);
  const second = parseTaskBreakdownFromAnalysisArtifact(artifact);
  const third = parseTaskBreakdownFromAnalysisArtifact(JSON.parse(serializedArtifact));

  assert.deepEqual(first, second);
  assert.deepEqual(first, third);
  assert.deepEqual(first, decomposeUserRequest(artifact.userRequestSummary));
  assert.equal(JSON.stringify(artifact), serializedArtifact);
});

test("task decomposition stability check proves repeated fixed artifact execution", () => {
  const result = checkTaskDecompositionStability();

  assert.equal(result.command, "ai-agent check-task-decomposition-stability");
  assert.equal(result.status, "passed");
  assert.equal(result.scenario, "fixed_request_analysis_artifact_repeated_runs");
  assert.equal(result.deterministic, true);
  assert.equal(result.runs.length, 5);
  assert.equal(new Set(result.runs.map((run) => run.outputSha256)).size, 1);
  assert.deepEqual(
    result.runs.map((run) => run.taskBreakdown),
    [
      result.artifact.taskBreakdown,
      result.artifact.taskBreakdown,
      result.artifact.taskBreakdown,
      result.artifact.taskBreakdown,
      result.artifact.taskBreakdown,
    ],
  );
  assert.deepEqual(
    result.artifact.taskBreakdown.map((task) => `${task.id}:${task.title}`),
    [
      "task-001:요청 의도와 성공 기준 정리",
      "task-002:OpenClaw 실행 초안 작성",
      "task-003:Hermes 리뷰와 수렴 판단",
      "task-004:최종 합성 또는 escalation",
    ],
  );
});

test("task decomposition stability command returns parseable JSON on success", () => {
  const result = executeCheckTaskDecompositionStabilityCommand();
  const output = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(output.command, "ai-agent check-task-decomposition-stability");
  assert.equal(output.deterministic, true);
  assert.equal(output.sourceInput.artifactPath, "tests/fixtures/request-analysis-output.json");
  assert.equal(output.runs.length, 5);
  assert.equal(new Set(output.runs.map((run: { outputSha256: string }) => run.outputSha256)).size, 1);
  assert.deepEqual(output.runs[0].taskBreakdown, output.artifact.taskBreakdown);
});

test("role routing is deterministic across repeated identical decomposed tasks", () => {
  const tasks = decomposeUserRequest("브랜드 영상 제작 회의로 콘셉트와 실행안을 정리해줘.");
  const expectedAssignments = [
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
  ];

  const repeatedAssignments = Array.from({ length: 5 }, () => buildRoleRoutes(tasks));

  assert.deepEqual(repeatedAssignments, [
    expectedAssignments,
    expectedAssignments,
    expectedAssignments,
    expectedAssignments,
    expectedAssignments,
  ]);
  assert.deepEqual(buildRoleRoutes([...tasks]), expectedAssignments);
});

test("public structured analysis artifact emits task decomposition in the expected schema", () => {
  const artifact = buildStructuredAnalysisArtifact("브랜드 영상 제작 회의로 콘셉트와 실행안을 정리해줘.");

  assert.equal(artifact.schemaVersion, "request-analysis.v1");
  assert.deepEqual(
    Object.keys(artifact).sort(),
    [
      "ambiguitySignals",
      "constraints",
      "intent",
      "loopContextSummary",
      "requiredOutputs",
      "roleRoutes",
      "schemaVersion",
      "taskBreakdown",
      "tokenStrategy",
      "userRequestSummary",
    ],
  );
  assert.deepEqual(
    artifact.taskBreakdown.map((task) => Object.keys(task).sort()),
    [
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
      ["dependsOn", "id", "rationale", "title"],
    ],
  );
  assert.deepEqual(
    artifact.taskBreakdown.map((task) => ({
      id: task.id,
      title: task.title,
      hasRationale: task.rationale.length > 0,
    })),
    [
      { id: "task-001", title: "요청 의도와 성공 기준 정리", hasRationale: true },
      { id: "task-002", title: "OpenClaw 실행 초안 작성", hasRationale: true },
      { id: "task-003", title: "Hermes 리뷰와 수렴 판단", hasRationale: true },
      { id: "task-004", title: "최종 합성 또는 escalation", hasRationale: true },
    ],
  );
  assert.deepEqual(
    artifact.roleRoutes.map((route) => route.taskId),
    artifact.taskBreakdown.map((task) => task.id),
  );
  assert.equal(artifact.intent.meetingSystem, "virtual-company-multi-agent-meeting");
  assert.equal(artifact.constraints.every((constraint) => constraint.description.length > 0), true);
  assert.equal(artifact.requiredOutputs.every((output) => output.description.length > 0), true);
});
