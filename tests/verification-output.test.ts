import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeCheckVerificationOutputCommand } from "../scripts/check-verification-output.ts";
import { deriveAcceptanceEvidenceFromArtifactEvidence } from "../src/acceptance-evidence.ts";
import {
  buildVerificationOutputCheckResult,
  buildVerificationOutputDocument,
  defaultVerificationOutputPath,
  validateVerificationOutputCheckResult,
  validateVerificationOutputDocument,
  verificationArtifactEvidenceSpecs,
  verificationOutputCheckResultRequiredFields,
  verificationOutputCheckResultSchema,
  verificationOutputRequiredFields,
  verificationOutputSchema,
  writeVerificationOutputDocument,
} from "../src/verification-output.ts";

test("verification output schema is machine-readable and has fixed artifact evidence fields", () => {
  assert.equal(verificationOutputSchema.$id, "ai-agent.verification-output.v1");
  assert.deepEqual(verificationOutputSchema.required, [
    "schemaVersion",
    "command",
    "status",
    "deterministic",
    "artifactEvidence",
    "acceptanceEvidence",
  ]);
  assert.deepEqual([...verificationOutputRequiredFields], [
    "schemaVersion",
    "command",
    "status",
    "deterministic",
    "artifactEvidence",
    "artifactEvidence[].id",
    "artifactEvidence[].path",
    "artifactEvidence[].schemaVersion",
    "artifactEvidence[].requiredFieldsPresent",
    "artifactEvidence[].evidence",
    "acceptanceEvidence.workflowRunnerPassed",
    "acceptanceEvidence.mvpObservable",
    "acceptanceEvidence.diagnosisComplete",
    "acceptanceEvidence.invalidInputHandled",
    "acceptanceEvidence.escalationHandled",
    "acceptanceEvidence.tokenStrategyDefined",
  ]);
  assert.deepEqual(
    verificationArtifactEvidenceSpecs.map((spec) => spec.id),
    [
      "diagnosis_report",
      "requirement_gap_mapping",
      "dry_run_final_output",
      "meeting_loop_transcript",
      "token_cost_control",
      "typecheck_check",
      "dry_run_fixture_harness",
      "verification_workflow_runner",
    ],
  );
});

