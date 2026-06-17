import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export type ReviewArtifactCompletenessStatus = "passed" | "failed";

export type RequiredReviewArtifactId =
  | "diagnosis_report_markdown"
  | "refactoring_plan_markdown"
  | "review_evidence"
  | "generated_diagnosis_report"
  | "requirement_gap_mapping"
  | "inventory_orchestration_report";

export interface ReviewArtifactCompletenessDetail {
  id: RequiredReviewArtifactId;
  path: string;
  present: boolean;
  schemaVersion?: string;
  requiredFields: string[];
  missingFields: string[];
  requiredContent: string[];
  missingContent: string[];
  validJson: boolean;
}

export interface ReviewArtifactCompletenessResult {
  command: "ai-agent check-review-artifact-completeness";
  status: ReviewArtifactCompletenessStatus;
  deterministic: true;
  schemaVersion: "review-artifact-completeness.v1";
  summary: {
    requiredArtifactCount: number;
    presentArtifactCount: number;
    completeArtifactCount: number;
    missingArtifactIds: RequiredReviewArtifactId[];
    incompleteArtifactIds: RequiredReviewArtifactId[];
  };
  artifacts: ReviewArtifactCompletenessDetail[];
}

interface ReviewArtifactSpec {
  id: RequiredReviewArtifactId;
  path: string;
  expectedSchemaVersion?: string;
  schemaVersionPath?: string;
  requiredFields: string[];
  requiredContent: Array<{
    id: string;
    pattern: RegExp;
  }>;
}

