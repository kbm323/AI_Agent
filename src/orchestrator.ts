import { AiAgentDatabase } from "./db.ts";
import { detectConvergenceFailure } from "./convergence-failure.ts";
import { createDefaultEscalationPolicy, summarizeForThread } from "./policies.ts";
import { analyzeUserRequest, formatRoleRoute } from "./planning.ts";
import { createRuntimeIdentifier } from "./runtime-data.ts";
import { detectStrongUserInputRequired } from "./user-input-required.ts";
import type { RequestAnalysis } from "./planning.ts";
import type {
  AgentRole,
  DiscordDelivery,
  EscalationPolicy,
  FinalizerExecutor,
  OrchestratorConfig,
  OwnerExecutor,
  ProjectRef,
  ReviewerExecutor,
  ReviewerVerdict,
  TaskRecord,
  TurnKind,
} from "./types.ts";

export interface CompanyOrchestratorDeps {
  db: AiAgentDatabase;
  discord: DiscordDelivery;
  owner: OwnerExecutor;
  reviewer: ReviewerExecutor;
  finalizer: FinalizerExecutor;
  escalationPolicy?: EscalationPolicy;
  config?: Partial<OrchestratorConfig>;
  idFactory?: () => string;
}

export interface RunTaskResult {
  task: TaskRecord;
  status: TaskRecord["status"];
  threadId: string;
  requestAnalysis: RequestAnalysis;
  meetingHistory: Array<{ round: number; role: AgentRole; kind: TurnKind; summary: string }>;
  intermediateDecisions: MeetingLoopDecision[];
  discussionResult: MeetingLoopDiscussionResult;
  finalSynthesis?: string;
  escalationReasons: string[];
}

export type MeetingLoopDiscussionStatus = "completed" | "escalated" | "failed";

export interface MeetingLoopFinalRouteState {
  taskId: string;
  threadId: string;
  taskStatus: TaskRecord["status"];
  finalRound: number;
  routeSequence: Array<{ taskId: string; role: AgentRole; title: string }>;
  finalTurn?: { round: number; role: AgentRole; kind: TurnKind };
  converged: boolean;
  escalationReasons: string[];
}

export interface MeetingLoopDiscussionResult {
  status: MeetingLoopDiscussionStatus;
  finalRouteState: MeetingLoopFinalRouteState;
  recordedDecisions: MeetingLoopDecision[];
  finalSynthesis?: string;
}

export type MeetingLoopDecisionType =
  | "request_escalated"
  | "owner_output_failed"
  | "draft_rejected_for_revision"
  | "draft_accepted"
  | "draft_accepted_with_changes"
  | "escalated_for_user_decision"
  | "escalated_for_convergence_failure";

export interface MeetingLoopDecision {
  round: number;
  role: AgentRole;
  decision: MeetingLoopDecisionType;
  reviewerVerdict?: ReviewerVerdict;
  summary: string;
  reasons: string[];
  sourceTurnKinds: TurnKind[];
}

export type EscalationTriggerType =
  | "ambiguous_request"
  | "meeting_loop"
  | "convergence_failure"
  | "model_output_failure"
  | "user_decision_required";

export interface EscalationSerializationInput {
  reasons: string[];
  triggerType: EscalationTriggerType;
  nextRequiredAction: string;
}

export interface SerializedEscalationResult {
  schemaVersion: "escalation-result.v1";
  escalation: {
    required: true;
    reasons: string[];
    triggerType: EscalationTriggerType;
    nextRequiredAction: string;
  };
}

export interface EscalationNotificationInput {
  reasons: string[];
  triggerType: EscalationTriggerType;
  taskId?: string;
  threadId?: string;
}

export interface EscalationNotificationEvent {
  schemaVersion: "escalation-notification.v1";
  event: "escalation_required";
  status: "waiting_for_user";
  escalation: {
    required: true;
    reasons: string[];
    triggerType: EscalationTriggerType;
  };
  task?: {
    id?: string;
    threadId?: string;
  };
}

