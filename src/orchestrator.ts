import { AiAgentDatabase } from "./db.ts";
import { createDefaultEscalationPolicy, summarizeForThread } from "./policies.ts";
import { classifyTeamRoute } from "./routing.ts";
import type {
  DiscordDelivery,
  EscalationPolicy,
  FinalizerExecutor,
  OrchestratorConfig,
  OwnerExecutor,
  ProjectRef,
  ReviewerExecutor,
  ReviewerVerdict,
  TaskRecord,
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
  logger?: Pick<Console, "log" | "error">;
}

export interface RunTaskResult {
  task: TaskRecord;
  status: TaskRecord["status"];
  threadId: string;
  finalSynthesis?: string;
  escalationReasons: string[];
}

export class CompanyOrchestrator {
  private readonly deps: CompanyOrchestratorDeps;
  private readonly escalationPolicy: EscalationPolicy;
  private readonly config: OrchestratorConfig;
  private readonly idFactory: () => string;
  private readonly logger: Pick<Console, "log" | "error">;

  constructor(deps: CompanyOrchestratorDeps) {
    this.deps = deps;
    this.escalationPolicy = deps.escalationPolicy ?? createDefaultEscalationPolicy();
    this.config = {
      maxRounds: deps.config?.maxRounds ?? 4,
      repeatIssueThreshold: deps.config?.repeatIssueThreshold ?? 3,
    };
    this.idFactory = deps.idFactory ?? (() => crypto.randomUUID());
    this.logger = deps.logger ?? console;
  }

