import type { TaskRecord, ReviewerExecutor, ReviewerVerdict } from "./types.ts";

/**
 * Configuration for a {@link ReviewPersona} instance.
 *
 * A review persona wraps an external agent CLI (Hermes, DeepSeek, etc.)
 * and provides the review capability required by the company orchestrator
 * meeting loop.
 */
export interface ReviewPersonaConfig {
  /** The persona role — always "hermes-reviewer" for this module. */
  role: "hermes-reviewer";
  /** Async function that sends a prompt to the external agent and returns its output. */
  executor: (prompt: string) => Promise<string>;
  /** Optional maximum output token hint (not enforced by this module). */
  maxOutputTokens?: number;
}

/**
 * A concrete review persona that satisfies {@link ReviewerExecutor}
 * so it can be wired directly into {@link CompanyOrchestrator}.
 *
 * @example
 * ```ts
 * import { ReviewPersona } from "ai-agent/review-persona";
 *
 * const reviewer = new ReviewPersona({
 *   role: "hermes-reviewer",
 *   executor: async (prompt) => runHermes(prompt),
 * });
 * const orchestrator = new CompanyOrchestrator({ ..., reviewer });
 * ```
 */
export class ReviewPersona implements ReviewerExecutor {
  private readonly config: ReviewPersonaConfig;

  constructor(config: ReviewPersonaConfig) {
    this.config = config;
  }

  /**
   * Produces a review verdict by sending a structured prompt to the
   * external agent. The prompt includes the user request, draft, and
   * current loop round.
   */
  async review(input: {
    task: TaskRecord;
    userRequest: string;
    draft: string;
    round: number;
  }): Promise<{ content: string; verdict: ReviewerVerdict }> {
    const prompt = buildReviewPersonaPrompt({
      userRequest: input.userRequest,
      round: input.round,
      taskId: input.task.id,
      draft: input.draft,
    });
    const rawOutput = await this.config.executor(prompt);
    const parsed = parseHermesReviewOutput(rawOutput);
    return parsed;
  }
}

/**
 * Run a single review turn through an executor function.
 *
 * This is the lowest-level entry point for sending a prompt to the
 * external review agent (Hermes / DeepSeek) and receiving a structured
 * review verdict. Callers that need the full meeting-loop integration
 * should wire a {@link ReviewPersona} into {@link CompanyOrchestrator}
 * instead.
 *
 * @param executor  An async function that sends a prompt to the agent.
 * @param input     The review input containing the task, user request,
 *                  draft, and current round.
 * @returns A structured review verdict with content and verdict.
 *
 * @example
 * ```ts
 * import { run_review } from "ai-agent/review-persona";
 *
 * const result = await run_review(
 *   async (p) => callHermes(p),
 *   { task: myTask, userRequest: "...", draft: "...", round: 1 },
 * );
 * ```
 */
export async function run_review(
  executor: (prompt: string) => Promise<string>,
  input: { task: TaskRecord; userRequest: string; draft: string; round: number },
): Promise<{ content: string; verdict: ReviewerVerdict }> {
  const prompt = buildReviewPersonaPrompt({
    userRequest: input.userRequest,
    round: input.round,
    taskId: input.task.id,
    draft: input.draft,
  });
  const rawOutput = await executor(prompt);
  return parseHermesReviewOutput(rawOutput);
}

// ── private helpers ──────────────────────────────────────────────

function buildReviewPersonaPrompt(input: {
  userRequest: string;
  round: number;
  taskId: string;
  draft: string;
}): string {
  return [
    `You are the Hermes review persona (role: reviewer).`,
    ``,
    `Task ID: ${input.taskId}`,
    `Round: ${input.round}`,
    ``,
    `Original user request:`,
    input.userRequest.trim(),
    ``,
    `OpenClaw owner draft to review:`,
    input.draft.trim(),
    ``,
    `Review instructions:`,
    `- Critically evaluate the draft against the user request.`,
    `- Identify strengths, weaknesses, risks, and missing elements.`,
    `- Suggest concrete improvements where applicable.`,
    `- Do NOT create a new independent proposal — critique the draft.`,
    ``,
    `Your response MUST end with exactly one verdict line:`,
    `VERDICT: agree`,
    `or VERDICT: agree_with_changes`,
    `or VERDICT: disagree`,
    `or VERDICT: needs_user_decision`,
  ].join("\n");
}

const VERDICT_LINE_RE = /^VERDICT:\s*(agree_with_changes|needs_user_decision|agree|disagree)\s*$/im;

function parseHermesReviewOutput(rawOutput: string): {
  content: string;
  verdict: ReviewerVerdict;
} {
  const verdictMatch = rawOutput.match(VERDICT_LINE_RE);
  const verdict: ReviewerVerdict = verdictMatch
    ? (verdictMatch[1] as ReviewerVerdict)
    : "disagree";

  // Strip the verdict line from the visible content (verdict is metadata).
  const content = rawOutput.replace(VERDICT_LINE_RE, "").trim();

  return { content, verdict };
}
