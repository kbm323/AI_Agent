import type { AgentRole } from "./types.ts";
import { resolveTaskRoleEligibility } from "./role-routing.ts";

export interface TaskBreakdownItem {
  id: string;
  title: string;
  rationale: string;
  dependsOn?: string[];
}

export interface TaskGraph {
  tasks: TaskBreakdownItem[];
  edges: Array<{ from: string; to: string }>;
  rootTaskIds: string[];
  leafTaskIds: string[];
  validation: TaskDecompositionSchemaValidationResult | null;
}

export interface TaskDecompositionSchemaValidationResult {
  valid: boolean;
  errors: string[];
  taskCount: number;
  requiredFields: Array<keyof TaskBreakdownItem>;
}

export type TaskOverlapRule = "duplicate_task_id" | "duplicate_title_fingerprint" | "duplicate_workflow_scope" | "unknown_workflow_scope";

export interface TaskOverlapFinding {
  rule: TaskOverlapRule;
  taskIds: string[];
  value: string;
}

export interface TaskDecompositionOverlapReport {
  nonOverlapping: boolean;
  checkedRules: TaskOverlapRule[];
  taskCount: number;
  overlaps: TaskOverlapFinding[];
}

export interface RoleRoute {
  taskId: string;
  role: AgentRole;
  responsibility: string;
}

export interface RoleRoutingMetadata {
  routeCount: number;
  roles: AgentRole[];
  workflowOrder: string[];
  hasHermesReview: boolean;
  hasFinalizer: boolean;
}

export interface TokenStrategy {
  rawStorage: string;
  exposedLoopContext: string;
  compressionPolicy: string;
  targetReduction: string;
}

export interface RequestIntent {
  summary: string;
  primaryGoal: string;
  meetingSystem: "virtual-company-multi-agent-meeting";
  workflow: "analysis-routing-openclaw-hermes-synthesis";
}

export interface RequestConstraint {
  id: string;
  source: "explicit" | "mvp-default";
  description: string;
}

export interface RequiredOutput {
  id: "task_breakdown" | "role_routes" | "meeting_loop_result" | "final_synthesis" | "escalation";
  producedBy: AgentRole | "system";
  description: string;
}

export interface RequestAnalysis {
  intent: RequestIntent;
  constraints: RequestConstraint[];
  requiredOutputs: RequiredOutput[];
  userRequestSummary: string;
  taskBreakdown: TaskBreakdownItem[];
  roleRoutes: RoleRoute[];
  loopContextSummary: string;
  tokenStrategy: TokenStrategy;
  ambiguitySignals: string[];
}

export interface StructuredAnalysisArtifact {
  schemaVersion: "request-analysis.v1";
  intent: RequestIntent;
  constraints: RequestConstraint[];
  requiredOutputs: RequiredOutput[];
  userRequestSummary: string;
  taskBreakdown: Array<{ id: string; title: string; rationale?: string; dependsOn?: string[] }>;
  roleRoutes: RoleRoute[];
  loopContextSummary: string;
  tokenStrategy: TokenStrategy;
  ambiguitySignals: string[];
}

const ambiguousPatterns = [
  { pattern: /알아서|적당히|대충|아무거나|모호|정해줘|추천만/i, signal: "underspecified_preference" },
  { pattern: /여러 개|몇 개|가능하면|좋은/i, signal: "unclear_success_criteria" },
];

const taskWorkflowScopeById: Record<string, string> = {
  "task-001": "request_analysis_and_success_criteria",
  "task-002": "openclaw_execution_draft",
  "task-003": "hermes_review_and_convergence",
  "task-004": "final_synthesis_or_escalation",
};

const taskDependenciesById: Record<string, string[]> = {
  "task-001": [],
  "task-002": ["task-001"],
  "task-003": ["task-002"],
  "task-004": ["task-003"],
};

export const taskDecompositionRequiredFields: Array<keyof TaskBreakdownItem> = ["id", "title", "rationale"];

