import type { AgentRole, TurnKind } from "./types.ts";
import { implementationDecisionLabels, type ImplementationDecisionLabel } from "./evaluation.ts";

export type FinalOutputStatus = "finalized" | "waiting_for_user" | "failed";
export type FinalOutputDecision = "keep" | "partial_redesign" | "full_replan";

export interface FinalOutputMeetingTurn {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  summary: string;
  content?: never;
  fullContent?: never;
}

export interface FinalOutputPersonaOutput {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  summary: string;
}

export interface FinalOutputEscalation {
  required: boolean;
  reasons: string[];
  decisionContext: {
    status: FinalOutputStatus;
    trigger: "none" | "ambiguous_request" | "meeting_loop";
    preservedTurns: number;
    latestMeetingSummary: string | null;
    diagnosisDecision: FinalOutputDecision;
  };
  nextAction: {
    type: "continue" | "user_input_required";
    prompt: string;
    requestedFields: string[];
  };
  preservedContext: {
    rawStorage: string;
    exposedSummary: string;
    compressedContext: string;
  };
}

export interface FinalOutputTokenStrategy {
  rawStorage: string;
  exposedLoopContext: string;
  compressionPolicy: string;
  targetReduction: string;
}

export type FinalOutputDiagnosticSectionTitle =
  | "Prior Review Evidence"
  | "Keep Decision"
  | "Partial Redesign Decision"
  | "Full Redesign Decision";

export interface FinalOutputDiagnosticSection {
  title: FinalOutputDiagnosticSectionTitle;
  evidence: Record<string, unknown>;
}

export interface FinalOutputModelSettings {
  provider: string;
  model: string;
  temperature: number;
  maxOutputTokens: number;
}

export interface FinalOutputRunSettings {
  executionMode: "dry_run";
  orchestrator: {
    maxRounds: number;
    escalationPolicy: string;
  };
  models: {
    openclawOwner: FinalOutputModelSettings;
    hermesReviewer: FinalOutputModelSettings;
    openclawFinalizer: FinalOutputModelSettings;
  };
}

export interface FinalOutputVersionMetadata {
  schemaVersion: "run-version-metadata.v1";
  artifactSchemaVersion: FinalOutputArtifact["schemaVersion"];
  commandVersion: "ai-agent-dry-run.v1";
  implementationVersion: "multi-agent-meeting-mvp.v1";
  runtime: {
    name: "node";
    version: string;
  };
}

export interface FinalOutputArtifact {
  schemaVersion: "final-output-artifact.v1";
  command: "ai-agent dry-run";
  metadata: {
    executionId: string;
    inputIdentifier: string;
    inputSource: "default" | "inline" | "file";
    version: FinalOutputVersionMetadata;
    runSettings: FinalOutputRunSettings;
  };
  status: FinalOutputStatus;
  threadId: string;
  userRequest: string;
  diagnosis: {
    decision: FinalOutputDecision;
    decisionLabel: ImplementationDecisionLabel;
    basis: string;
    justification: unknown;
  };
  diagnosticOutput: {
    sections: FinalOutputDiagnosticSection[];
  };
  requestAnalysis: {
    taskBreakdown: string[];
    roleRoutes: string[];
    tokenStrategy: string;
  };
  openclawOutputs: FinalOutputPersonaOutput[];
  hermesReviews: FinalOutputPersonaOutput[];
  meetingHistory: FinalOutputMeetingTurn[];
  finalSynthesis?: string;
  escalation: FinalOutputEscalation;
  tokenStrategy: FinalOutputTokenStrategy;
}

export interface FinalOutputSchemaValidationResult {
  valid: boolean;
  schemaVersion: FinalOutputArtifact["schemaVersion"];
  checkedFields: string[];
  errors: string[];
}