export class CompanyOrchestrator {
  private readonly deps: CompanyOrchestratorDeps;
  private readonly escalationPolicy: EscalationPolicy;
  private readonly config: OrchestratorConfig;
  private readonly idFactory: () => string;

  constructor(deps: CompanyOrchestratorDeps) {
    this.deps = deps;
    this.escalationPolicy = deps.escalationPolicy ?? createDefaultEscalationPolicy();
    this.config = { maxRounds: deps.config?.maxRounds ?? 4 };
    this.idFactory = deps.idFactory ?? createRuntimeIdentifier;
  }

  async runUserRequest(input: { project: ProjectRef; userRequest: string }): Promise<RunTaskResult> {
    const requestAnalysis = analyzeUserRequest(input.userRequest);
    const thread = await this.deps.discord.createThread({
      parentChannelId: input.project.channelId,
      name: buildThreadName(input.userRequest),
      initialMessage: input.userRequest,
    });
    const threadLabel = thread.url ?? thread.threadId;
    await this.deps.discord.postParent({
      channelId: input.project.channelId,
      content: `Agent discussion started -> ${threadLabel}`,
    });

    let task = this.deps.db.createTask({
      id: this.idFactory(),
      projectChannelId: input.project.channelId,
      threadId: thread.threadId,
      userRequest: input.userRequest,
    });
    await this.recordAndPost(
      task,
      0,
      "openclaw-owner",
      "request_analysis",
      buildRequestAnalysisMessage(requestAnalysis),
      JSON.stringify(requestAnalysis, null, 2),
    );
    const intermediateDecisions: MeetingLoopDecision[] = [];

    const requestInputRequired = detectStrongUserInputRequired({
      requestAmbiguitySignals: requestAnalysis.ambiguitySignals,
    });
    if (requestInputRequired.required) {
      const escalationReasons = requestInputRequired.reasons;
      const escalation = buildEscalationMessage(escalationReasons);
      await this.recordAndPost(task, 0, "openclaw-finalizer", "escalation", escalation);
      intermediateDecisions.push({
        round: 0,
        role: "openclaw-finalizer",
        decision: "request_escalated",
        summary: "Request analysis found ambiguity signals, so the meeting pauses before OpenClaw execution.",
        reasons: escalationReasons,
        sourceTurnKinds: ["request_analysis", "escalation"],
      });
      this.deps.db.insertDecision({
        taskId: task.id,
        requiresUserDecision: true,
        reasons: escalationReasons,
      });
      this.deps.db.updateTaskStatus(task.id, "waiting_for_user");
      task = this.deps.db.getTask(task.id) ?? task;
      return this.buildResult({ task, requestAnalysis, intermediateDecisions, escalationReasons });
    }

    let draft = "";
    let review = "";
    let reviewerVerdict: ReviewerVerdict = "disagree";
    const acceptedFeedback: string[] = [];
    const rejectedFeedback: string[] = [];
    let escalationReasons: string[] = [];

    for (let round = 1; round <= this.config.maxRounds; round++) {
      draft = await this.deps.owner.createDraft({ task, userRequest: input.userRequest, round });
      if (!isUsableModelOutput(draft, input.userRequest)) {
        await this.recordAndPost(task, round, "openclaw-owner", "escalation", "OpenClaw draft capture failed");
        intermediateDecisions.push({
          round,
          role: "openclaw-owner",
          decision: "owner_output_failed",
          summary: "OpenClaw output was empty or only repeated the user request, so Hermes review was skipped.",
          reasons: ["owner_draft_failed"],
          sourceTurnKinds: ["escalation"],
        });
        this.deps.db.updateTaskStatus(task.id, "failed");
        task = this.deps.db.getTask(task.id) ?? task;
        return this.buildResult({ task, requestAnalysis, intermediateDecisions, escalationReasons: ["owner_draft_failed"] });
      }

      await this.recordAndPost(task, round, "openclaw-owner", "owner_draft", `OpenClaw draft\n\n${draft}`, draft);
      this.deps.db.updateTaskStatus(task.id, "owner_drafted");
      task = this.deps.db.getTask(task.id) ?? task;

      const reviewerRequest = buildReviewerRequest({ userRequest: input.userRequest, draft, round });
      await this.recordAndPost(task, round, "openclaw-owner", "review_request", reviewerRequest);
      this.deps.db.updateTaskStatus(task.id, "review_requested");
      task = this.deps.db.getTask(task.id) ?? task;

      const reviewerResult = await this.deps.reviewer.review({ task, userRequest: input.userRequest, draft, round });
      review = reviewerResult.content;
      reviewerVerdict = reviewerResult.verdict;
      await this.recordAndPost(task, round, "hermes-reviewer", "review", `Hermes review\n\n${review}`, review);
      this.deps.db.updateTaskStatus(task.id, "reviewed");
      task = this.deps.db.getTask(task.id) ?? task;

      const policyReasons = this.escalationPolicy.requiresUserDecision({
        userRequest: input.userRequest,
        draft,
        review,
        reviewerVerdict,
      });
      const meetingInputRequired = detectStrongUserInputRequired({
        reviewerVerdict,
        policyReasons,
      });
      escalationReasons = meetingInputRequired.reasons;
      if (meetingInputRequired.required) {
        const escalation = buildEscalationMessage(escalationReasons);
        await this.recordAndPost(task, round, "openclaw-finalizer", "escalation", escalation);
        intermediateDecisions.push({
          round,
          role: "openclaw-finalizer",
          decision: "escalated_for_user_decision",
          reviewerVerdict,
          summary: "Hermes review or policy checks require a user decision before final synthesis.",
          reasons: escalationReasons,
          sourceTurnKinds: ["review", "escalation"],
        });
        this.deps.db.insertDecision({
          taskId: task.id,
          requiresUserDecision: true,
          reasons: escalationReasons,
        });
        this.deps.db.updateTaskStatus(task.id, "waiting_for_user");
        task = this.deps.db.getTask(task.id) ?? task;
        return this.buildResult({ task, requestAnalysis, intermediateDecisions, escalationReasons });
      }

      if (reviewerVerdict === "agree" || reviewerVerdict === "agree_with_changes") {
        acceptedFeedback.push(
          reviewerVerdict === "agree"
            ? "Hermes reviewer agreed with the draft."
            : "Hermes reviewer agreed with changes for final synthesis.",
        );
        intermediateDecisions.push({
          round,
          role: "hermes-reviewer",
          decision: reviewerVerdict === "agree" ? "draft_accepted" : "draft_accepted_with_changes",
          reviewerVerdict,
          summary:
            reviewerVerdict === "agree"
              ? "Hermes accepted the OpenClaw draft for final synthesis."
              : "Hermes accepted the OpenClaw draft with changes for final synthesis.",
          reasons: [],
          sourceTurnKinds: ["owner_draft", "review"],
        });
        break;
      }

      acceptedFeedback.push(`Round ${round} Hermes feedback queued for OpenClaw revision.`);
      intermediateDecisions.push({
        round,
        role: "hermes-reviewer",
        decision: "draft_rejected_for_revision",
        reviewerVerdict,
        summary: `Hermes rejected round ${round} for revision before final synthesis.`,
        reasons: [`round_${round}_revision_required`],
        sourceTurnKinds: ["owner_draft", "review"],
      });
      const convergenceFailure = detectConvergenceFailure({
        iteration: round,
        maxIterations: this.config.maxRounds,
        converged: false,
        reviewerVerdict,
      });
      if (convergenceFailure.failed) {
        const maxRoundEscalation = [convergenceFailure.reason ?? "max_rounds_without_agreement"];
        const escalation = buildEscalationMessage(maxRoundEscalation);
        rejectedFeedback.push("Reached maxRounds hard stop before full agreement.");
        await this.recordAndPost(task, round, "openclaw-finalizer", "escalation", escalation);
        intermediateDecisions.push({
          round,
          role: "openclaw-finalizer",
          decision: "escalated_for_convergence_failure",
          reviewerVerdict,
          summary: "The meeting reached maxRounds without Hermes agreement, so it escalates.",
          reasons: maxRoundEscalation,
          sourceTurnKinds: ["review", "escalation"],
        });
        this.deps.db.insertDecision({
          taskId: task.id,
          requiresUserDecision: true,
          reasons: maxRoundEscalation,
        });
        this.deps.db.updateTaskStatus(task.id, "waiting_for_user");
        task = this.deps.db.getTask(task.id) ?? task;
        return this.buildResult({ task, requestAnalysis, intermediateDecisions, escalationReasons: maxRoundEscalation });
      }
    }

    const finalSynthesis = await this.deps.finalizer.synthesize({
      task,
      userRequest: input.userRequest,
      draft,
      review,
      reviewerVerdict,
      acceptedFeedback,
      rejectedFeedback,
    });
    await this.recordAndPost(task, this.config.maxRounds + 1, "openclaw-finalizer", "final_synthesis", `Final synthesis\n\n${finalSynthesis}`, finalSynthesis);
    this.deps.db.insertDecision({ taskId: task.id, requiresUserDecision: false, reasons: [] });
    this.deps.db.updateTaskStatus(task.id, "finalized");
    task = this.deps.db.getTask(task.id) ?? task;

    return this.buildResult({ task, requestAnalysis, intermediateDecisions, finalSynthesis, escalationReasons: [] });
  }

