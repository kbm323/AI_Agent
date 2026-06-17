import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { deriveAcceptanceEvidenceFromArtifactEvidence } from "./acceptance-evidence.ts";

export type VerificationArtifactEvidenceId =
  | "diagnosis_report"
  | "requirement_gap_mapping"
  | "dry_run_final_output"
  | "meeting_loop_transcript"
  | "token_cost_control"
  | "typecheck_check"
  | "dry_run_fixture_harness"
  | "verification_workflow_runner";

export interface VerificationArtifactEvidence {
  id: VerificationArtifactEvidenceId;
  path: string;
  schemaVersion: string;
  requiredFieldsPresent: true;
  evidence: Record<string, boolean | number | string | string[]>;
}

export interface VerificationOutputDocument {
  schemaVersion: "verification-output.v1";
  command: "ai-agent check-verification-output";
  status: "passed";
  deterministic: true;
  artifactEvidence: VerificationArtifactEvidence[];
  acceptanceEvidence: {
    workflowRunnerPassed: true;
    mvpObservable: true;
    diagnosisComplete: true;
    invalidInputHandled: true;
    escalationHandled: true;
    tokenStrategyDefined: true;
  };
}

export interface VerificationOutputValidationResult {
  valid: boolean;
  schemaVersion: VerificationOutputDocument["schemaVersion"];
  checkedFields: string[];
  errors: string[];
}

export interface WriteVerificationOutputDocumentInput {
  projectRoot?: string;
  outputPath?: string;
  document: VerificationOutputDocument;
}

export interface WrittenVerificationOutputDocument {
  path: string;
  document: VerificationOutputDocument;
}

export interface VerificationOutputCheckResult {
  command: "ai-agent check-verification-output";
  status: "passed";
  schema: {
    schemaVersion: "verification-output.v1";
    schemaId: "ai-agent.verification-output.v1";
    requiredFields: string[];
  };
  artifact: {
    path: string;
    schemaVersion: "verification-output.v1";
    evidenceCount: number;
    validationValid: true;
  };
}

export interface VerificationOutputCheckResultValidationResult {
  valid: boolean;
  schemaVersion: "verification-output-check-result.v1";
  checkedFields: string[];
  errors: string[];
}

interface ArtifactEvidenceSpec {
  id: VerificationArtifactEvidenceId;
  path: string;
  schemaVersionPath?: string;
  syntheticSchemaVersion?: string;
  expectedSchemaVersion: string;
  requiredFields: string[];
  requiredEvidence?: Record<string, boolean | number | string>;
  evidence: Record<string, string>;
}

export const defaultVerificationOutputPath = "docs/generated/verification-output.json";

export const verificationOutputRequiredFields = [
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
] as const;

export const verificationOutputCheckResultRequiredFields = [
  "command",
  "status",
  "schema.schemaVersion",
  "schema.schemaId",
  "schema.requiredFields",
  "artifact.path",
  "artifact.schemaVersion",
  "artifact.evidenceCount",
  "artifact.validationValid",
] as const;

export const verificationOutputSchema = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: "ai-agent.verification-output.v1",
  title: "AI Agent verification output",
  type: "object",
  required: ["schemaVersion", "command", "status", "deterministic", "artifactEvidence", "acceptanceEvidence"],
  properties: {
    schemaVersion: { const: "verification-output.v1" },
    command: { const: "ai-agent check-verification-output" },
    status: { const: "passed" },
    deterministic: { const: true },
    artifactEvidence: { type: "array", minItems: 8 },
    acceptanceEvidence: {
      type: "object",
      required: [
        "workflowRunnerPassed",
        "mvpObservable",
        "diagnosisComplete",
        "invalidInputHandled",
        "escalationHandled",
        "tokenStrategyDefined",
      ],
    },
  },
} as const;

export const verificationOutputCheckResultSchema = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: "ai-agent.verification-output-check-result.v1",
  title: "AI Agent verification output check result",
  type: "object",
  required: ["command", "status", "schema", "artifact"],
  properties: {
    command: { const: "ai-agent check-verification-output" },
    status: { const: "passed" },
    schema: {
      type: "object",
      required: ["schemaVersion", "schemaId", "requiredFields"],
    },
    artifact: {
      type: "object",
      required: ["path", "schemaVersion", "evidenceCount", "validationValid"],
    },
  },
} as const;

