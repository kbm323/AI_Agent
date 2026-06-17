import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkDryRunContract } from "./check-dry-run-contract.ts";
import { checkFinalOutputSchema } from "./check-final-output-schema.ts";
import { checkMeetingLoopArtifacts } from "./check-meeting-loop-artifacts.ts";
import { checkRequestAnalysis } from "./check-request-analysis.ts";
import { checkRequirementGapMapping } from "./check-requirement-gap.ts";
import { checkRoutingAssignment } from "./check-routing-assignment.ts";
import { executeLoopContextCompressionVerificationCommand } from "./check-loop-context-compression-verification.ts";
import { executeTokenCostControlCheckCommand } from "./check-token-cost-control.ts";

type CheckStatus = "passed" | "failed";

export interface MvpCompletionCheckStep {
  criterion:
    | "request_analysis_and_work_breakdown"
    | "role_based_routing"
    | "openclaw_hermes_preserved_loop"
    | "final_synthesis_and_escalation"
    | "diagnosis_and_requirement_gap"
    | "token_strategy_and_compression"
    | "invalid_input_contract";
  command: string;
  status: CheckStatus;
  evidence: Record<string, boolean | number | string | string[]>;
}

export interface MvpCompletionCheckResult {
  command: "ai-agent check:mvp-completion";
  status: CheckStatus;
  deterministic: true;
  schemaVersion: "mvp-completion-check.v1";
  steps: MvpCompletionCheckStep[];
}

export interface MvpCompletionCheckDeps {
  checkRequestAnalysis?: typeof checkRequestAnalysis;
  checkRoutingAssignment?: typeof checkRoutingAssignment;
  checkMeetingLoopArtifacts?: typeof checkMeetingLoopArtifacts;
  checkFinalOutputSchema?: typeof checkFinalOutputSchema;
  checkDryRunContract?: typeof checkDryRunContract;
  checkRequirementGapMapping?: typeof checkRequirementGapMapping;
  executeLoopContextCompressionVerificationCommand?: typeof executeLoopContextCompressionVerificationCommand;
  executeTokenCostControlCheckCommand?: typeof executeTokenCostControlCheckCommand;
}

export async function checkMvpCompletion(deps: MvpCompletionCheckDeps = {}): Promise<MvpCompletionCheckResult> {
  const requestAnalysis = (deps.checkRequestAnalysis ?? checkRequestAnalysis)();
  const routing = (deps.checkRoutingAssignment ?? checkRoutingAssignment)();
  const meetingLoop = await (deps.checkMeetingLoopArtifacts ?? checkMeetingLoopArtifacts)();
  const finalOutputSchema = await (deps.checkFinalOutputSchema ?? checkFinalOutputSchema)();
  const dryRunContract = await (deps.checkDryRunContract ?? checkDryRunContract)();
  const requirementGap = (deps.checkRequirementGapMapping ?? checkRequirementGapMapping)();
  const compression = parseCommandJson(
    (deps.executeLoopContextCompressionVerificationCommand ?? executeLoopContextCompressionVerificationCommand)([]),
  );
  const tokenCost = parseCommandJson((deps.executeTokenCostControlCheckCommand ?? executeTokenCostControlCheckCommand)([]));

  const steps: MvpCompletionCheckStep[] = [
    buildStep({
      criterion: "request_analysis_and_work_breakdown",
      command: requestAnalysis.command,
      baseStatus: requestAnalysis.status,
      evidence: {
        deterministic: requestAnalysis.deterministic,
        taskCount: requestAnalysis.artifact.taskBreakdown.length,
        hasTokenStrategyTarget: requestAnalysis.artifact.tokenStrategy.targetReduction.includes("40-50%"),
      },
      requiredTruth: ["deterministic", "hasTokenStrategyTarget"],
      requiredPositiveNumbers: ["taskCount"],
    }),
    buildStep({
      criterion: "role_based_routing",
      command: routing.command,
      baseStatus: routing.status,
      evidence: {
        deterministic: routing.deterministic,
        allExecutionTasksAssignedToOpenClaw: routing.executionResponsibilityProof.allExecutionTasksAssignedToOpenClaw,
        allReviewTasksAssignedToHermes: routing.reviewResponsibilityProof.allReviewTasksAssignedToHermes,
        routeCount: routing.artifact.routeCount,
      },
      requiredTruth: ["deterministic", "allExecutionTasksAssignedToOpenClaw", "allReviewTasksAssignedToHermes"],
      requiredPositiveNumbers: ["routeCount"],
    }),
    buildStep({
      criterion: "openclaw_hermes_preserved_loop",
      command: meetingLoop.command,
      baseStatus: meetingLoop.status,
      evidence: {
        deterministic: meetingLoop.contract.deterministic,
        hermesReviewedOpenClawDraft: meetingLoop.contract.transcriptArtifact.hermesReviewedOpenClawDraft,
        transcriptSummaryOnly: meetingLoop.contract.transcriptArtifact.transcriptSummaryOnly,
        stableWrite: meetingLoop.contract.transcriptArtifact.stableWrite,
      },
      requiredTruth: ["deterministic", "hermesReviewedOpenClawDraft", "transcriptSummaryOnly", "stableWrite"],
    }),
    buildStep({
      criterion: "final_synthesis_and_escalation",
      command: finalOutputSchema.command,
      baseStatus: finalOutputSchema.status,
      evidence: {
        clearRequestValid: finalOutputSchema.validation.clearRequestValid,
        ambiguousRequestValid: finalOutputSchema.validation.ambiguousRequestValid,
        finalSynthesisCovered: finalOutputSchema.schema.mvpCoverage.finalSynthesis,
        escalationCovered: finalOutputSchema.schema.mvpCoverage.escalation,
      },
      requiredTruth: ["clearRequestValid", "ambiguousRequestValid", "finalSynthesisCovered", "escalationCovered"],
    }),
    buildStep({
      criterion: "diagnosis_and_requirement_gap",
      command: requirementGap.command,
      baseStatus: requirementGap.artifact.present ? "passed" : "failed",
      evidence: {
        priorityOrderVerified: requirementGap.artifact.priorityOrderVerified,
        implementedCount: requirementGap.artifact.implementedCount ?? 0,
        missingCount: requirementGap.artifact.missingCount ?? 0,
        capabilityIds: requirementGap.artifact.capabilityIds,
      },
      requiredTruth: ["priorityOrderVerified"],
      requiredPositiveNumbers: ["implementedCount"],
      requiredEmptyNumbers: ["missingCount"],
      requiredNonEmptyArrays: ["capabilityIds"],
    }),
    buildStep({
      criterion: "token_strategy_and_compression",
      command: `${tokenCost.command} + ${compression.command}`,
      baseStatus: tokenCost.status === "passed" && compression.status === "passed" ? "passed" : "failed",
      evidence: {
        tokenCostPass: Boolean(tokenCost.pass),
        compressionStatus: String(compression.status),
        rawFullTextHiddenFromCompressedContext: Boolean(compression.rawExposure?.rawFullTextHiddenFromCompressedContext),
        rawFullTextRetainedOutsideLoopContext: Boolean(compression.rawExposure?.rawFullTextRetainedOutsideLoopContext),
        percentSavings: Number(tokenCost.percentSavings),
        minimumTargetSavingsPercent: Number(tokenCost.targetThreshold?.percentSavings),
      },
      requiredTruth: ["tokenCostPass", "rawFullTextHiddenFromCompressedContext", "rawFullTextRetainedOutsideLoopContext"],
      requiredEqual: { compressionStatus: "passed" },
      requiredMinimums: { percentSavings: Number(tokenCost.targetThreshold?.percentSavings) },
    }),
  ];

  const invalidInputCase = dryRunContract.contract.cases.find((contractCase) => contractCase.case === "invalid_input");
  steps.push(buildStep({
    criterion: "invalid_input_contract",
    command: dryRunContract.command,
    baseStatus: invalidInputCase?.exitCode === 2 && invalidInputCase.error === "invalid_input" ? "passed" : "failed",
    evidence: {
      deterministic: dryRunContract.contract.deterministic,
      exitCode: invalidInputCase?.exitCode ?? -1,
      stream: invalidInputCase?.stream ?? "missing",
      parseableJson: invalidInputCase?.parseableJson ?? false,
    },
    requiredTruth: ["deterministic", "parseableJson"],
    requiredEqual: { exitCode: 2, stream: "stderr" },
  }));

  return {
    command: "ai-agent check:mvp-completion",
    status: steps.every((step) => step.status === "passed") ? "passed" : "failed",
    deterministic: true,
    schemaVersion: "mvp-completion-check.v1",
    steps,
  };
}

