import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeCheckArtifactsCommand } from "../scripts/check-artifacts.ts";
import {
  checkDiagnosticArtifacts,
  requiredDiagnosticArtifacts,
} from "../src/artifact-check.ts";

test("diagnostic artifact checker passes with required reports and generated artifacts", () => {
  const root = buildCompleteArtifactFixture();
  try {
    const result = checkDiagnosticArtifacts(root);

    assert.equal(result.command, "ai-agent check-artifacts");
    assert.equal(result.schemaVersion, "diagnostic-artifact-check.v1");
    assert.equal(result.status, "passed");
    assert.equal(result.deterministic, true);
    assert.deepEqual(result.summary, {
      requiredArtifactCount: requiredDiagnosticArtifacts.length,
      presentArtifactCount: requiredDiagnosticArtifacts.length,
      completeArtifactCount: requiredDiagnosticArtifacts.length,
      missingArtifactIds: [],
      incompleteArtifactIds: [],
    });
    assert.equal(result.artifacts.every((artifact) => artifact.present), true);
    assert.equal(result.artifacts.every((artifact) => artifact.missingSections.length === 0), true);
    assert.equal(result.artifacts.every((artifact) => artifact.missingFields.length === 0), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnostic artifact checker reports missing files deterministically", () => {
  const root = buildCompleteArtifactFixture();
  try {
    rmSync(join(root, "docs", "refactoring-plan.md"), { force: true });

    const first = executeCheckArtifactsCommand(root);
    const second = executeCheckArtifactsCommand(root);
    const parsed = JSON.parse(first.stdout);
    const missing = parsed.artifacts.find((artifact: { id: string }) => artifact.id === "refactoring_plan_markdown");

    assert.equal(first.exitCode, 1);
    assert.equal(first.stderr, "");
    assert.equal(first.stdout, second.stdout);
    assert.deepEqual(parsed.summary.missingArtifactIds, ["refactoring_plan_markdown"]);
    assert.equal(missing.present, false);
    assert.deepEqual(missing.requiredSections, [
      "evaluation-priority",
      "mvp-coverage",
      "phase-1",
      "phase-2",
      "phase-3",
      "phase-4",
      "phase-5",
      "phase-6",
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnostic artifact checker reports missing sections and malformed json deterministically", () => {
  const root = buildCompleteArtifactFixture();
  try {
    writeFileSync(
      join(root, "docs", "diagnosis-report.md"),
      ["# AI_Agent MVP Diagnosis", "## Decision", "Recommendation: **partial redesign**.", ""].join("\n"),
      "utf8",
    );
    writeFileSync(join(root, "docs", "review-evidence.json"), "{ malformed json\n", "utf8");

    const first = executeCheckArtifactsCommand(root);
    const second = executeCheckArtifactsCommand(root);
    const parsed = JSON.parse(first.stdout);
    const diagnosis = parsed.artifacts.find((artifact: { id: string }) => artifact.id === "diagnosis_report_markdown");
    const reviewEvidence = parsed.artifacts.find((artifact: { id: string }) => artifact.id === "review_evidence_json");

    assert.equal(first.exitCode, 1);
    assert.equal(first.stdout, second.stdout);
    assert.deepEqual(parsed.summary.incompleteArtifactIds, ["diagnosis_report_markdown", "review_evidence_json"]);
    assert.deepEqual(diagnosis.missingSections, [
      "scope",
      "prior-review-artifact",
      "priority-assessment",
      "requirement-to-gap-mapping",
      "token-strategy",
    ]);
    assert.equal(reviewEvidence.validJson, false);
    assert.deepEqual(reviewEvidence.missingFields, [
      "schemaVersion",
      "inventory.length",
      "findings.length",
      "summary.inspectedModules",
      "summary.findingCount",
      "summary.recommendation",
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnostic artifact checker reports mismatched required field values", () => {
  const root = buildCompleteArtifactFixture();
  try {
    writeJson(join(root, "docs", "generated", "diagnosis-report.json"), {
      schemaVersion: "diagnosis-report-generation.v1",
      diagnosisReport: {
        schemaVersion: "diagnosis-report.v1",
        diagnosis: {
          decision: "keep",
        },
        requirementToGapMappingArtifact: {
          summary: {},
        },
      },
      reviewEvidence: {
        recommendation: "keep",
      },
      decisionGate: {
        accepted: false,
      },
    });

    const result = checkDiagnosticArtifacts(root);
    const diagnosis = result.artifacts.find((artifact) => artifact.id === "generated_diagnosis_report_json");

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.incompleteArtifactIds, ["generated_diagnosis_report_json"]);
    assert.deepEqual(diagnosis?.mismatchedFields, [
      "diagnosisReport.diagnosis.decision",
      "reviewEvidence.recommendation",
      "decisionGate.accepted",
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function buildCompleteArtifactFixture(): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-artifact-check-"));
  mkdirSync(join(root, "docs", "generated"), { recursive: true });

  writeFileSync(
    join(root, "docs", "diagnosis-report.md"),
    [
      "# AI_Agent MVP Diagnosis",
      "## Scope",
      "Uses README and Seed requirements.",
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
      "Generated from README requirements.",
      "## Token Strategy",
      "Compressed context keeps raw text out of exposed loop context.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "docs", "refactoring-plan.md"),
    [
      "# AI_Agent Refactoring Plan",
      "## Evaluation Priority",
      "Seed priority order.",
      "## MVP Coverage",
      "OpenClaw/Hermes loop.",
      "## Phase 1: Stabilize MVP Surface",
      "Status: implemented.",
      "## Phase 2: Separate Planning From Orchestration",
      "Status: implemented.",
      "## Phase 3: Preserve Full Text, Expose Summaries",
      "Status: implemented.",
      "## Phase 4: Convergence and Escalation Rules",
      "Status: implemented.",
      "## Phase 5: Verification Hardening",
      "Status: implemented.",
      "## Phase 6: Later Non-MVP Work",
      "Out of scope.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "docs", "token-reduction-strategy.md"),
    [
      "# Token Reduction Strategy",
      "## 40-50% Savings Target",
      "Target band.",
      "## Original Text Retention Policy",
      "Persist raw text.",
      "## Exposed Context Summary Separation",
      "Expose summaries.",
      "## Compressed Context Approach",
      "Carry compact fields.",
      "## Baseline Measurement",
      "Measured savings.",
      "## Validation Sections",
      "Required sections.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "docs", "context-storage-boundary.md"),
    [
      "# Context Storage Boundary",
      "## Source Of Truth",
      "Raw fields.",
      "## Loop Visible Fields",
      "Summary fields.",
      "## Audit Only Fields",
      "Raw text.",
      "## Invariants",
      "Separation.",
      "## Verification Checks",
      "Concrete checks.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "docs", "loop-context-compression-policy.md"),
    [
      "# Loop Context Compression Policy",
      "## Retained Fields",
      "Raw storage.",
      "## Summarized Fields",
      "Loop summaries.",
      "## Dropped Fields",
      "Redundant replay.",
      "## Iteration Boundaries",
      "OpenClaw/Hermes transitions.",
      "## Deterministic Ordering",
      "Stable output.",
      "",
    ].join("\n"),
    "utf8",
  );
  writeJson(join(root, "docs", "review-evidence.json"), {
    schemaVersion: "review-evidence.v1",
    inventory: [{ id: "src/orchestrator.ts" }],
    findings: [{ id: "finding-1" }],
    summary: {
      inspectedModules: 1,
      findingCount: 1,
      recommendation: "partial_redesign",
    },
  });
  writeJson(join(root, "docs", "generated", "diagnosis-report.json"), {
    schemaVersion: "diagnosis-report-generation.v1",
    diagnosisReport: {
      schemaVersion: "diagnosis-report.v1",
      diagnosis: {
        decision: "partial_redesign",
      },
      requirementToGapMappingArtifact: {
        summary: {},
      },
    },
    reviewEvidence: {
      recommendation: "partial_redesign",
    },
    decisionGate: {
      accepted: true,
    },
  });
  writeJson(join(root, "docs", "generated", "requirement-gap-mapping.json"), {
    schemaVersion: "implementation-capabilities.v1",
    capabilities: [{ id: "request-analysis" }],
    readmeRequirementMappings: [{ id: "mvp_goal_flow:001" }],
    summary: {
      implementedCount: 1,
      missingCount: 0,
      readmeRequirementCount: 1,
    },
  });
  writeJson(join(root, "docs", "generated", "inventory-orchestration-report.json"), {
    schemaVersion: "inventory-orchestration.v1",
    sourceFiles: [{ path: "src/orchestrator.ts" }],
    runnableEntryPoints: [{ path: "scripts/dry-run.ts" }],
    testFiles: [{ path: "tests/orchestrator.test.ts" }],
    configFiles: [{ path: "package.json" }],
    moduleFeatureSummary: {
      schemaVersion: "implemented-module-features.v1",
    },
    summary: {
      sourceFileCount: 1,
    },
  });

  return root;
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}