export const verificationArtifactEvidenceSpecs: ArtifactEvidenceSpec[] = [
  {
    id: "diagnosis_report",
    path: "docs/generated/diagnosis-report.json",
    schemaVersionPath: "diagnosisReport.schemaVersion",
    expectedSchemaVersion: "diagnosis-report.v1",
    requiredFields: [
      "diagnosisReport.diagnosis.decision",
      "diagnosisReport.diagnosis.decisionEvidenceArtifact",
      "reviewEvidence.recommendation",
    ],
    requiredEvidence: {
      "diagnosisReport.diagnosis.decision": "partial_redesign",
      "reviewEvidence.recommendation": "partial_redesign",
    },
    evidence: {
      decision: "diagnosisReport.diagnosis.decision",
      decisionEvidenceArtifact: "diagnosisReport.diagnosis.decisionEvidenceArtifact",
      recommendation: "reviewEvidence.recommendation",
    },
  },
  {
    id: "requirement_gap_mapping",
    path: "docs/generated/requirement-gap-mapping.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "implementation-capabilities.v1",
    requiredFields: ["summary.implementedCount", "summary.missingCount", "summary.readmeRequirementCount", "capabilities"],
    requiredEvidence: {
      "summary.implementedCount": 6,
      "summary.missingCount": 0,
    },
    evidence: {
      implementedCount: "summary.implementedCount",
      missingCount: "summary.missingCount",
      readmeRequirementCount: "summary.readmeRequirementCount",
    },
  },
  {
    id: "dry_run_final_output",
    path: "docs/generated/dry-run-final-output.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "final-output-artifact.v1",
    requiredFields: ["command", "status", "requestAnalysis.taskBreakdown", "meetingHistory", "escalation.required", "tokenStrategy.targetReduction"],
    requiredEvidence: {
      command: "ai-agent dry-run",
      status: "finalized",
      "escalation.required": false,
    },
    evidence: {
      command: "command",
      status: "status",
      escalationRequired: "escalation.required",
      targetReduction: "tokenStrategy.targetReduction",
      meetingTurnCount: "meetingHistory.length",
    },
  },
  {
    id: "meeting_loop_transcript",
    path: "docs/generated/meeting-loop-transcript.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "preserved-meeting-transcript.v1",
    requiredFields: ["preservedLoop.executionTurnId", "preservedLoop.reviewTurnId", "retentionEvidence.transcriptSummaryOnly"],
    requiredEvidence: {
      "retentionEvidence.transcriptSummaryOnly": true,
    },
    evidence: {
      executionTurnId: "preservedLoop.executionTurnId",
      reviewTurnId: "preservedLoop.reviewTurnId",
      transcriptSummaryOnly: "retentionEvidence.transcriptSummaryOnly",
    },
  },
  {
    id: "token_cost_control",
    path: "docs/generated/token-reduction-check-result.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "token-cost-control-check.v1",
    requiredFields: ["status", "percentSavings", "targetThreshold.percentSavings", "pass"],
    requiredEvidence: {
      status: "passed",
      "targetThreshold.percentSavings": 40,
      pass: true,
    },
    evidence: {
      status: "status",
      percentSavings: "percentSavings",
      minimumTargetSavingsPercent: "targetThreshold.percentSavings",
      pass: "pass",
    },
  },
  {
    id: "typecheck_check",
    path: "docs/generated/typecheck-check-result.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "typecheck-proof-artifact.v1",
    requiredFields: ["command", "status", "typecheck.exitCode"],
    requiredEvidence: {
      command: "ai-agent check:typecheck",
      status: "passed",
      "typecheck.exitCode": 0,
    },
    evidence: {
      command: "command",
      status: "status",
      exitCode: "typecheck.exitCode",
    },
  },
  {
    id: "dry_run_fixture_harness",
    path: "tests/fixtures/dry-run-harness-fixtures.json",
    syntheticSchemaVersion: "dry-run-fixture-harness.v1",
    expectedSchemaVersion: "dry-run-fixture-harness.v1",
    requiredFields: ["0.expected.jsonStatus", "1.expected.jsonStatus", "2.expected.exitCode", "2.expected.jsonError"],
    requiredEvidence: {
      "0.expected.jsonStatus": "finalized",
      "1.expected.jsonStatus": "waiting_for_user",
      "2.expected.exitCode": 2,
      "2.expected.jsonError": "invalid_input",
    },
    evidence: {
      finalizedFixtureStatus: "0.expected.jsonStatus",
      escalationFixtureStatus: "1.expected.jsonStatus",
      invalidInputFixtureExitCode: "2.expected.exitCode",
      invalidInputFixtureError: "2.expected.jsonError",
    },
  },
  {
    id: "verification_workflow_runner",
    path: "docs/generated/verification-workflow-result.json",
    schemaVersionPath: "schemaVersion",
    expectedSchemaVersion: "verification-workflow-runner.v1",
    requiredFields: [
      "command",
      "status",
      "summary.mvpWorkflowExecuted",
      "summary.escalationWorkflowExecuted",
      "summary.rawStorageSeparatedFromLoopContext",
      "summary.passedCaseCount",
      "cases.length",
    ],
    requiredEvidence: {
      command: "ai-agent run-verification-workflow",
      status: "passed",
      "summary.mvpWorkflowExecuted": true,
      "summary.escalationWorkflowExecuted": true,
      "summary.rawStorageSeparatedFromLoopContext": true,
    },
    evidence: {
      command: "command",
      status: "status",
      mvpWorkflowExecuted: "summary.mvpWorkflowExecuted",
      escalationWorkflowExecuted: "summary.escalationWorkflowExecuted",
      rawStorageSeparatedFromLoopContext: "summary.rawStorageSeparatedFromLoopContext",
      passedCaseCount: "summary.passedCaseCount",
      caseCount: "cases.length",
    },
  },
];

