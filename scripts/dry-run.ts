import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { AiAgentDatabase } from "../src/db.ts";
import { assertImplementationDecisionLabel, evaluateProjectFindings } from "../src/evaluation.ts";
import { handlePriorReviewArtifact } from "../src/inspection.ts";
import { CompanyOrchestrator } from "../src/orchestrator.ts";
import type { PriorReviewArtifactHandlerResponse } from "../src/inspection.ts";
import type { ImplementationDecisionJustification, ImplementationDecisionLabel } from "../src/evaluation.ts";
import type { ReviewEvidenceArtifact } from "../src/inspection.ts";
import type {
  FinalOutputArtifact,
  FinalOutputDiagnosticSection,
  FinalOutputPersonaOutput,
  FinalOutputRunSettings,
  FinalOutputStatus,
  FinalOutputVersionMetadata,
} from "../src/final-output-schema.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor } from "../src/types.ts";

interface DryRunOutput extends FinalOutputArtifact {
  command: "ai-agent dry-run";
  selectedDecision: {
    outcome: "keep" | "partial_redesign" | "full_replan";
    label: ImplementationDecisionLabel;
    basis: string;
    justification: ImplementationDecisionJustification;
  };
  diagnosis: {
    decision: "keep" | "partial_redesign" | "full_replan";
    decisionLabel: ImplementationDecisionLabel;
    basis: string;
    justification: ImplementationDecisionJustification;
  };
  priorReview?: PriorReviewArtifactHandlerResponse;
  generatedArtifact?: {
    path: string;
    schemaVersion: FinalOutputArtifact["schemaVersion"];
  };
}

type DryRunInputSource = "default" | "inline" | "file";

interface DryRunOptions {
  priorReviewArtifact?: string;
  inputIdentifier?: string;
  inputSource?: DryRunInputSource;
  executionId?: string;
}

interface EntrypointStreams {
  stdout: Pick<typeof process.stdout, "write">;
  stderr: Pick<typeof process.stderr, "write">;
}

const diagnosisDecision = {
  decision: "partial_redesign",
  decisionLabel: "partial redesign",
  basis: "docs/diagnosis-report.md priority assessment: error frequency > maintenance difficulty > token cost > architecture fit > feature completeness",
} as const;
const reviewEvidenceArtifactPath = "docs/review-evidence.json";
const defaultDryRunArtifactPath = "docs/generated/dry-run-final-output.json";
const dryRunRunSettings: FinalOutputRunSettings = {
  executionMode: "dry_run",
  orchestrator: {
    maxRounds: 4,
    escalationPolicy: "default",
  },
  models: {
    openclawOwner: {
      provider: "local-deterministic",
      model: "openclaw-dry-run-owner-v1",
      temperature: 0,
      maxOutputTokens: 512,
    },
    hermesReviewer: {
      provider: "local-deterministic",
      model: "hermes-dry-run-reviewer-v1",
      temperature: 0,
      maxOutputTokens: 512,
    },
    openclawFinalizer: {
      provider: "local-deterministic",
      model: "openclaw-dry-run-finalizer-v1",
      temperature: 0,
      maxOutputTokens: 768,
    },
  },
};

async function main(): Promise<void> {
  process.exitCode = await runDryRunEntrypoint(process.argv.slice(2));
}

export async function runDryRunEntrypoint(
  args: string[],
  streams: EntrypointStreams = process,
): Promise<number> {
  const result = await executeDryRunCommand(args);
  if (result.stdout) {
    streams.stdout.write(result.stdout);
  }
  if (result.stderr) {
    streams.stderr.write(result.stderr);
  }
  return result.exitCode;
}

