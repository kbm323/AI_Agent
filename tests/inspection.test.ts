import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  buildImplementationCapabilityArtifact,
  buildGovernedRecommendationDecision,
  buildInspectionInventory,
  buildReviewEvidenceArtifact,
  buildReviewEvidencePathArtifact,
  discoverTestAndConfigFiles,
  checkPriorReviewEvidenceCompletenessForRedesignDecision,
  discoverRunnableEntryPoints,
  discoverSourceFiles,
  emitGovernedRecommendation,
  extractReadmeMvpRequirementList,
  extractReviewFindings,
  gateRedesignDecision,
  handlePriorReviewArtifact,
  loadDiagnosisReportArtifact,
  parseReadmeDerivedMvpRequirements,
  parseReadmeMvpRequirements,
  renderDiagnosisReportWithRequirementGapSection,
  renderRequirementGapMappingSection,
  resolvePriorReviewArtifactIdentifier,
  validateReadmeMvpRequirementExtraction,
  validatePriorReviewEvidenceForRedesignDecision,
  validateRequirementGapMappingArtifact,
  writeReviewEvidenceArtifact,
} from "../src/inspection.ts";

const inspectionWorkflowSuccessPathCoverage = new Set([
  "buildImplementationCapabilityArtifact",
  "buildGovernedRecommendationDecision",
  "buildInspectionInventory",
  "buildReviewEvidenceArtifact",
  "buildReviewEvidencePathArtifact",
  "checkPriorReviewEvidenceCompletenessForRedesignDecision",
  "discoverRunnableEntryPoints",
  "discoverSourceFiles",
  "discoverTestAndConfigFiles",
  "emitGovernedRecommendation",
  "extractReadmeMvpRequirementList",
  "extractReviewFindings",
  "gateRedesignDecision",
  "handlePriorReviewArtifact",
  "loadDiagnosisReportArtifact",
  "parseReadmeDerivedMvpRequirements",
  "parseReadmeMvpRequirements",
  "renderDiagnosisReportWithRequirementGapSection",
  "renderRequirementGapMappingSection",
  "resolvePriorReviewArtifactIdentifier",
  "validatePriorReviewEvidenceForRedesignDecision",
  "validateReadmeMvpRequirementExtraction",
  "validateRequirementGapMappingArtifact",
  "writeReviewEvidenceArtifact",
]);

test("inspection workflow module has primary success-path coverage for every public function export", () => {
  const source = readFileSync(join(process.cwd(), "src", "inspection.ts"), "utf8");
  const exportedFunctions = [...source.matchAll(/^export function (\w+)/gm)].map((match) => match[1]).sort();

  assert.deepEqual([...inspectionWorkflowSuccessPathCoverage].sort(), exportedFunctions);
});