export function analyzeUserRequest(userRequest: string): RequestAnalysis {
  const normalized = userRequest.trim().replace(/\s+/g, " ");
  if (normalized.length === 0) {
    throw new TypeError("userRequest must be a non-empty string");
  }

  const taskBreakdown = decomposeUserRequest(normalized);

  return {
    intent: buildRequestIntent(normalized),
    constraints: extractRequestConstraints(normalized),
    requiredOutputs: buildRequiredOutputs(),
    userRequestSummary: summarizeRequest(normalized),
    taskBreakdown,
    roleRoutes: buildRoleRoutes(taskBreakdown),
    loopContextSummary: buildLoopContextSummary(normalized),
    tokenStrategy: buildDefaultTokenStrategy(),
    ambiguitySignals: ambiguousPatterns.filter(({ pattern }) => pattern.test(normalized)).map(({ signal }) => signal),
  };
}

export function buildStructuredAnalysisArtifact(userRequest: string): StructuredAnalysisArtifact {
  const analysis = analyzeUserRequest(userRequest);

  return {
    schemaVersion: "request-analysis.v1",
    intent: analysis.intent,
    constraints: analysis.constraints,
    requiredOutputs: analysis.requiredOutputs,
    userRequestSummary: analysis.userRequestSummary,
    taskBreakdown: analysis.taskBreakdown,
    roleRoutes: analysis.roleRoutes,
    loopContextSummary: analysis.loopContextSummary,
    tokenStrategy: analysis.tokenStrategy,
    ambiguitySignals: analysis.ambiguitySignals,
  };
}

export function decomposeUserRequest(userRequest: string): TaskBreakdownItem[] {
  const normalized = userRequest.trim().replace(/\s+/g, " ");
  if (normalized.length === 0) {
    throw new TypeError("userRequest must be a non-empty string");
  }

  return [
    {
      id: "task-001",
      title: "요청 의도와 성공 기준 정리",
      rationale: "회의 루프가 같은 목표를 검토하도록 사용자 요청을 짧은 기준으로 고정한다.",
      dependsOn: [],
    },
    {
      id: "task-002",
      title: "OpenClaw 실행 초안 작성",
      rationale: "owner persona가 실행 가능한 1차 산출물을 만든다.",
      dependsOn: ["task-001"],
    },
    {
      id: "task-003",
      title: "Hermes 리뷰와 수렴 판단",
      rationale: "reviewer persona가 초안의 오류, 누락, 사용자 결정 필요성을 판정한다.",
      dependsOn: ["task-002"],
    },
    {
      id: "task-004",
      title: "최종 합성 또는 escalation",
      rationale: "수렴된 회의 결과를 final synthesis로 정리하거나 사용자 입력 필요성을 구조화한다.",
      dependsOn: ["task-003"],
    },
  ];
}

export function buildTaskGraph(tasks: TaskBreakdownItem[]): TaskGraph {
  const validation = validateTaskDecompositionOutput(tasks);
  const edges: Array<{ from: string; to: string }> = [];
  const dependentIds = new Set<string>();
  const dependencyIds = new Set<string>();

  for (const task of tasks) {
    for (const depId of task.dependsOn ?? []) {
      edges.push({ from: depId, to: task.id });
      dependentIds.add(task.id);
      dependencyIds.add(depId);
    }
  }

  const rootTaskIds = tasks
    .filter((task) => !dependentIds.has(task.id))
    .map((task) => task.id);
  const leafTaskIds = tasks
    .filter((task) => !dependencyIds.has(task.id))
    .map((task) => task.id);

  return {
    tasks,
    edges,
    rootTaskIds: rootTaskIds.length > 0 ? rootTaskIds : [tasks[0]?.id ?? "unknown"],
    leafTaskIds: leafTaskIds.length > 0 ? leafTaskIds : [tasks[tasks.length - 1]?.id ?? "unknown"],
    validation,
  };
}

