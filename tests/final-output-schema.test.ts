import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { executeCheckFinalOutputSchemaCommand } from "../scripts/check-final-output-schema.ts";
import { executeDryRunCommand } from "../scripts/dry-run.ts";
import * as finalOutputSchemaModule from "../src/final-output-schema.ts";
import {
  finalOutputArtifactSchema,
  finalOutputRequiredFields,
  validateFinalOutputArtifact,
} from "../src/final-output-schema.ts";

test("final output artifact schema is machine-readable and covers README MVP fields", () => {
  assert.equal(finalOutputArtifactSchema.$id, "ai-agent.final-output-artifact.v1");
  assert.equal(finalOutputArtifactSchema.properties.schemaVersion.const, "final-output-artifact.v1");
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
  assert.deepEqual([...finalOutputRequiredFields], [
    "schemaVersion",
    "command",
    "metadata.executionId",
    "metadata.inputIdentifier",
    "metadata.inputSource",
    "metadata.version",
    "metadata.version.schemaVersion",
    "metadata.version.artifactSchemaVersion",
    "metadata.version.commandVersion",
    "metadata.version.implementationVersion",
    "metadata.version.runtime.name",
    "metadata.version.runtime.version",
    "metadata.runSettings",
    "metadata.runSettings.executionMode",
    "metadata.runSettings.orchestrator.maxRounds",
    "metadata.runSettings.orchestrator.escalationPolicy",
    "metadata.runSettings.models.openclawOwner.provider",
    "metadata.runSettings.models.openclawOwner.model",
    "metadata.runSettings.models.openclawOwner.temperature",
    "metadata.runSettings.models.openclawOwner.maxOutputTokens",
    "metadata.runSettings.models.hermesReviewer.provider",
    "metadata.runSettings.models.hermesReviewer.model",
    "metadata.runSettings.models.hermesReviewer.temperature",
    "metadata.runSettings.models.hermesReviewer.maxOutputTokens",
    "metadata.runSettings.models.openclawFinalizer.provider",
    "metadata.runSettings.models.openclawFinalizer.model",
    "metadata.runSettings.models.openclawFinalizer.temperature",
    "metadata.runSettings.models.openclawFinalizer.maxOutputTokens",
    "status",
    "threadId",
    "userRequest",
    "diagnosis.decision",
    "diagnosis.decisionLabel",
    "diagnosis.basis",
    "diagnosis.justification",
    "diagnosticOutput.sections",
    "requestAnalysis.taskBreakdown",
    "requestAnalysis.roleRoutes",
    "requestAnalysis.tokenStrategy",
    "openclawOutputs",
    "hermesReviews",
    "meetingHistory",
    "escalation.required",
    "escalation.reasons",
    "escalation.decisionContext",
    "escalation.nextAction",
    "escalation.preservedContext",
    "tokenStrategy.rawStorage",
    "tokenStrategy.exposedLoopContext",
    "tokenStrategy.compressionPolicy",
    "tokenStrategy.targetReduction",
  ]);
});

test("schema module public runtime exports cover the primary valid-artifact success path", async () => {
  assert.deepEqual(Object.keys(finalOutputSchemaModule).sort(), [
    "finalOutputArtifactSchema",
    "finalOutputRequiredFields",
    "validateFinalOutputArtifact",
  ]);

  const result = await executeDryRunCommand(["--request", "브랜드 캠페인 제작 회의를 진행하고 최종안을 만들어줘."]);
  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  const artifact = JSON.parse(result.stdout);

  assert.equal(finalOutputSchemaModule.finalOutputArtifactSchema.$id, "ai-agent.final-output-artifact.v1");
  for (const requiredField of finalOutputSchemaModule.finalOutputArtifactSchema.required) {
    assert.notEqual(readPath(artifact, requiredField), undefined, `${requiredField} should be present in valid artifact`);
  }
  for (const requiredField of finalOutputSchemaModule.finalOutputRequiredFields) {
    assert.notEqual(readPath(artifact, requiredField), undefined, `${requiredField} should be present in valid artifact`);
  }
  assert.deepEqual(finalOutputSchemaModule.validateFinalOutputArtifact(artifact), {
    valid: true,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputSchemaModule.finalOutputRequiredFields],
    errors: [],
  });
});

test("dry-run final output artifact validates against schema", async () => {
  const result = await executeDryRunCommand(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);

  assert.equal(result.exitCode, 0);
  const parsed = JSON.parse(result.stdout);
  const validation = validateFinalOutputArtifact(parsed);

  assert.deepEqual(validation, {
    valid: true,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputRequiredFields],
    errors: [],
  });
  assert.equal(parsed.schemaVersion, "final-output-artifact.v1");
  assert.deepEqual(parsed.metadata, expectedMetadata("request:f42143edc0867a0d", "inline"));
  assert.equal(parsed.userRequest, "뮤직비디오 오프닝 아이디어를 회의해줘.");
  assert.equal(parsed.openclawOutputs.some((turn: any) => turn.kind === "owner_draft"), true);
  assert.equal(parsed.hermesReviews.some((turn: any) => turn.kind === "review"), true);
  assert.deepEqual(parsed.diagnosticOutput.sections[0], {
    title: "Prior Review Evidence",
    evidence: {
      artifactPath: join(process.cwd(), "docs", "review-evidence.json"),
      schemaVersion: "review-evidence.v1",
      recommendation: "partial_redesign",
      ...readCurrentReviewEvidenceCounts(),
      validationValid: true,
      completenessComplete: true,
      decisionGateAccepted: true,
    },
  });
  assert.equal(parsed.tokenStrategy.targetReduction.includes("40-50%"), true);
});