export async function executeDryRunCommand(args: string[]): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    validateDryRunArgs(args);
    const priorReviewArtifact = readPriorReviewArtifactArg(args);
    const request = requireRequestInputArg(args);
    const inputIdentifier = readInputIdentifierArg(args) ?? createInputIdentifier(request);
    const executionId = readExecutionIdentifierArg(args) ?? createExecutionIdentifier(inputIdentifier);
    const output = await runDryRun(request, {
      priorReviewArtifact,
      inputIdentifier,
      executionId,
      inputSource: detectInputSource(args),
    });
    if (shouldWriteArtifact(args)) {
      const artifactPath = writeDryRunArtifact(output);
      output.generatedArtifact = {
        path: artifactPath,
        schemaVersion: output.schemaVersion,
      };
    }
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(output, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown dry-run failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

export async function runDryRun(userRequest: string, optionsOrPriorReviewArtifact?: DryRunOptions | string): Promise<DryRunOutput> {
  const options = normalizeDryRunOptions(optionsOrPriorReviewArtifact);
  const db = new AiAgentDatabase();
  const decisionJustification = loadDecisionJustification();
  const threadPosts: Array<{ threadId: string; content: string; fullContent?: string }> = [];
  const discord: DiscordDelivery = {
    async createThread({ parentChannelId, name }) {
      assertNonEmpty(parentChannelId, "project channel id");
      assertNonEmpty(name, "thread name");
      return { threadId: "thread-demo-1", url: "https://discord.test/thread-demo-1" };
    },
    async postParent() {},
    async postThread(input) {
      threadPosts.push(input);
    },
  };

  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      return [
        `OpenClaw owner draft round ${round}:`,
        "- 요청을 3막 구조의 실행안으로 정리한다.",
        "- 첫 3초는 강한 후킹 컷으로 시작한다.",
        "- 후반부는 캐릭터 실루엣과 브랜드 컬러를 반복 노출한다.",
      ].join("\n");
    },
  };

  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree",
        content: [
          "Hermes review:",
          "agree. 후킹/구조/브랜드 반복은 타당하다.",
          "썸네일 전환 컷과 숏폼 재사용 가능성을 final에 명시하라.",
        ].join("\n"),
      };
    },
  };

  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      return [
        "Final synthesis:",
        "뮤직비디오 오프닝은 3초 후킹 컷, 3막 감정선, 반복 가능한 브랜드 컬러를 중심으로 구성한다.",
        "Hermes 피드백에 따라 썸네일 전환 컷과 숏폼 재사용 포인트를 포함한다.",
        "",
        "Compressed evidence:",
        summarizeEvidence(draft),
        summarizeEvidence(review),
      ].join("\n");
    },
  };

  try {
    const orchestrator = new CompanyOrchestrator({
      db,
      discord,
      owner,
      reviewer,
      finalizer,
      idFactory: () => "task-demo-1",
    });

    const result = await orchestrator.runUserRequest({
      project: { channelId: "project-channel-demo", name: "Virtual AI Company" },
      userRequest,
    });
    const priorReviewEvidence = handlePriorReviewArtifact({ identifier: reviewEvidenceArtifactPath });

    const output: DryRunOutput = {
      schemaVersion: "final-output-artifact.v1",
      command: "ai-agent dry-run",
      metadata: {
        executionId: options.executionId ?? createExecutionIdentifier(options.inputIdentifier ?? createInputIdentifier(userRequest)),
        inputIdentifier: options.inputIdentifier ?? createInputIdentifier(userRequest),
        inputSource: options.inputSource ?? "inline",
        version: buildRunVersionMetadata(),
        runSettings: dryRunRunSettings,
      },
      status: toFinalOutputStatus(result.status),
      threadId: result.threadId,
      userRequest,
      selectedDecision: {
        outcome: diagnosisDecision.decision,
        label: diagnosisDecision.decisionLabel,
        basis: diagnosisDecision.basis,
        justification: decisionJustification,
      },
      diagnosis: {
        ...diagnosisDecision,
        justification: decisionJustification,
      },
      diagnosticOutput: {
        sections: [buildPriorReviewEvidenceSection(priorReviewEvidence), ...buildDecisionDiagnosticSections(decisionJustification)],
      },
      requestAnalysis: {
        taskBreakdown: result.requestAnalysis.taskBreakdown.map((item) => `${item.id}:${item.title}`),
        roleRoutes: result.requestAnalysis.roleRoutes.map((route) => `${route.taskId}->${route.role}`),
        tokenStrategy: result.requestAnalysis.tokenStrategy.targetReduction,
      },
      openclawOutputs: buildPersonaOutputs(result.meetingHistory, ["request_analysis", "owner_draft", "review_request"]),
      hermesReviews: buildPersonaOutputs(result.meetingHistory, ["review"]),
      meetingHistory: result.meetingHistory,
      finalSynthesis: result.finalSynthesis,
      escalation: buildEscalationArtifact(result, diagnosisDecision.decision),
      tokenStrategy: result.requestAnalysis.tokenStrategy,
    };
    if (options.priorReviewArtifact !== undefined) {
      output.priorReview = handlePriorReviewArtifact({ identifier: options.priorReviewArtifact });
    }
    return output;
  } finally {
    db.close();
    void threadPosts;
  }
}