export function validateTaskDecompositionOutput(tasks: unknown): TaskDecompositionSchemaValidationResult {
  const errors: string[] = [];
  const validTasks: TaskBreakdownItem[] = [];

  if (!Array.isArray(tasks)) {
    return {
      valid: false,
      errors: ["task decomposition output must be an array"],
      taskCount: 0,
      requiredFields: taskDecompositionRequiredFields,
    };
  }

  tasks.forEach((task, index) => {
    if (typeof task !== "object" || task === null || Array.isArray(task)) {
      errors.push(`taskBreakdown[${index}] must be an object`);
      return;
    }

    const candidate = task as Record<string, unknown>;
    const candidateErrors: string[] = [];
    for (const field of taskDecompositionRequiredFields) {
      if (typeof candidate[field] !== "string" || candidate[field].trim().length === 0) {
        candidateErrors.push(`taskBreakdown[${index}].${field} must be a non-empty string`);
      }
    }

    errors.push(...candidateErrors);
    if (candidateErrors.length === 0) {
      const dependsOn = parseTaskDependencies(candidate.dependsOn, index, errors);
      validTasks.push({
        id: candidate.id as string,
        title: candidate.title as string,
        rationale: candidate.rationale as string,
        ...(dependsOn ? { dependsOn } : {}),
      });
    }
  });

  if (errors.length === 0) {
    const overlapReport = assessTaskDecompositionOverlap(validTasks);
    for (const overlap of overlapReport.overlaps) {
      errors.push(
        `taskBreakdown sibling overlap ${overlap.rule} for ${overlap.taskIds.join(", ")} using value ${overlap.value}`,
      );
    }
    errors.push(...validateTaskDependencyGraph(validTasks));
  }

  return {
    valid: errors.length === 0,
    errors,
    taskCount: tasks.length,
    requiredFields: taskDecompositionRequiredFields,
  };
}

export function parseTaskBreakdownFromAnalysisArtifact(artifact: StructuredAnalysisArtifact): TaskBreakdownItem[] {
  validateStructuredAnalysisArtifact(artifact);

  const parsedTasks = artifact.taskBreakdown.map((item) => {
    const canonicalTask = decomposeUserRequest(artifact.userRequestSummary).find((task) => task.id === item.id);
    if (!canonicalTask) {
      throw new RangeError(`Unknown task id in structured analysis artifact: ${item.id}`);
    }

    return {
      id: item.id,
      title: item.title,
      rationale: item.rationale ?? canonicalTask.rationale,
      dependsOn: item.dependsOn ?? canonicalTask.dependsOn ?? taskDependenciesById[item.id] ?? [],
    };
  });
  assertValidNonOverlappingTaskDecomposition(parsedTasks);

  return parsedTasks;
}

export function assessTaskDecompositionOverlap(tasks: TaskBreakdownItem[]): TaskDecompositionOverlapReport {
  const checkedRules: TaskOverlapRule[] = [
    "duplicate_task_id",
    "duplicate_title_fingerprint",
    "duplicate_workflow_scope",
    "unknown_workflow_scope",
  ];
  const overlaps: TaskOverlapFinding[] = [
    ...findDuplicateValues(tasks, "duplicate_task_id", (task) => task.id),
    ...findDuplicateValues(tasks, "duplicate_title_fingerprint", (task) => normalizeTaskTitle(task.title)),
    ...findDuplicateValues(tasks, "duplicate_workflow_scope", (task) => taskWorkflowScopeById[task.id] ?? `unknown:${task.id}`),
    ...tasks
      .filter((task) => taskWorkflowScopeById[task.id] === undefined)
      .map((task) => ({
        rule: "unknown_workflow_scope" as const,
        taskIds: [task.id],
        value: task.id,
      })),
  ];

  return {
    nonOverlapping: overlaps.length === 0,
    checkedRules,
    taskCount: tasks.length,
    overlaps,
  };
}

export function matchRoleForTask(task: TaskBreakdownItem | string): RoleRoute {
  const eligibility = resolveTaskRoleEligibility(task);

  return {
    taskId: eligibility.taskId,
    role: eligibility.primaryRole,
    responsibility: eligibility.responsibility,
  };
}

export function buildRoleRoutes(tasks: TaskBreakdownItem[]): RoleRoute[] {
  assertValidNonOverlappingTaskDecomposition(tasks);
  return tasks.map((task) => matchRoleForTask(task));
}