test("buildInspectionInventory returns stable ordered entries for inspected AI_Agent files", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-inventory-"));
  try {
    writeFileSync(join(root, "package.json"), "{}");
    writeFileSync(join(root, "README.md"), "# test\n");
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "node_modules", "ignored"), { recursive: true });
    writeFileSync(join(root, "src", "orchestrator.ts"), "export const orchestrator = true;\n");
    writeFileSync(join(root, "src", "db.ts"), "export const db = true;\n");
    writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry run');\n");
    writeFileSync(join(root, "docs", "diagnosis.md"), "# diagnosis\n");
    writeFileSync(join(root, "src", "ignored.txt"), "ignored\n");

    const first = buildInspectionInventory(root);
    const second = buildInspectionInventory(root);

    assert.deepEqual(first, second);
    assert.deepEqual(
      first.map((entry) => entry.relativePath),
      [
        "README.md",
        "docs/diagnosis.md",
        "package.json",
        "scripts/dry-run.ts",
        "src/db.ts",
        "src/orchestrator.ts",
        "tests/orchestrator.test.ts",
      ],
    );
    assert.deepEqual(
      first.map((entry) => entry.id),
      [
        "existing:README.md",
        "existing:docs/diagnosis.md",
        "existing:package.json",
        "existing:scripts/dry-run.ts",
        "existing:src/db.ts",
        "existing:src/orchestrator.ts",
        "existing:tests/orchestrator.test.ts",
      ],
    );
    assert.deepEqual(
      first.map((entry) => entry.kind),
      ["doc", "doc", "config", "script", "source", "source", "test"],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverRunnableEntryPoints identifies package metadata, common main files, scripts, and guarded main blocks", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-runnable-entrypoints-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    writeFileSync(join(root, "README.md"), "# fixture\n");
    writeFileSync(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          type: "module",
          main: "./src/main.ts",
          exports: {
            ".": "./src/index.ts",
            "./cli": {
              import: "./src/cli.ts",
            },
          },
          bin: {
            "ai-agent": "./scripts/dry-run.ts",
          },
          scripts: {
            "dry-run": "node scripts/dry-run.ts",
            typecheck: "node --check src/*.ts",
            format: "prettier .",
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "index.ts"), "export const api = true;\n");
    writeFileSync(join(root, "src", "main.ts"), "export const main = true;\n");
    writeFileSync(
      join(root, "src", "cli.ts"),
      [
        'import { fileURLToPath } from "node:url";',
        'const invokedPath = process.argv[1] ?? "";',
        "if (invokedPath === fileURLToPath(import.meta.url)) {",
        "  console.log('cli');",
        "}",
        "",
      ].join("\n"),
    );
    writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
    writeFileSync(join(root, "scripts", "generate.ts"), "if (process.argv[1]?.endsWith('generate.ts')) console.log('generate');\n");

    const entryPoints = discoverRunnableEntryPoints(root);

    assert.deepEqual(
      entryPoints.map((entry) => `${entry.source}:${entry.name}:${entry.relativePath ?? entry.command}`),
      [
        "common_main_file:index:src/index.ts",
        "common_main_file:main:src/main.ts",
        "guarded_main_block:src.cli:src/cli.ts",
        "guarded_main_block:scripts.generate:scripts/generate.ts",
        "package_bin:ai-agent:scripts/dry-run.ts",
        "package_exports:.:src/index.ts",
        "package_exports:./cli.import:src/cli.ts",
        "package_main:main:src/main.ts",
        "package_script:dry-run:npm run dry-run",
        "package_script:typecheck:npm run typecheck",
        "scripts_directory:dry-run:scripts/dry-run.ts",
        "scripts_directory:generate:scripts/generate.ts",
      ],
    );
    assert.equal(entryPoints.some((entry) => entry.name === "format"), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverRunnableEntryPoints derives evidence from concrete files instead of package-only assumptions", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-runnable-entrypoints-negative-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    writeFileSync(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          type: "module",
          main: "./src/missing.ts",
          scripts: {
            "dry-run": "node scripts/missing.ts",
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "not-main.ts"), "export const helper = true;\n");

    const entryPoints = discoverRunnableEntryPoints(root);

    assert.deepEqual(entryPoints, [
      {
        id: "package_main:main:src/missing.ts",
        name: "main",
        source: "package_main",
        relativePath: "src/missing.ts",
        command: undefined,
        evidence: "package.json main",
      },
      {
        id: "package_script:dry-run",
        name: "dry-run",
        source: "package_script",
        command: "npm run dry-run",
        evidence: "node scripts/missing.ts",
      },
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverSourceFiles returns stable classified source files only", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-source-discovery-"));
  try {
    writeFileSync(join(root, "package.json"), "{}");
    writeFileSync(join(root, "README.md"), "# test\n");
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    writeFileSync(join(root, "src", "orchestrator.ts"), "export const orchestrator = true;\n");
    writeFileSync(join(root, "src", "adapter.js"), "export const adapter = true;\n");
    writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry run');\n");
    writeFileSync(join(root, "docs", "diagnosis.md"), "# diagnosis\n");
    writeFileSync(join(root, "src", "ignored.txt"), "ignored\n");

    const first = discoverSourceFiles(root);
    const second = discoverSourceFiles(root);

    assert.deepEqual(first, second);
    assert.deepEqual(first, [
      {
        id: "existing:scripts/dry-run.ts",
        relativePath: "scripts/dry-run.ts",
        kind: "script",
        moduleName: "scripts.dry-run",
        extension: ".ts",
      },
      {
        id: "existing:src/adapter.js",
        relativePath: "src/adapter.js",
        kind: "source",
        moduleName: "src.adapter",
        extension: ".js",
      },
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
        extension: ".ts",
      },
      {
        id: "existing:tests/orchestrator.test.ts",
        relativePath: "tests/orchestrator.test.ts",
        kind: "test",
        moduleName: "tests.orchestrator.test",
        extension: ".ts",
      },
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("discoverTestAndConfigFiles inventories tests and project config with normalized metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-test-config-discovery-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests", "nested"), { recursive: true });
    mkdirSync(join(root, "docs", "generated"), { recursive: true });
    writeFileSync(
      join(root, "package.json"),
      `${JSON.stringify(
        {
          private: true,
          scripts: {
            typecheck: "node --check src/*.ts",
            test: "node --test tests/*.test.ts",
            "dry-run": "node scripts/dry-run.ts",
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "tsconfig.json"), "{}\n");
    writeFileSync(join(root, "vitest.config.ts"), "export default {};\n");
    writeFileSync(join(root, "src", "index.ts"), "export const ok = true;\n");
    writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\ntest('ok', () => {});\n");
    writeFileSync(join(root, "tests", "nested", "routing.test.js"), "const test = require('node:test');\ntest('ok', () => {});\n");
    writeFileSync(join(root, "docs", "generated", "verification-output.json"), "{}\n");

    const first = discoverTestAndConfigFiles(root);
    const second = discoverTestAndConfigFiles(root);

    assert.deepEqual(first, second);
    assert.deepEqual(first.schemaVersion, "test-config-discovery.v1");
    assert.deepEqual(
      first.testFiles.map((entry) => ({
        id: entry.id,
        relativePath: entry.relativePath,
        kind: entry.kind,
        extension: entry.extension,
        metadata: entry.metadata,
      })),
      [
        {
          id: "test:tests/nested/routing.test.js",
          relativePath: "tests/nested/routing.test.js",
          kind: "test_file",
          extension: ".js",
          metadata: {
            directory: "tests/nested",
            fileName: "routing.test.js",
            framework: "node:test",
            containsTestDeclaration: true,
          },
        },
        {
          id: "test:tests/orchestrator.test.ts",
          relativePath: "tests/orchestrator.test.ts",
          kind: "test_file",
          extension: ".ts",
          metadata: {
            directory: "tests",
            fileName: "orchestrator.test.ts",
            framework: "node:test",
            containsTestDeclaration: true,
          },
        },
      ],
    );
    assert.deepEqual(
      first.configFiles.map((entry) => ({
        id: entry.id,
        relativePath: entry.relativePath,
        kind: entry.kind,
        extension: entry.extension,
        metadata: entry.metadata,
      })),
      [
        {
          id: "config:package.json",
          relativePath: "package.json",
          kind: "config_file",
          extension: ".json",
          metadata: {
            directory: "",
            fileName: "package.json",
            configType: "package_manifest",
            packageScriptNames: ["dry-run", "test", "typecheck"],
            hasTypecheckScript: true,
            hasTestScript: true,
          },
        },
        {
          id: "config:tsconfig.json",
          relativePath: "tsconfig.json",
          kind: "config_file",
          extension: ".json",
          metadata: {
            directory: "",
            fileName: "tsconfig.json",
            configType: "typescript",
            packageScriptNames: undefined,
            hasTypecheckScript: undefined,
            hasTestScript: undefined,
          },
        },
        {
          id: "config:vitest.config.ts",
          relativePath: "vitest.config.ts",
          kind: "config_file",
          extension: ".ts",
          metadata: {
            directory: "",
            fileName: "vitest.config.ts",
            configType: "vitest",
            packageScriptNames: undefined,
            hasTypecheckScript: undefined,
            hasTestScript: undefined,
          },
        },
      ],
    );
    assert.deepEqual(first.summary, {
      testFileCount: 2,
      configFileCount: 3,
      normalizedPathSeparator: "/",
    });
    assert.equal(first.configFiles.some((entry) => entry.relativePath === "docs/generated/verification-output.json"), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("source discovery excludes generated cache virtual environment and irrelevant directories", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-source-exclusions-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "src", "generated"), { recursive: true });
    mkdirSync(join(root, "src", "__pycache__"), { recursive: true });
    mkdirSync(join(root, "src", ".pytest_cache"), { recursive: true });
    mkdirSync(join(root, "src", ".ruff_cache"), { recursive: true });
    mkdirSync(join(root, "src", ".cache"), { recursive: true });
    mkdirSync(join(root, "src", ".venv"), { recursive: true });
    mkdirSync(join(root, "src", "venv"), { recursive: true });
    mkdirSync(join(root, "src", "node_modules"), { recursive: true });
    mkdirSync(join(root, "src", "dist"), { recursive: true });
    mkdirSync(join(root, "src", "coverage"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "tests", "generated"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "docs", "generated"), { recursive: true });
    writeFileSync(join(root, "src", "kept.ts"), "export const kept = true;\n");
    writeFileSync(join(root, "tests", "kept.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "scripts", "kept.js"), "console.log('kept');\n");
    writeFileSync(join(root, "src", "generated", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", "__pycache__", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", ".pytest_cache", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", ".ruff_cache", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", ".cache", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", ".venv", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", "venv", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", "node_modules", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", "dist", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "src", "coverage", "ignored.ts"), "export const ignored = true;\n");
    writeFileSync(join(root, "tests", "generated", "ignored.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "docs", "generated", "ignored.ts"), "export const ignored = true;\n");

    assert.deepEqual(
      discoverSourceFiles(root).map((entry) => entry.relativePath),
      ["scripts/kept.js", "src/kept.ts", "tests/kept.test.ts"],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("buildReviewEvidencePathArtifact returns stable content-hash evidence for fixed inspected paths", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-review-evidence-paths-"));
  try {
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    writeFileSync(join(root, "src", "orchestrator.ts"), "export const orchestrator = true;\n");
    writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "README.md"), "# AI_Agent\n");

    const fixedPaths = [
      "tests/orchestrator.test.ts",
      "src/orchestrator.ts",
      join(root, "README.md"),
      "src/orchestrator.ts",
    ];
    const first = buildReviewEvidencePathArtifact({ projectRoot: root, paths: fixedPaths });
    const second = buildReviewEvidencePathArtifact({ projectRoot: root, paths: fixedPaths });

    assert.deepEqual(first, second);
    assert.deepEqual(
      first.inspectedPaths.map((entry) => entry.relativePath),
      ["README.md", "src/orchestrator.ts", "tests/orchestrator.test.ts"],
    );
    assert.deepEqual(first.summary, {
      inspectedPathCount: 3,
      hashAlgorithm: "sha256",
    });
    assert.deepEqual(
      first.inspectedPaths.map((entry) => entry.contentHash.algorithm),
      ["sha256", "sha256", "sha256"],
    );
    assert.deepEqual(
      first.inspectedPaths.map((entry) => entry.contentHash.value),
      [
        "56c22c06047fd532d3a389a3e8b76419cb77c7e3e5341dd4ca88816708364c1f",
        "75610148a3b3b2026932427dae5332596d61a33fc058f07def168b8636811a8b",
        "33b61e17e012555c9845638dc903355a4c32b81d58ff533ef6dd201259a7c64e",
      ],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("implementation scanner produces stable README-relevant capability artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-capability-scan-"));
  try {
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
        "## 실행",
        "",
        "```bash",
        "npm test",
        "npm run dry-run",
        "```",
        "",
        "## Public API",
        "",
        "```ts",
        'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
        "```",
        "",
      ].join("\n"),
    );
    writeFileSync(join(root, "package.json"), "{}\n");
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
    writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
    writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");

    const readmeRequirements = parseReadmeDerivedMvpRequirements(readFileSync(join(root, "README.md"), "utf8"));
    const first = buildImplementationCapabilityArtifact({
      inventory: buildInspectionInventory(root),
      readmeRequirements,
    });
    const second = buildImplementationCapabilityArtifact({
      inventory: buildInspectionInventory(root),
      readmeRequirements,
    });

    assert.deepEqual(first, second);
    assert.equal(first.schemaVersion, "implementation-capabilities.v1");
    assert.deepEqual(first.capabilities, [
        {
          id: "request-analysis-work-breakdown",
          requirement: "Analyze user request and decompose it into task_breakdown items.",
          gapDescription:
            "Gap exists when the implementation cannot turn a user request into stable, inspectable task_breakdown items.",
          readmeRequirementIds: ["mvp_goal_flow:001", "mvp_goal_flow:002"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/planning.ts"],
        },
        {
          id: "role-based-routing",
          requirement: "Route work items to OpenClaw owner/finalizer and Hermes reviewer personas.",
          gapDescription:
            "Gap exists when work items are not assigned to explicit job roles for OpenClaw execution and Hermes review.",
          readmeRequirementIds: ["mvp_goal_flow:003", "mvp_goal_flow:004", "mvp_goal_flow:005"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/planning.ts", "existing:src/types.ts"],
        },
        {
          id: "openclaw-hermes-meeting-loop",
          requirement: "Preserve OpenClaw execution and Hermes review turns in a meeting loop.",
          gapDescription:
            "Gap exists when meeting turns are not durably preserved across OpenClaw draft and Hermes review iterations.",
          readmeRequirementIds: ["mvp_goal_flow:003", "mvp_goal_flow:004", "mvp_goal_flow:006"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts"],
        },
        {
          id: "final-synthesis",
          requirement: "Produce final synthesis after reviewer convergence.",
          gapDescription:
            "Gap exists when converged review feedback cannot be converted into one final synthesized output artifact.",
          readmeRequirementIds: ["mvp_goal_flow:005"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/orchestrator.ts"],
        },
        {
          id: "escalation-artifact",
          requirement: "Surface convergence failure or user-decision needs as escalation artifacts.",
          gapDescription:
            "Gap exists when ambiguity, failed convergence, or required user decisions do not produce a structured escalation artifact.",
          readmeRequirementIds: ["operating_principle:002"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/policies.ts"],
        },
        {
          id: "raw-storage-summary-context",
          requirement: "Separate raw full-text storage from exposed loop summaries and compressed context.",
          gapDescription:
            "Gap exists when raw full text is exposed directly to loop context instead of bounded summaries and compressed context.",
          readmeRequirementIds: ["mvp_goal_flow:006", "operating_principle:001"],
          gapDetected: false,
          status: "implemented",
          evidenceSourceIds: ["existing:src/db.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
        },
    ]);
    assert.deepEqual(first.summary, {
      implementedCount: 6,
      missingCount: 0,
      readmeRequirementCount: 12,
      readmeRequirementStatusCounts: {
        covered: 8,
        partial: 0,
        missing: 0,
        unknown: 4,
      },
      requirementCapabilityMatchStatusCounts: {
        matched: 8,
        partial: 0,
        missing: 4,
      },
    });
    assert.deepEqual(
      first.readmeRequirementMappings.map((requirement) => [requirement.id, requirement.status]),
      [
        ["mvp_goal_flow:001", "covered"],
        ["mvp_goal_flow:002", "covered"],
        ["mvp_goal_flow:003", "covered"],
        ["mvp_goal_flow:004", "covered"],
        ["mvp_goal_flow:005", "covered"],
        ["mvp_goal_flow:006", "covered"],
        ["operating_principle:001", "covered"],
        ["operating_principle:002", "covered"],
        ["execution_command:001", "unknown"],
        ["execution_command:002", "unknown"],
        ["public_api_symbol:001", "unknown"],
        ["public_api_symbol:002", "unknown"],
      ],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("gap mapping produces exactly one entry for each README-required MVP area", () => {
  const readmeRequirements = parseReadmeDerivedMvpRequirements(
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
      "- OpenClaw = orchestrator / owner / finalizer",
      "- Hermes = reviewer-only, mention/reply when requested",
      "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
      "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
      "",
      "## 실행",
      "",
      "```bash",
      "npm test",
      "npm run dry-run",
      "```",
      "",
      "## Public API",
      "",
      "```ts",
      'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
      "```",
      "",
    ].join("\n"),
  );
  const inventory = [
    {
      id: "existing:src/db.ts",
      relativePath: "src/db.ts",
      kind: "source" as const,
      moduleName: "src.db",
    },
    {
      id: "existing:src/orchestrator.ts",
      relativePath: "src/orchestrator.ts",
      kind: "source" as const,
      moduleName: "src.orchestrator",
    },
    {
      id: "existing:src/planning.ts",
      relativePath: "src/planning.ts",
      kind: "source" as const,
      moduleName: "src.planning",
    },
    {
      id: "existing:src/policies.ts",
      relativePath: "src/policies.ts",
      kind: "source" as const,
      moduleName: "src.policies",
    },
    {
      id: "existing:src/types.ts",
      relativePath: "src/types.ts",
      kind: "source" as const,
      moduleName: "src.types",
    },
  ];

  const artifact = buildImplementationCapabilityArtifact({ inventory, readmeRequirements });
  const requiredMvpAreas = [
    "request-analysis-work-breakdown",
    "role-based-routing",
    "openclaw-hermes-meeting-loop",
    "final-synthesis",
    "escalation-artifact",
    "raw-storage-summary-context",
  ];
  const mappedMvpAreas = artifact.capabilities.map((capability) => capability.id);

  assert.deepEqual(mappedMvpAreas, requiredMvpAreas);
  assert.equal(new Set(mappedMvpAreas).size, requiredMvpAreas.length);
  assert.equal(artifact.capabilities.length, requiredMvpAreas.length);
  for (const area of requiredMvpAreas) {
    assert.equal(
      artifact.capabilities.filter((capability) => capability.id === area).length,
      1,
      `${area} should have exactly one gap-mapping entry`,
    );
  }
});

test("requirement-gap mapping validation and diagnosis rendering return stable success-path output", () => {
  const readmeRequirements = parseReadmeDerivedMvpRequirements(
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
  const artifact = buildImplementationCapabilityArtifact({
    inventory: [
      { id: "existing:src/db.ts", relativePath: "src/db.ts", kind: "source", moduleName: "src.db" },
      { id: "existing:src/orchestrator.ts", relativePath: "src/orchestrator.ts", kind: "source", moduleName: "src.orchestrator" },
      { id: "existing:src/planning.ts", relativePath: "src/planning.ts", kind: "source", moduleName: "src.planning" },
      { id: "existing:src/policies.ts", relativePath: "src/policies.ts", kind: "source", moduleName: "src.policies" },
      { id: "existing:src/types.ts", relativePath: "src/types.ts", kind: "source", moduleName: "src.types" },
    ],
    readmeRequirements,
  });

  const validation = validateRequirementGapMappingArtifact(artifact);
  const section = renderRequirementGapMappingSection(artifact);
  const diagnosis = renderDiagnosisReportWithRequirementGapSection({
    markdown: "# AI_Agent MVP Diagnosis\n\n## Requirement-to-Gap Mapping\n\nstale section\n\n## Decision\n\npartial redesign\n",
    mapping: artifact,
  });

  assert.deepEqual(validation, {
    valid: true,
    errors: [],
    computed: {
      readmeRequirementCount: artifact.readmeRequirementMappings.length,
      readmeRequirementStatusCounts: artifact.summary.readmeRequirementStatusCounts,
    },
  });
  assert.match(section, /^## Requirement-to-Gap Mapping/);
  assert.match(section, /\| Requirement \| Status \| Capabilities \| Evidence \| Gap \|/);
  assert.match(section, /Mapped requirements: 8\./);
  assert.equal(diagnosis.includes("stale section"), false);
  assert.match(diagnosis, /## Decision\n\npartial redesign/);
});

test("gap mapping does not duplicate or overlap mapped README requirements across MVP areas", () => {
  const readmeRequirements = parseReadmeDerivedMvpRequirements(
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
      "- OpenClaw = orchestrator / owner / finalizer",
      "- Hermes = reviewer-only, mention/reply when requested",
      "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
      "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
      "",
      "## 실행",
      "",
      "```bash",
      "npm test",
      "```",
      "",
    ].join("\n"),
  );
  const artifact = buildImplementationCapabilityArtifact({
    inventory: [],
    readmeRequirements,
  });
  const requirementsByArea = new Map(
    artifact.capabilities.map((capability) => [
      capability.id,
      capability.requirement.trim().replace(/\s+/g, " ").toLowerCase(),
    ]),
  );
  const areasByRequirement = new Map<string, string[]>();

  for (const [area, requirement] of requirementsByArea) {
    assert.notEqual(requirement, "", `${area} should map to a README requirement`);
    areasByRequirement.set(requirement, [...(areasByRequirement.get(requirement) ?? []), area]);
  }

  assert.equal(requirementsByArea.size, artifact.capabilities.length);
  for (const [requirement, areas] of areasByRequirement) {
    assert.deepEqual(areas, [areas[0]], `${requirement} is mapped across overlapping MVP areas: ${areas.join(", ")}`);
  }
  assert.equal(areasByRequirement.size, artifact.capabilities.length);
});

test("requirement-gap report rendering rejects invalid mapped requirement evidence", () => {
  const artifact = buildImplementationCapabilityArtifact({
    inventory: [
      { id: "existing:src/planning.ts", relativePath: "src/planning.ts", kind: "source", moduleName: "src.planning" },
    ],
    readmeRequirements: {
      mvpGoalFlow: ["parent channel user request"],
      operatingPrinciples: [],
      executionCommands: [],
      publicApiSymbols: [],
    },
  });
  const invalidArtifact = {
    ...artifact,
    summary: {
      ...artifact.summary,
      readmeRequirementCount: artifact.summary.readmeRequirementCount + 1,
    },
  };

  const validation = validateRequirementGapMappingArtifact(invalidArtifact);

  assert.equal(validation.valid, false);
  assert.deepEqual(validation.errors, ["summary.readmeRequirementCount must match readmeRequirementMappings length"]);
  assert.throws(
    () => renderRequirementGapMappingSection(invalidArtifact),
    /requirement-gap mapping is invalid: summary\.readmeRequirementCount must match readmeRequirementMappings length/,
  );
});

test("gap mapping assigns current implementation status to every mapped README requirement", () => {
  const readmeRequirements = parseReadmeDerivedMvpRequirements(
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
      "## 실행",
      "",
      "```bash",
      "npm test",
      "```",
      "",
    ].join("\n"),
  );
  const inventory = [
    {
      id: "existing:src/planning.ts",
      relativePath: "src/planning.ts",
      kind: "source" as const,
      moduleName: "src.planning",
    },
  ];

  const artifact = buildImplementationCapabilityArtifact({ inventory, readmeRequirements });
  const statusesByRequirement = new Map(
    artifact.capabilities.map((capability) => [capability.id, capability.status]),
  );

  assert.equal(statusesByRequirement.size, artifact.capabilities.length);
  assert.equal(artifact.capabilities.every((capability) => capability.status === "implemented" || capability.status === "missing"), true);
  assert.deepEqual(Object.fromEntries(statusesByRequirement), {
    "request-analysis-work-breakdown": "implemented",
    "role-based-routing": "implemented",
    "openclaw-hermes-meeting-loop": "missing",
    "final-synthesis": "missing",
    "escalation-artifact": "missing",
    "raw-storage-summary-context": "implemented",
  });
  assert.deepEqual(artifact.summary, {
    implementedCount: 3,
    missingCount: 3,
    readmeRequirementCount: 9,
      readmeRequirementStatusCounts: {
        covered: 3,
        partial: 4,
        missing: 1,
        unknown: 1,
      },
      requirementCapabilityMatchStatusCounts: {
        matched: 3,
        partial: 4,
        missing: 2,
      },
    });
  assert.deepEqual(
    Object.fromEntries(
      artifact.requirementCapabilityMatches.map((match) => [match.requirementId, match.status]),
    ),
    {
      "mvp_goal_flow:001": "matched",
      "mvp_goal_flow:002": "matched",
      "mvp_goal_flow:003": "partial",
      "mvp_goal_flow:004": "partial",
      "mvp_goal_flow:005": "partial",
      "mvp_goal_flow:006": "partial",
      "operating_principle:001": "matched",
      "operating_principle:002": "missing",
      "execution_command:001": "missing",
    },
  );
  assert.deepEqual(
    artifact.readmeRequirementMappings.map((requirement) => [requirement.id, requirement.status]),
    [
      ["mvp_goal_flow:001", "covered"],
      ["mvp_goal_flow:002", "covered"],
      ["mvp_goal_flow:003", "partial"],
      ["mvp_goal_flow:004", "partial"],
      ["mvp_goal_flow:005", "partial"],
      ["mvp_goal_flow:006", "partial"],
      ["operating_principle:001", "covered"],
      ["operating_principle:002", "missing"],
      ["execution_command:001", "unknown"],
    ],
  );
});

test("gap mapping assigns a non-empty gap description to every mapped README requirement", () => {
  const readmeRequirements = parseReadmeDerivedMvpRequirements(
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

  const artifact = buildImplementationCapabilityArtifact({
    inventory: [],
    readmeRequirements,
  });

  assert.equal(artifact.capabilities.length, 6);
  for (const capability of artifact.capabilities) {
    assert.notEqual(capability.requirement.trim(), "");
    assert.notEqual(capability.gapDescription.trim(), "");
    assert.doesNotMatch(capability.gapDescription, /\b(tbd|todo|n\/a|placeholder)\b/i);
  }
});

test("loadDiagnosisReportArtifact produces a stable artifact with original README-derived MVP requirements", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-report-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
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
        "  -> Discord thread 생성",
        "  -> OpenClaw owner draft",
        "  -> Hermes review",
        "  -> OpenClaw final synthesis",
        "```",
        "",
        "## 운영 원칙",
        "",
        "- Channel = project",
        "- Thread = task",
        "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
        "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
        "",
        "## 실행",
        "",
        "```bash",
        "npm test",
        "npm run dry-run",
        "```",
        "",
        "## Public API",
        "",
        "```ts",
        'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
        "```",
        "",
      ].join("\n"),
    );
    writeFileSync(
      join(root, "docs", "diagnosis-report.md"),
      [
        "# AI_Agent MVP Diagnosis",
        "",
        "Decision evidence artifact: `docs/review-evidence.json`.",
        "",
        "Recommendation: **partial redesign**.",
        "",
      ].join("\n"),
    );

    const artifact = loadDiagnosisReportArtifact({ projectRoot: root });

    assert.equal(artifact.schemaVersion, "diagnosis-report.v1");
    assert.deepEqual(artifact.source, {
      readmePath: join(root, "README.md"),
      diagnosisReportPath: join(root, "docs", "diagnosis-report.md"),
      externalReadmePath: "C:\\Users\\KBM\\Downloads\\260526_README.md",
      externalReadmeAccessible: false,
    });
    assert.deepEqual(artifact.readmeDerivedMvpRequirements, {
      mvpGoalFlow: [
        "parent channel user request",
        "-> task 생성",
        "-> Discord thread 생성",
        "-> OpenClaw owner draft",
        "-> Hermes review",
        "-> OpenClaw final synthesis",
      ],
      operatingPrinciples: [
        "Channel = project",
        "Thread = task",
        "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
        "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
      ],
      executionCommands: ["npm test", "npm run dry-run"],
      publicApiSymbols: ["AiAgentDatabase", "CompanyOrchestrator"],
    });
    assert.deepEqual(artifact.diagnosis, {
      decision: "partial_redesign",
      decisionEvidenceArtifact: "docs/review-evidence.json",
    });
    assert.deepEqual(
      artifact.requirementToGapMappingArtifact.capabilities.map((capability) => [capability.id, capability.status]),
      [
        ["request-analysis-work-breakdown", "missing"],
        ["role-based-routing", "missing"],
        ["openclaw-hermes-meeting-loop", "missing"],
        ["final-synthesis", "missing"],
        ["escalation-artifact", "missing"],
        ["raw-storage-summary-context", "missing"],
      ],
    );
    assert.deepEqual(artifact.requirementToGapMappingArtifact.summary, {
      implementedCount: 0,
      missingCount: 6,
      readmeRequirementCount: 14,
      readmeRequirementStatusCounts: {
        covered: 0,
        partial: 0,
        missing: 7,
        unknown: 7,
      },
      requirementCapabilityMatchStatusCounts: {
        matched: 0,
        partial: 0,
        missing: 14,
      },
    });
    assert.deepEqual(
      artifact.requirementToGapMappingArtifact.readmeRequirementMappings.map((requirement) => [requirement.id, requirement.status]),
      [
        ["mvp_goal_flow:001", "missing"],
        ["mvp_goal_flow:002", "missing"],
        ["mvp_goal_flow:003", "unknown"],
        ["mvp_goal_flow:004", "missing"],
        ["mvp_goal_flow:005", "missing"],
        ["mvp_goal_flow:006", "missing"],
        ["operating_principle:001", "unknown"],
        ["operating_principle:002", "unknown"],
        ["operating_principle:003", "missing"],
        ["operating_principle:004", "missing"],
        ["execution_command:001", "unknown"],
        ["execution_command:002", "unknown"],
        ["public_api_symbol:001", "unknown"],
        ["public_api_symbol:002", "unknown"],
      ],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("diagnosis report artifact output contains requirement-to-gap mapping artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-diagnosis-gap-map-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
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
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
    writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
    writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");

    const artifact = loadDiagnosisReportArtifact({ projectRoot: root });
    const gapMapping = artifact.requirementToGapMappingArtifact;

    assert.equal(gapMapping.schemaVersion, "implementation-capabilities.v1");
    assert.deepEqual(
      gapMapping.capabilities.map((capability) => ({
        id: capability.id,
        requirement: capability.requirement,
        gapDescription: capability.gapDescription,
        status: capability.status,
      })),
      [
        {
          id: "request-analysis-work-breakdown",
          requirement: "Analyze user request and decompose it into task_breakdown items.",
          gapDescription:
            "Gap exists when the implementation cannot turn a user request into stable, inspectable task_breakdown items.",
          status: "implemented",
        },
        {
          id: "role-based-routing",
          requirement: "Route work items to OpenClaw owner/finalizer and Hermes reviewer personas.",
          gapDescription:
            "Gap exists when work items are not assigned to explicit job roles for OpenClaw execution and Hermes review.",
          status: "implemented",
        },
        {
          id: "openclaw-hermes-meeting-loop",
          requirement: "Preserve OpenClaw execution and Hermes review turns in a meeting loop.",
          gapDescription:
            "Gap exists when meeting turns are not durably preserved across OpenClaw draft and Hermes review iterations.",
          status: "implemented",
        },
        {
          id: "final-synthesis",
          requirement: "Produce final synthesis after reviewer convergence.",
          gapDescription:
            "Gap exists when converged review feedback cannot be converted into one final synthesized output artifact.",
          status: "implemented",
        },
        {
          id: "escalation-artifact",
          requirement: "Surface convergence failure or user-decision needs as escalation artifacts.",
          gapDescription:
            "Gap exists when ambiguity, failed convergence, or required user decisions do not produce a structured escalation artifact.",
          status: "implemented",
        },
        {
          id: "raw-storage-summary-context",
          requirement: "Separate raw full-text storage from exposed loop summaries and compressed context.",
          gapDescription:
            "Gap exists when raw full text is exposed directly to loop context instead of bounded summaries and compressed context.",
          status: "implemented",
        },
      ],
    );
    assert.deepEqual(gapMapping.summary, {
      implementedCount: 6,
      missingCount: 0,
      readmeRequirementCount: 8,
      readmeRequirementStatusCounts: {
        covered: 8,
        partial: 0,
        missing: 0,
        unknown: 0,
      },
      requirementCapabilityMatchStatusCounts: {
        matched: 8,
        partial: 0,
        missing: 0,
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("parseReadmeDerivedMvpRequirements returns empty stable fields when README sections are absent", () => {
  assert.deepEqual(parseReadmeDerivedMvpRequirements("# Minimal\n"), {
    mvpGoalFlow: [],
    operatingPrinciples: [],
    executionCommands: [],
    publicApiSymbols: [],
  });
});

test("parseReadmeMvpRequirements and validator accept structured README MVP requirements", () => {
  const readme = [
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
    "## 실행",
    "",
    "```bash",
    "npm test",
    "npm run dry-run",
    "```",
    "",
    "## Public API",
    "",
    "```ts",
    'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
    "```",
    "",
  ].join("\n");

  const extraction = parseReadmeMvpRequirements(readme);
  const validation = validateReadmeMvpRequirementExtraction(extraction);

  assert.deepEqual(extraction.source.sections, ["## MVP 목표", "## 운영 원칙", "## 실행", "## Public API"]);
  assert.deepEqual(extraction.summary, {
    totalCount: 12,
    countByCategory: {
      mvp_goal_flow: 6,
      operating_principle: 2,
      execution_command: 2,
      public_api_symbol: 2,
    },
  });
  assert.deepEqual(
    extraction.requirements.map((requirement) => [requirement.id, requirement.text]),
    [
      ["mvp_goal_flow:001", "parent channel user request"],
      ["mvp_goal_flow:002", "-> task 생성"],
      ["mvp_goal_flow:003", "-> OpenClaw owner draft"],
      ["mvp_goal_flow:004", "-> Hermes review"],
      ["mvp_goal_flow:005", "-> OpenClaw final synthesis"],
      ["mvp_goal_flow:006", "-> thread timeline 게시"],
      ["operating_principle:001", "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다."],
      ["operating_principle:002", "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다."],
      ["execution_command:001", "npm test"],
      ["execution_command:002", "npm run dry-run"],
      ["public_api_symbol:001", "AiAgentDatabase"],
      ["public_api_symbol:002", "CompanyOrchestrator"],
    ],
  );
  assert.deepEqual(validation, {
    valid: true,
    errors: [],
    computed: {
      totalCount: 12,
      countByCategory: {
        mvp_goal_flow: 6,
        operating_principle: 2,
        execution_command: 2,
        public_api_symbol: 2,
      },
      sections: ["## MVP 목표", "## 운영 원칙", "## 실행", "## Public API"],
    },
  });
});

test("README requirements extractor returns a stable structured MVP requirement list", () => {
  const readme = [
    "# AI_Agent",
    "",
    "Discord 기반 Virtual AI Company orchestration core.",
    "",
    "## MVP 목표",
    "",
    "```text",
    "parent channel user request",
    "  -> task 생성",
    "  -> Discord thread 생성",
    '  -> parent에는 "Agent discussion started -> <thread>"만 게시',
    "  -> OpenClaw owner draft",
    "  -> Hermes reviewer request",
    "  -> Hermes review",
    "  -> OpenClaw final synthesis",
    "  -> thread timeline 게시",
    "```",
    "",
    "## 운영 원칙",
    "",
    "- Channel = project",
    "- Thread = task",
    "- OpenClaw = orchestrator / owner / finalizer",
    "- Hermes = reviewer-only, mention/reply when requested",
    "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
    "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
    "",
    "## 실행",
    "",
    "```bash",
    "npm test",
    "npm run dry-run",
    "```",
    "",
    "## Public API",
    "",
    "```ts",
    'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
    "```",
    "",
  ].join("\n");

  const first = extractReadmeMvpRequirementList(readme);
  const second = extractReadmeMvpRequirementList(readme);

  assert.deepEqual(first, second);
  assert.deepEqual(first, [
    {
      id: "mvp_goal_flow:001",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 1,
      text: "parent channel user request",
    },
    {
      id: "mvp_goal_flow:002",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 2,
      text: "-> task 생성",
    },
    {
      id: "mvp_goal_flow:003",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 3,
      text: "-> Discord thread 생성",
    },
    {
      id: "mvp_goal_flow:004",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 4,
      text: '-> parent에는 "Agent discussion started -> <thread>"만 게시',
    },
    {
      id: "mvp_goal_flow:005",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 5,
      text: "-> OpenClaw owner draft",
    },
    {
      id: "mvp_goal_flow:006",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 6,
      text: "-> Hermes reviewer request",
    },
    {
      id: "mvp_goal_flow:007",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 7,
      text: "-> Hermes review",
    },
    {
      id: "mvp_goal_flow:008",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 8,
      text: "-> OpenClaw final synthesis",
    },
    {
      id: "mvp_goal_flow:009",
      category: "mvp_goal_flow",
      sourceSection: "## MVP 목표",
      order: 9,
      text: "-> thread timeline 게시",
    },
    {
      id: "operating_principle:001",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 1,
      text: "Channel = project",
    },
    {
      id: "operating_principle:002",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 2,
      text: "Thread = task",
    },
    {
      id: "operating_principle:003",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 3,
      text: "OpenClaw = orchestrator / owner / finalizer",
    },
    {
      id: "operating_principle:004",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 4,
      text: "Hermes = reviewer-only, mention/reply when requested",
    },
    {
      id: "operating_principle:005",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 5,
      text: "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
    },
    {
      id: "operating_principle:006",
      category: "operating_principle",
      sourceSection: "## 운영 원칙",
      order: 6,
      text: "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
    },
    {
      id: "execution_command:001",
      category: "execution_command",
      sourceSection: "## 실행",
      order: 1,
      text: "npm test",
    },
    {
      id: "execution_command:002",
      category: "execution_command",
      sourceSection: "## 실행",
      order: 2,
      text: "npm run dry-run",
    },
    {
      id: "public_api_symbol:001",
      category: "public_api_symbol",
      sourceSection: "## Public API",
      order: 1,
      text: "AiAgentDatabase",
    },
    {
      id: "public_api_symbol:002",
      category: "public_api_symbol",
      sourceSection: "## Public API",
      order: 2,
      text: "CompanyOrchestrator",
    },
  ]);
});

test("extractReviewFindings converts inspected modules into structured review findings", () => {
  const findings = extractReviewFindings([
    {
      id: "existing:src/meeting-loop.ts",
      relativePath: "src/meeting-loop.ts",
      kind: "source",
      moduleName: "src.meeting-loop",
      content: [
        "export function runLoop(fullContent: string) {",
        "  // TODO: compress visible loop context",
        "  return fullContent;",
        "}",
      ].join("\n"),
    },
    {
      id: "existing:tests/meeting-loop.test.ts",
      relativePath: "tests/meeting-loop.test.ts",
      kind: "test",
      moduleName: "tests.meeting-loop.test",
      content: "import test from 'node:test';\n",
    },
  ]);

  assert.deepEqual(
    findings.map((finding) => ({
      id: finding.id,
      sourceId: finding.sourceId,
      relativePath: finding.relativePath,
      severity: finding.severity,
      category: finding.category,
      title: finding.title,
    })),
    [
      {
        id: "finding:existing:src/meeting-loop.ts:missing-test",
        sourceId: "existing:src/meeting-loop.ts",
        relativePath: "src/meeting-loop.ts",
        severity: "high",
        category: "error_frequency",
        title: "Source module has no observable test coverage",
      },
      {
        id: "finding:existing:src/meeting-loop.ts:open-marker",
        sourceId: "existing:src/meeting-loop.ts",
        relativePath: "src/meeting-loop.ts",
        severity: "medium",
        category: "maintainability",
        title: "Open implementation marker remains in inspected code",
      },
      {
        id: "finding:existing:src/meeting-loop.ts:raw-content-exposure",
        sourceId: "existing:src/meeting-loop.ts",
        relativePath: "src/meeting-loop.ts",
        severity: "medium",
        category: "token_cost",
        title: "Raw content handling needs explicit summary boundary",
      },
    ],
  );
  assert.match(findings[1].evidence, /to-do/);
  assert.match(findings[2].recommendation, /bounded summaries/);
});

test("writeReviewEvidenceArtifact persists stable combined inspection evidence", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-evidence-"));
  try {
    const inventory = [
      {
        id: "existing:src/b.ts",
        relativePath: "src/b.ts",
        kind: "source" as const,
        moduleName: "src.b",
      },
      {
        id: "existing:README.md",
        relativePath: "README.md",
        kind: "doc" as const,
        moduleName: "README",
      },
    ];
    const findings = [
      {
        id: "finding:existing:src/b.ts:raw-content-exposure",
        sourceId: "existing:src/b.ts",
        relativePath: "src/b.ts",
        moduleName: "src.b",
        severity: "medium" as const,
        category: "token_cost" as const,
        title: "Raw content handling needs explicit summary boundary",
        evidence: "return fullContent;",
        recommendation: "Expose only bounded summaries to loop prompts.",
      },
      {
        id: "finding:existing:src/b.ts:missing-test",
        sourceId: "existing:src/b.ts",
        relativePath: "src/b.ts",
        moduleName: "src.b",
        severity: "high" as const,
        category: "error_frequency" as const,
        title: "Source module has no observable test coverage",
        evidence: "No test reference was detected for this source module.",
        recommendation: "Add a focused runnable test.",
      },
    ];
    const outputPath = join(root, "artifacts", "review-evidence.json");

    const artifact = writeReviewEvidenceArtifact({ outputPath, inventory, findings });
    const persisted = JSON.parse(readFileSync(outputPath, "utf8"));

    assert.deepEqual(persisted, artifact);
    assert.deepEqual(
      persisted.inventory.map((entry: { id: string }) => entry.id),
      ["existing:README.md", "existing:src/b.ts"],
    );
    assert.deepEqual(
      persisted.findings.map((finding: { id: string }) => finding.id),
      ["finding:existing:src/b.ts:missing-test", "finding:existing:src/b.ts:raw-content-exposure"],
    );
    assert.deepEqual(persisted.summary, {
      inspectedModules: 2,
      findingCount: 2,
      findingsBySeverity: {
        critical: 0,
        high: 1,
        medium: 1,
        low: 0,
      },
      findingsByCategory: {
        error_frequency: 1,
        maintainability: 0,
        token_cost: 1,
        architecture_fit: 0,
        feature_completeness: 0,
      },
      recommendation: "partial_redesign",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("handlePriorReviewArtifact resolves a prior review artifact and returns an observable runnable response", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-prior-review-"));
  try {
    const outputPath = join(root, "docs", "review-evidence.json");
    const artifact = writeReviewEvidenceArtifact({
      outputPath,
      inventory: [
        {
          id: "existing:src/orchestrator.ts",
          relativePath: "src/orchestrator.ts",
          kind: "source",
          moduleName: "src.orchestrator",
        },
      ],
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
          recommendation: "Add a focused runnable test before using this module in redesign decisions.",
        },
      ],
    });

    assert.equal(resolvePriorReviewArtifactIdentifier({ identifier: "review-evidence", projectRoot: root }), outputPath);

    const response = handlePriorReviewArtifact({ identifier: "review-evidence", projectRoot: root });

    assert.equal(response.command, "ai-agent prior-review");
    assert.deepEqual(response.artifact, {
      identifier: "review-evidence",
      path: outputPath,
      schemaVersion: "review-evidence.v1",
      recommendation: artifact.summary.recommendation,
      inspectedModules: 1,
      findingCount: 1,
    });
    assert.deepEqual(response.decisionBasis, {
      priorReviewArtifactPath: outputPath,
      recommendation: "partial_redesign",
    });
    assert.deepEqual(response.validation, { valid: true, missingFields: [] });
    assert.deepEqual(response.completeness, { complete: true, missingFields: [], insufficientContent: [] });
    assert.equal(response.decisionGate.accepted, true);
    assert.equal(response.escalation, undefined);
    assert.match(response.runnable.dryRunCommand, /--prior-review-artifact/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("emitGovernedRecommendation blocks redesign recommendations until review evidence is created", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test.",
      },
    ],
  });

  assert.equal(artifact.summary.recommendation, "partial_redesign");
  assert.throws(
    () => emitGovernedRecommendation({ artifact, evidenceArtifactCreated: false }),
    /Review evidence artifact must be created before emitting a redesign recommendation/,
  );
});

test("buildGovernedRecommendationDecision marks redesign output incomplete before review evidence is present", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test before using this module in redesign decisions.",
      },
    ],
  });

  const decision = buildGovernedRecommendationDecision({
    artifact,
    evidenceArtifactCreated: false,
  });

  assert.equal(artifact.summary.recommendation, "partial_redesign");
  assert.equal(decision.status, "incomplete");
  assert.equal(decision.recommendation, undefined);
  assert.equal(decision.blockedRecommendation, "partial_redesign");
  assert.deepEqual(decision.incompleteReasons, ["review_evidence_artifact_not_created"]);
  assert.equal(decision.decisionGate.accepted, false);
});

test("validatePriorReviewEvidenceForRedesignDecision reports missing required evidence fields", () => {
  const validation = validatePriorReviewEvidenceForRedesignDecision({
    schemaVersion: "review-evidence.v1",
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        kind: "source",
        moduleName: "",
      },
    ],
    findings: [
      {
        id: "finding:existing:src/orchestrator.ts:missing-test",
        sourceId: "",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "high",
        category: "error_frequency",
        title: "Source module has no observable test coverage",
        evidence: "",
      },
    ],
    summary: {
      inspectedModules: 1,
      findingCount: 1,
      findingsBySeverity: {
        high: 1,
      },
      recommendation: "partial_redesign",
    },
  });

  assert.deepEqual(validation, {
    valid: false,
    missingFields: [
      "inventory[0].relativePath",
      "inventory[0].moduleName",
      "findings[0].sourceId",
      "findings[0].evidence",
      "findings[0].recommendation",
      "summary.findingsByCategory",
    ],
  });
});

test("validatePriorReviewEvidenceForRedesignDecision accepts complete redesign evidence artifact", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test.",
      },
    ],
  });

  assert.deepEqual(validatePriorReviewEvidenceForRedesignDecision(artifact), {
    valid: true,
    missingFields: [],
  });
});

test("checkPriorReviewEvidenceCompletenessForRedesignDecision reports incomplete and insufficient evidence content", () => {
  const completeness = checkPriorReviewEvidenceCompletenessForRedesignDecision({
    schemaVersion: "review-evidence.v1",
    inventory: [],
    findings: [
      {
        id: "finding:existing:src/orchestrator.ts:missing-test",
        sourceId: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "urgent",
        category: "guesswork",
        title: "TBD",
        evidence: "placeholder",
        recommendation: "fix",
      },
      {
        id: "finding:existing:src/planning.ts:token-strategy",
        sourceId: "existing:src/planning.ts",
        relativePath: "src/planning.ts",
        moduleName: "src.planning",
        severity: "medium",
        category: "token_cost",
        title: "Token strategy lacks durable raw storage boundary",
        evidence: "The module passes full request text into loop-visible context without a compressed summary handoff.",
        recommendation: "Persist raw text separately and expose bounded summaries to meeting turns.",
      },
    ],
    summary: {
      inspectedModules: 0,
      findingCount: 2,
      findingsBySeverity: {
        critical: 0,
        high: 0,
        medium: 1,
        low: 0,
      },
      findingsByCategory: {
        error_frequency: 0,
        maintainability: 0,
        token_cost: 1,
        architecture_fit: 0,
        feature_completeness: 0,
      },
      recommendation: "partial_redesign",
    },
  });

  assert.deepEqual(completeness, {
    complete: false,
    missingFields: [],
    insufficientContent: [
      "inventory[0]",
      "findings[0].title",
      "findings[0].evidence",
      "findings[0].recommendation",
      "findings[0].category",
      "findings[0].severity",
    ],
  });
});

test("checkPriorReviewEvidenceCompletenessForRedesignDecision accepts complete redesign evidence content", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test before using this module in redesign decisions.",
      },
    ],
  });

  assert.deepEqual(checkPriorReviewEvidenceCompletenessForRedesignDecision(artifact), {
    complete: true,
    missingFields: [],
    insufficientContent: [],
  });
});

