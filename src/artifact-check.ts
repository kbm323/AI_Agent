import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export type ArtifactCheckStatus = "passed" | "failed";

export type RequiredDiagnosticArtifactId =
  | "diagnosis_report_markdown"
  | "refactoring_plan_markdown"
  | "token_reduction_strategy_markdown"
  | "context_storage_boundary_markdown"
  | "loop_context_compression_policy_markdown"
  | "review_evidence_json"
  | "generated_diagnosis_report_json"
  | "requirement_gap_mapping_json"
  | "inventory_orchestration_report_json";

export interface ArtifactSectionRequirement {
  id: string;
  pattern: RegExp;
}

export interface ArtifactFieldRequirement {
  path: string;
  expected?: string | number | boolean;
}

export interface RequiredDiagnosticArtifactSpec {
  id: RequiredDiagnosticArtifactId;
  path: string;
  sections: ArtifactSectionRequirement[];
  fields: ArtifactFieldRequirement[];
}

export interface DiagnosticArtifactCheckDetail {
  id: RequiredDiagnosticArtifactId;
  path: string;
  present: boolean;
  validJson: boolean;
  requiredSections: string[];
  missingSections: string[];
  requiredFields: string[];
  missingFields: string[];
  mismatchedFields: string[];
}

export interface DiagnosticArtifactCheckResult {
  command: "ai-agent check-artifacts";
  schemaVersion: "diagnostic-artifact-check.v1";
  status: ArtifactCheckStatus;
  deterministic: true;
  summary: {
    requiredArtifactCount: number;
    presentArtifactCount: number;
    completeArtifactCount: number;
    missingArtifactIds: RequiredDiagnosticArtifactId[];
    incompleteArtifactIds: RequiredDiagnosticArtifactId[];
  };
  artifacts: DiagnosticArtifactCheckDetail[];
}

