import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { executeDryRunCommand } from "./dry-run.ts";
import { assertImplementationDecisionLabel, implementationDecisionLabels } from "../src/evaluation.ts";

interface DecisionDeterminismRun {
  run: 1 | 2;
  exitCode: 0;
  stdoutSha256: string;
  decisionResult: DecisionResultSnapshot;
}

interface DecisionResultSnapshot {
  selectedDecision: unknown;
  diagnosis: unknown;
  decisionDiagnosticSections: unknown[];
  metadata: {
    inputIdentifier: string;
    executionId: string;
    inputSource: string;
  };
}

interface DecisionDeterminismCheckResult {
  command: "ai-agent check-decision-determinism";
  status: "passed";
  scenario: "fixed_input_repository_state_repeated_runs";
  sourceCommand: "npm run dry-run -- --request <fixed_request>";
  fixedInput: {
    userRequest: string;
    inputIdentifier: string;
    executionId: string;
  };
  runs: [DecisionDeterminismRun, DecisionDeterminismRun];
  determinism: {
    deterministic: true;
    stdoutEqual: true;
    selectedDecisionEqual: true;
    diagnosisEqual: true;
    decisionDiagnosticSectionsEqual: true;
  };
  allowedDecisionResults: typeof implementationDecisionLabels;
}

const fixedRequest = "뮤직비디오 오프닝 아이디어를 회의해줘.";

export async function checkDecisionDeterminism(): Promise<DecisionDeterminismCheckResult> {
  const first = await executeDryRunCommand(["--request", fixedRequest]);
  const second = await executeDryRunCommand(["--request", fixedRequest]);

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(second.exitCode, 0);
  assert.equal(second.stderr, "");
  assert.equal(first.stdout, second.stdout, "dry-run stdout must be deterministic for the same fixed request");

  const firstOutput = parseJson(first.stdout);
  const secondOutput = parseJson(second.stdout);
  const firstDecision = snapshotDecisionResult(firstOutput);
  const secondDecision = snapshotDecisionResult(secondOutput);

  assert.deepEqual(firstDecision, secondDecision, "decision result must be deterministic for repeated fixed-input runs");
  assert.equal(getNested(firstOutput, ["selectedDecision", "outcome"]), "partial_redesign");
  assert.equal(getNested(firstOutput, ["diagnosis", "decision"]), "partial_redesign");
  assertImplementationDecisionLabel(getNested(firstOutput, ["selectedDecision", "label"]), "selectedDecision.label");
  assertImplementationDecisionLabel(getNested(firstOutput, ["diagnosis", "decisionLabel"]), "diagnosis.decisionLabel");
  assertDecisionDiagnosticLabels(firstDecision.decisionDiagnosticSections);
  assert.equal(firstDecision.metadata.inputIdentifier, "request:f42143edc0867a0d");
  assert.equal(firstDecision.metadata.executionId, "run:5f605735bb696dec");
  assert.equal(firstDecision.decisionDiagnosticSections.length, 3);

  return {
    command: "ai-agent check-decision-determinism",
    status: "passed",
    scenario: "fixed_input_repository_state_repeated_runs",
    sourceCommand: "npm run dry-run -- --request <fixed_request>",
    fixedInput: {
      userRequest: fixedRequest,
      inputIdentifier: firstDecision.metadata.inputIdentifier,
      executionId: firstDecision.metadata.executionId,
    },
    runs: [
      { run: 1, exitCode: 0, stdoutSha256: sha256(first.stdout), decisionResult: firstDecision },
      { run: 2, exitCode: 0, stdoutSha256: sha256(second.stdout), decisionResult: secondDecision },
    ],
    determinism: {
      deterministic: true,
      stdoutEqual: true,
      selectedDecisionEqual: true,
      diagnosisEqual: true,
      decisionDiagnosticSectionsEqual: true,
    },
    allowedDecisionResults: implementationDecisionLabels,
  };
}

export async function executeCheckDecisionDeterminismCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkDecisionDeterminism();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown decision determinism check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "decision_determinism_check_failed", message }, null, 2)}\n`,
    };
  }
}

function snapshotDecisionResult(output: Record<string, any>): DecisionResultSnapshot {
  const sections = getNested(output, ["diagnosticOutput", "sections"]);
  assert.equal(Array.isArray(sections), true);

  return {
    selectedDecision: output.selectedDecision,
    diagnosis: output.diagnosis,
    decisionDiagnosticSections: sections.filter((section: any) =>
      ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"].includes(section.title),
    ),
    metadata: {
      inputIdentifier: String(getNested(output, ["metadata", "inputIdentifier"])),
      executionId: String(getNested(output, ["metadata", "executionId"])),
      inputSource: String(getNested(output, ["metadata", "inputSource"])),
    },
  };
}

function parseJson(value: string): Record<string, any> {
  const parsed = JSON.parse(value);
  assert.equal(typeof parsed, "object");
  assert.notEqual(parsed, null);
  return parsed;
}

function getNested(value: Record<string, any>, path: string[]): unknown {
  return path.reduce<unknown>((current, key) => {
    assert.equal(typeof current, "object");
    assert.notEqual(current, null);
    return (current as Record<string, unknown>)[key];
  }, value);
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function assertDecisionDiagnosticLabels(sections: unknown[]): void {
  assert.deepEqual(
    sections.map((section) => getNested(section as Record<string, unknown>, ["evidence", "label"])),
    [...implementationDecisionLabels],
  );
}

const invokedAsScript = process.argv[1]?.endsWith("check-decision-determinism.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckDecisionDeterminismCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