test("fixture-backed final output command emits valid machine-readable artifact", async () => {
  const fixturePath = "tests/fixtures/final-output-request.txt";
  const result = await executeDryRunCommand(["--request-file", fixturePath]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.doesNotThrow(() => JSON.parse(result.stdout));

  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(validateFinalOutputArtifact(parsed), {
    valid: true,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputRequiredFields],
    errors: [],
  });
  assert.equal(parsed.status, "finalized");
  assert.equal(parsed.userRequest, readFileSync(fixturePath, "utf8"));
  assert.equal(parsed.metadata.inputSource, "file");
  assert.equal(parsed.command, "ai-agent dry-run");
  assert.equal(parsed.meetingHistory.every((turn: any) => turn.content === undefined && turn.fullContent === undefined), true);
});

test("fixture-backed final output artifact is deterministic across repeated runs", async () => {
  const fixturePath = "tests/fixtures/final-output-request.txt";
  const first = await executeDryRunCommand(["--request-file", fixturePath]);
  const second = await executeDryRunCommand(["--request-file", fixturePath]);

  assert.equal(first.exitCode, 0);
  assert.equal(second.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(second.stderr, "");
  assert.equal(first.stdout, second.stdout);

  const parsed = JSON.parse(first.stdout);
  assert.deepEqual(validateFinalOutputArtifact(parsed), {
    valid: true,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputRequiredFields],
    errors: [],
  });
  for (const field of finalOutputRequiredFields) {
    assert.notEqual(readPath(parsed, field), undefined, `${field} should be present`);
  }
  assert.equal(parsed.schemaVersion, "final-output-artifact.v1");
  assert.equal(parsed.command, "ai-agent dry-run");
  assert.equal(parsed.userRequest, readFileSync(fixturePath, "utf8"));
  assert.equal(parsed.status, "finalized");
});

test("final output schema rejects missing required MVP fields", async () => {
  const result = await executeDryRunCommand(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);
  const parsed = JSON.parse(result.stdout);
  delete parsed.hermesReviews;

  const validation = validateFinalOutputArtifact(parsed);

  assert.equal(validation.valid, false);
  assert.match(validation.errors.join("\n"), /hermesReviews/);
});

test("final output schema rejects decision labels outside the allowed result set", async () => {
  const result = await executeDryRunCommand(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);
  const parsed = JSON.parse(result.stdout);
  parsed.diagnosis.decisionLabel = "full redesign";

  const validation = validateFinalOutputArtifact(parsed);

  assert.equal(validation.valid, false);
  assert.match(validation.errors.join("\n"), /diagnosis\.decisionLabel must be one of Keep, partial redesign, full replan/);
});

test("dry-run decision diagnostic labels match the exact allowed result set", async () => {
  const result = await executeDryRunCommand(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);
  const parsed = JSON.parse(result.stdout);
  const decisionLabels = parsed.diagnosticOutput.sections
    .filter((section: any) => ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"].includes(section.title))
    .map((section: any) => section.evidence.label);

  assert.deepEqual(decisionLabels, ["Keep", "partial redesign", "full replan"]);
});

test("final output schema command validates clear and escalation outputs", async () => {
  const result = await executeCheckFinalOutputSchemaCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(parsed.validation, {
    clearRequestValid: true,
    ambiguousRequestValid: true,
    missingRequiredFieldRejected: true,
  });
  assert.deepEqual(parsed.schema.mvpCoverage, {
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
  });
});

function readPath(value: Record<string, any>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (current === undefined || current === null || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}

function readCurrentReviewEvidenceCounts(): { inspectedModules: number; findingCount: number } {
  const artifact = JSON.parse(readFileSync(join(process.cwd(), "docs", "review-evidence.json"), "utf8"));
  return {
    inspectedModules: artifact.summary.inspectedModules,
    findingCount: artifact.summary.findingCount,
  };
}

function expectedMetadata(inputIdentifier: string, inputSource: "default" | "inline" | "file") {
  return {
    executionId: expectedExecutionId(inputIdentifier),
    inputIdentifier,
    inputSource,
    version: {
      schemaVersion: "run-version-metadata.v1",
      artifactSchemaVersion: "final-output-artifact.v1",
      commandVersion: "ai-agent-dry-run.v1",
      implementationVersion: "multi-agent-meeting-mvp.v1",
      runtime: {
        name: "node",
        version: process.versions.node,
      },
    },
    runSettings: {
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
    },
  };
}

function expectedExecutionId(inputIdentifier: string): string {
  const known: Record<string, string> = {
    "request:f42143edc0867a0d": "run:5f605735bb696dec",
  };
  return known[inputIdentifier] ?? assert.fail(`missing expected execution id for ${inputIdentifier}`);
}