export const requiredDiagnosticArtifacts: RequiredDiagnosticArtifactSpec[] = [
  {
    id: "diagnosis_report_markdown",
    path: "docs/diagnosis-report.md",
    sections: [
      { id: "scope", pattern: /^## Scope$/m },
      { id: "prior-review-artifact", pattern: /^## Prior Review Artifact$/m },
      { id: "decision", pattern: /^## Decision$/m },
      { id: "priority-assessment", pattern: /^## Priority Assessment$/m },
      { id: "requirement-to-gap-mapping", pattern: /^## Requirement-to-Gap Mapping$/m },
      { id: "token-strategy", pattern: /^## Token Strategy$/m },
    ],
    fields: [],
  },
  {
    id: "refactoring_plan_markdown",
    path: "docs/refactoring-plan.md",
    sections: [
      { id: "evaluation-priority", pattern: /^## Evaluation Priority$/m },
      { id: "mvp-coverage", pattern: /^## MVP Coverage$/m },
      { id: "phase-1", pattern: /^## Phase 1: Stabilize MVP Surface$/m },
      { id: "phase-2", pattern: /^## Phase 2: Separate Planning From Orchestration$/m },
      { id: "phase-3", pattern: /^## Phase 3: Preserve Full Text, Expose Summaries$/m },
      { id: "phase-4", pattern: /^## Phase 4: Convergence and Escalation Rules$/m },
      { id: "phase-5", pattern: /^## Phase 5: Verification Hardening$/m },
      { id: "phase-6", pattern: /^## Phase 6: Later Non-MVP Work$/m },
    ],
    fields: [],
  },
  {
    id: "token_reduction_strategy_markdown",
    path: "docs/token-reduction-strategy.md",
    sections: [
      { id: "savings-target", pattern: /^## 40-50% Savings Target$/m },
      { id: "original-text-retention-policy", pattern: /^## Original Text Retention Policy$/m },
      { id: "exposed-context-summary-separation", pattern: /^## Exposed Context Summary Separation$/m },
      { id: "compressed-context-approach", pattern: /^## Compressed Context Approach$/m },
      { id: "baseline-measurement", pattern: /^## Baseline Measurement$/m },
      { id: "validation-sections", pattern: /^## Validation Sections$/m },
    ],
    fields: [],
  },
  {
    id: "context_storage_boundary_markdown",
    path: "docs/context-storage-boundary.md",
    sections: [
      { id: "source-of-truth", pattern: /^## Source Of Truth$/m },
      { id: "loop-visible-fields", pattern: /^## Loop Visible Fields$/m },
      { id: "audit-only-fields", pattern: /^## Audit Only Fields$/m },
      { id: "invariants", pattern: /^## Invariants$/m },
      { id: "verification-checks", pattern: /^## Verification Checks$/m },
    ],
    fields: [],
  },
  {
    id: "loop_context_compression_policy_markdown",
    path: "docs/loop-context-compression-policy.md",
    sections: [
      { id: "retained-fields", pattern: /^## Retained Fields$/m },
      { id: "summarized-fields", pattern: /^## Summarized Fields$/m },
      { id: "dropped-fields", pattern: /^## Dropped Fields$/m },
      { id: "iteration-boundaries", pattern: /^## Iteration Boundaries$/m },
      { id: "deterministic-ordering", pattern: /^## Deterministic Ordering$/m },
    ],
    fields: [],
  },
  {
    id: "review_evidence_json",
    path: "docs/review-evidence.json",
    sections: [],
    fields: [
      { path: "schemaVersion", expected: "review-evidence.v1" },
      { path: "inventory.length" },
      { path: "findings.length" },
      { path: "summary.inspectedModules" },
      { path: "summary.findingCount" },
      { path: "summary.recommendation", expected: "partial_redesign" },
    ],
  },
  {
    id: "generated_diagnosis_report_json",
    path: "docs/generated/diagnosis-report.json",
    sections: [],
    fields: [
      { path: "schemaVersion", expected: "diagnosis-report-generation.v1" },
      { path: "diagnosisReport.schemaVersion", expected: "diagnosis-report.v1" },
      { path: "diagnosisReport.diagnosis.decision", expected: "partial_redesign" },
      { path: "diagnosisReport.requirementToGapMappingArtifact.summary" },
      { path: "reviewEvidence.recommendation", expected: "partial_redesign" },
      { path: "decisionGate.accepted", expected: true },
    ],
  },
  {
    id: "requirement_gap_mapping_json",
    path: "docs/generated/requirement-gap-mapping.json",
    sections: [],
    fields: [
      { path: "schemaVersion", expected: "implementation-capabilities.v1" },
      { path: "capabilities.length" },
      { path: "readmeRequirementMappings.length" },
      { path: "summary.implementedCount" },
      { path: "summary.missingCount" },
      { path: "summary.readmeRequirementCount" },
    ],
  },
  {
    id: "inventory_orchestration_report_json",
    path: "docs/generated/inventory-orchestration-report.json",
    sections: [],
    fields: [
      { path: "schemaVersion", expected: "inventory-orchestration.v1" },
      { path: "sourceFiles.length" },
      { path: "runnableEntryPoints.length" },
      { path: "testFiles.length" },
      { path: "configFiles.length" },
      { path: "moduleFeatureSummary.schemaVersion" },
      { path: "summary.sourceFileCount" },
    ],
  },
];

export function checkDiagnosticArtifacts(projectRoot = process.cwd()): DiagnosticArtifactCheckResult {
  const artifacts = requiredDiagnosticArtifacts.map((spec) => checkArtifact(projectRoot, spec));
  const missingArtifactIds = artifacts.filter((artifact) => !artifact.present).map((artifact) => artifact.id);
  const incompleteArtifactIds = artifacts
    .filter((artifact) => artifact.present && !isCompleteArtifact(artifact))
    .map((artifact) => artifact.id);

  return {
    command: "ai-agent check-artifacts",
    schemaVersion: "diagnostic-artifact-check.v1",
    status: missingArtifactIds.length === 0 && incompleteArtifactIds.length === 0 ? "passed" : "failed",
    deterministic: true,
    summary: {
      requiredArtifactCount: artifacts.length,
      presentArtifactCount: artifacts.filter((artifact) => artifact.present).length,
      completeArtifactCount: artifacts.filter(isCompleteArtifact).length,
      missingArtifactIds,
      incompleteArtifactIds,
    },
    artifacts,
  };
}

function checkArtifact(projectRoot: string, spec: RequiredDiagnosticArtifactSpec): DiagnosticArtifactCheckDetail {
  const absolutePath = resolve(projectRoot, spec.path);
  const detail: DiagnosticArtifactCheckDetail = {
    id: spec.id,
    path: absolutePath,
    present: existsSync(absolutePath),
    validJson: true,
    requiredSections: spec.sections.map((section) => section.id),
    missingSections: [],
    requiredFields: spec.fields.map((field) => field.path),
    missingFields: [],
    mismatchedFields: [],
  };

  if (!detail.present) return detail;

  const content = readFileSync(absolutePath, "utf8");
  for (const section of spec.sections) {
    if (!section.pattern.test(content)) detail.missingSections.push(section.id);
  }

  if (spec.fields.length === 0) return detail;

  let payload: unknown;
  try {
    payload = JSON.parse(content);
  } catch {
    detail.validJson = false;
    detail.missingFields.push(...detail.requiredFields);
    return detail;
  }

  for (const field of spec.fields) {
    const actual = readPath(payload, field.path);
    if (actual === undefined) {
      detail.missingFields.push(field.path);
    } else if ("expected" in field && actual !== field.expected) {
      detail.mismatchedFields.push(field.path);
    }
  }

  return detail;
}

function isCompleteArtifact(artifact: DiagnosticArtifactCheckDetail): boolean {
  return (
    artifact.present &&
    artifact.validJson &&
    artifact.missingSections.length === 0 &&
    artifact.missingFields.length === 0 &&
    artifact.mismatchedFields.length === 0
  );
}

function readPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (current === undefined || current === null) return undefined;
    if (key === "length") return Array.isArray(current) || typeof current === "string" ? current.length : undefined;
    if (Array.isArray(current) && /^\d+$/.test(key)) return current[Number(key)];
    if (typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}
