import assert from "node:assert/strict";
import {
  finalOutputArtifactSchema,
  finalOutputRequiredFields,
  validateFinalOutputArtifact,
} from "../src/final-output-schema.ts";
import { executeDryRunCommand } from "./dry-run.ts";

interface FinalOutputSchemaCheckResult {
  command: "ai-agent check-final-output-schema";
  status: "passed";
  schema: {
    schemaVersion: "final-output-artifact.v1";
    schemaId: "ai-agent.final-output-artifact.v1";
    requiredFields: string[];
    mvpCoverage: {
      userRequestAnalysis: true;
      taskBreakdown: true;
      roleRouting: true;
      openclawExecutionOutputs: true;
      hermesReviews: true;
      preservedMeetingHistory: true;
      finalSynthesis: true;
      escalation: true;
      diagnosis: true;
      priorReviewEvidence: true;
      tokenStrategy: true;
    };
  };
  validation: {
    clearRequestValid: true;
    ambiguousRequestValid: true;
    missingRequiredFieldRejected: true;
  };
}

const clearRequest = "뮤직비디오 오프닝 아이디어를 회의해줘.";
const ambiguousRequest = "대충 좋은 후보 여러 개 추천만 해줘.";

export async function checkFinalOutputSchema(): Promise<FinalOutputSchemaCheckResult> {
  assert.equal(finalOutputArtifactSchema.$id, "ai-agent.final-output-artifact.v1");
  assert.deepEqual(finalOutputArtifactSchema.required, [
    "schemaVersion",
    "command",
    "metadata",
    "status",
    "threadId",
    "userRequest",
    "diagnosis",
    "diagnosticOutput",
    "requestAnalysis",
    "openclawOutputs",
    "hermesReviews",
    "meetingHistory",
    "escalation",
    "tokenStrategy",
  ]);

  const clearOutput = await parseSuccessfulDryRun(["--request", clearRequest]);
  const ambiguousOutput = await parseSuccessfulDryRun(["--request", ambiguousRequest]);

  const clearValidation = validateFinalOutputArtifact(clearOutput);
  const ambiguousValidation = validateFinalOutputArtifact(ambiguousOutput);
  assert.deepEqual(clearValidation, {
    valid: true,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputRequiredFields],
    errors: [],
  });
  assert.equal(ambiguousValidation.valid, true);
  assert.deepEqual(ambiguousValidation.errors, []);
  assert.equal(clearOutput.schemaVersion, "final-output-artifact.v1");
  assert.equal(clearOutput.status, "finalized");
  assert.equal(clearOutput.openclawOutputs.length > 0, true);
  assert.equal(clearOutput.hermesReviews.length > 0, true);
  assert.equal(clearOutput.meetingHistory.every((turn: any) => turn.content === undefined && turn.fullContent === undefined), true);
  assert.equal(
    clearOutput.diagnosticOutput.sections.some((section: any) => section.title === "Prior Review Evidence"),
    true,
  );
  assert.equal(clearOutput.tokenStrategy.targetReduction.includes("40-50%"), true);
  assert.equal(ambiguousOutput.status, "waiting_for_user");
  assert.equal(ambiguousOutput.escalation.required, true);

  const invalid = structuredClone(clearOutput);
  delete (invalid as any).tokenStrategy;
  const invalidValidation = validateFinalOutputArtifact(invalid);
  assert.equal(invalidValidation.valid, false);
  assert.match(invalidValidation.errors.join("\n"), /tokenStrategy/);

  return {
    command: "ai-agent check-final-output-schema",
    status: "passed",
    schema: {
      schemaVersion: "final-output-artifact.v1",
      schemaId: finalOutputArtifactSchema.$id,
      requiredFields: [...finalOutputRequiredFields],
      mvpCoverage: {
        userRequestAnalysis: true,
        taskBreakdown: true,
        roleRouting: true,
        openclawExecutionOutputs: true,
        hermesReviews: true,
        preservedMeetingHistory: true,
        finalSynthesis: true,
        escalation: true,
        diagnosis: true,
        priorReviewEvidence: true,
        tokenStrategy: true,
      },
    },
    validation: {
      clearRequestValid: true,
      ambiguousRequestValid: true,
      missingRequiredFieldRejected: true,
    },
  };
}

export async function executeCheckFinalOutputSchemaCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkFinalOutputSchema();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown final output schema check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "final_output_schema_check_failed", message }, null, 2)}\n`,
    };
  }
}

async function parseSuccessfulDryRun(args: string[]): Promise<Record<string, any>> {
  const result = await executeDryRunCommand(args);
  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  const parsed = JSON.parse(result.stdout);
  assert.equal(typeof parsed, "object");
  assert.notEqual(parsed, null);
  return parsed;
}

const invokedAsScript = process.argv[1]?.endsWith("check-final-output-schema.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckFinalOutputSchemaCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