  private buildResult(input: {
    task: TaskRecord;
    requestAnalysis: RequestAnalysis;
    intermediateDecisions: MeetingLoopDecision[];
    finalSynthesis?: string;
    escalationReasons: string[];
  }): RunTaskResult {
    const meetingHistory = this.deps.db.getTurns(input.task.id).map((turn) => ({
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      summary: turn.visibleSummary,
    }));
    const recordedDecisions = input.intermediateDecisions.map((decision) => ({
      ...decision,
      reasons: [...decision.reasons],
      sourceTurnKinds: [...decision.sourceTurnKinds],
    }));
    const finalTurn = meetingHistory.at(-1);
    return {
      task: input.task,
      status: input.task.status,
      threadId: input.task.threadId,
      requestAnalysis: input.requestAnalysis,
      meetingHistory,
      intermediateDecisions: recordedDecisions,
      discussionResult: {
        status: mapTaskStatusToDiscussionStatus(input.task.status),
        finalRouteState: {
          taskId: input.task.id,
          threadId: input.task.threadId,
          taskStatus: input.task.status,
          finalRound: meetingHistory.reduce((maxRound, turn) => Math.max(maxRound, turn.round), 0),
          routeSequence: input.requestAnalysis.roleRoutes.map((route) => {
            const task = input.requestAnalysis.taskBreakdown.find((item) => item.id === route.taskId);
            return {
              taskId: route.taskId,
              role: route.role,
              title: task?.title ?? route.taskId,
            };
          }),
          finalTurn: finalTurn ? { round: finalTurn.round, role: finalTurn.role, kind: finalTurn.kind } : undefined,
          converged: input.task.status === "finalized" && finalTurn?.kind === "final_synthesis",
          escalationReasons: [...input.escalationReasons],
        },
        recordedDecisions: recordedDecisions.map((decision) => ({
          ...decision,
          reasons: [...decision.reasons],
          sourceTurnKinds: [...decision.sourceTurnKinds],
        })),
        finalSynthesis: input.finalSynthesis,
      },
      finalSynthesis: input.finalSynthesis,
      escalationReasons: input.escalationReasons,
    };
  }

