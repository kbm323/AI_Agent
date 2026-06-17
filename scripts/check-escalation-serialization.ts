import assert from "node:assert/strict";
import { serializeEscalationResult, type EscalationSerializationInput } from "../src/index.ts";

interface EscalationSerializationCheckResult {
  command: "ai-agent check-escalation-serialization";
  status: "passed";
  deterministic: true;
  serializationPath: "serializeEscalationResult";
  artifact: {
    schemaVersion: "escalation-result.v1";
    escalation: {
      required: true;
      reasons: string[];
      triggerType: string;
      nextRequiredAction: string;
    };
  };
  serializedArtifact: string;
}

const representativeEscalationInput: EscalationSerializationInput = {
  reasons: ["reviewer_requested_user_decision", "max_rounds_without_agreement"],
  triggerType: "meeting_loop",
  nextRequiredAction: "Ask the user to choose a direction or provide stronger success criteria before continuing.",
};

export function checkEscalationSerialization(): EscalationSerializationCheckResult {
  const firstSerialized = serializeEscalationResult(representativeEscalationInput);
  const secondSerialized = serializeEscalationResult(representativeEscalationInput);
  assert.equal(firstSerialized, secondSerialized, "escalation serialization must be deterministic");

  const artifact = JSON.parse(firstSerialized);
  assert.deepEqual(artifact, {
    schemaVersion: "escalation-result.v1",
    escalation: {
      required: true,
      reasons: ["reviewer_requested_user_decision", "max_rounds_without_agreement"],
      triggerType: "meeting_loop",
      nextRequiredAction: "Ask the user to choose a direction or provide stronger success criteria before continuing.",
    },
  });

  return {
    command: "ai-agent check-escalation-serialization",
    status: "passed",
    deterministic: true,
    serializationPath: "serializeEscalationResult",
    artifact,
    serializedArtifact: firstSerialized,
  };
}

export function executeCheckEscalationSerializationCommand(): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkEscalationSerialization();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown escalation serialization check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "escalation_serialization_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-escalation-serialization.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckEscalationSerializationCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
