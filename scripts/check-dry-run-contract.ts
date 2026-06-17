import assert from "node:assert/strict";
import { executeDryRunCommand } from "./dry-run.ts";

interface DryRunContractCase {
  case: "clear_request" | "ambiguous_request" | "invalid_input";
  exitCode: number;
  stream: "stdout" | "stderr";
  parseableJson: boolean;
  status?: string;
  error?: string;
  requiredFields: string[];
}

interface DryRunContractCheckResult {
  command: "ai-agent check-dry-run-contract";
  status: "passed";
  contract: {
    schemaVersion: "dry-run-contract.v1";
    deterministic: boolean;
    dryRunCommand: string;
    cases: DryRunContractCase[];
  };
}

const clearRequest = "뮤직비디오 오프닝 아이디어를 회의해줘.";
const ambiguousRequest = "대충 좋은 후보 여러 개 추천만 해줘.";

export async function checkDryRunContract(): Promise<DryRunContractCheckResult> {
  const firstClear = await executeDryRunCommand(["--request", clearRequest]);
  const secondClear = await executeDryRunCommand(["--request", clearRequest]);
  assert.equal(firstClear.stdout, secondClear.stdout, "clear dry-run stdout must be deterministic");
  assert.equal(firstClear.stderr, secondClear.stderr, "clear dry-run stderr must be deterministic");

  const clearOutput = parseJson(firstClear.stdout);
  assert.equal(firstClear.exitCode, 0);
  assert.equal(clearOutput.schemaVersion, "final-output-artifact.v1");
  assert.equal(clearOutput.command, "ai-agent dry-run");
  assert.deepEqual(clearOutput.metadata, expectedMetadata("request:f42143edc0867a0d", "inline"));
  assert.equal(clearOutput.status, "finalized");
  assert.equal(clearOutput.userRequest, clearRequest);
  assert.equal(clearOutput.selectedDecision.outcome, "partial_redesign");
  assert.equal(clearOutput.selectedDecision.label, "partial redesign");
  assert.equal(
    clearOutput.selectedDecision.basis,
    "docs/diagnosis-report.md priority assessment: error frequency > maintenance difficulty > token cost > architecture fit > feature completeness",
  );
  assert.equal(clearOutput.selectedDecision.justification.outcome, "partial_redesign");
  assert.equal(clearOutput.selectedDecision.justification.rule, "high_or_token_cost_evidence");
  assert.equal(Array.isArray(clearOutput.selectedDecision.justification.supportingEvidence), true);
  assert.equal(clearOutput.selectedDecision.justification.supportingEvidence.length > 0, true);
  const firstSupportingEvidence = clearOutput.selectedDecision.justification.supportingEvidence[0];
  assert.equal(firstSupportingEvidence.rank, 1);
  assert.equal(firstSupportingEvidence.priority, 1);
  assert.equal(firstSupportingEvidence.category, "error_frequency");
  assert.equal(firstSupportingEvidence.severity, "high");
  assert.match(firstSupportingEvidence.findingId, /^finding:existing:src\/.+/);
  assert.equal(firstSupportingEvidence.title, "Source module has no observable test coverage");
  assert.deepEqual(clearOutput.diagnosis.justification, clearOutput.selectedDecision.justification);
  assert.deepEqual(omitJustification(clearOutput.selectedDecision), {
    outcome: "partial_redesign",
    label: "partial redesign",
    basis: "docs/diagnosis-report.md priority assessment: error frequency > maintenance difficulty > token cost > architecture fit > feature completeness",
  });
  assert.equal(clearOutput.diagnosis.decision, "partial_redesign");
  assert.equal(
    clearOutput.diagnosticOutput.sections.some((section: any) => section.title === "Prior Review Evidence"),
    true,
  );
  assert.equal(Array.isArray(clearOutput.requestAnalysis.taskBreakdown), true);
  assert.equal(Array.isArray(clearOutput.requestAnalysis.roleRoutes), true);
  assert.equal(Array.isArray(clearOutput.openclawOutputs), true);
  assert.equal(clearOutput.openclawOutputs.length > 0, true);
  assert.equal(Array.isArray(clearOutput.hermesReviews), true);
  assert.equal(clearOutput.hermesReviews.length > 0, true);
  assert.equal(Array.isArray(clearOutput.meetingHistory), true);
  assert.equal(typeof clearOutput.finalSynthesis, "string");
  assert.equal(clearOutput.escalation.required, false);
  assert.equal(clearOutput.escalation.decisionContext.status, "finalized");
  assert.equal(clearOutput.escalation.decisionContext.trigger, "none");
  assert.equal(clearOutput.escalation.nextAction.type, "continue");
  assert.deepEqual(clearOutput.escalation.nextAction.requestedFields, []);
  assert.equal(typeof clearOutput.escalation.preservedContext.compressedContext, "string");
  assert.equal(typeof clearOutput.tokenStrategy.rawStorage, "string");
  assert.equal(typeof clearOutput.tokenStrategy.exposedLoopContext, "string");
  assert.equal(typeof clearOutput.tokenStrategy.compressionPolicy, "string");
  assert.match(clearOutput.tokenStrategy.targetReduction, /40-50%/);

  const ambiguous = await executeDryRunCommand(["--request", ambiguousRequest]);
  const ambiguousOutput = parseJson(ambiguous.stdout);
  assert.equal(ambiguous.exitCode, 0);
  assert.equal(ambiguousOutput.schemaVersion, "final-output-artifact.v1");
  assert.equal(ambiguousOutput.userRequest, ambiguousRequest);
  assert.equal(ambiguousOutput.metadata.inputIdentifier, "request:4b663e07326e850a");
  assert.equal(ambiguousOutput.metadata.executionId, "run:af5510a30d6bdc67");
  assert.equal(ambiguousOutput.metadata.inputSource, "inline");
  assert.deepEqual(ambiguousOutput.metadata.version, expectedVersionMetadata());
  assert.deepEqual(ambiguousOutput.metadata.runSettings, expectedRunSettings());
  assert.equal(ambiguousOutput.status, "waiting_for_user");
  assert.equal(
    ambiguousOutput.diagnosticOutput.sections.some((section: any) => section.title === "Prior Review Evidence"),
    true,
  );
  assert.equal(Array.isArray(ambiguousOutput.openclawOutputs), true);
  assert.equal(Array.isArray(ambiguousOutput.hermesReviews), true);
  assert.equal(ambiguousOutput.escalation.required, true);
  assert.equal(Array.isArray(ambiguousOutput.escalation.reasons), true);
  assert.equal(ambiguousOutput.escalation.decisionContext.status, "waiting_for_user");
  assert.equal(ambiguousOutput.escalation.decisionContext.trigger, "ambiguous_request");
  assert.equal(ambiguousOutput.escalation.decisionContext.diagnosisDecision, "partial_redesign");
  assert.equal(typeof ambiguousOutput.escalation.decisionContext.latestMeetingSummary, "string");
  assert.equal(ambiguousOutput.escalation.nextAction.type, "user_input_required");
  assert.deepEqual(ambiguousOutput.escalation.nextAction.requestedFields, [
    "success_criteria",
    "preferred_direction",
    "constraints_or_examples",
  ]);
  assert.equal(typeof ambiguousOutput.escalation.preservedContext.rawStorage, "string");
  assert.equal(typeof ambiguousOutput.escalation.preservedContext.exposedSummary, "string");
  assert.equal(typeof ambiguousOutput.escalation.preservedContext.compressedContext, "string");

  const invalid = await executeDryRunCommand(["--request", "   "]);
  const invalidOutput = parseJson(invalid.stderr);
  assert.equal(invalid.exitCode, 2);
  assert.equal(invalid.stdout, "");
  assert.deepEqual(invalidOutput, {
    error: "invalid_input",
    message: "userRequest must be a non-empty string",
  });

  return {
    command: "ai-agent check-dry-run-contract",
    status: "passed",
    contract: {
      schemaVersion: "dry-run-contract.v1",
      deterministic: true,
      dryRunCommand: "npm run dry-run -- --request <user_request>",
      cases: [
        {
          case: "clear_request",
          exitCode: firstClear.exitCode,
          stream: "stdout",
          parseableJson: true,
          status: clearOutput.status,
          requiredFields: [
            "command",
            "metadata",
            "metadata.executionId",
            "metadata.version",
            "metadata.runSettings",
            "metadata.runSettings.models",
            "schemaVersion",
            "status",
            "userRequest",
            "selectedDecision",
            "selectedDecision.justification",
            "diagnosis",
            "diagnosis.justification",
            "diagnosticOutput",
            "diagnosticOutput.sections",
            "requestAnalysis",
            "openclawOutputs",
            "hermesReviews",
            "meetingHistory",
            "finalSynthesis",
            "escalation",
            "escalation.decisionContext",
            "escalation.nextAction",
            "escalation.preservedContext",
            "tokenStrategy",
          ],
        },
        {
          case: "ambiguous_request",
          exitCode: ambiguous.exitCode,
          stream: "stdout",
          parseableJson: true,
          status: ambiguousOutput.status,
          requiredFields: [
            "command",
            "metadata",
            "metadata.executionId",
            "metadata.version",
            "metadata.runSettings",
            "metadata.runSettings.models",
            "schemaVersion",
            "status",
            "userRequest",
            "selectedDecision",
            "selectedDecision.justification",
            "diagnosis",
            "diagnosis.justification",
            "diagnosticOutput",
            "diagnosticOutput.sections",
            "requestAnalysis",
            "openclawOutputs",
            "hermesReviews",
            "meetingHistory",
            "escalation",
            "escalation.decisionContext",
            "escalation.nextAction",
            "escalation.preservedContext",
            "tokenStrategy",
          ],
        },
        {
          case: "invalid_input",
          exitCode: invalid.exitCode,
          stream: "stderr",
          parseableJson: true,
          error: invalidOutput.error,
          requiredFields: ["error", "message"],
        },
      ],
    },
  };
}