function buildPriorReviewEvidenceSection(
  priorReviewEvidence: PriorReviewArtifactHandlerResponse,
): FinalOutputDiagnosticSection {
  return {
    title: "Prior Review Evidence",
    evidence: {
      artifactPath: priorReviewEvidence.artifact.path,
      schemaVersion: priorReviewEvidence.artifact.schemaVersion,
      recommendation: priorReviewEvidence.artifact.recommendation,
      inspectedModules: priorReviewEvidence.artifact.inspectedModules,
      findingCount: priorReviewEvidence.artifact.findingCount,
      validationValid: priorReviewEvidence.validation.valid,
      completenessComplete: priorReviewEvidence.completeness.complete,
      decisionGateAccepted: priorReviewEvidence.decisionGate.accepted,
    },
  };
}

function buildDecisionDiagnosticSections(
  selectedDecision: ImplementationDecisionJustification,
): FinalOutputDiagnosticSection[] {
  return [
    {
      title: "Keep Decision",
      evidence: {
        outcome: "keep",
        label: "Keep",
        selected: selectedDecision.outcome === "keep",
        rule: "no_redesign_evidence",
        criterion: "Preserve the implementation when no critical, high-severity, or token-cost redesign evidence is ranked.",
      },
    },
    {
      title: "Partial Redesign Decision",
      evidence: {
        outcome: "partial_redesign",
        label: "partial redesign",
        selected: selectedDecision.outcome === "partial_redesign",
        rule: "high_or_token_cost_evidence",
        criterion: "Preserve the implementation shape while redesigning scoped error, maintenance, or token-cost pressure points.",
      },
    },
    {
      title: "Full Redesign Decision",
      evidence: {
        outcome: "full_replan",
        label: "full replan",
        selected: selectedDecision.outcome === "full_replan",
        rule: "critical_evidence",
        criterion: "Replace the plan only when critical evidence shows the current implementation cannot satisfy the MVP safely.",
      },
    },
  ];
}

function toFinalOutputStatus(status: string): FinalOutputStatus {
  if (status === "finalized" || status === "waiting_for_user" || status === "failed") {
    return status;
  }
  throw new TypeError(`dry-run cannot emit final output artifact status: ${status}`);
}

function buildRunVersionMetadata(): FinalOutputVersionMetadata {
  return {
    schemaVersion: "run-version-metadata.v1",
    artifactSchemaVersion: "final-output-artifact.v1",
    commandVersion: "ai-agent-dry-run.v1",
    implementationVersion: "multi-agent-meeting-mvp.v1",
    runtime: {
      name: "node",
      version: process.versions.node,
    },
  };
}

function buildPersonaOutputs(
  meetingHistory: DryRunOutput["meetingHistory"],
  acceptedKinds: Array<FinalOutputPersonaOutput["kind"]>,
): FinalOutputPersonaOutput[] {
  return meetingHistory
    .filter((turn) => acceptedKinds.includes(turn.kind))
    .map((turn) => ({
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      summary: turn.summary,
    }));
}

