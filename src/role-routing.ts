import type { AgentRole } from "./types.ts";

export interface RoleRoutableTask {
  id: string;
  title: string;
  rationale: string;
  dependsOn?: string[];
}

export type TaskRoutingAttribute =
  | "request_analysis"
  | "execution_draft"
  | "review_convergence"
  | "final_synthesis"
  | "escalation";

export interface TaskRoleEligibility {
  taskId: string;
  attributes: TaskRoutingAttribute[];
  eligibleRoles: AgentRole[];
  primaryRole: AgentRole;
  responsibility: string;
}

export interface TaskRoleAssignment {
  taskId: string;
  taskTitle: string;
  taskRationale: string;
  assignedRole: AgentRole;
  responsibility: string;
}

export type TaskRoleAssignmentValidationFailureKind =
  | "malformed_assignment"
  | "unknown_task"
  | "duplicate_assignment"
  | "missing_assignment"
  | "unsupported_role"
  | "role_mismatch";

export interface TaskRoleAssignmentValidationFailure {
  kind: TaskRoleAssignmentValidationFailureKind;
  taskId?: string;
  assignmentIndex?: number;
  assignedRole?: string;
  expectedRoles?: AgentRole[];
  message: string;
}

export interface TaskRoleAssignmentValidationReport {
  valid: boolean;
  inputTaskCount: number;
  assignmentCount: number;
  checkedTaskIds: string[];
  failures: TaskRoleAssignmentValidationFailure[];
}

export interface TaskRoleAssignmentReport {
  inputTaskCount: number;
  assignmentCount: number;
  oneAssignmentPerTask: boolean;
  assignments: TaskRoleAssignment[];
}

export const roleResponsibilitiesByTaskAttribute: Record<TaskRoutingAttribute, { role: AgentRole; responsibility: string }> = {
  request_analysis: {
    role: "openclaw-owner",
    responsibility: "요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
  },
  execution_draft: {
    role: "openclaw-owner",
    responsibility: "실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
  },
  review_convergence: {
    role: "hermes-reviewer",
    responsibility: "초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
  },
  final_synthesis: {
    role: "openclaw-finalizer",
    responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
  },
  escalation: {
    role: "openclaw-finalizer",
    responsibility: "합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
  },
};

const canonicalTaskAttributesById: Record<string, TaskRoutingAttribute[]> = {
  "task-001": ["request_analysis"],
  "task-002": ["execution_draft"],
  "task-003": ["review_convergence"],
  "task-004": ["final_synthesis", "escalation"],
};

const supportedAgentRoles: AgentRole[] = ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"];

export function deriveTaskRoutingAttributes(task: RoleRoutableTask | string): TaskRoutingAttribute[] {
  if (typeof task === "string") {
    return requireConfiguredTaskAttributes(task);
  }

  const configuredAttributes = canonicalTaskAttributesById[task.id];
  if (configuredAttributes) {
    return configuredAttributes;
  }

  const searchableText = `${task.title} ${task.rationale}`.toLowerCase();
  const derivedAttributes: TaskRoutingAttribute[] = [];

  if (/요청|의도|성공 기준|분해|analysis|breakdown/.test(searchableText)) {
    derivedAttributes.push("request_analysis");
  }
  if (/openclaw|실행|draft|owner/.test(searchableText)) {
    derivedAttributes.push("execution_draft");
  }
  if (/hermes|리뷰|검토|판정|review|convergence/.test(searchableText)) {
    derivedAttributes.push("review_convergence");
  }
  if (/최종|합성|final|synthesis/.test(searchableText)) {
    derivedAttributes.push("final_synthesis");
  }
  if (/escalation|사용자 결정|사용자 입력|결정 필요/.test(searchableText)) {
    derivedAttributes.push("escalation");
  }

  return [...new Set(derivedAttributes)];
}

export function mapTaskAttributesToEligibleRoles(attributes: TaskRoutingAttribute[]): AgentRole[] {
  const roles = attributes.map((attribute) => roleResponsibilitiesByTaskAttribute[attribute].role);
  return [...new Set(roles)];
}

export function resolveTaskRoleEligibility(task: RoleRoutableTask | string): TaskRoleEligibility {
  const taskId = typeof task === "string" ? task : task.id;
  const attributes = deriveTaskRoutingAttributes(task);
  if (attributes.length === 0) {
    throw new RangeError(`No routing attributes could be derived for task id: ${taskId}`);
  }

  const eligibleRoles = mapTaskAttributesToEligibleRoles(attributes);
  const primaryAttribute = attributes[0];
  const primaryRoute = roleResponsibilitiesByTaskAttribute[primaryAttribute];

  return {
    taskId,
    attributes,
    eligibleRoles,
    primaryRole: primaryRoute.role,
    responsibility: primaryRoute.responsibility,
  };
}

export function assignDecomposedTasksToAgentRoles(tasks: RoleRoutableTask[]): TaskRoleAssignmentReport {
  const assignments = tasks.map((task) => {
    const eligibility = resolveTaskRoleEligibility(task);

    return {
      taskId: task.id,
      taskTitle: task.title,
      taskRationale: task.rationale,
      assignedRole: eligibility.primaryRole,
      responsibility: eligibility.responsibility,
    };
  });

  return {
    inputTaskCount: tasks.length,
    assignmentCount: assignments.length,
    oneAssignmentPerTask:
      assignments.length === tasks.length &&
      new Set(assignments.map((assignment) => assignment.taskId)).size === tasks.length &&
      assignments.every((assignment) => assignment.assignedRole.length > 0),
    assignments,
  };
}

