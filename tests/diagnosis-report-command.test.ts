import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  executeGenerateDiagnosisReportCommand,
  runGenerateDiagnosisReportCommand,
} from "../scripts/generate-diagnosis-report.ts";
import { evaluateProjectFindings } from "../src/evaluation.ts";

const requiredDiagnosticReportSections = [
  "# AI_Agent MVP Diagnosis",
  "## Scope",
  "## Prior Review Artifact",
  "## Decision",
  "## Priority Assessment",
  "## Existing Components to Keep",
  "## Required Changes Implemented",
  "## Requirement-to-Gap Mapping",
  "## Token Strategy",
];

test("repository diagnostic report artifact exists and contains all required sections", () => {
  const diagnosisReportPath = join(process.cwd(), "docs", "diagnosis-report.md");
  const diagnosisReportMarkdown = readFileSync(diagnosisReportPath, "utf8");

  assert.equal(existsSync(diagnosisReportPath), true);
  for (const section of requiredDiagnosticReportSections) {
    assert.match(diagnosisReportMarkdown, new RegExp(`^${escapeRegExp(section)}$`, "m"));
  }
});

test("diagnosis-report command generates stable observable report artifact output", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-command-"));
  try {
    writeFixtureProject(root);

    const apiResult = runGenerateDiagnosisReportCommand({ projectRoot: root });
    const commandResult = executeGenerateDiagnosisReportCommand(["--project-root", root]);
    const artifact = JSON.parse(readFileSync(join(root, "docs", "generated", "diagnosis-report.json"), "utf8"));
    const requirementGapMapping = JSON.parse(
      readFileSync(join(root, "docs", "generated", "requirement-gap-mapping.json"), "utf8"),
    );
    const inventoryOrchestration = JSON.parse(
      readFileSync(join(root, "docs", "generated", "inventory-orchestration-report.json"), "utf8"),
    );
    const diagnosisReportPath = join(root, "docs", "diagnosis-report.md");
    const diagnosisReportMarkdown = readFileSync(diagnosisReportPath, "utf8");
    const renderedRequirementRows = diagnosisReportMarkdown.match(/^\| `[^`]+` .* \| (covered|partial|missing|unknown) \|/gm) ?? [];

    assert.equal(commandResult.exitCode, 0);
    assert.equal(commandResult.stderr, "");
    assert.deepEqual(JSON.parse(commandResult.stdout), apiResult);
    assert.deepEqual(apiResult, {
      command: "ai-agent generate-diagnosis-report",
      decisionResult: {
        recommendation: "partial_redesign",
        decisionGateAccepted: true,
        evidenceArtifactPath: join(root, "docs", "review-evidence.json"),
      },
      artifact: {
        path: join(root, "docs", "generated", "diagnosis-report.json"),
        schemaVersion: "diagnosis-report-generation.v1",
        diagnosisReportPath: join(root, "docs", "diagnosis-report.md"),
        requirementGapMappingPath: join(root, "docs", "generated", "requirement-gap-mapping.json"),
        reviewEvidencePath: join(root, "docs", "review-evidence.json"),
        inventoryOrchestrationReportPath: join(root, "docs", "generated", "inventory-orchestration-report.json"),
        recommendation: "partial_redesign",
        decisionGateAccepted: true,
      },
    });
    assert.equal(existsSync(apiResult.artifact.path), true);
    assert.equal(artifact.schemaVersion, "diagnosis-report-generation.v1");
    assert.equal(artifact.diagnosisReport.schemaVersion, "diagnosis-report.v1");
    assert.deepEqual(requirementGapMapping, artifact.diagnosisReport.requirementToGapMappingArtifact);
    assert.deepEqual(artifact.requirementGapMapping, {
      artifactPath: join(root, "docs", "generated", "requirement-gap-mapping.json"),
      schemaVersion: "implementation-capabilities.v1",
      implementedCount: 6,
      missingCount: 0,
      readmeRequirementCount: 8,
    });
    assert.equal(artifact.reviewEvidence.schemaVersion, "review-evidence.v1");
    assert.equal(artifact.reviewEvidence.recommendation, "partial_redesign");
    assert.equal(artifact.inventoryOrchestration.schemaVersion, "inventory-orchestration.v1");
    assert.equal(artifact.inventoryOrchestration.verification.valid, true);
    assert.equal(inventoryOrchestration.schemaVersion, "inventory-orchestration.v1");
    assert.deepEqual(inventoryOrchestration.summary, {
      sourceFileCount: 7,
      runnableEntryPointCount: 1,
      testFileCount: 1,
      configFileCount: 1,
      normalizedPathSeparator: "/",
    });
    assert.equal(artifact.decisionGate.accepted, true);
    assert.equal(artifact.reviewArtifactCompleteness.status, "passed");
    assert.deepEqual(artifact.reviewArtifactCompleteness.summary.missingArtifactIds, []);
    assert.deepEqual(artifact.reviewArtifactCompleteness.summary.incompleteArtifactIds, []);
    assert.deepEqual(apiResult.decisionResult, {
      recommendation: artifact.diagnosisReport.diagnosis.decision,
      decisionGateAccepted: artifact.decisionGate.accepted,
      evidenceArtifactPath: artifact.reviewEvidence.artifactPath,
    });
    assert.equal(existsSync(diagnosisReportPath), true);
    for (const section of requiredDiagnosticReportSections) {
      assert.match(diagnosisReportMarkdown, new RegExp(`^${escapeRegExp(section)}$`, "m"));
    }
    assert.equal(renderedRequirementRows.length, requirementGapMapping.readmeRequirementMappings.length);
    for (const requirement of requirementGapMapping.readmeRequirementMappings) {
      assert.match(diagnosisReportMarkdown, new RegExp(`^\\| \`${escapeRegExp(requirement.id)}\` `, "m"));
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnosis-report artifact is stable across repeated runs with the same inputs", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-stability-"));
  try {
    writeFixtureProject(root);

    const first = executeGenerateDiagnosisReportCommand(["--project-root", root]);
    const firstArtifact = JSON.parse(readFileSync(join(root, "docs", "generated", "diagnosis-report.json"), "utf8"));
    const second = executeGenerateDiagnosisReportCommand(["--project-root", root]);
    const secondArtifact = JSON.parse(readFileSync(join(root, "docs", "generated", "diagnosis-report.json"), "utf8"));

    assert.equal(first.exitCode, 0);
    assert.equal(first.stderr, "");
    assert.equal(second.exitCode, 0);
    assert.equal(second.stderr, "");
    assert.deepEqual(JSON.parse(second.stdout), JSON.parse(first.stdout));
    assert.deepEqual(secondArtifact, firstArtifact);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnosis-report command output references generated diagnostic artifacts for the selected decision", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-decision-artifacts-"));
  try {
    writeFixtureProject(root);

    const result = executeGenerateDiagnosisReportCommand(["--project-root", root]);
    const output = JSON.parse(result.stdout);
    const generationArtifact = JSON.parse(readFileSync(join(root, "docs", "generated", "diagnosis-report.json"), "utf8"));
    const reviewEvidence = JSON.parse(readFileSync(join(root, "docs", "review-evidence.json"), "utf8"));
    const requirementGapMapping = JSON.parse(
      readFileSync(join(root, "docs", "generated", "requirement-gap-mapping.json"), "utf8"),
    );
    const selectedDecisionSupportingFindings = reviewEvidence.findings.filter(
      (finding: { severity: string; category: string }) => finding.severity === "high" || finding.category === "token_cost",
    );
    const evaluatedFindings = evaluateProjectFindings(reviewEvidence.findings);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(output.decisionResult.recommendation, "partial_redesign");
    assert.equal(output.decisionResult.decisionGateAccepted, true);
    assert.equal(output.artifact.recommendation, output.decisionResult.recommendation);
    assert.equal(output.artifact.decisionGateAccepted, output.decisionResult.decisionGateAccepted);
    assert.equal(output.artifact.path, join(root, "docs", "generated", "diagnosis-report.json"));
    assert.equal(output.artifact.requirementGapMappingPath, join(root, "docs", "generated", "requirement-gap-mapping.json"));
    assert.equal(output.artifact.reviewEvidencePath, output.decisionResult.evidenceArtifactPath);
    assert.equal(output.artifact.inventoryOrchestrationReportPath, join(root, "docs", "generated", "inventory-orchestration-report.json"));
    assert.equal(existsSync(output.artifact.path), true);
    assert.equal(existsSync(output.artifact.requirementGapMappingPath), true);
    assert.equal(existsSync(output.artifact.reviewEvidencePath), true);
    assert.equal(existsSync(output.artifact.inventoryOrchestrationReportPath), true);
    assert.equal(generationArtifact.diagnosisReport.diagnosis.decision, output.decisionResult.recommendation);
    assert.equal(generationArtifact.requirementGapMapping.artifactPath, output.artifact.requirementGapMappingPath);
    assert.equal(generationArtifact.reviewEvidence.artifactPath, output.artifact.reviewEvidencePath);
    assert.deepEqual(reviewEvidence.summary.recommendation, output.decisionResult.recommendation);
    assert.equal(reviewEvidence.summary.findingCount, reviewEvidence.findings.length);
    assert.equal(reviewEvidence.summary.findingsBySeverity.high > 0, true);
    assert.equal(reviewEvidence.summary.findingsByCategory.error_frequency > 0, true);
    assert.equal(selectedDecisionSupportingFindings.length > 0, true);
    assert.equal(evaluatedFindings.recommendation, output.decisionResult.recommendation);
    assert.equal(evaluatedFindings.justification.rule, "high_or_token_cost_evidence");
    assert.deepEqual(
      evaluatedFindings.justification.supportingEvidence.map((evidence) => evidence.findingId),
      selectedDecisionSupportingFindings
        .map((finding: { id: string }) => finding.id)
        .sort(),
    );
    assert.deepEqual(requirementGapMapping, generationArtifact.diagnosisReport.requirementToGapMappingArtifact);
    assert.equal(requirementGapMapping.summary.missingCount, 0);
    assert.equal(generationArtifact.decisionGate.accepted, true);
    assert.deepEqual(generationArtifact.decisionGate.reasons, []);
    assert.equal(generationArtifact.reviewArtifactCompleteness.status, "passed");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnosis-report command supports explicit output paths", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-output-"));
  try {
    writeFixtureProject(root);

    const result = executeGenerateDiagnosisReportCommand([
      "--project-root",
      root,
      "--output",
      "artifacts/diagnosis-report.json",
      "--review-evidence-output",
      "artifacts/review-evidence.json",
    ]);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(existsSync(join(root, "artifacts", "diagnosis-report.json")), true);
    assert.equal(existsSync(join(root, "docs", "generated", "requirement-gap-mapping.json")), true);
    assert.equal(existsSync(join(root, "docs", "generated", "inventory-orchestration-report.json")), true);
    assert.equal(existsSync(join(root, "artifacts", "review-evidence.json")), true);
    assert.equal(JSON.parse(result.stdout).artifact.path, join(root, "artifacts", "diagnosis-report.json"));
    assert.equal(
      JSON.parse(result.stdout).artifact.requirementGapMappingPath,
      join(root, "docs", "generated", "requirement-gap-mapping.json"),
    );
    assert.equal(JSON.parse(result.stdout).artifact.reviewEvidencePath, join(root, "artifacts", "review-evidence.json"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnosis-report command rejects invalid input with stable non-zero failure", () => {
  const result = executeGenerateDiagnosisReportCommand(["--output"]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "--output requires a non-empty value",
  });
});

