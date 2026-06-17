import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import {
  decideImplementationDirection,
  rankReviewEvidence,
  type ReviewFinding,
} from "../src/evaluation.ts";

const evaluationSourcePath = join(process.cwd(), "src", "evaluation.ts");

test("recommendation decision source has no report-generation or formatting module dependency", () => {
  const source = readFileSync(evaluationSourcePath, "utf8");
  const importStatements = source.match(/^import\s.+from\s+["'][^"']+["'];$/gm) ?? [];
  const runtimeImports = importStatements.filter((statement) => !statement.startsWith("import type "));
  const reportOrFormattingImports = importStatements.filter((statement) =>
    [
      "generate-diagnosis-report",
      "loadDiagnosisReportArtifact",
      "renderDiagnosisReportWithRequirementGapSection",
      "format",
      "diagnostic",
    ].some((forbiddenTerm) => statement.includes(forbiddenTerm)),
  );

  assert.deepEqual(runtimeImports, []);
  assert.deepEqual(reportOrFormattingImports, []);
  assert.equal(source.includes("../scripts/generate-diagnosis-report.ts"), false);
  assert.equal(importStatements.some((statement) => statement.includes("./inspection.ts") && !statement.startsWith("import type ")), false);
  assert.equal(source.includes("generateDiagnosisReport"), false);
  assert.equal(source.includes("loadDiagnosisReportArtifact"), false);
  assert.equal(source.includes("renderDiagnosisReportWithRequirementGapSection"), false);
  assert.equal(source.includes("formatRoleRoute"), false);
  assert.equal(source.includes("diagnosticOutput"), false);
});

test("recommendation decision runs when report and formatting modules are absent", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-recommendation-isolation-"));
  try {
    const srcDir = join(root, "src");
    mkdirSync(srcDir, { recursive: true });
    writeFileSync(join(srcDir, "evaluation.ts"), readFileSync(evaluationSourcePath, "utf8"), "utf8");

    const script = `
      import assert from "node:assert/strict";
      import { decideImplementationDirection, rankReviewEvidence } from ${JSON.stringify(pathToFileURL(join(srcDir, "evaluation.ts")).href)};

      const finding = {
        id: "token-low",
        sourceId: "isolated:review-evidence",
        relativePath: "src/evaluation.ts",
        moduleName: "src.evaluation",
        severity: "low",
        category: "token_cost",
        title: "Token cost finding",
        evidence: "Concrete ranked evidence supplied without report artifacts.",
        recommendation: "Reduce exposed loop context.",
      };
      const decision = decideImplementationDirection(rankReviewEvidence([finding]));
      assert.deepEqual(decision, { outcome: "partial_redesign", label: "partial redesign" });
    `;

    const result = spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
      cwd: root,
      encoding: "utf8",
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, "");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("recommendation decision accepts ranked evidence without report-rendered fields", () => {
  const findings: ReviewFinding[] = [
    {
      id: "error-high",
      sourceId: "isolated:review-evidence",
      relativePath: "src/orchestrator.ts",
      moduleName: "src.orchestrator",
      severity: "high",
      category: "error_frequency",
      title: "Frequent execution error",
      evidence: "A concrete finding object is enough input for the decision.",
      recommendation: "Add a focused correction before report generation.",
    },
  ];

  assert.deepEqual(decideImplementationDirection(rankReviewEvidence(findings)), {
    outcome: "partial_redesign",
    label: "partial redesign",
  });
});
