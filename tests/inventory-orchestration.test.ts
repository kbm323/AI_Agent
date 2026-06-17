import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildGovernedRedesignPlanStep,
  buildInventoryOrchestrationReport,
  gateRedesignPlanStepWithInventory,
  verifyInventoryOrchestrationReportGenerated,
  writeInventoryOrchestrationReport,
} from "../src/inventory-orchestration.ts";

test("inventory orchestration report combines source entry point test and config discovery", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-orchestration-"));
  try {
    writeFixtureProject(root);

    const first = buildInventoryOrchestrationReport(root);
    const second = buildInventoryOrchestrationReport(root);

    assert.deepEqual(first, second);
    assert.equal(first.schemaVersion, "inventory-orchestration.v1");
    assert.deepEqual(
      first.sourceFiles.map((entry) => entry.relativePath),
      ["scripts/dry-run.ts", "src/index.ts", "src/orchestrator.ts", "tests/orchestrator.test.ts"],
    );
    assert.deepEqual(
      first.runnableEntryPoints.map((entry) => `${entry.source}:${entry.name}:${entry.relativePath ?? entry.command}`),
      [
        "common_main_file:index:src/index.ts",
        "package_exports:.:src/index.ts",
        "package_script:dry-run:npm run dry-run",
        "package_script:test:npm run test",
        "scripts_directory:dry-run:scripts/dry-run.ts",
      ],
    );
    assert.deepEqual(
      first.testFiles.map((entry) => entry.relativePath),
      ["tests/orchestrator.test.ts"],
    );
    assert.deepEqual(
      first.configFiles.map((entry) => entry.relativePath),
      ["package.json", "tsconfig.json"],
    );
    assert.deepEqual(first.moduleFeatureSummary, {
      schemaVersion: "implemented-module-features.v1",
      modules: [
        {
          id: "existing:scripts/dry-run.ts",
          relativePath: "scripts/dry-run.ts",
          moduleName: "scripts.dry-run",
          kind: "script",
          exportedSymbols: [],
          localDependencies: [],
          coveredByTests: [],
          runnableEntryPointIds: ["scripts_directory:scripts/dry-run.ts"],
          featureTags: [],
        },
        {
          id: "existing:src/index.ts",
          relativePath: "src/index.ts",
          moduleName: "src.index",
          kind: "source",
          exportedSymbols: ["api"],
          localDependencies: [],
          coveredByTests: [],
          runnableEntryPointIds: ["common_main_file:src/index.ts", "package_exports:.:src/index.ts"],
          featureTags: ["public_api"],
        },
        {
          id: "existing:src/orchestrator.ts",
          relativePath: "src/orchestrator.ts",
          moduleName: "src.orchestrator",
          kind: "source",
          exportedSymbols: ["orchestrator"],
          localDependencies: [],
          coveredByTests: ["tests/orchestrator.test.ts"],
          runnableEntryPointIds: [],
          featureTags: [],
        },
        {
          id: "existing:tests/orchestrator.test.ts",
          relativePath: "tests/orchestrator.test.ts",
          moduleName: "tests.orchestrator.test",
          kind: "test",
          exportedSymbols: [],
          localDependencies: [],
          coveredByTests: ["tests/orchestrator.test.ts"],
          runnableEntryPointIds: [],
          featureTags: [],
        },
      ],
      summary: {
        moduleCount: 4,
        modulesWithExports: 2,
        modulesWithTestCoverage: 2,
        runnableModuleCount: 2,
        featureTags: ["public_api"],
        normalizedPathSeparator: "/",
      },
    });
    assert.deepEqual(first.summary, {
      sourceFileCount: 4,
      runnableEntryPointCount: 5,
      testFileCount: 1,
      configFileCount: 2,
      normalizedPathSeparator: "/",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration report extracts current implementation capabilities from a controlled fixture", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-capability-extraction-"));
  try {
    writeCapabilityFixtureProject(root);

    const report = buildInventoryOrchestrationReport(root);
    const requestAnalysis = report.capabilityInventory.capabilities.find((entry) => entry.id === "request-analysis");
    const roleRouting = report.capabilityInventory.capabilities.find((entry) => entry.id === "role-routing");

    assert.equal(report.capabilityInventory.schemaVersion, "capability-inventory.v1");
    assert.equal(requestAnalysis?.status, "implemented");
    assert.deepEqual(requestAnalysis?.evidence.implementationModules, ["src/planning.ts"]);
    assert.deepEqual(requestAnalysis?.evidence.testFiles, ["tests/planning.test.ts"]);
    assert.deepEqual(requestAnalysis?.evidence.featureTags, ["request_analysis"]);
    assert.equal(roleRouting?.status, "implemented");
    assert.deepEqual(roleRouting?.evidence.implementationModules, ["src/planning.ts"]);
    assert.deepEqual(roleRouting?.evidence.testFiles, ["tests/planning.test.ts"]);
    assert.deepEqual(roleRouting?.evidence.featureTags, ["role_routing"]);
    assert.equal(report.capabilityInventory.summary.requiredMvpImplementedCount >= 2, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification requires a generated concrete artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-verification-"));
  try {
    writeFixtureProject(root);

    const missing = verifyInventoryOrchestrationReportGenerated({
      projectRoot: root,
      reportPath: "docs/generated/inventory-orchestration-report.json",
    });

    assert.deepEqual(missing, {
      generated: false,
      valid: false,
      reportPath: join(root, "docs", "generated", "inventory-orchestration-report.json"),
      missingFields: ["inventory_orchestration_report"],
      inconsistentFields: [],
    });

    const written = writeInventoryOrchestrationReport({ projectRoot: root });
    const verification = verifyInventoryOrchestrationReportGenerated({
      projectRoot: root,
      reportPath: written.reportPath,
    });

    assert.equal(existsSync(written.reportPath), true);
    assert.equal(verification.generated, true);
    assert.equal(verification.valid, true);
    assert.equal(verification.schemaVersion, "inventory-orchestration.v1");
    assert.deepEqual(verification.summary, written.report.summary);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification rejects malformed artifact counts", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-malformed-"));
  try {
    writeFixtureProject(root);
    const { report } = writeInventoryOrchestrationReport({ projectRoot: root });
    const malformed = {
      ...report,
      summary: {
        ...report.summary,
        sourceFileCount: report.summary.sourceFileCount + 1,
      },
    };

    const verification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, report: malformed });

    assert.equal(verification.generated, true);
    assert.equal(verification.valid, false);
    assert.deepEqual(verification.missingFields, []);
    assert.deepEqual(verification.inconsistentFields, ["summary.sourceFileCount"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification rejects malformed module feature summaries", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-malformed-features-"));
  try {
    writeFixtureProject(root);
    const { report } = writeInventoryOrchestrationReport({ projectRoot: root });
    const wrongCount = {
      ...report,
      moduleFeatureSummary: {
        ...report.moduleFeatureSummary,
        summary: {
          ...report.moduleFeatureSummary.summary,
          moduleCount: report.moduleFeatureSummary.summary.moduleCount + 1,
        },
      },
    };
    const missingConcreteFields = {
      ...report,
      moduleFeatureSummary: {
        ...report.moduleFeatureSummary,
        modules: report.moduleFeatureSummary.modules.map(({ exportedSymbols, ...moduleEntry }) => moduleEntry),
      },
    };

    const wrongCountVerification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, report: wrongCount });
    const missingFieldVerification = verifyInventoryOrchestrationReportGenerated({
      projectRoot: root,
      report: missingConcreteFields,
    });

    assert.equal(wrongCountVerification.generated, true);
    assert.equal(wrongCountVerification.valid, false);
    assert.deepEqual(wrongCountVerification.inconsistentFields, ["moduleFeatureSummary.summary.moduleCount"]);
    assert.equal(missingFieldVerification.generated, true);
    assert.equal(missingFieldVerification.valid, false);
    assert.equal(missingFieldVerification.missingFields.includes("moduleFeatureSummary.modules[].exportedSymbols"), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification rejects malformed capability extraction evidence", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-malformed-capabilities-"));
  try {
    writeCapabilityFixtureProject(root);
    const { report } = writeInventoryOrchestrationReport({ projectRoot: root });
    const malformed = {
      ...report,
      capabilityInventory: {
        ...report.capabilityInventory,
        capabilities: report.capabilityInventory.capabilities.map((capability) =>
          capability.id === "request-analysis"
            ? {
                ...capability,
                status: "implemented",
                evidence: {
                  ...capability.evidence,
                  testFiles: [],
                },
              }
            : capability,
        ),
      },
    };

    const verification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, report: malformed });

    assert.equal(verification.generated, true);
    assert.equal(verification.valid, false);
    assert.deepEqual(verification.missingFields, []);
    assert.equal(verification.inconsistentFields.includes("capabilityInventory.capabilities[].status:request-analysis"), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification rejects self-consistent stale repository inventory", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-stale-"));
  try {
    writeFixtureProject(root);
    const { report } = writeInventoryOrchestrationReport({ projectRoot: root });
    const stale = {
      ...report,
      sourceFiles: report.sourceFiles.filter((entry) => entry.relativePath !== "src/orchestrator.ts"),
      moduleFeatureSummary: {
        ...report.moduleFeatureSummary,
        modules: report.moduleFeatureSummary.modules.filter((entry) => entry.relativePath !== "src/orchestrator.ts"),
        summary: {
          ...report.moduleFeatureSummary.summary,
          moduleCount: report.moduleFeatureSummary.summary.moduleCount - 1,
          modulesWithExports: report.moduleFeatureSummary.summary.modulesWithExports - 1,
          modulesWithTestCoverage: report.moduleFeatureSummary.summary.modulesWithTestCoverage - 1,
        },
      },
      summary: {
        ...report.summary,
        sourceFileCount: report.summary.sourceFileCount - 1,
      },
    };

    const verification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, report: stale });

    assert.equal(verification.generated, true);
    assert.equal(verification.valid, false);
    assert.deepEqual(verification.missingFields, []);
    assert.equal(verification.inconsistentFields.includes("currentRepository.sourceFiles"), true);
    assert.equal(verification.inconsistentFields.includes("moduleFeatureSummary.modules"), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("inventory orchestration verification requires at least one runnable module or unit test", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-empty-execution-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(join(root, "package.json"), `${JSON.stringify({ type: "module" }, null, 2)}\n`);
    writeFileSync(join(root, "src", "planning.ts"), "export const planning = 'request analysis';\n");

    const { report } = writeInventoryOrchestrationReport({ projectRoot: root });
    const verification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, report });

    assert.equal(verification.generated, true);
    assert.equal(verification.valid, false);
    assert.equal(verification.missingFields.includes("runnable_module_or_unit_test"), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("redesign plan steps are blocked until inventory orchestration verification passes", () => {
  const missingVerification = verifyInventoryOrchestrationReportGenerated({
    projectRoot: "/tmp",
    reportPath: "missing-inventory-report.json",
  });
  const gate = gateRedesignPlanStepWithInventory({
    recommendation: "partial_redesign",
    inventoryVerification: missingVerification,
  });

  assert.equal(gate.accepted, false);
  assert.deepEqual(gate.reasons, ["inventory_orchestration_report_not_generated"]);
  assert.throws(
    () =>
      buildGovernedRedesignPlanStep({
        id: "step-001",
        title: "Refactor request analysis",
        action: "Preserve existing planning module and tighten tests.",
        recommendation: "partial_redesign",
        inventoryVerification: missingVerification,
      }),
    /Inventory orchestration report must be generated before redesign plan steps run/,
  );
});

test("redesign plan steps run after inventory orchestration verification passes", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-plan-step-"));
  try {
    writeFixtureProject(root);
    const { reportPath } = writeInventoryOrchestrationReport({ projectRoot: root });
    const inventoryVerification = verifyInventoryOrchestrationReportGenerated({ projectRoot: root, reportPath });

    const step = buildGovernedRedesignPlanStep({
      id: "step-001",
      title: "Refactor request analysis",
      action: "Preserve existing planning module and tighten tests.",
      recommendation: "partial_redesign",
      inventoryVerification,
    });

    assert.deepEqual(step, {
      id: "step-001",
      title: "Refactor request analysis",
      action: "Preserve existing planning module and tighten tests.",
      recommendation: "partial_redesign",
      inventoryReportPath: reportPath,
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function writeFixtureProject(root: string): void {
  mkdirSync(join(root, "src"), { recursive: true });
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "tests"), { recursive: true });
  writeFileSync(
    join(root, "package.json"),
    `${JSON.stringify(
      {
        type: "module",
        exports: {
          ".": "./src/index.ts",
        },
        scripts: {
          "dry-run": "node scripts/dry-run.ts",
          test: "node --test tests/*.test.ts",
        },
      },
      null,
      2,
    )}\n`,
  );
  writeFileSync(join(root, "tsconfig.json"), "{}\n");
  writeFileSync(join(root, "src", "index.ts"), "export const api = true;\n");
  writeFileSync(join(root, "src", "orchestrator.ts"), "export const orchestrator = true;\n");
  writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry run');\n");
  writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\ntest('ok', () => {});\n");
}

function writeCapabilityFixtureProject(root: string): void {
  writeFixtureProject(root);
  writeFileSync(
    join(root, "src", "planning.ts"),
    [
      "export function analyzeUserRequest(request: string): string {",
      "  return `user request: ${request}`;",
      "}",
      "",
      "export function decomposeUserRequest(request: string): string[] {",
      "  return [`task breakdown: ${request}`];",
      "}",
      "",
      "export function buildRoleRoutes(workItems: string[]): string[] {",
      "  return workItems.map((item) => `role route assignment: ${item}`);",
      "}",
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(root, "tests", "planning.test.ts"),
    "import test from 'node:test';\nimport { analyzeUserRequest } from '../src/planning.ts';\ntest('planning fixture', () => analyzeUserRequest('build MVP'));\n",
  );
}