  private async recordAndPost(
    task: TaskRecord,
    round: number,
    role: Parameters<AiAgentDatabase["insertTurn"]>[0]["role"],
    kind: Parameters<AiAgentDatabase["insertTurn"]>[0]["kind"],
    visibleContent: string,
    fullContent = visibleContent,
  ): Promise<void> {
    const visibleSummary = summarizeForThread(visibleContent);
    this.deps.db.insertTurn({
      taskId: task.id,
      round,
      role,
      kind,
      content: fullContent,
      visibleSummary,
    });
    await this.deps.discord.postThread({
      threadId: task.threadId,
      content: visibleSummary,
      fullContent,
    });
  }
}

function buildRequestAnalysisMessage(requestAnalysis: RequestAnalysis): string {
  return [
    "Request analysis",
    "",
    `Summary: ${requestAnalysis.userRequestSummary}`,
    "",
    "Task breakdown:",
    ...requestAnalysis.taskBreakdown.map((item) => `- ${item.id}: ${item.title}`),
    "",
    "Role routing:",
    ...requestAnalysis.roleRoutes.map((route) => formatRoleRoute(route)),
    "",
    requestAnalysis.loopContextSummary,
  ].join("\n");
}

export function buildReviewerRequest(input: { userRequest: string; draft: string; round: number }): string {
  return [
    `Hermes reviewer request (round ${input.round})`,
    "",
    "User request:",
    input.userRequest.trim(),
    "",
    "Captured OpenClaw draft:",
    input.draft.trim(),
    "",
    "Review task:",
    "OpenClaw draft를 기준으로 비판/보완/동의 여부를 판단하라.",
    "독립 제안을 새로 만들지 말고, draft의 장점/문제/리스크/수정안을 분리하라.",
    "",
    "Verdict must be one of: agree, agree_with_changes, disagree, needs_user_decision.",
  ].join("\n");
}

