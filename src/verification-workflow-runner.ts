import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { AiAgentDatabase } from "./db.ts";
import { CompanyOrchestrator } from "./orchestrator.ts";
import type { AgentRole, DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor, TurnKind } from "./types.ts";

export const defaultVerificationWorkflowResultPath = "docs/generated/verification-workflow-result.json";

export interface VerificationWorkflowRunnerResult {
  schemaVersion: "verification-workflow-runner.v1";
  command: "ai-agent run-verification-workflow";
  status: "passed" | "failed";
  deterministicInputs: {
    clearRequest: string;
    ambiguousRequest: string;
    projectChannelId: string;
  };
  cases: VerificationWorkflowCaseResult[];
  summary: {
    caseCount: number;
    passedCaseCount: number;
    failedCaseCount: number;
    mvpWorkflowExecuted: boolean;
    escalationWorkflowExecuted: boolean;
    rawStorageSeparatedFromLoopContext: boolean;
  };
  errors: string[];
}

export interface VerificationWorkflowCaseResult {
  name: "finalized_meeting_loop" | "ambiguous_request_escalation";
  status: "passed" | "failed";
  observed: Record<string, boolean | number | string | string[]>;
  failures: string[];
}

export interface WrittenVerificationWorkflowResult {
  path: string;
  result: VerificationWorkflowRunnerResult;
}

const deterministicInputs = {
  clearRequest: "브랜드 캠페인 제작 회의를 열고 OpenClaw 실행안과 Hermes 검토를 거쳐 최종안을 합성해줘.",
  ambiguousRequest: "대충 좋은 후보 여러 개를 추천만 해줘.",
  projectChannelId: "verification-parent-channel",
} as const;

const rawStorageSentinel = "RAW_VERIFICATION_SENTINEL_ONLY_IN_STORAGE";

export async function runVerificationWorkflow(): Promise<VerificationWorkflowRunnerResult> {
  const cases: VerificationWorkflowCaseResult[] = [];
  const errors: string[] = [];

  for (const runCase of [runFinalizedMeetingLoopCase, runAmbiguousRequestEscalationCase]) {
    try {
      cases.push(await runCase());
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown verification workflow failure";
      const name = runCase === runFinalizedMeetingLoopCase ? "finalized_meeting_loop" : "ambiguous_request_escalation";
      cases.push({ name, status: "failed", observed: {}, failures: [message] });
      errors.push(message);
    }
  }

  const finalizedCase = cases.find((entry) => entry.name === "finalized_meeting_loop");
  const escalationCase = cases.find((entry) => entry.name === "ambiguous_request_escalation");
  const passedCaseCount = cases.filter((entry) => entry.status === "passed").length;
  const result: VerificationWorkflowRunnerResult = {
    schemaVersion: "verification-workflow-runner.v1",
    command: "ai-agent run-verification-workflow",
    status: passedCaseCount === cases.length ? "passed" : "failed",
    deterministicInputs,
    cases,
    summary: {
      caseCount: cases.length,
      passedCaseCount,
      failedCaseCount: cases.length - passedCaseCount,
      mvpWorkflowExecuted: finalizedCase?.observed.finalSynthesisCreated === true,
      escalationWorkflowExecuted: escalationCase?.observed.status === "waiting_for_user",
      rawStorageSeparatedFromLoopContext: finalizedCase?.observed.rawSentinelStored === true &&
        finalizedCase?.observed.rawSentinelHiddenFromSummaries === true,
    },
    errors,
  };

  if (result.status !== "passed") {
    result.errors.push(...cases.flatMap((entry) => entry.failures));
  }

  return result;
}