export function validateTaskRoleAssignments(tasks: RoleRoutableTask[], assignments: readonly unknown[]): TaskRoleAssignmentValidationReport {
  const failures: TaskRoleAssignmentValidationFailure[] = [];
  const tasksById = new Map(tasks.map((task) => [task.id, task]));
  const seenAssignedTaskIds = new Set<string>();

  assignments.forEach((assignment, assignmentIndex) => {
    if (typeof assignment !== "object" || assignment === null || Array.isArray(assignment)) {
      failures.push({
        kind: "malformed_assignment",
        assignmentIndex,
        message: `assignments[${assignmentIndex}] must be an object`,
      });
      return;
    }

    const candidate = assignment as Record<string, unknown>;
    const taskId = typeof candidate.taskId === "string" && candidate.taskId.trim().length > 0 ? candidate.taskId : undefined;
    const assignedRole =
      typeof candidate.assignedRole === "string" && candidate.assignedRole.trim().length > 0 ? candidate.assignedRole : undefined;

    if (!taskId) {
      failures.push({
        kind: "malformed_assignment",
        assignmentIndex,
        message: `assignments[${assignmentIndex}].taskId must be a non-empty string`,
      });
      return;
    }

    if (seenAssignedTaskIds.has(taskId)) {
      failures.push({
        kind: "duplicate_assignment",
        taskId,
        assignmentIndex,
        message: `task ${taskId} has more than one routing assignment`,
      });
      return;
    }
    seenAssignedTaskIds.add(taskId);

    const task = tasksById.get(taskId);
    if (!task) {
      failures.push({
        kind: "unknown_task",
        taskId,
        assignmentIndex,
        assignedRole,
        message: `assignment references unknown task id: ${taskId}`,
      });
      return;
    }

    if (!assignedRole) {
      failures.push({
        kind: "malformed_assignment",
        taskId,
        assignmentIndex,
        message: `assignments[${assignmentIndex}].assignedRole must be a non-empty string`,
      });
      return;
    }

    if (!isSupportedAgentRole(assignedRole)) {
      failures.push({
        kind: "unsupported_role",
        taskId,
        assignmentIndex,
        assignedRole,
        expectedRoles: supportedAgentRoles,
        message: `task ${taskId} is assigned to unsupported role: ${assignedRole}`,
      });
      return;
    }

    const eligibility = resolveTaskRoleEligibility(task);
    if (!eligibility.eligibleRoles.includes(assignedRole)) {
      failures.push({
        kind: "role_mismatch",
        taskId,
        assignmentIndex,
        assignedRole,
        expectedRoles: eligibility.eligibleRoles,
        message: `task ${taskId} must be assigned to one of: ${eligibility.eligibleRoles.join(", ")}`,
      });
    }
  });

  for (const task of tasks) {
    if (!seenAssignedTaskIds.has(task.id)) {
      failures.push({
        kind: "missing_assignment",
        taskId: task.id,
        expectedRoles: resolveTaskRoleEligibility(task).eligibleRoles,
        message: `task ${task.id} has no routing assignment`,
      });
    }
  }

  return {
    valid: failures.length === 0,
    inputTaskCount: tasks.length,
    assignmentCount: assignments.length,
    checkedTaskIds: tasks.map((task) => task.id),
    failures,
  };
}

function requireConfiguredTaskAttributes(taskId: string): TaskRoutingAttribute[] {
  const attributes = canonicalTaskAttributesById[taskId];
  if (!attributes) {
    throw new RangeError(`No routing attributes are configured for task id: ${taskId}`);
  }

  return attributes;
}

function isSupportedAgentRole(role: string): role is AgentRole {
  return supportedAgentRoles.includes(role as AgentRole);
}

// ── PersonaRouter ──────────────────────────────────────────────

export interface PersonaRouterConfig {
  /** Max meeting loop rounds before forced escalation (default: 3). */
  maxRounds?: number;
}

/**
 * Stateful router that maps decomposed tasks to agent personas and
 * validates routing assignments against the persona responsibility table.
 *
 * ```ts
 * import { PersonaRouter } from "ai-agent/role-routing";
 * const router = new PersonaRouter();
 * const routes = router.route(decomposedTasks);
 * const validation = router.validateAssignments(decomposedTasks, routes);
 * ```
 */
export class PersonaRouter {
  readonly maxRounds: number;

  constructor(config: PersonaRouterConfig = {}) {
    this.maxRounds = config.maxRounds ?? 3;
  }

  /**
   * Route every decomposed task to its primary agent persona.
   * Returns one deterministic assignment per task.
   */
  route(tasks: RoleRoutableTask[]): TaskRoleAssignmentReport {
    const report = assignDecomposedTasksToAgentRoles(tasks);

    if (!report.oneAssignmentPerTask) {
      throw new Error("PersonaRouter.route: internal invariant violation — not one assignment per task");
    }

    return report;
  }

  /**
   * Validate that every task has a correct assignment to a supported role.
   */
  validateAssignments(tasks: RoleRoutableTask[], assignments: readonly unknown[]): TaskRoleAssignmentValidationReport {
    return validateTaskRoleAssignments(tasks, assignments);
  }
}

// ── route_to_persona ───────────────────────────────────────────

/**
 * Convenience function: map a single task to its primary agent persona.
 *
 * ```ts
 * import { route_to_persona } from "ai-agent/role-routing";
 * const persona = route_to_persona({ id: "t1", title: "리뷰", rationale: "검토" });
 * // persona = "hermes-reviewer"
 * ```
 */
export function route_to_persona(task: RoleRoutableTask | string): AgentRole {
  const eligibility = resolveTaskRoleEligibility(task);
  return eligibility.primaryRole;
}