  async runUserRequest(input: { project: ProjectRef; userRequest: string }): Promise<RunTaskResult> {
    const thread = await this.deps.discord.createThread({
      parentChannelId: input.project.channelId,
      name: buildThreadName(input.userRequest),
      initialMessage: input.userRequest,
    });
    this.logger.log(`[AI_AGENT-LIVE] auto thread created threadId=${thread.threadId}`);
    this.logger.log(`[AI_AGENT-LIVE] orchestration target switched threadId=${thread.threadId}`);
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
      teamRoute: classifyTeamRoute(input.userRequest),
    });

    let draft = "";
    let review = "";
    let reviewerVerdict: ReviewerVerdict = "disagree";
    const acceptedFeedback: string[] = [];
    const rejectedFeedback: string[] = [];
    let escalationReasons: string[] = [];
    const unresolvedIssueCounts = new Map<string, number>();

    for (let round = 1; round <= this.config.maxRounds; round++) {
      draft = await this.deps.owner.createDraft({ task, userRequest: input.userRequest, round });
      if (!isUsableModelOutput(draft, input.userRequest)) {
        this.logger.error(`[AI_AGENT-LIVE] OpenClaw draft capture failed threadId=${task.threadId} round=${round}`);
        await this.recordAndPost(task, round, "openclaw-owner", "escalation", "OpenClaw draft capture failed");
        this.deps.db.updateTaskStatus(task.id, "failed");
        task = this.deps.db.getTask(task.id) ?? task;
        return { task, status: task.status, threadId: task.threadId, escalationReasons: ["owner_draft_failed"] };
      }

      this.logger.log(`[AI_AGENT-LIVE] OpenClaw draft captured threadId=${task.threadId} round=${round} chars=${draft.length}`);
      await this.recordAndPost(task, round, "openclaw-owner", "owner_draft", `OpenClaw draft\n\n${draft}`, draft);
      this.logger.log(`[AI_AGENT-LIVE] OpenClaw draft posted threadId=${task.threadId}`);
      this.deps.db.updateTaskStatus(task.id, "owner_drafted");
      task = this.deps.db.getTask(task.id) ?? task;

      const reviewerRequest = buildReviewerRequest({ userRequest: input.userRequest, draft, round });
      await this.recordAndPost(task, round, "openclaw-owner", "review_request", reviewerRequest);
      this.logger.log(`[AI_AGENT-LIVE] reviewer request includes captured draft threadId=${task.threadId} draftChars=${draft.length}`);
      this.deps.db.updateTaskStatus(task.id, "review_requested");
      task = this.deps.db.getTask(task.id) ?? task;

      const reviewerResult = await this.deps.reviewer.review({ task, userRequest: input.userRequest, draft, round });
      review = reviewerResult.content;
      reviewerVerdict = reviewerResult.verdict;
      await this.recordAndPost(task, round, "hermes-reviewer", "review", `Hermes review\n\n${review}`, review);
      this.logger.log(`[AI_AGENT-LIVE] Hermes reply detected threadId=${task.threadId} verdict=${reviewerVerdict} chars=${review.length}`);
      this.deps.db.updateTaskStatus(task.id, "reviewed");
      task = this.deps.db.getTask(task.id) ?? task;

      escalationReasons = this.escalationPolicy.requiresUserDecision({
        userRequest: input.userRequest,
        draft,
        review,
        reviewerVerdict,
      });
      if (reviewerVerdict === "disagree" || reviewerVerdict === "needs_user_decision") {
        const signature = normalizeIssueSignature(review);
        const nextCount = (unresolvedIssueCounts.get(signature) ?? 0) + 1;
        unresolvedIssueCounts.set(signature, nextCount);
        if (nextCount >= this.config.repeatIssueThreshold) {
          escalationReasons = Array.from(new Set([...escalationReasons, "repeated_unresolved_issue"]));
        }
      }

      if (escalationReasons.length > 0) {
        const escalation = buildEscalationMessage(escalationReasons);
        await this.recordAndPost(task, round, "openclaw-finalizer", "escalation", escalation);
        this.deps.db.insertDecision({
          taskId: task.id,
          requiresUserDecision: true,
          reasons: escalationReasons,
        });
        this.deps.db.updateTaskStatus(task.id, "waiting_for_user");
        task = this.deps.db.getTask(task.id) ?? task;
        return { task, status: task.status, threadId: task.threadId, escalationReasons };
      }

      if (reviewerVerdict === "agree") {
        acceptedFeedback.push("Hermes reviewer agreed with the draft.");
        break;
      }

      if (reviewerVerdict === "partial_agree") {
        acceptedFeedback.push(`Round ${round} Hermes changes accepted for final synthesis.`);
        break;
      }

      acceptedFeedback.push(`Round ${round} Hermes feedback queued for OpenClaw revision.`);
      if (round === this.config.maxRounds) {
        rejectedFeedback.push("Reached maxRounds hard stop before full agreement.");
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
    this.logger.log(`[AI_AGENT-LIVE] Final synthesis posted threadId=${task.threadId} chars=${finalSynthesis.length}`);
    this.deps.db.insertDecision({ taskId: task.id, requiresUserDecision: false, reasons: [] });
    this.deps.db.updateTaskStatus(task.id, "finalized");
    task = this.deps.db.getTask(task.id) ?? task;

    return { task, status: task.status, threadId: task.threadId, finalSynthesis, escalationReasons: [] };
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
      role,
      kind,
    });
  }
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
    "Verdict must be one of: agree, partial_agree, disagree, needs_user_decision.",
  ].join("\n");
}

export function buildThreadName(userRequest: string): string {
  const compact = userRequest.trim().replace(/\s+/g, " ");
  return `Task: ${compact.slice(0, 50)}`;
}

function buildEscalationMessage(reasons: string[]): string {
  return [
    "User decision required",
    "",
    "Reasons:",
    ...reasons.map((reason) => `- ${reason}`),
  ].join("\n");
}

function isUsableModelOutput(output: string, userRequest: string): boolean {
  const normalizedOutput = output.trim().toLowerCase().replace(/\s+/g, " ");
  const normalizedRequest = userRequest.trim().toLowerCase().replace(/\s+/g, " ");
  return normalizedOutput.length > 0 && normalizedOutput !== normalizedRequest;
}

function normalizeIssueSignature(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim().slice(0, 240);
}
