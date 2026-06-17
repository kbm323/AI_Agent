import type { TaskRecord, OwnerExecutor, FinalizerExecutor, ReviewerVerdict } from "./types.ts";

/**
 * Configuration for an {@link ExecPersona} instance.
 *
 * An execution persona wraps an external agent CLI (OpenClaw, Claude, etc.)
 * and provides both the owner-draft and final-synthesis capabilities
 * required by the company orchestrator meeting loop.
 */
export interface ExecPersonaConfig {
  /** The persona role — owner creates drafts, finalizer synthesizes results. */
  role: "openclaw-owner" | "openclaw-finalizer";
  /** Async function that sends a prompt to the external agent and returns its output. */
  executor: (prompt: string) => Promise<string>;
  /** Optional maximum output token hint (not enforced by this module). */
  maxOutputTokens?: number;
}

/**
 * A concrete execution persona that satisfies both {@link OwnerExecutor}
 * and {@link FinalizerExecutor} so it can be wired directly into
 * {@link CompanyOrchestrator}.
 *
 * @example
 * ```ts
 * import { ExecPersona } from "ai-agent/execution-persona";
 *
 * const owner = new ExecPersona({
 *   role: "openclaw-owner",
 *   executor: async (prompt) => runOpenClaw(prompt),
 * });
 * const orchestrator = new CompanyOrchestrator({ ..., owner });
 * ```
 */
export class ExecPersona implements OwnerExecutor, FinalizerExecutor {
  private readonly config: ExecPersonaConfig;

  constructor(config: ExecPersonaConfig) {
    this.config = config;
  }

  /**
   * Produces an owner draft by sending a structured prompt to the
   * external agent.  The prompt includes the user request, task
   * identity, and current loop round.
   */
  async createDraft(input: { task: TaskRecord; userRequest: string; round: number }): Promise<string> {
    const prompt = buildExecPersonaDraftPrompt({
      userRequest: input.userRequest,
      round: input.round,
      taskId: input.task.id,
    });
    return this.config.executor(prompt);
  }

  /**
   * Produces a final synthesis by sending the full meeting context
   * (draft, review, verdict, and feedback) to the external agent.
   */
  async synthesize(input: {
    task: TaskRecord;
    userRequest: string;
    draft: string;
    review: string;
    reviewerVerdict: ReviewerVerdict;
    acceptedFeedback: string[];
    rejectedFeedback: string[];
  }): Promise<string> {
    const prompt = buildExecPersonaSynthesisPrompt({
      userRequest: input.userRequest,
      taskId: input.task.id,
      draft: input.draft,
      review: input.review,
      reviewerVerdict: input.reviewerVerdict,
      acceptedFeedback: input.acceptedFeedback,
      rejectedFeedback: input.rejectedFeedback,
    });
    return this.config.executor(prompt);
  }
}

/**
 * Run a single execution turn through an executor function.
 *
 * This is the lowest-level entry point for sending a prompt to the
 * external agent (OpenClaw / Claude) and receiving a raw response.
 * Callers that need the full meeting-loop integration should wire an
 * {@link ExecPersona} into {@link CompanyOrchestrator} instead.
 *
 * @param executor  An async function that sends a prompt to the agent.
 * @param prompt    The prompt string to send.
 * @returns The raw agent output.
 *
 * @example
 * ```ts
 * import { run_execution } from "ai-agent/execution-persona";
 *
 * const result = await run_execution(
 *   async (p) => callOpenClaw(p),
 *   "Draft a marketing plan for product X.",
 * );
 * ```
 */
export async function run_execution(
  executor: (prompt: string) => Promise<string>,
  prompt: string,
): Promise<string> {
  return executor(prompt);
}

// ── private helpers ──────────────────────────────────────────────

function buildExecPersonaDraftPrompt(input: { userRequest: string; round: number; taskId: string }): string {
  return [
    `You are the OpenClaw execution persona (role: owner).`,
    ``,
    `Task ID: ${input.taskId}`,
    `Round: ${input.round}`,
    ``,
    `User request:`,
    input.userRequest.trim(),
    ``,
    `Produce a concrete, actionable draft that addresses the request above.`,
    `Include: objectives, deliverables, timeline, and risks.`,
  ].join("\n");
}

function buildExecPersonaSynthesisPrompt(input: {
  userRequest: string;
  taskId: string;
  draft: string;
  review: string;
  reviewerVerdict: ReviewerVerdict;
  acceptedFeedback: string[];
  rejectedFeedback: string[];
}): string {
  const feedbackLines: string[] = [];
  if (input.acceptedFeedback.length > 0) {
    feedbackLines.push("Accepted feedback:", ...input.acceptedFeedback.map((f) => `  - ${f}`));
  }
  if (input.rejectedFeedback.length > 0) {
    feedbackLines.push("Rejected feedback:", ...input.rejectedFeedback.map((f) => `  - ${f}`));
  }

  return [
    `You are the OpenClaw execution persona (role: finalizer).`,
    ``,
    `Task ID: ${input.taskId}`,
    ``,
    `Original user request:`,
    input.userRequest.trim(),
    ``,
    `OpenClaw owner draft:`,
    input.draft.trim(),
    ``,
    `Hermes reviewer verdict: ${input.reviewerVerdict}`,
    ``,
    `Hermes review:`,
    input.review.trim(),
    ``,
    ...feedbackLines,
    ``,
    `Produce a consolidated final synthesis that incorporates the`,
    `reviewer feedback and delivers a complete, actionable answer.`,
  ].join("\n");
}