test("gateRedesignDecision rejects redesign decisions when prior review evidence is missing", () => {
  assert.deepEqual(
    gateRedesignDecision({
      recommendation: "partial_redesign",
      evidenceArtifactCreated: false,
    }),
    {
      accepted: false,
      reasons: ["review_evidence_artifact_not_created", "prior_review_evidence_missing", "prior_review_evidence_incomplete"],
      reviewRequired: false,
      reviewReasons: [],
      missingFields: ["schemaVersion", "inventory", "findings", "summary"],
      insufficientContent: [],
    },
  );
});

test("gateRedesignDecision rejects redesign decisions when prior review evidence is incomplete", () => {
  const artifact = {
    schemaVersion: "review-evidence.v1",
    inventory: [],
    findings: [],
    summary: {
      inspectedModules: 0,
      findingCount: 0,
      findingsBySeverity: {},
      findingsByCategory: {},
      recommendation: "full_replan",
    },
  };

  assert.deepEqual(
    gateRedesignDecision({
      recommendation: "full_replan",
      priorReviewEvidence: artifact,
      evidenceArtifactCreated: true,
    }),
    {
      accepted: false,
      reasons: ["prior_review_evidence_incomplete"],
      reviewRequired: true,
      reviewReasons: ["prior_review_evidence_incomplete_but_present"],
      missingFields: ["findings[0]"],
      insufficientContent: ["inventory[0]"],
    },
  );
});