function buildStep(input: {
  criterion: MvpCompletionCheckStep["criterion"];
  command: string;
  baseStatus: CheckStatus;
  evidence: Record<string, boolean | number | string | string[]>;
  requiredTruth?: string[];
  requiredPositiveNumbers?: string[];
  requiredEmptyNumbers?: string[];
  requiredNonEmptyArrays?: string[];
  requiredEqual?: Record<string, boolean | number | string>;
  requiredMinimums?: Record<string, number>;
}): MvpCompletionCheckStep {
  return {
    criterion: input.criterion,
    command: input.command,
    status: completionEvidencePresent(input) ? input.baseStatus : "failed",
    evidence: input.evidence,
  };
}

function completionEvidencePresent(input: {
  evidence: Record<string, boolean | number | string | string[]>;
  requiredTruth?: string[];
  requiredPositiveNumbers?: string[];
  requiredEmptyNumbers?: string[];
  requiredNonEmptyArrays?: string[];
  requiredEqual?: Record<string, boolean | number | string>;
  requiredMinimums?: Record<string, number>;
}): boolean {
  const evidence = input.evidence;
  for (const key of input.requiredTruth ?? []) {
    if (evidence[key] !== true) return false;
  }
  for (const key of input.requiredPositiveNumbers ?? []) {
    if (typeof evidence[key] !== "number" || evidence[key] <= 0) return false;
  }
  for (const key of input.requiredEmptyNumbers ?? []) {
    if (typeof evidence[key] !== "number" || evidence[key] !== 0) return false;
  }
  for (const key of input.requiredNonEmptyArrays ?? []) {
    if (!Array.isArray(evidence[key]) || evidence[key].length === 0) return false;
  }
  for (const [key, expected] of Object.entries(input.requiredEqual ?? {})) {
    if (evidence[key] !== expected) return false;
  }
  for (const [key, minimum] of Object.entries(input.requiredMinimums ?? {})) {
    if (!Number.isFinite(minimum) || typeof evidence[key] !== "number" || evidence[key] < minimum) return false;
  }
  return true;
}

export async function executeMvpCompletionCheckCommand(
  deps: MvpCompletionCheckDeps = {},
): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkMvpCompletion(deps);
    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown MVP completion check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "mvp_completion_check_failed", message }, null, 2)}\n`,
    };
  }
}

function parseCommandJson(result: { exitCode: number; stdout: string; stderr: string }): any {
  if (result.exitCode !== 0) {
    throw new Error(result.stderr.trim() || result.stdout.trim() || `command failed with exit code ${result.exitCode}`);
  }
  return JSON.parse(result.stdout);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = await executeMvpCompletionCheckCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