export const finalOutputArtifactSchema = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: "ai-agent.final-output-artifact.v1",
  title: "AI Agent final output artifact",
  type: "object",
  required: [
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
  ],
  properties: {
    schemaVersion: { const: "final-output-artifact.v1" },
    command: { const: "ai-agent dry-run" },
    metadata: {
      type: "object",
      required: ["executionId", "inputIdentifier", "inputSource", "version", "runSettings"],
    },
    status: { enum: ["finalized", "waiting_for_user", "failed"] },
    threadId: { type: "string", minLength: 1 },
    userRequest: { type: "string", minLength: 1 },
    diagnosis: {
      type: "object",
      required: ["decision", "decisionLabel", "basis", "justification"],
    },
    diagnosticOutput: {
      type: "object",
      required: ["sections"],
    },
    requestAnalysis: {
      type: "object",
      required: ["taskBreakdown", "roleRoutes", "tokenStrategy"],
    },
    openclawOutputs: { type: "array" },
    hermesReviews: { type: "array" },
    meetingHistory: { type: "array" },
    finalSynthesis: { type: "string" },
    escalation: {
      type: "object",
      required: ["required", "reasons", "decisionContext", "nextAction", "preservedContext"],
    },
    tokenStrategy: {
      type: "object",
      required: ["rawStorage", "exposedLoopContext", "compressionPolicy", "targetReduction"],
    },
  },
} as const;

export const finalOutputRequiredFields = [
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
] as const;

export function validateFinalOutputArtifact(artifact: unknown): FinalOutputSchemaValidationResult {
  const errors: string[] = [];
  const value = asRecord(artifact);
  if (!value) {
    return {
      valid: false,
      schemaVersion: "final-output-artifact.v1",
      checkedFields: [...finalOutputRequiredFields],
      errors: ["artifact must be an object"],
    };
  }

  for (const field of finalOutputRequiredFields) {
    if (readPath(value, field) === undefined) {
      errors.push(`${field} is required`);
    }
  }

  expectLiteral(value.schemaVersion, "final-output-artifact.v1", "schemaVersion", errors);
  expectLiteral(value.command, "ai-agent dry-run", "command", errors);
  expectNonEmptyString(readPath(value, "metadata.executionId"), "metadata.executionId", errors);
  expectNonEmptyString(readPath(value, "metadata.inputIdentifier"), "metadata.inputIdentifier", errors);
  expectOneOf(readPath(value, "metadata.inputSource"), ["default", "inline", "file"], "metadata.inputSource", errors);
  validateVersionMetadata(readPath(value, "metadata.version"), errors);
  validateRunSettings(readPath(value, "metadata.runSettings"), errors);
  expectOneOf(value.status, ["finalized", "waiting_for_user", "failed"], "status", errors);
  expectNonEmptyString(value.threadId, "threadId", errors);
  expectNonEmptyString(value.userRequest, "userRequest", errors);
  expectOneOf(readPath(value, "diagnosis.decision"), ["keep", "partial_redesign", "full_replan"], "diagnosis.decision", errors);
  expectOneOf(
    readPath(value, "diagnosis.decisionLabel"),
    [...implementationDecisionLabels],
    "diagnosis.decisionLabel",
    errors,
  );
  validateDiagnosticOutput(readPath(value, "diagnosticOutput"), errors);
  expectArray(readPath(value, "requestAnalysis.taskBreakdown"), "requestAnalysis.taskBreakdown", errors);
  expectArray(readPath(value, "requestAnalysis.roleRoutes"), "requestAnalysis.roleRoutes", errors);
  validatePersonaOutputs(value.openclawOutputs, "openclawOutputs", errors);
  validatePersonaOutputs(value.hermesReviews, "hermesReviews", errors);
  validateMeetingHistory(value.meetingHistory, errors);
  validateEscalation(value, errors);
  validateTokenStrategy(readPath(value, "tokenStrategy"), errors);

  if (value.status === "finalized") {
    expectNonEmptyString(value.finalSynthesis, "finalSynthesis", errors);
    if (Array.isArray(value.openclawOutputs) && value.openclawOutputs.length === 0) {
      errors.push("finalized output requires at least one OpenClaw output");
    }
    if (Array.isArray(value.hermesReviews) && value.hermesReviews.length === 0) {
      errors.push("finalized output requires at least one Hermes review");
    }
  }

  return {
    valid: errors.length === 0,
    schemaVersion: "final-output-artifact.v1",
    checkedFields: [...finalOutputRequiredFields],
    errors,
  };
}