test("gateRedesignDecision flags redesign decisions for review when prior review evidence is incomplete but present", () => {
  const artifact = {
    schemaVersion: "review-evidence.v1",
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
    findings: [
      {
        id: "finding:existing:src/orchestrator.ts:missing-test",
        sourceId: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "high",
        category: "error_frequency",
        title: "TBD",
        evidence: "placeholder",
        recommendation: "fix",
      },
    ],
    summary: {
      inspectedModules: 1,
      findingCount: 1,
      findingsBySeverity: {
        critical: 0,
        high: 1,
        medium: 0,
        low: 0,
      },
      findingsByCategory: {
        error_frequency: 1,
        maintainability: 0,
        token_cost: 0,
        architecture_fit: 0,
        feature_completeness: 0,
      },
      recommendation: "partial_redesign",
    },
  };

  const gate = gateRedesignDecision({
    recommendation: "partial_redesign",
    priorReviewEvidence: artifact,
    evidenceArtifactCreated: true,
  });

  assert.equal(gate.accepted, false);
  assert.equal(gate.reviewRequired, true);
  assert.deepEqual(gate.reviewReasons, ["prior_review_evidence_incomplete_but_present"]);
  assert.deepEqual(gate.reasons, ["prior_review_evidence_incomplete"]);
  assert.deepEqual(gate.missingFields, []);
  assert.deepEqual(gate.insufficientContent, ["findings[0].title", "findings[0].evidence", "findings[0].recommendation"]);
});