function buildEscalationArtifact(
  result: Awaited<ReturnType<CompanyOrchestrator["runUserRequest"]>>,
  diagnosisDecisionValue: "keep" | "partial_redesign" | "full_replan",
): NonNullable<DryRunOutput["escalation"]> {
  const required = result.escalationReasons.length > 0;
  const latestMeetingSummary = result.meetingHistory.at(-1)?.summary ?? null;
  const hasOwnerDraft = result.meetingHistory.some((turn) => turn.kind === "owner_draft");
  const trigger = !required
    ? "none"
    : result.status === "waiting_for_user" && !hasOwnerDraft
      ? "ambiguous_request"
      : "meeting_loop";

  return {
    required,
    reasons: result.escalationReasons,
    decisionContext: {
      status: toFinalOutputStatus(result.status),
      trigger,
      preservedTurns: result.meetingHistory.length,
      latestMeetingSummary,
      diagnosisDecision: diagnosisDecisionValue,
    },
    nextAction: required
      ? {
          type: "user_input_required",
          prompt: "Clarify the blocked decision before continuing the OpenClaw/Hermes loop.",
          requestedFields: ["success_criteria", "preferred_direction", "constraints_or_examples"],
        }
      : {
          type: "continue",
          prompt: "No user decision is required; use finalSynthesis as the current result.",
          requestedFields: [],
        },
    preservedContext: {
      rawStorage: "Full request, draft, review, and escalation text is retained in turns.content.",
      exposedSummary: "Normal dry-run output exposes bounded meetingHistory summaries instead of raw full text.",
      compressedContext: "Next loop turn should carry request summary, latest meeting summary, reasons, and requestedFields only.",
    },
  };
}

function readRequestInputArg(args: string[]): string | undefined {
  const inlineRequest = readRequestArg(args);
  const fileRequest = readRequestFileArg(args);
  if (inlineRequest !== undefined && fileRequest !== undefined) {
    throw new TypeError("use either --request or --request-file, not both");
  }
  return inlineRequest ?? fileRequest;
}

function requireRequestInputArg(args: string[]): string {
  const request = readRequestInputArg(args);
  if (request === undefined) {
    throw new TypeError("missing required request input: provide --request <text> or --request-file <path>");
  }
  return request;
}

function readRequestArg(args: string[]): string | undefined {
  const requestFlagIndex = args.indexOf("--request");
  if (requestFlagIndex === -1) return undefined;
  return readFlagValue(args, requestFlagIndex);
}

function readRequestFileArg(args: string[]): string | undefined {
  const requestFileFlagIndex = args.indexOf("--request-file");
  if (requestFileFlagIndex === -1) return undefined;
  const requestFilePath = readFlagValue(args, requestFileFlagIndex);
  assertNonEmpty(requestFilePath, "requestFile");
  return readFileSync(requestFilePath, "utf8");
}

function readPriorReviewArtifactArg(args: string[]): string | undefined {
  const artifactFlagIndex = args.indexOf("--prior-review-artifact");
  if (artifactFlagIndex === -1) return undefined;
  const artifact = readFlagValue(args, artifactFlagIndex);
  if (artifact.trim().length === 0) {
    throw new TypeError("priorReviewArtifact must be a non-empty string");
  }
  return artifact;
}

function readInputIdentifierArg(args: string[]): string | undefined {
  const inputIdFlagIndex = args.indexOf("--input-id");
  if (inputIdFlagIndex === -1) return undefined;
  const inputIdentifier = readFlagValue(args, inputIdFlagIndex);
  assertNonEmpty(inputIdentifier, "inputIdentifier");
  return inputIdentifier;
}