test("verification output module emits a stable document containing required artifact evidence", () => {
  const root = buildFixtureProject();
  try {
    const first = buildVerificationOutputDocument(root);
    const second = buildVerificationOutputDocument(root);

    assert.deepEqual(second, first);
    assert.deepEqual(validateVerificationOutputDocument(first), {
      valid: true,
      schemaVersion: "verification-output.v1",
      checkedFields: [...verificationOutputRequiredFields],
      errors: [],
    });
    assert.equal(first.command, "ai-agent check-verification-output");
    assert.equal(first.status, "passed");
    assert.equal(first.deterministic, true);
    assert.equal(first.artifactEvidence.length, 8);
    assert.deepEqual(
      first.artifactEvidence.map((entry) => ({
        id: entry.id,
        schemaVersion: entry.schemaVersion,
        requiredFieldsPresent: entry.requiredFieldsPresent,
      })),
      [
        { id: "diagnosis_report", schemaVersion: "diagnosis-report.v1", requiredFieldsPresent: true },
        { id: "requirement_gap_mapping", schemaVersion: "implementation-capabilities.v1", requiredFieldsPresent: true },
        { id: "dry_run_final_output", schemaVersion: "final-output-artifact.v1", requiredFieldsPresent: true },
        { id: "meeting_loop_transcript", schemaVersion: "preserved-meeting-transcript.v1", requiredFieldsPresent: true },
        { id: "token_cost_control", schemaVersion: "token-cost-control-check.v1", requiredFieldsPresent: true },
        { id: "typecheck_check", schemaVersion: "typecheck-proof-artifact.v1", requiredFieldsPresent: true },
        { id: "dry_run_fixture_harness", schemaVersion: "dry-run-fixture-harness.v1", requiredFieldsPresent: true },
        { id: "verification_workflow_runner", schemaVersion: "verification-workflow-runner.v1", requiredFieldsPresent: true },
      ],
    );
    assert.deepEqual(first.acceptanceEvidence, {
      workflowRunnerPassed: true,
      mvpObservable: true,
      diagnosisComplete: true,
      invalidInputHandled: true,
      escalationHandled: true,
      tokenStrategyDefined: true,
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("acceptance evidence derivation module computes positive evidence from validated artifacts", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);

    const acceptanceEvidence = deriveAcceptanceEvidenceFromArtifactEvidence(document.artifactEvidence);

    assert.deepEqual(acceptanceEvidence, {
      workflowRunnerPassed: true,
      mvpObservable: true,
      diagnosisComplete: true,
      invalidInputHandled: true,
      escalationHandled: true,
      tokenStrategyDefined: true,
    });
    assert.deepEqual(acceptanceEvidence, document.acceptanceEvidence);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output command writes stable artifact and returns schema validation evidence", () => {
  const root = buildFixtureProject();
  try {
    const first = executeCheckVerificationOutputCommand(root);
    const firstArtifact = readFileSync(join(root, defaultVerificationOutputPath), "utf8");
    const second = executeCheckVerificationOutputCommand(root);
    const secondArtifact = readFileSync(join(root, defaultVerificationOutputPath), "utf8");

    assert.equal(first.exitCode, 0);
    assert.equal(first.stderr, "");
    assert.equal(second.exitCode, 0);
    assert.equal(second.stderr, "");
    assert.equal(first.stdout, second.stdout);
    assert.equal(firstArtifact, secondArtifact);
    assert.equal(existsSync(join(root, defaultVerificationOutputPath)), true);

    const parsed = JSON.parse(first.stdout);
    assert.deepEqual(parsed, {
      command: "ai-agent check-verification-output",
      status: "passed",
      schema: {
        schemaVersion: "verification-output.v1",
        schemaId: "ai-agent.verification-output.v1",
        requiredFields: [...verificationOutputRequiredFields],
      },
      artifact: {
        path: join(root, defaultVerificationOutputPath),
        schemaVersion: "verification-output.v1",
        evidenceCount: 8,
        validationValid: true,
      },
    });
    assert.equal(validateVerificationOutputDocument(JSON.parse(firstArtifact)).valid, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output document writer persists the exported artifact document on its primary success path", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    const outputPath = "artifacts/custom-verification-output.json";

    const first = writeVerificationOutputDocument({ projectRoot: root, outputPath, document });
    const firstArtifact = readFileSync(first.path, "utf8");
    const second = writeVerificationOutputDocument({ projectRoot: root, outputPath, document });
    const secondArtifact = readFileSync(second.path, "utf8");

    assert.deepEqual(first, {
      path: join(root, outputPath),
      document,
    });
    assert.deepEqual(second, first);
    assert.equal(firstArtifact, secondArtifact);
    assert.equal(firstArtifact, `${JSON.stringify(document, null, 2)}\n`);
    assert.deepEqual(JSON.parse(firstArtifact), document);
    assert.equal(validateVerificationOutputDocument(JSON.parse(firstArtifact)).valid, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output command fails deterministically for invalid completion evidence", () => {
  const root = buildFixtureProject();
  try {
    writeJson(join(root, "docs", "generated", "token-reduction-check-result.json"), {
      schemaVersion: "token-cost-control-check.v1",
      status: "failed",
      percentSavings: 12,
      targetThreshold: {
        percentSavings: 40,
      },
      pass: false,
    });

    const first = executeCheckVerificationOutputCommand(root);
    const second = executeCheckVerificationOutputCommand(root);

    assert.equal(first.exitCode, 1);
    assert.equal(first.stdout, "");
    assert.equal(first.stderr, second.stderr);
    assert.deepEqual(JSON.parse(first.stderr), {
      error: "verification_output_check_failed",
      message:
        "docs/generated/token-reduction-check-result.json invalid completion evidence status: expected \"passed\", got \"failed\"",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output command fails when invalid-input fixture evidence is missing", () => {
  const root = buildFixtureProject();
  try {
    writeJson(join(root, "tests", "fixtures", "dry-run-harness-fixtures.json"), [
      {
        name: "clear_request_from_file",
        invocation: "api",
        args: ["--request-file", "tests/fixtures/final-output-request.txt"],
        repetitions: 3,
        expected: {
          exitCode: 0,
          stream: "stdout",
          jsonStatus: "finalized",
        },
      },
      {
        name: "ambiguous_request_inline",
        invocation: "api",
        args: ["--request", "대충 좋은 후보 여러 개 추천만 해줘."],
        repetitions: 2,
        expected: {
          exitCode: 0,
          stream: "stdout",
          jsonStatus: "waiting_for_user",
        },
      },
    ]);

    const result = executeCheckVerificationOutputCommand(root);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.deepEqual(JSON.parse(result.stderr), {
      error: "verification_output_check_failed",
      message: "tests/fixtures/dry-run-harness-fixtures.json missing required evidence field 2.expected.exitCode",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output command fails when acceptance evidence cannot be computed from artifacts", () => {
  const root = buildFixtureProject();
  try {
    writeJson(join(root, "docs", "generated", "dry-run-final-output.json"), {
      schemaVersion: "final-output-artifact.v1",
      command: "ai-agent dry-run",
      status: "finalized",
      requestAnalysis: {
        taskBreakdown: ["분석"],
      },
      meetingHistory: [{ id: "turn-001" }],
      escalation: {
        required: false,
      },
      tokenStrategy: {
        targetReduction: "40-50%",
      },
    });

    const result = executeCheckVerificationOutputCommand(root);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.deepEqual(JSON.parse(result.stderr), {
      error: "verification_output_check_failed",
      message: "acceptanceEvidence.mvpObservable could not be computed from passing artifact evidence",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output command fails when workflow runner artifact does not prove the MVP workflow", () => {
  const root = buildFixtureProject();
  try {
    writeJson(join(root, "docs", "generated", "verification-workflow-result.json"), {
      schemaVersion: "verification-workflow-runner.v1",
      command: "ai-agent run-verification-workflow",
      status: "passed",
      cases: [
        {
          name: "finalized_meeting_loop",
          status: "failed",
          observed: {
            status: "failed",
          },
          failures: ["missing final synthesis"],
        },
      ],
      summary: {
        caseCount: 1,
        passedCaseCount: 0,
        failedCaseCount: 1,
        mvpWorkflowExecuted: false,
        escalationWorkflowExecuted: true,
        rawStorageSeparatedFromLoopContext: true,
      },
      errors: ["missing final synthesis"],
    });

    const result = executeCheckVerificationOutputCommand(root);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.deepEqual(JSON.parse(result.stderr), {
      error: "verification_output_check_failed",
      message:
        "docs/generated/verification-workflow-result.json invalid completion evidence summary.mvpWorkflowExecuted: expected true, got false",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output validation rejects hardcoded acceptance evidence when artifacts do not prove it", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    document.artifactEvidence[2].evidence.meetingTurnCount = 1;

    const validation = validateVerificationOutputDocument(document);

    assert.equal(validation.valid, false);
    assert.match(
      validation.errors.join("\n"),
      /acceptanceEvidence\.mvpObservable could not be computed from passing artifact evidence/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output check rejects artifacts missing acceptance evidence before writing a passing artifact", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root) as Partial<ReturnType<typeof buildVerificationOutputDocument>>;
    delete document.acceptanceEvidence;

    assert.throws(
      () => buildVerificationOutputCheckResult({ projectRoot: root, document: document as ReturnType<typeof buildVerificationOutputDocument> }),
      /verification output schema validation failed: acceptanceEvidence must be an object/,
    );
    assert.equal(existsSync(join(root, defaultVerificationOutputPath)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output check rejects malformed acceptance evidence values before writing a passing artifact", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    const malformedDocument = structuredClone(document) as ReturnType<typeof buildVerificationOutputDocument> & {
      acceptanceEvidence: Record<string, unknown>;
    };
    malformedDocument.acceptanceEvidence.workflowRunnerPassed = "true";
    malformedDocument.acceptanceEvidence.unvalidatedConclusion = true;

    assert.throws(
      () => buildVerificationOutputCheckResult({ projectRoot: root, document: malformedDocument }),
      /verification output schema validation failed: acceptanceEvidence\.unvalidatedConclusion is not supported; acceptanceEvidence\.workflowRunnerPassed must equal true/,
    );
    assert.equal(existsSync(join(root, defaultVerificationOutputPath)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output check result schema validates fixed CLI and API evidence fields", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    const first = buildVerificationOutputCheckResult({ projectRoot: root, document });
    const second = buildVerificationOutputCheckResult({ projectRoot: root, document });

    assert.equal(verificationOutputCheckResultSchema.$id, "ai-agent.verification-output-check-result.v1");
    assert.deepEqual(verificationOutputCheckResultSchema.required, ["command", "status", "schema", "artifact"]);
    assert.deepEqual([...verificationOutputCheckResultRequiredFields], [
      "command",
      "status",
      "schema.schemaVersion",
      "schema.schemaId",
      "schema.requiredFields",
      "artifact.path",
      "artifact.schemaVersion",
      "artifact.evidenceCount",
      "artifact.validationValid",
    ]);
    assert.deepEqual(second, first);
    assert.deepEqual(validateVerificationOutputCheckResult(first), {
      valid: true,
      schemaVersion: "verification-output-check-result.v1",
      checkedFields: [...verificationOutputCheckResultRequiredFields],
      errors: [],
    });
    assert.deepEqual(first, {
      command: "ai-agent check-verification-output",
      status: "passed",
      schema: {
        schemaVersion: "verification-output.v1",
        schemaId: "ai-agent.verification-output.v1",
        requiredFields: [...verificationOutputRequiredFields],
      },
      artifact: {
        path: join(root, defaultVerificationOutputPath),
        schemaVersion: "verification-output.v1",
        evidenceCount: 8,
        validationValid: true,
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output check result validation rejects missing or changed evidence fields", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    const result = buildVerificationOutputCheckResult({ projectRoot: root, document });
    const invalid = {
      ...result,
      artifact: {
        ...result.artifact,
        evidenceCount: 5,
        validationValid: undefined,
      },
    };

    const validation = validateVerificationOutputCheckResult(invalid);

    assert.equal(validation.valid, false);
    assert.match(validation.errors.join("\n"), /artifact\.evidenceCount must equal 8/);
    assert.match(validation.errors.join("\n"), /artifact\.validationValid must equal true/);
    assert.match(validation.errors.join("\n"), /artifact\.validationValid must be present/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output document validation rejects fixed-schema negative artifact evidence cases", () => {
  const root = buildFixtureProject();
  try {
    const validDocument = buildVerificationOutputDocument(root);
    const cases: Array<{
      name: string;
      mutate: (document: ReturnType<typeof buildVerificationOutputDocument>) => void;
      expectedError: RegExp;
    }> = [
      {
        name: "missing artifact evidence key",
        mutate: (document) => {
          delete (document.artifactEvidence[0].evidence as Record<string, unknown>).decision;
        },
        expectedError: /artifactEvidence\[diagnosis_report\]\.evidence\.decision must be present/,
      },
      {
        name: "malformed artifact evidence value",
        mutate: (document) => {
          (document.artifactEvidence[2].evidence as Record<string, unknown>).meetingTurnCount = { count: 1 };
        },
        expectedError: /artifactEvidence\[dry_run_final_output\]\.evidence\.meetingTurnCount must be a scalar evidence value/,
      },
      {
        name: "changed fixed artifact schema version",
        mutate: (document) => {
          document.artifactEvidence[4].schemaVersion = "token-cost-control-check.v2";
        },
        expectedError: /artifactEvidence\[token_cost_control\]\.schemaVersion must equal "token-cost-control-check\.v1"/,
      },
      {
        name: "missing artifact entry path",
        mutate: (document) => {
          document.artifactEvidence[5].path = "";
        },
        expectedError: /artifactEvidence\[\]\.path must be a non-empty string/,
      },
    ];

    for (const testCase of cases) {
      const invalid = structuredClone(validDocument);
      testCase.mutate(invalid);

      const validation = validateVerificationOutputDocument(invalid);

      assert.equal(validation.valid, false, testCase.name);
      assert.match(validation.errors.join("\n"), testCase.expectedError, testCase.name);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output check result validation rejects fixed-schema negative CLI and API result cases", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    const validResult = buildVerificationOutputCheckResult({ projectRoot: root, document });
    const cases: Array<{
      name: string;
      mutate: (result: ReturnType<typeof buildVerificationOutputCheckResult>) => void;
      expectedError: RegExp;
    }> = [
      {
        name: "missing schema required fields list",
        mutate: (result) => {
          delete (result.schema as Record<string, unknown>).requiredFields;
        },
        expectedError: /schema\.requiredFields must be present/,
      },
      {
        name: "changed schema id",
        mutate: (result) => {
          result.schema.schemaId = "ai-agent.verification-output.v2";
        },
        expectedError: /schema\.schemaId must equal "ai-agent\.verification-output\.v1"/,
      },
      {
        name: "missing artifact validation flag",
        mutate: (result) => {
          delete (result.artifact as Record<string, unknown>).validationValid;
        },
        expectedError: /artifact\.validationValid must be present/,
      },
      {
        name: "malformed artifact evidence count",
        mutate: (result) => {
          result.artifact.evidenceCount = 5;
        },
        expectedError: /artifact\.evidenceCount must equal 8/,
      },
    ];

    for (const testCase of cases) {
      const invalid = structuredClone(validResult);
      testCase.mutate(invalid);

      const validation = validateVerificationOutputCheckResult(invalid);

      assert.equal(validation.valid, false, testCase.name);
      assert.match(validation.errors.join("\n"), testCase.expectedError, testCase.name);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification output schema validation rejects missing required artifact evidence", () => {
  const root = buildFixtureProject();
  try {
    const document = buildVerificationOutputDocument(root);
    document.artifactEvidence[0].requiredFieldsPresent = false as true;

    const validation = validateVerificationOutputDocument(document);

    assert.equal(validation.valid, false);
    assert.match(validation.errors.join("\n"), /artifactEvidence\[\]\.requiredFieldsPresent/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function buildFixtureProject(): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-output-"));
  mkdirSync(join(root, "docs", "generated"), { recursive: true });
  mkdirSync(join(root, "tests", "fixtures"), { recursive: true });

  writeJson(join(root, "docs", "generated", "diagnosis-report.json"), {
    schemaVersion: "diagnosis-report-generation.v1",
    diagnosisReport: {
      schemaVersion: "diagnosis-report.v1",
      diagnosis: {
        decision: "partial_redesign",
        decisionEvidenceArtifact: "docs/review-evidence.json",
      },
    },
    reviewEvidence: {
      recommendation: "partial_redesign",
    },
  });
  writeJson(join(root, "docs", "generated", "requirement-gap-mapping.json"), {
    schemaVersion: "implementation-capabilities.v1",
    capabilities: [],
    summary: {
      implementedCount: 6,
      missingCount: 0,
      readmeRequirementCount: 19,
    },
  });
  writeJson(join(root, "docs", "generated", "dry-run-final-output.json"), {
    schemaVersion: "final-output-artifact.v1",
    command: "ai-agent dry-run",
    status: "finalized",
    requestAnalysis: {
      taskBreakdown: ["분석"],
    },
    meetingHistory: [{ id: "turn-001" }, { id: "turn-002" }],
    escalation: {
      required: false,
    },
    tokenStrategy: {
      targetReduction: "40-50%",
    },
  });
  writeJson(join(root, "docs", "generated", "meeting-loop-transcript.json"), {
    schemaVersion: "preserved-meeting-transcript.v1",
    preservedLoop: {
      executionTurnId: "turn-002:owner_draft",
      reviewTurnId: "turn-004:review",
    },
    retentionEvidence: {
      transcriptSummaryOnly: true,
    },
  });
  writeJson(join(root, "docs", "generated", "token-reduction-check-result.json"), {
    schemaVersion: "token-cost-control-check.v1",
    status: "passed",
    percentSavings: 74.3,
    targetThreshold: {
      percentSavings: 40,
    },
    pass: true,
  });
  writeJson(join(root, "docs", "generated", "typecheck-check-result.json"), {
    schemaVersion: "typecheck-proof-artifact.v1",
    command: "ai-agent check:typecheck",
    status: "passed",
    typecheck: {
      exitCode: 0,
    },
  });
  writeJson(join(root, "docs", "generated", "verification-workflow-result.json"), {
    schemaVersion: "verification-workflow-runner.v1",
    command: "ai-agent run-verification-workflow",
    status: "passed",
    deterministicInputs: {
      clearRequest: "브랜드 캠페인 제작 회의를 열고 OpenClaw 실행안과 Hermes 검토를 거쳐 최종안을 합성해줘.",
      ambiguousRequest: "대충 좋은 후보 여러 개를 추천만 해줘.",
      projectChannelId: "verification-parent-channel",
    },
    cases: [
      {
        name: "finalized_meeting_loop",
        status: "passed",
        observed: {
          status: "finalized",
          finalSynthesisCreated: true,
        },
        failures: [],
      },
      {
        name: "ambiguous_request_escalation",
        status: "passed",
        observed: {
          status: "waiting_for_user",
        },
        failures: [],
      },
    ],
    summary: {
      caseCount: 2,
      passedCaseCount: 2,
      failedCaseCount: 0,
      mvpWorkflowExecuted: true,
      escalationWorkflowExecuted: true,
      rawStorageSeparatedFromLoopContext: true,
    },
    errors: [],
  });
  writeJson(join(root, "tests", "fixtures", "dry-run-harness-fixtures.json"), [
    {
      name: "clear_request_from_file",
      invocation: "api",
      args: ["--request-file", "tests/fixtures/final-output-request.txt"],
      repetitions: 3,
      expected: {
        exitCode: 0,
        stream: "stdout",
        jsonStatus: "finalized",
      },
    },
    {
      name: "ambiguous_request_inline",
      invocation: "api",
      args: ["--request", "대충 좋은 후보 여러 개 추천만 해줘."],
      repetitions: 2,
      expected: {
        exitCode: 0,
        stream: "stdout",
        jsonStatus: "waiting_for_user",
      },
    },
    {
      name: "invalid_input_inline",
      invocation: "api",
      args: ["--request", "   "],
      repetitions: 2,
      expected: {
        exitCode: 2,
        stream: "stderr",
        jsonError: "invalid_input",
      },
    },
  ]);

  return root;
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}