function validateDiagnosticOutput(value: unknown, errors: string[]): void {
  const record = asRecord(value);
  if (!record) {
    errors.push("diagnosticOutput must be an object");
    return;
  }
  expectArray(record.sections, "diagnosticOutput.sections", errors);
  if (!Array.isArray(record.sections)) return;
  for (const title of ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"]) {
    if (!record.sections.some((section) => asRecord(section)?.title === title)) {
      errors.push(`diagnosticOutput.sections must include ${title}`);
    }
  }
  const priorReviewSection = record.sections.find((section) => asRecord(section)?.title === "Prior Review Evidence");
  if (!priorReviewSection) {
    errors.push("diagnosticOutput.sections must include Prior Review Evidence");
    return;
  }
  const evidence = readPath(asRecord(priorReviewSection) ?? {}, "evidence");
  const evidenceRecord = asRecord(evidence);
  if (!evidenceRecord) {
    errors.push("diagnosticOutput.sections Prior Review Evidence evidence must be an object");
    return;
  }
  expectNonEmptyString(evidenceRecord.artifactPath, "diagnosticOutput.sections Prior Review Evidence artifactPath", errors);
  if (typeof evidenceRecord.validationValid !== "boolean") {
    errors.push("diagnosticOutput.sections Prior Review Evidence validationValid must be boolean");
  }
  if (typeof evidenceRecord.completenessComplete !== "boolean") {
    errors.push("diagnosticOutput.sections Prior Review Evidence completenessComplete must be boolean");
  }
  if (typeof evidenceRecord.decisionGateAccepted !== "boolean") {
    errors.push("diagnosticOutput.sections Prior Review Evidence decisionGateAccepted must be boolean");
  }
}

function validatePersonaOutputs(value: unknown, label: string, errors: string[]): void {
  if (!Array.isArray(value)) {
    errors.push(`${label} must be an array`);
    return;
  }
  value.forEach((item, index) => {
    const record = asRecord(item);
    if (!record) {
      errors.push(`${label}[${index}] must be an object`);
      return;
    }
    expectInteger(record.round, `${label}[${index}].round`, errors);
    expectNonEmptyString(record.role, `${label}[${index}].role`, errors);
    expectNonEmptyString(record.kind, `${label}[${index}].kind`, errors);
    expectNonEmptyString(record.summary, `${label}[${index}].summary`, errors);
  });
}

function validateMeetingHistory(value: unknown, errors: string[]): void {
  validatePersonaOutputs(value, "meetingHistory", errors);
  if (!Array.isArray(value)) return;
  value.forEach((turn, index) => {
    const record = asRecord(turn);
    if (record && ("content" in record || "fullContent" in record)) {
      errors.push(`meetingHistory[${index}] must expose summaries only`);
    }
  });
}

function validateEscalation(value: Record<string, unknown>, errors: string[]): void {
  const required = readPath(value, "escalation.required");
  if (typeof required !== "boolean") errors.push("escalation.required must be boolean");
  expectArray(readPath(value, "escalation.reasons"), "escalation.reasons", errors);
  expectInteger(readPath(value, "escalation.decisionContext.preservedTurns"), "escalation.decisionContext.preservedTurns", errors);
  expectOneOf(
    readPath(value, "escalation.nextAction.type"),
    ["continue", "user_input_required"],
    "escalation.nextAction.type",
    errors,
  );
  expectArray(readPath(value, "escalation.nextAction.requestedFields"), "escalation.nextAction.requestedFields", errors);
  expectNonEmptyString(readPath(value, "escalation.preservedContext.rawStorage"), "escalation.preservedContext.rawStorage", errors);
  expectNonEmptyString(readPath(value, "escalation.preservedContext.exposedSummary"), "escalation.preservedContext.exposedSummary", errors);
  expectNonEmptyString(readPath(value, "escalation.preservedContext.compressedContext"), "escalation.preservedContext.compressedContext", errors);
}