export function formatRoleRoute(route: RoleRoute): string {
  return `- ${route.taskId} -> ${route.role}: ${route.responsibility}`;
}

export function buildRoleRoutingMetadata(routes: RoleRoute[]): RoleRoutingMetadata {
  const roles = [...new Set(routes.map((route) => route.role))];

  return {
    routeCount: routes.length,
    roles,
    workflowOrder: routes.map((route) => `${route.taskId}:${route.role}`),
    hasHermesReview: routes.some((route) => route.role === "hermes-reviewer"),
    hasFinalizer: routes.some((route) => route.role === "openclaw-finalizer"),
  };
}

export function buildDefaultTokenStrategy(): TokenStrategy {
  return {
    rawStorage: "SQLite turns.content stores full model outputs and reviewer requests for audit/history.",
    exposedLoopContext: "Discord thread messages and loop prompts use bounded summaries from turns.visibleSummary.",
    compressionPolicy:
      "Each round carries request summary, latest draft summary, latest Hermes verdict, accepted feedback, rejected feedback, and escalation reasons instead of replaying full raw text.",
    targetReduction: "Reduce exposed loop tokens by at least 40-50% compared with replaying every full turn.",
  };
}

function summarizeRequest(userRequest: string): string {
  if (userRequest.length <= 160) return userRequest;
  return `${userRequest.slice(0, 159)}…`;
}

function findDuplicateValues(
  tasks: TaskBreakdownItem[],
  rule: Exclude<TaskOverlapRule, "unknown_workflow_scope">,
  valueOf: (task: TaskBreakdownItem) => string,
): TaskOverlapFinding[] {
  const taskIdsByValue = new Map<string, string[]>();
  for (const task of tasks) {
    const value = valueOf(task);
    taskIdsByValue.set(value, [...(taskIdsByValue.get(value) ?? []), task.id]);
  }

  return [...taskIdsByValue.entries()]
    .filter(([, taskIds]) => taskIds.length > 1)
    .map(([value, taskIds]) => ({
      rule,
      taskIds,
      value,
    }));
}

function normalizeTaskTitle(title: string): string {
  return title.trim().toLowerCase().replace(/\s+/g, " ");
}

function assertValidNonOverlappingTaskDecomposition(tasks: TaskBreakdownItem[]): void {
  const validation = validateTaskDecompositionOutput(tasks);
  if (!validation.valid) {
    throw new TypeError(`task decomposition must contain non-overlapping sibling tasks: ${validation.errors.join("; ")}`);
  }
}

function parseTaskDependencies(dependsOn: unknown, taskIndex: number, errors: string[]): string[] | undefined {
  if (dependsOn === undefined) {
    return undefined;
  }
  if (!Array.isArray(dependsOn)) {
    errors.push(`taskBreakdown[${taskIndex}].dependsOn must be an array when provided`);
    return undefined;
  }

  const parsedDependencies: string[] = [];
  dependsOn.forEach((dependencyId, dependencyIndex) => {
    if (typeof dependencyId !== "string" || dependencyId.trim().length === 0) {
      errors.push(`taskBreakdown[${taskIndex}].dependsOn[${dependencyIndex}] must be a non-empty string`);
      return;
    }
    parsedDependencies.push(dependencyId);
  });

  return parsedDependencies;
}

function validateTaskDependencyGraph(tasks: TaskBreakdownItem[]): string[] {
  const errors: string[] = [];
  const taskIds = new Set(tasks.map((task) => task.id));
  const seenTaskIds = new Set<string>();

  for (const task of tasks) {
    for (const dependencyId of task.dependsOn ?? []) {
      if (!taskIds.has(dependencyId)) {
        errors.push(`taskBreakdown dependency ${task.id} references unknown task id ${dependencyId}`);
      }
      if (!seenTaskIds.has(dependencyId)) {
        errors.push(`taskBreakdown dependency ${task.id} must reference an earlier task id: ${dependencyId}`);
      }
      if (dependencyId === task.id) {
        errors.push(`taskBreakdown dependency ${task.id} must not reference itself`);
      }
    }
    seenTaskIds.add(task.id);
  }

  return errors;
}