export function buildVerificationOutputDocument(projectRoot = process.cwd()): VerificationOutputDocument {
  const artifactEvidence = verificationArtifactEvidenceSpecs.map((spec) => buildArtifactEvidence(projectRoot, spec));
  const acceptanceEvidence = deriveAcceptanceEvidenceFromArtifactEvidence(artifactEvidence);
  return {
    schemaVersion: "verification-output.v1",
    command: "ai-agent check-verification-output",
    status: "passed",
    deterministic: true,
    artifactEvidence,
    acceptanceEvidence,
  };
}

export function validateVerificationOutputDocument(document: unknown): VerificationOutputValidationResult {
  const errors: string[] = [];
  const value = asRecord(document);
  if (!value) {
    return {
      valid: false,
      schemaVersion: "verification-output.v1",
      checkedFields: [...verificationOutputRequiredFields],
      errors: ["document must be an object"],
    };
  }

  assertEqual(value.schemaVersion, "verification-output.v1", "schemaVersion", errors);
  assertEqual(value.command, "ai-agent check-verification-output", "command", errors);
  assertEqual(value.status, "passed", "status", errors);
  assertEqual(value.deterministic, true, "deterministic", errors);

  if (!Array.isArray(value.artifactEvidence)) {
    errors.push("artifactEvidence must be an array");
  } else {
    const expectedIds = verificationArtifactEvidenceSpecs.map((spec) => spec.id);
    const actualIds = value.artifactEvidence.map((entry) => asRecord(entry)?.id);
    if (JSON.stringify(actualIds) !== JSON.stringify(expectedIds)) {
      errors.push(`artifactEvidence ids must be ${expectedIds.join(", ")}`);
    }
    for (const entry of value.artifactEvidence) {
      validateArtifactEvidenceEntry(entry, errors);
    }
  }

  const acceptanceEvidence = asRecord(value.acceptanceEvidence);
  if (!acceptanceEvidence) {
    errors.push("acceptanceEvidence must be an object");
  } else {
    const acceptanceEvidenceKeys = [
      "workflowRunnerPassed",
      "mvpObservable",
      "diagnosisComplete",
      "invalidInputHandled",
      "escalationHandled",
      "tokenStrategyDefined",
    ];
    for (const key of Object.keys(acceptanceEvidence)) {
      if (!acceptanceEvidenceKeys.includes(key)) {
        errors.push(`acceptanceEvidence.${key} is not supported`);
      }
    }
    for (const key of acceptanceEvidenceKeys) {
      assertEqual(acceptanceEvidence[key], true, `acceptanceEvidence.${key}`, errors);
    }
    if (Array.isArray(value.artifactEvidence)) {
      try {
        const derivedAcceptanceEvidence = deriveAcceptanceEvidenceFromArtifactEvidence(
          value.artifactEvidence as VerificationArtifactEvidence[],
        );
        for (const [key, expected] of Object.entries(derivedAcceptanceEvidence)) {
          assertEqual(acceptanceEvidence[key], expected, `acceptanceEvidence.${key}`, errors);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "acceptanceEvidence could not be derived";
        errors.push(message);
      }
    }
  }

  for (const field of verificationOutputRequiredFields) {
    if (field.startsWith("artifactEvidence[].")) {
      const property = field.replace("artifactEvidence[].", "");
      if (
        !Array.isArray(value.artifactEvidence) ||
        !value.artifactEvidence.every((entry) => asRecord(entry)?.[property] !== undefined)
      ) {
        errors.push(`${field} must be present`);
      }
      continue;
    }
    if (readPath(value, field) === undefined) errors.push(`${field} must be present`);
  }

  return {
    valid: errors.length === 0,
    schemaVersion: "verification-output.v1",
    checkedFields: [...verificationOutputRequiredFields],
    errors,
  };
}

export function buildVerificationOutputCheckResult(input: {
  projectRoot?: string;
  document: VerificationOutputDocument;
}): VerificationOutputCheckResult {
  const projectRoot = input.projectRoot ?? process.cwd();
  const validation = validateVerificationOutputDocument(input.document);
  if (!validation.valid) {
    throw new Error(`verification output schema validation failed: ${validation.errors.join("; ")}`);
  }
  const written = writeVerificationOutputDocument({ projectRoot, document: input.document });

  return {
    command: "ai-agent check-verification-output",
    status: "passed",
    schema: {
      schemaVersion: "verification-output.v1",
      schemaId: verificationOutputSchema.$id,
      requiredFields: [...verificationOutputRequiredFields],
    },
    artifact: {
      path: written.path,
      schemaVersion: written.document.schemaVersion,
      evidenceCount: written.document.artifactEvidence.length,
      validationValid: true,
    },
  };
}

export function validateVerificationOutputCheckResult(result: unknown): VerificationOutputCheckResultValidationResult {
  const errors: string[] = [];
  const value = asRecord(result);
  if (!value) {
    return {
      valid: false,
      schemaVersion: "verification-output-check-result.v1",
      checkedFields: [...verificationOutputCheckResultRequiredFields],
      errors: ["result must be an object"],
    };
  }

  assertEqual(value.command, "ai-agent check-verification-output", "command", errors);
  assertEqual(value.status, "passed", "status", errors);

  const schema = asRecord(value.schema);
  if (!schema) {
    errors.push("schema must be an object");
  } else {
    assertEqual(schema.schemaVersion, "verification-output.v1", "schema.schemaVersion", errors);
    assertEqual(schema.schemaId, verificationOutputSchema.$id, "schema.schemaId", errors);
    if (JSON.stringify(schema.requiredFields) !== JSON.stringify([...verificationOutputRequiredFields])) {
      errors.push("schema.requiredFields must match verificationOutputRequiredFields");
    }
  }

  const artifact = asRecord(value.artifact);
  if (!artifact) {
    errors.push("artifact must be an object");
  } else {
    if (typeof artifact.path !== "string" || artifact.path.length === 0) {
      errors.push("artifact.path must be a non-empty string");
    }
    assertEqual(artifact.schemaVersion, "verification-output.v1", "artifact.schemaVersion", errors);
    if (typeof artifact.evidenceCount !== "number" || artifact.evidenceCount !== verificationArtifactEvidenceSpecs.length) {
      errors.push(`artifact.evidenceCount must equal ${verificationArtifactEvidenceSpecs.length}`);
    }
    assertEqual(artifact.validationValid, true, "artifact.validationValid", errors);
  }

  for (const field of verificationOutputCheckResultRequiredFields) {
    if (readPath(value, field) === undefined) errors.push(`${field} must be present`);
  }

  return {
    valid: errors.length === 0,
    schemaVersion: "verification-output-check-result.v1",
    checkedFields: [...verificationOutputCheckResultRequiredFields],
    errors,
  };
}

export function writeVerificationOutputDocument(input: WriteVerificationOutputDocumentInput): WrittenVerificationOutputDocument {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? defaultVerificationOutputPath;
  const resolvedPath = resolve(projectRoot, outputPath);
  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, `${JSON.stringify(input.document, null, 2)}\n`, "utf8");
  return { path: resolvedPath, document: input.document };
}