export const requiredCompletedReviewArtifacts: ReviewArtifactSpec[] = [
  {
    id: "diagnosis_report_markdown",
    path: "docs/diagnosis-report.md",
    requiredFields: [],
    requiredContent: [
      { id: "prior-review-artifact-section", pattern: /## Prior Review Artifact/i },
      { id: "decision-section", pattern: /## Decision/i },
      { id: "priority-assessment-section", pattern: /## Priority Assessment/i },
      { id: "requirement-gap-mapping-section", pattern: /## Requirement-to-Gap Mapping/i },
      { id: "token-strategy-section", pattern: /## Token Strategy/i },
      { id: "decision-evidence-artifact", pattern: /Decision evidence artifact:\s*`docs\/review-evidence\.json`/i },
      { id: "partial-redesign-recommendation", pattern: /Recommendation:\s*\*\*partial redesign\*\*/i },
      {
        id: "seed-priority-order",
        pattern: /Error frequency[\s\S]*Maintenance difficulty[\s\S]*Token cost[\s\S]*Architecture fit[\s\S]*Feature completeness/i,
      },
    ],
  },
  {
    id: "refactoring_plan_markdown",
    path: "docs/refactoring-plan.md",
    requiredFields: [],
    requiredContent: [
      { id: "decision-basis", pattern: /Decision basis:\s*`docs\/review-evidence\.json`/i },
      { id: "phase-1", pattern: /## Phase 1: Stabilize MVP Surface/i },
      { id: "phase-2", pattern: /## Phase 2: Separate Planning From Orchestration/i },
      { id: "phase-3", pattern: /## Phase 3: Preserve Full Text, Expose Summaries/i },
      { id: "phase-4", pattern: /## Phase 4: Convergence and Escalation Rules/i },
      { id: "phase-5", pattern: /## Phase 5: Verification Hardening/i },
      { id: "phase-6", pattern: /## Phase 6: Later Non-MVP Work/i },
      { id: "implemented-status", pattern: /Status:\s*implemented/i },
      { id: "compressed-context", pattern: /compressed context/i },
    ],
  },
  {
    id: "review_evidence",
    path: "docs/review-evidence.json",
    expectedSchemaVersion: "review-evidence.v1",
    schemaVersionPath: "schemaVersion",
    requiredFields: [
      "schemaVersion",
      "inventory",
      "findings",
      "findings.0.evidence",
      "findings.0.recommendation",
      "summary.inspectedModules",
      "summary.findingCount",
      "summary.findingsBySeverity",
      "summary.findingsByCategory",
      "summary.recommendation",
    ],
    requiredContent: [],
  },
  {
    id: "generated_diagnosis_report",
    path: "docs/generated/diagnosis-report.json",
    expectedSchemaVersion: "diagnosis-report-generation.v1",
    schemaVersionPath: "schemaVersion",
    requiredFields: [
      "schemaVersion",
      "diagnosisReport.schemaVersion",
      "diagnosisReport.diagnosis.decision",
      "diagnosisReport.diagnosis.decisionEvidenceArtifact",
      "diagnosisReport.requirementToGapMappingArtifact.summary",
      "reviewEvidence.schemaVersion",
      "reviewEvidence.recommendation",
      "inventoryOrchestration.schemaVersion",
      "decisionGate.accepted",
    ],
    requiredContent: [],
  },
  {
    id: "requirement_gap_mapping",
    path: "docs/generated/requirement-gap-mapping.json",
    expectedSchemaVersion: "implementation-capabilities.v1",
    schemaVersionPath: "schemaVersion",
    requiredFields: [
      "schemaVersion",
      "capabilities",
      "readmeRequirementMappings",
      "summary.implementedCount",
      "summary.missingCount",
      "summary.readmeRequirementCount",
    ],
    requiredContent: [],
  },
  {
    id: "inventory_orchestration_report",
    path: "docs/generated/inventory-orchestration-report.json",
    expectedSchemaVersion: "inventory-orchestration.v1",
    schemaVersionPath: "schemaVersion",
    requiredFields: [
      "schemaVersion",
      "sourceFiles",
      "runnableEntryPoints",
      "testFiles",
      "configFiles",
      "moduleFeatureSummary.schemaVersion",
      "summary.sourceFileCount",
      "summary.runnableEntryPointCount",
      "summary.testFileCount",
      "summary.configFileCount",
    ],
    requiredContent: [],
  },
];

export function validateCompletedReviewArtifacts(
  projectRoot = process.cwd(),
  pathOverrides: Partial<Record<RequiredReviewArtifactId, string>> = {},
): ReviewArtifactCompletenessResult {
  const artifacts = requiredCompletedReviewArtifacts.map((spec) =>
    validateReviewArtifact(projectRoot, { ...spec, path: pathOverrides[spec.id] ?? spec.path })
  );
  const missingArtifactIds = artifacts.filter((artifact) => !artifact.present).map((artifact) => artifact.id);
  const incompleteArtifactIds = artifacts
    .filter((artifact) => artifact.present && !artifactIsComplete(artifact))
    .map((artifact) => artifact.id);

  return {
    command: "ai-agent check-review-artifact-completeness",
    status: missingArtifactIds.length === 0 && incompleteArtifactIds.length === 0 ? "passed" : "failed",
    deterministic: true,
    schemaVersion: "review-artifact-completeness.v1",
    summary: {
      requiredArtifactCount: artifacts.length,
      presentArtifactCount: artifacts.filter((artifact) => artifact.present).length,
      completeArtifactCount: artifacts.filter(artifactIsComplete).length,
      missingArtifactIds,
      incompleteArtifactIds,
    },
    artifacts,
  };
}

function validateReviewArtifact(projectRoot: string, spec: ReviewArtifactSpec): ReviewArtifactCompletenessDetail {
  const absolutePath = resolve(projectRoot, spec.path);
  const detail: ReviewArtifactCompletenessDetail = {
    id: spec.id,
    path: absolutePath,
    present: existsSync(absolutePath),
    requiredFields: [...spec.requiredFields],
    missingFields: [],
    requiredContent: spec.requiredContent.map((entry) => entry.id),
    missingContent: [],
    validJson: true,
  };

  if (!detail.present) {
    return detail;
  }

  const content = readFileSync(absolutePath, "utf8");
  for (const requirement of spec.requiredContent) {
    if (!requirement.pattern.test(content)) {
      detail.missingContent.push(requirement.id);
    }
  }

  if (spec.expectedSchemaVersion || spec.requiredFields.length > 0) {
    let payload: unknown;
    try {
      payload = JSON.parse(content);
    } catch {
      detail.validJson = false;
      detail.missingFields.push(...spec.requiredFields);
      return detail;
    }

    if (spec.schemaVersionPath) {
      const schemaVersion = readPath(payload, spec.schemaVersionPath);
      if (typeof schemaVersion === "string") {
        detail.schemaVersion = schemaVersion;
      }
      if (schemaVersion !== spec.expectedSchemaVersion) {
        detail.missingFields.push(spec.schemaVersionPath);
      }
    }

    for (const field of spec.requiredFields) {
      if (readPath(payload, field) === undefined) {
        detail.missingFields.push(field);
      }
    }
  }

  return detail;
}

function artifactIsComplete(artifact: ReviewArtifactCompletenessDetail): boolean {
  return artifact.present && artifact.validJson && artifact.missingFields.length === 0 && artifact.missingContent.length === 0;
}

function readPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (current === undefined || current === null) return undefined;
    if (Array.isArray(current) && /^\d+$/.test(key)) return current[Number(key)];
    if (typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}