function buildLoopContextSummary(userRequest: string): string {
  return [
    "Loop context:",
    `- request_summary: ${summarizeRequest(userRequest)}`,
    "- required_flow: analysis -> routing -> OpenClaw draft -> Hermes review -> final synthesis/escalation",
    "- storage_boundary: full text stays in SQLite; exposed context uses summaries",
  ].join("\n");
}

function buildRequestIntent(userRequest: string): RequestIntent {
  return {
    summary: summarizeRequest(userRequest),
    primaryGoal: "Run the requested work through the MVP virtual-company multi-agent meeting flow.",
    meetingSystem: "virtual-company-multi-agent-meeting",
    workflow: "analysis-routing-openclaw-hermes-synthesis",
  };
}

function extractRequestConstraints(userRequest: string): RequestConstraint[] {
  const constraints: RequestConstraint[] = [
    {
      id: "mvp-flow-required",
      source: "mvp-default",
      description: "Preserve request analysis, role routing, OpenClaw execution, Hermes review, final synthesis, and escalation boundaries.",
    },
    {
      id: "compressed-loop-context",
      source: "mvp-default",
      description: "Expose bounded summaries in the meeting loop while keeping raw full text in storage.",
    },
  ];

  const explicitConstraintMatch = userRequest.match(/(?:제약|조건|constraint|constraints?)[:：]?\s*([^.!?\n。]+)/i);
  if (explicitConstraintMatch?.[1]?.trim()) {
    constraints.unshift({
      id: "explicit-user-constraint",
      source: "explicit",
      description: explicitConstraintMatch[1].trim(),
    });
  }

  return constraints;
}

function buildRequiredOutputs(): RequiredOutput[] {
  return [
    {
      id: "task_breakdown",
      producedBy: "openclaw-owner",
      description: "Structured work breakdown derived from the user request.",
    },
    {
      id: "role_routes",
      producedBy: "system",
      description: "Role assignments that route work to OpenClaw, Hermes, and the finalizer.",
    },
    {
      id: "meeting_loop_result",
      producedBy: "hermes-reviewer",
      description: "Reviewed meeting loop outcome with convergence or decision-needed evidence.",
    },
    {
      id: "final_synthesis",
      producedBy: "openclaw-finalizer",
      description: "Final consolidated answer when the meeting loop converges.",
    },
    {
      id: "escalation",
      producedBy: "openclaw-finalizer",
      description: "Structured escalation artifact when user input is required.",
    },
  ];
}

function validateStructuredAnalysisArtifact(artifact: StructuredAnalysisArtifact): void {
  if (artifact.schemaVersion !== "request-analysis.v1") {
    throw new TypeError("structured analysis artifact schemaVersion must be request-analysis.v1");
  }
  if (artifact.intent.workflow !== "analysis-routing-openclaw-hermes-synthesis") {
    throw new TypeError("structured analysis artifact intent.workflow must match the MVP meeting flow");
  }
  if (artifact.constraints.length === 0) {
    throw new TypeError("structured analysis artifact constraints must not be empty");
  }
  if (artifact.requiredOutputs.length === 0) {
    throw new TypeError("structured analysis artifact requiredOutputs must not be empty");
  }
  if (artifact.userRequestSummary.trim().length === 0) {
    throw new TypeError("structured analysis artifact userRequestSummary must be a non-empty string");
  }
  if (artifact.taskBreakdown.length === 0) {
    throw new TypeError("structured analysis artifact taskBreakdown must not be empty");
  }

  const parsedTaskIds = artifact.taskBreakdown.map((task) => task.id);
  const routedTaskIds = artifact.roleRoutes.map((route) => route.taskId);
  if (JSON.stringify(parsedTaskIds) !== JSON.stringify(routedTaskIds)) {
    throw new TypeError("structured analysis artifact roleRoutes must align with taskBreakdown order");
  }
}
