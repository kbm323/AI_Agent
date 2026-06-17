import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeCheckReviewArtifactCompletenessCommand } from "../scripts/check-review-artifact-completeness.ts";
import {
  requiredCompletedReviewArtifacts,
  validateCompletedReviewArtifacts,
} from "../src/review-artifact-completeness.ts";

test("completed review artifact validator passes with required concrete artifacts", () => {
  const root = buildCompleteReviewArtifactFixture();
  try {
    const result = validateCompletedReviewArtifacts(root);

    assert.equal(result.status, "passed");
    assert.equal(result.deterministic, true);
    assert.deepEqual(result.summary, {
      requiredArtifactCount: requiredCompletedReviewArtifacts.length,
      presentArtifactCount: requiredCompletedReviewArtifacts.length,
      completeArtifactCount: requiredCompletedReviewArtifacts.length,
      missingArtifactIds: [],
      incompleteArtifactIds: [],
    });
    assert.equal(result.artifacts.every((artifact) => artifact.present), true);
    assert.equal(result.artifacts.every((artifact) => artifact.missingFields.length === 0), true);
    assert.equal(result.artifacts.every((artifact) => artifact.missingContent.length === 0), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("completed review artifact validator reports missing artifact details", () => {
  const root = buildCompleteReviewArtifactFixture();
  try {
    rmSync(join(root, "docs", "refactoring-plan.md"), { force: true });

    const result = validateCompletedReviewArtifacts(root);
    const detail = result.artifacts.find((artifact) => artifact.id === "refactoring_plan_markdown");

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.missingArtifactIds, ["refactoring_plan_markdown"]);
    assert.equal(detail?.present, false);
    assert.deepEqual(detail?.requiredContent, [
      "decision-basis",
      "phase-1",
      "phase-2",
      "phase-3",
      "phase-4",
      "phase-5",
      "phase-6",
      "implemented-status",
      "compressed-context",
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("completed review artifact validator reports malformed and incomplete artifact details", () => {
  const root = buildCompleteReviewArtifactFixture();
  try {
    writeFileSync(
      join(root, "docs", "review-evidence.json"),
      `${JSON.stringify({ schemaVersion: "review-evidence.v1", inventory: [], summary: {} }, null, 2)}\n`,
      "utf8",
    );
    writeFileSync(
      join(root, "docs", "diagnosis-report.md"),
      "# Diagnosis\n\nRecommendation: **partial redesign**.\n",
      "utf8",
    );

    const result = validateCompletedReviewArtifacts(root);
    const reviewEvidence = result.artifacts.find((artifact) => artifact.id === "review_evidence");
    const diagnosis = result.artifacts.find((artifact) => artifact.id === "diagnosis_report_markdown");

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.incompleteArtifactIds, ["diagnosis_report_markdown", "review_evidence"]);
    assert.deepEqual(reviewEvidence?.missingFields, [
      "findings",
      "findings.0.evidence",
      "findings.0.recommendation",
      "summary.inspectedModules",
      "summary.findingCount",
      "summary.findingsBySeverity",
      "summary.findingsByCategory",
      "summary.recommendation",
    ]);
    assert.deepEqual(diagnosis?.missingContent, [
      "prior-review-artifact-section",
      "decision-section",
      "priority-assessment-section",
      "requirement-gap-mapping-section",
      "token-strategy-section",
      "decision-evidence-artifact",
      "seed-priority-order",
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("completed review artifact completeness command returns stable pass/fail JSON", () => {
  const root = buildCompleteReviewArtifactFixture();
  try {
    const first = executeCheckReviewArtifactCompletenessCommand(root);
    const second = executeCheckReviewArtifactCompletenessCommand(root);

    assert.equal(first.exitCode, 0);
    assert.equal(first.stderr, "");
    assert.equal(first.stdout, second.stdout);
    assert.equal(JSON.parse(first.stdout).command, "ai-agent check-review-artifact-completeness");

    rmSync(join(root, "docs", "generated", "inventory-orchestration-report.json"), { force: true });
    const failed = executeCheckReviewArtifactCompletenessCommand(root);

    assert.equal(failed.exitCode, 1);
    assert.equal(failed.stderr, "");
    assert.deepEqual(JSON.parse(failed.stdout).summary.missingArtifactIds, ["inventory_orchestration_report"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function buildCompleteReviewArtifactFixture(): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-review-artifact-completeness-"));
  mkdirSync(join(root, "docs", "generated"), { recursive: true });

  writeFileSync(
    join(root, "docs", "diagnosis-report.md"),
    [
      "# AI_Agent MVP Diagnosis",
      "## Prior Review Artifact",
      "Decision evidence artifact: `docs/review-evidence.json`.",
      "## Decision",
      "Recommendation: **partial redesign**.",
      "## Priority Assessment",
      "1. Error frequency",
      "2. Maintenance difficulty",
      "3. Token cost",
      "4. Architecture fit",
      "5. Feature completeness",
      "## Requirement-to-Gap Mapping",
      "The mapping is generated.",
      "## Token Strategy",
      "Compressed context keeps raw full text out of the exposed loop.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "docs", "refactoring-plan.md"),
    [
      "# AI_Agent Refactoring Plan",
      "Decision basis: `docs/review-evidence.json` (`review-evidence.v1`, recommendation `partial_redesign`).",
      "## Phase 1: Stabilize MVP Surface",
      "Status: implemented.",
      "## Phase 2: Separate Planning From Orchestration",
      "Status: implemented.",
      "## Phase 3: Preserve Full Text, Expose Summaries",
      "Status: implemented for fake-executor MVP.",
      "Use compressed context.",
      "## Phase 4: Convergence and Escalation Rules",
      "Status: implemented.",
      "## Phase 5: Verification Hardening",
      "Status: implemented for current verification artifacts.",
      "Compute acceptanceEvidence from generated artifacts.",
      "## Phase 6: Later Non-MVP Work",
      "Out of scope.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeJson(join(root, "docs", "review-evidence.json"), {
    schemaVersion: "review-evidence.v1",
    inventory: [{ id: "existing:src/orchestrator.ts", relativePath: "src/orchestrator.ts", kind: "source", moduleName: "src.orchestrator" }],
    findings: [
      {
        id: "finding:existing:src/orchestrator.ts:missing-test",
        sourceId: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "high",
        category: "error_frequency",
        title: "Source module has no observable test coverage",
        evidence: "No test reference was detected for this source module.",
        recommendation: "Add a focused runnable test before using this module as part of the MVP diagnosis or meeting loop.",
      },
    ],
    summary: {
      inspectedModules: 1,
      findingCount: 1,
      findingsBySeverity: { critical: 0, high: 1, medium: 0, low: 0 },
      findingsByCategory: {
        error_frequency: 1,
        maintainability: 0,
        token_cost: 0,
        architecture_fit: 0,
        feature_completeness: 0,
      },
      recommendation: "partial_redesign",
    },
  });
  writeJson(join(root, "docs", "generated", "diagnosis-report.json"), {
    schemaVersion: "diagnosis-report-generation.v1",
    diagnosisReport: {
      schemaVersion: "diagnosis-report.v1",
      diagnosis: {
        decision: "partial_redesign",
        decisionEvidenceArtifact: "docs/review-evidence.json",
      },
      requirementToGapMappingArtifact: {
        summary: { implementedCount: 6, missingCount: 0, readmeRequirementCount: 19 },
      },
    },
    reviewEvidence: {
      schemaVersion: "review-evidence.v1",
      recommendation: "partial_redesign",
    },
    inventoryOrchestration: {
      schemaVersion: "inventory-orchestration.v1",
    },
    decisionGate: {
      accepted: true,
    },
  });
  writeJson(join(root, "docs", "generated", "requirement-gap-mapping.json"), {
    schemaVersion: "implementation-capabilities.v1",
    capabilities: [],
    readmeRequirementMappings: [],
    summary: {
      implementedCount: 6,
      missingCount: 0,
      readmeRequirementCount: 19,
    },
  });
  writeJson(join(root, "docs", "generated", "inventory-orchestration-report.json"), {
    schemaVersion: "inventory-orchestration.v1",
    sourceFiles: [],
    runnableEntryPoints: [],
    testFiles: [],
    configFiles: [],
    moduleFeatureSummary: {
      schemaVersion: "implemented-module-features.v1",
    },
    summary: {
      sourceFileCount: 1,
      runnableEntryPointCount: 1,
      testFileCount: 1,
      configFileCount: 1,
    },
  });

  return root;
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}