test("diagnosis-report command blocks redesign recommendation when completed review artifacts are incomplete", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-artifact-gate-"));
  try {
    writeFixtureProject(root);
    rmSync(join(root, "docs", "refactoring-plan.md"), { force: true });

    const result = executeGenerateDiagnosisReportCommand(["--project-root", root]);
    const generationArtifact = JSON.parse(readFileSync(join(root, "docs", "generated", "diagnosis-report.json"), "utf8"));

    assert.equal(result.exitCode, 2);
    assert.equal(result.stdout, "");
    assert.match(JSON.parse(result.stderr).message, /Review artifact completeness gate blocked redesign recommendation generation/);
    assert.equal(generationArtifact.decisionGate.accepted, false);
    assert.deepEqual(generationArtifact.decisionGate.reasons, ["completed_review_artifacts_incomplete"]);
    assert.deepEqual(generationArtifact.reviewArtifactCompleteness.summary.missingArtifactIds, ["refactoring_plan_markdown"]);
    assert.equal(generationArtifact.reviewArtifactCompleteness.status, "failed");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function writeFixtureProject(root: string): void {
  mkdirSync(join(root, "docs"), { recursive: true });
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "src"), { recursive: true });
  mkdirSync(join(root, "tests"), { recursive: true });
  writeFileSync(
    join(root, "README.md"),
    [
      "# AI_Agent",
      "",
      "## MVP 목표",
      "",
      "```text",
      "parent channel user request",
      "  -> task 생성",
      "  -> OpenClaw owner draft",
      "  -> Hermes review",
      "  -> OpenClaw final synthesis",
      "  -> thread timeline 게시",
      "```",
      "",
      "## 운영 원칙",
      "",
      "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
      "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
      "",
    ].join("\n"),
  );
  writeFileSync(join(root, "package.json"), "{}\n");
  writeFileSync(
    join(root, "docs", "diagnosis-report.md"),
    [
      "# AI_Agent MVP Diagnosis",
      "## Scope",
      "This fixture diagnosis reviews the README MVP and generated project inventory.",
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
      "## Existing Components to Keep",
      "- `src/orchestrator.ts`",
      "- `src/db.ts`",
      "- `scripts/dry-run.ts`",
      "## Required Changes Implemented",
      "- Request analysis and task breakdown.",
      "- Role-based routing.",
      "- OpenClaw/Hermes meeting history.",
      "- Final synthesis and escalation artifacts.",
      "## Requirement-to-Gap Mapping",
      "The mapping is generated from README and project inventory.",
      "## Token Strategy",
      "Compressed context separates raw full text from exposed loop summaries.",
      "",
    ].join("\n"),
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
      "Status: implemented.",
      "Use compressed context for loop prompts.",
      "## Phase 4: Convergence and Escalation Rules",
      "Status: implemented.",
      "## Phase 5: Verification Hardening",
      "Status: implemented for current verification artifacts.",
      "Compute acceptanceEvidence from generated artifacts.",
      "## Phase 6: Later Non-MVP Work",
      "Out of scope.",
      "",
    ].join("\n"),
  );
  writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
  writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
  writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
  writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
  writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");
  writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
  writeFileSync(join(root, "tests", "planning.test.ts"), "import test from 'node:test';\n");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