function readExecutionIdentifierArg(args: string[]): string | undefined {
  const runIdFlagIndex = args.indexOf("--run-id");
  if (runIdFlagIndex === -1) return undefined;
  const executionId = readFlagValue(args, runIdFlagIndex);
  assertNonEmpty(executionId, "executionId");
  return executionId;
}

function validateDryRunArgs(args: string[]): void {
  const flagsWithValues = new Set(["--request", "--request-file", "--prior-review-artifact", "--input-id", "--run-id"]);
  const booleanFlags = new Set(["--write-artifact"]);

  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (!arg.startsWith("--")) {
      throw new TypeError(`unexpected positional argument: ${arg}`);
    }
    if (booleanFlags.has(arg)) {
      continue;
    }
    if (!flagsWithValues.has(arg)) {
      throw new TypeError(`unknown dry-run option: ${arg}`);
    }
    index++;
    readFlagValue(args, index - 1);
  }
}

function readFlagValue(args: string[], flagIndex: number): string {
  const flag = args[flagIndex];
  const value = args[flagIndex + 1] ?? "";
  if (value.startsWith("--")) {
    throw new TypeError(`${flag} requires a value`);
  }
  return value;
}

function detectInputSource(args: string[]): DryRunInputSource {
  if (args.includes("--request-file")) return "file";
  if (args.includes("--request")) return "inline";
  throw new TypeError("missing required request input: provide --request <text> or --request-file <path>");
}

function shouldWriteArtifact(args: string[]): boolean {
  return args.includes("--write-artifact");
}

function writeDryRunArtifact(output: DryRunOutput): string {
  const artifactPath = defaultDryRunArtifactPath;
  const resolvedPath = resolve(process.cwd(), artifactPath);
  mkdirSync(dirname(resolvedPath), { recursive: true });
  const { generatedArtifact, ...persistedArtifact } = output;
  void generatedArtifact;
  writeFileSync(resolvedPath, `${JSON.stringify(persistedArtifact, null, 2)}\n`, "utf8");
  return artifactPath;
}

function createInputIdentifier(userRequest: string): string {
  const digest = createHash("sha256").update(userRequest).digest("hex").slice(0, 16);
  return `request:${digest}`;
}

function createExecutionIdentifier(inputIdentifier: string): string {
  const digest = createHash("sha256").update(`dry-run:${inputIdentifier}`).digest("hex").slice(0, 16);
  return `run:${digest}`;
}

function normalizeDryRunOptions(optionsOrPriorReviewArtifact: DryRunOptions | string | undefined): DryRunOptions {
  if (typeof optionsOrPriorReviewArtifact === "string") {
    return { priorReviewArtifact: optionsOrPriorReviewArtifact };
  }
  return optionsOrPriorReviewArtifact ?? {};
}

function assertNonEmpty(value: string, label: string): void {
  if (value.trim().length === 0) {
    throw new TypeError(`${label} must be non-empty`);
  }
}

function summarizeEvidence(content: string): string {
  const firstLine = content.trim().split(/\r?\n/)[0] ?? "";
  return `- ${firstLine.slice(0, 120)}`;
}

function loadDecisionJustification(): ImplementationDecisionJustification {
  const artifact = JSON.parse(readFileSync(reviewEvidenceArtifactPath, "utf8")) as ReviewEvidenceArtifact;
  const evaluation = evaluateProjectFindings(artifact.findings);
  if (evaluation.recommendation !== diagnosisDecision.decision) {
    throw new TypeError(
      `diagnosis decision ${diagnosisDecision.decision} does not match ${reviewEvidenceArtifactPath} recommendation ${evaluation.recommendation}`,
    );
  }
  assertImplementationDecisionLabel(diagnosisDecision.decisionLabel, "diagnosis decision label");
  assertImplementationDecisionLabel(evaluation.justification.label, "evaluation decision label");
  return evaluation.justification;
}

const invokedAsScript = process.argv[1]?.endsWith("dry-run.ts") ?? false;
if (invokedAsScript) {
  await main();
}