function buildArtifactEvidence(projectRoot: string, spec: ArtifactEvidenceSpec): VerificationArtifactEvidence {
  const absolutePath = resolve(projectRoot, spec.path);
  const payload = JSON.parse(readFileSync(absolutePath, "utf8"));
  const schemaVersion = spec.schemaVersionPath
    ? String(readPath(payload, spec.schemaVersionPath))
    : spec.syntheticSchemaVersion;
  if (schemaVersion === undefined) {
    throw new Error(`${spec.path} must define schemaVersion evidence`);
  }
  if (schemaVersion !== spec.expectedSchemaVersion) {
    throw new Error(`${spec.path} schemaVersion must be ${spec.expectedSchemaVersion}`);
  }
  for (const field of spec.requiredFields) {
    if (readPath(payload, field) === undefined) {
      throw new Error(`${spec.path} missing required evidence field ${field}`);
    }
  }
  for (const [field, expected] of Object.entries(spec.requiredEvidence ?? {})) {
    const actual = readPath(payload, field);
    if (actual !== expected) {
      throw new Error(`${spec.path} invalid completion evidence ${field}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
  }

  return {
    id: spec.id,
    path: absolutePath,
    schemaVersion,
    requiredFieldsPresent: true,
    evidence: Object.fromEntries(Object.entries(spec.evidence).map(([key, path]) => [key, readEvidenceValue(payload, path)])),
  };
}

function validateArtifactEvidenceEntry(entry: unknown, errors: string[]): void {
  const value = asRecord(entry);
  if (!value) {
    errors.push("artifactEvidence[] must be an object");
    return;
  }
  const spec = verificationArtifactEvidenceSpecs.find((artifactSpec) => artifactSpec.id === value.id);
  if (typeof value.id !== "string" || !verificationArtifactEvidenceSpecs.some((spec) => spec.id === value.id)) {
    errors.push("artifactEvidence[].id must be a known artifact evidence id");
  }
  if (typeof value.path !== "string" || value.path.length === 0) {
    errors.push("artifactEvidence[].path must be a non-empty string");
  }
  if (typeof value.schemaVersion !== "string" || value.schemaVersion.length === 0) {
    errors.push("artifactEvidence[].schemaVersion must be a non-empty string");
  } else if (spec && value.schemaVersion !== spec.expectedSchemaVersion) {
    errors.push(`artifactEvidence[${spec.id}].schemaVersion must equal ${JSON.stringify(spec.expectedSchemaVersion)}`);
  }
  assertEqual(value.requiredFieldsPresent, true, "artifactEvidence[].requiredFieldsPresent", errors);
  const evidence = asRecord(value.evidence);
  if (!evidence) {
    errors.push("artifactEvidence[].evidence must be an object");
  } else if (spec) {
    for (const key of Object.keys(spec.evidence)) {
      const evidenceValue = evidence[key];
      if (evidenceValue === undefined) {
        errors.push(`artifactEvidence[${spec.id}].evidence.${key} must be present`);
      } else if (!isEvidenceScalar(evidenceValue)) {
        errors.push(`artifactEvidence[${spec.id}].evidence.${key} must be a scalar evidence value`);
      }
    }
  }
}

function isEvidenceScalar(value: unknown): value is boolean | number | string | string[] {
  return (
    ["boolean", "number", "string"].includes(typeof value) ||
    (Array.isArray(value) && value.every((item) => typeof item === "string"))
  );
}

function readEvidenceValue(payload: unknown, path: string): boolean | number | string | string[] {
  const value = readPath(payload, path);
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value;
  if (["boolean", "number", "string"].includes(typeof value)) return value as boolean | number | string;
  throw new Error(`${path} must resolve to a scalar evidence value`);
}

function readPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (current === undefined || current === null) return undefined;
    if (key === "length" && Array.isArray(current)) return current.length;
    if (Array.isArray(current) && /^\d+$/.test(key)) return current[Number(key)];
    if (typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function assertEqual(actual: unknown, expected: unknown, field: string, errors: string[]): void {
  if (actual !== expected) errors.push(`${field} must equal ${JSON.stringify(expected)}`);
}