function validateTokenStrategy(value: unknown, errors: string[]): void {
  const record = asRecord(value);
  if (!record) {
    errors.push("tokenStrategy must be an object");
    return;
  }
  expectNonEmptyString(record.rawStorage, "tokenStrategy.rawStorage", errors);
  expectNonEmptyString(record.exposedLoopContext, "tokenStrategy.exposedLoopContext", errors);
  expectNonEmptyString(record.compressionPolicy, "tokenStrategy.compressionPolicy", errors);
  expectNonEmptyString(record.targetReduction, "tokenStrategy.targetReduction", errors);
  if (typeof record.targetReduction === "string" && !/40-50%/.test(record.targetReduction)) {
    errors.push("tokenStrategy.targetReduction must include the 40-50% reduction target");
  }
}

function validateVersionMetadata(value: unknown, errors: string[]): void {
  const record = asRecord(value);
  if (!record) {
    errors.push("metadata.version must be an object");
    return;
  }
  expectLiteral(record.schemaVersion, "run-version-metadata.v1", "metadata.version.schemaVersion", errors);
  expectLiteral(record.artifactSchemaVersion, "final-output-artifact.v1", "metadata.version.artifactSchemaVersion", errors);
  expectLiteral(record.commandVersion, "ai-agent-dry-run.v1", "metadata.version.commandVersion", errors);
  expectLiteral(record.implementationVersion, "multi-agent-meeting-mvp.v1", "metadata.version.implementationVersion", errors);
  expectLiteral(readPath(record, "runtime.name"), "node", "metadata.version.runtime.name", errors);
  expectNonEmptyString(readPath(record, "runtime.version"), "metadata.version.runtime.version", errors);
}

function validateRunSettings(value: unknown, errors: string[]): void {
  const record = asRecord(value);
  if (!record) {
    errors.push("metadata.runSettings must be an object");
    return;
  }
  expectLiteral(record.executionMode, "dry_run", "metadata.runSettings.executionMode", errors);
  expectInteger(readPath(record, "orchestrator.maxRounds"), "metadata.runSettings.orchestrator.maxRounds", errors);
  expectNonEmptyString(readPath(record, "orchestrator.escalationPolicy"), "metadata.runSettings.orchestrator.escalationPolicy", errors);
  validateModelSettings(readPath(record, "models.openclawOwner"), "metadata.runSettings.models.openclawOwner", errors);
  validateModelSettings(readPath(record, "models.hermesReviewer"), "metadata.runSettings.models.hermesReviewer", errors);
  validateModelSettings(readPath(record, "models.openclawFinalizer"), "metadata.runSettings.models.openclawFinalizer", errors);
}

function validateModelSettings(value: unknown, label: string, errors: string[]): void {
  const record = asRecord(value);
  if (!record) {
    errors.push(`${label} must be an object`);
    return;
  }
  expectNonEmptyString(record.provider, `${label}.provider`, errors);
  expectNonEmptyString(record.model, `${label}.model`, errors);
  expectNumber(record.temperature, `${label}.temperature`, errors);
  expectInteger(record.maxOutputTokens, `${label}.maxOutputTokens`, errors);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function readPath(value: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => (asRecord(current)?.[key]), value);
}

function expectLiteral(value: unknown, expected: string, label: string, errors: string[]): void {
  if (value !== expected) errors.push(`${label} must be ${expected}`);
}

function expectOneOf(value: unknown, expected: readonly string[], label: string, errors: string[]): void {
  if (typeof value !== "string" || !expected.includes(value)) {
    errors.push(`${label} must be one of ${expected.join(", ")}`);
  }
}

function expectNonEmptyString(value: unknown, label: string, errors: string[]): void {
  if (typeof value !== "string" || value.trim().length === 0) {
    errors.push(`${label} must be a non-empty string`);
  }
}

function expectArray(value: unknown, label: string, errors: string[]): void {
  if (!Array.isArray(value)) errors.push(`${label} must be an array`);
}

function expectInteger(value: unknown, label: string, errors: string[]): void {
  if (!Number.isInteger(value)) errors.push(`${label} must be an integer`);
}

function expectNumber(value: unknown, label: string, errors: string[]): void {
  if (typeof value !== "number" || !Number.isFinite(value)) errors.push(`${label} must be a finite number`);
}