export function buildThreadName(userRequest: string): string {
  const compact = userRequest.trim().replace(/\s+/g, " ");
  return `Task: ${compact.slice(0, 50)}`;
}

export function buildEscalationMessage(reasons: string[]): string {
  return [
    "User decision required",
    "",
    "Reasons:",
    ...reasons.map((reason) => `- ${reason}`),
  ].join("\n");
}

export function serializeEscalationResult(input: EscalationSerializationInput): string {
  const result: SerializedEscalationResult = {
    schemaVersion: "escalation-result.v1",
    escalation: {
      required: true,
      reasons: [...input.reasons],
      triggerType: input.triggerType,
      nextRequiredAction: input.nextRequiredAction,
    },
  };

  return `${JSON.stringify(result, null, 2)}\n`;
}

export function emitEscalationNotification(input: EscalationNotificationInput): EscalationNotificationEvent {
  const event: EscalationNotificationEvent = {
    schemaVersion: "escalation-notification.v1",
    event: "escalation_required",
    status: "waiting_for_user",
    escalation: {
      required: true,
      reasons: [...input.reasons],
      triggerType: input.triggerType,
    },
  };

  if (input.taskId !== undefined || input.threadId !== undefined) {
    event.task = {
      id: input.taskId,
      threadId: input.threadId,
    };
  }

  return event;
}

function isUsableModelOutput(output: string, userRequest: string): boolean {
  const normalizedOutput = output.trim().toLowerCase().replace(/\s+/g, " ");
  const normalizedRequest = userRequest.trim().toLowerCase().replace(/\s+/g, " ");
  return normalizedOutput.length > 0 && normalizedOutput !== normalizedRequest;
}

function mapTaskStatusToDiscussionStatus(status: TaskRecord["status"]): MeetingLoopDiscussionStatus {
  if (status === "finalized") return "completed";
  if (status === "failed") return "failed";
  return "escalated";
}