export async function executeCheckDryRunContractCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkDryRunContract();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown dry-run contract check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "dry_run_contract_failed", message }, null, 2)}\n`,
    };
  }
}

function parseJson(value: string): Record<string, any> {
  const parsed = JSON.parse(value);
  assert.equal(typeof parsed, "object");
  assert.notEqual(parsed, null);
  return parsed;
}

function omitJustification<T extends { justification?: unknown }>(value: T): Omit<T, "justification"> {
  const { justification, ...rest } = value;
  void justification;
  return rest;
}

function expectedMetadata(inputIdentifier: string, inputSource: "default" | "inline" | "file") {
  return {
    executionId: expectedExecutionId(inputIdentifier),
    inputIdentifier,
    inputSource,
    version: expectedVersionMetadata(),
    runSettings: expectedRunSettings(),
  };
}

function expectedVersionMetadata() {
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

function expectedExecutionId(inputIdentifier: string): string {
  const known: Record<string, string> = {
    "request:f42143edc0867a0d": "run:5f605735bb696dec",
    "request:4b663e07326e850a": "run:af5510a30d6bdc67",
  };
  return known[inputIdentifier] ?? assert.fail(`missing expected execution id for ${inputIdentifier}`);
}

function expectedRunSettings() {
  return {
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
}

const invokedAsScript = process.argv[1]?.endsWith("check-dry-run-contract.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckDryRunContractCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