export async function writeVerificationWorkflowResult(input: {
  projectRoot?: string;
  outputPath?: string;
  runner?: () => Promise<VerificationWorkflowRunnerResult>;
} = {}): Promise<WrittenVerificationWorkflowResult> {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? defaultVerificationWorkflowResultPath;
  const result = await (input.runner ?? runVerificationWorkflow)();
  const path = resolve(projectRoot, outputPath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(`${path}.tmp`, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  renameSync(`${path}.tmp`, path);
  return { path, result };
}

function buildCaseResult(
  name: VerificationWorkflowCaseResult["name"],
  observed: VerificationWorkflowCaseResult["observed"],
  checks: Record<string, boolean>,
): VerificationWorkflowCaseResult {
  const failures = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([check]) => check);
  return {
    name,
    status: failures.length === 0 ? "passed" : "failed",
    observed,
    failures,
  };
}

async function runFinalizedMeetingLoopCase(): Promise<VerificationWorkflowCaseResult> {
  const db = new AiAgentDatabase();
  const discord = createVerificationDiscord("verification-finalized-thread");
  const ownerCalls: number[] = [];
  const reviewerCalls: number[] = [];
  const finalizerCalls: string[] = [];
  const ownerDrafts = [
    `OpenClaw draft round 1: agenda, owners, acceptance gates, and launch checklist. ${"x".repeat(1400)} ${rawStorageSentinel}`,
    "OpenClaw draft round 2: refined agenda with owners, acceptance gates, launch checklist, and review actions.",
  ];
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      ownerCalls.push(round);
      return ownerDrafts[round - 1] ?? ownerDrafts.at(-1) ?? "";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      reviewerCalls.push(round);
      return round === 1
        ? {
            verdict: "disagree",
            content: "Hermes review round 1: disagree. Add review actions before final synthesis.",
          }
        : {
            verdict: "agree_with_changes",
            content: "Hermes review round 2: agree with changes. Keep review actions in the final synthesis.",
          };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      const synthesis = `Final synthesis: ${draft} ${review}`;
      finalizerCalls.push(synthesis);
      return synthesis;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "verification-finalized-task",
    config: { maxRounds: 3 },
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: deterministicInputs.projectChannelId, name: "verification-project" },
      userRequest: deterministicInputs.clearRequest,
    });
    const turns = db.getTurns("verification-finalized-task");
    const routeRoles = result.requestAnalysis.roleRoutes.map((route) => route.role);
    const meetingKinds = result.meetingHistory.map((turn) => turn.kind);
    const rawSentinelStored = turns.some((turn) => turn.content.includes(rawStorageSentinel));
    const rawSentinelHiddenFromSummaries =
      result.meetingHistory.every((turn) => !turn.summary.includes(rawStorageSentinel)) &&
      discord.threadPosts.every((post) => !post.content.includes(rawStorageSentinel));
    const observed = {
      status: result.status,
      taskBreakdownCount: result.requestAnalysis.taskBreakdown.length,
      routeRoles,
      meetingKinds,
      ownerCallRounds: ownerCalls.map(String),
      reviewerCallRounds: reviewerCalls.map(String),
      finalSynthesisCreated: typeof result.finalSynthesis === "string" && result.finalSynthesis.startsWith("Final synthesis:"),
      parentPostCount: discord.parentPosts.length,
      rawSentinelStored,
      rawSentinelHiddenFromSummaries,
      threadFullContentPreserved: discord.threadPosts.some((post) => post.fullContent?.includes(rawStorageSentinel)),
    };

    return buildCaseResult("finalized_meeting_loop", observed, {
      "clear workflow finalizes": result.status === "finalized",
      "request analysis decomposes work": result.requestAnalysis.taskBreakdown.length >= 4,
      "role routes include OpenClaw and Hermes": hasRoles(routeRoles, ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"]),
      "meeting loop records required turns": hasKinds(meetingKinds, ["owner_draft", "review_request", "review", "final_synthesis"]),
      "OpenClaw executed revisions": ownerCalls.length >= 2,
      "Hermes reviewed revisions": reviewerCalls.length >= 2,
      "final synthesis created": observed.finalSynthesisCreated === true && finalizerCalls.length === 1,
      "parent receives only thread start": discord.parentPosts.length === 1 && !discord.parentPosts.join("\n").includes("OpenClaw draft"),
      "raw full text remains stored": rawSentinelStored,
      "loop context exposes summaries only": rawSentinelHiddenFromSummaries,
      "delivery preserves full content out of visible loop context": observed.threadFullContentPreserved === true,
    });
  } finally {
    db.close();
  }
}

async function runAmbiguousRequestEscalationCase(): Promise<VerificationWorkflowCaseResult> {
  const db = new AiAgentDatabase();
  const discord = createVerificationDiscord("verification-escalation-thread");
  let ownerCalled = false;
  let reviewerCalled = false;
  let finalizerCalled = false;
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner: {
      async createDraft() {
        ownerCalled = true;
        return "should not execute";
      },
    },
    reviewer: {
      async review() {
        reviewerCalled = true;
        return { verdict: "agree", content: "should not execute" };
      },
    },
    finalizer: {
      async synthesize() {
        finalizerCalled = true;
        return "should not execute";
      },
    },
    idFactory: () => "verification-escalation-task",
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: deterministicInputs.projectChannelId, name: "verification-project" },
      userRequest: deterministicInputs.ambiguousRequest,
    });
    const meetingKinds = result.meetingHistory.map((turn) => turn.kind);
    const observed = {
      status: result.status,
      escalationReasons: result.escalationReasons,
      meetingKinds,
      ownerCalled,
      reviewerCalled,
      finalizerCalled,
      parentPostCount: discord.parentPosts.length,
    };

    return buildCaseResult("ambiguous_request_escalation", observed, {
      "ambiguous workflow waits for user": result.status === "waiting_for_user",
      "escalation reasons are concrete": result.escalationReasons.length > 0,
      "escalation turn is recorded": meetingKinds.includes("escalation"),
      "owner is not called before user decision": !ownerCalled,
      "reviewer is not called before user decision": !reviewerCalled,
      "finalizer is not called before user decision": !finalizerCalled,
      "parent receives thread start": discord.parentPosts.length === 1,
    });
  } finally {
    db.close();
  }
}

function createVerificationDiscord(threadId: string): DiscordDelivery & {
  parentPosts: string[];
  threadPosts: Array<{ threadId: string; content: string; fullContent?: string }>;
} {
  return {
    parentPosts: [],
    threadPosts: [],
    async createThread() {
      return { threadId, url: `https://discord.test/${threadId}` };
    },
    async postParent(input) {
      this.parentPosts.push(input.content);
    },
    async postThread(input) {
      this.threadPosts.push(input);
    },
  };
}

function hasRoles(actual: AgentRole[], expected: AgentRole[]): boolean {
  return expected.every((role) => actual.includes(role));
}

function hasKinds(actual: TurnKind[], expected: TurnKind[]): boolean {
  return expected.every((kind) => actual.includes(kind));
}