test("gateRedesignDecision accepts redesign decisions only after complete prior review evidence", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test before using this module in redesign decisions.",
      },
    ],
  });

  assert.deepEqual(
    gateRedesignDecision({
      recommendation: "partial_redesign",
      priorReviewEvidence: artifact,
      evidenceArtifactCreated: true,
    }),
    {
      accepted: true,
      reasons: [],
      reviewRequired: false,
      reviewReasons: [],
      missingFields: [],
      insufficientContent: [],
    },
  );
});

test("gateRedesignDecision accepts redesign recommendations after all required review artifacts are complete", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test before using this module in redesign decisions.",
      },
    ],
  });

  const gate = gateRedesignDecision({
    recommendation: "partial_redesign",
    priorReviewEvidence: artifact,
    evidenceArtifactCreated: true,
    completedReviewArtifacts: {
      status: "passed",
      summary: {
        missingArtifactIds: [],
        incompleteArtifactIds: [],
      },
    },
  });

  assert.deepEqual(gate, {
    accepted: true,
    reasons: [],
    reviewRequired: false,
    reviewReasons: [],
    missingFields: [],
    insufficientContent: [],
  });
});

test("gateRedesignDecision rejects redesign recommendations when required review artifacts are incomplete", () => {
  const artifact = buildReviewEvidenceArtifact({
    inventory: [
      {
        id: "existing:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        kind: "source",
        moduleName: "src.orchestrator",
      },
    ],
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
        recommendation: "Add a focused runnable test before using this module in redesign decisions.",
      },
    ],
  });

  const gate = gateRedesignDecision({
    recommendation: "partial_redesign",
    priorReviewEvidence: artifact,
    evidenceArtifactCreated: true,
    completedReviewArtifacts: {
      status: "failed",
      summary: {
        missingArtifactIds: ["refactoring_plan_markdown"],
        incompleteArtifactIds: [],
      },
    },
  });

  assert.deepEqual(gate, {
    accepted: false,
    reasons: ["completed_review_artifacts_incomplete"],
    reviewRequired: false,
    reviewReasons: [],
    missingFields: [],
    insufficientContent: [],
  });
});

test("emitGovernedRecommendation emits redesign recommendation after review evidence artifact creation", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-recommendation-gate-"));
  try {
    const outputPath = join(root, "review-evidence.json");
    const artifact = writeReviewEvidenceArtifact({
      outputPath,
      inventory: [
        {
          id: "existing:src/orchestrator.ts",
          relativePath: "src/orchestrator.ts",
          kind: "source",
          moduleName: "src.orchestrator",
        },
      ],
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
          recommendation: "Add a focused runnable test.",
        },
      ],
    });

    assert.deepEqual(emitGovernedRecommendation({ artifact, evidenceArtifactCreated: true, evidenceArtifactPath: outputPath }), {
      recommendation: "partial_redesign",
      evidenceArtifactPath: outputPath,
      evidenceArtifactCreated: true,
      findingCount: 1,
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
