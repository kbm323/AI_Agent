import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { executeCheckArtifactsCommand } from "../scripts/check-artifacts.ts";
import { executeContextStorageBoundaryCheckCommand } from "../scripts/check-context-storage-boundary.ts";
import { executeCheckDecisionDeterminismCommand } from "../scripts/check-decision-determinism.ts";
import { executeCheckDryRunContractCommand } from "../scripts/check-dry-run-contract.ts";
import { executeCheckEnvironmentDependenciesCommand } from "../scripts/check-environment-dependencies.ts";
import { executeCheckEscalationSerializationCommand } from "../scripts/check-escalation-serialization.ts";
import { executeCheckFinalOutputSchemaCommand } from "../scripts/check-final-output-schema.ts";
import { executeCheckFinalSynthesisArtifactCommand } from "../scripts/check-final-synthesis-artifact.ts";
import { executeCheckFinalSynthesisStabilityCommand } from "../scripts/check-final-synthesis-stability.ts";
import { executeCheckFixtureHarnessCommand } from "../scripts/check-fixture-harness.ts";
import { executeLoopContextCompressionPolicyCheckCommand } from "../scripts/check-loop-context-compression-policy.ts";
import { executeLoopContextCompressionVerificationCommand } from "../scripts/check-loop-context-compression-verification.ts";
import { executeCheckMeetingLoopArtifactsCommand } from "../scripts/check-meeting-loop-artifacts.ts";
import { executeCheckMeetingLoopRoutingCommand } from "../scripts/check-meeting-loop-routing.ts";
import { executeMvpCompletionCheckCommand } from "../scripts/check-mvp-completion.ts";
import { executeCheckOpenClawHermesLoopCommand } from "../scripts/check-openclaw-hermes-loop.ts";
import { checkPublicApi, PUBLIC_API_ARTIFACT_PATH } from "../scripts/check-public-api.ts";
import { executeCheckRequestAnalysisCommand } from "../scripts/check-request-analysis.ts";
import { executeRequirementGapCheckCommand } from "../scripts/check-requirement-gap.ts";
import { executeCheckReviewArtifactCompletenessCommand } from "../scripts/check-review-artifact-completeness.ts";
import { executeCheckRoutingAssignmentCommand } from "../scripts/check-routing-assignment.ts";
import { executeCheckTaskDecompositionStabilityCommand } from "../scripts/check-task-decomposition-stability.ts";
import { executeCheckTaskOverlapCommand } from "../scripts/check-task-overlap.ts";
import { executeTokenCostControlCheckCommand } from "../scripts/check-token-cost-control.ts";
import { executeTokenReductionSavingsBandCheckCommand } from "../scripts/check-token-reduction-savings-band.ts";
import { executeTokenStrategyCheckCommand } from "../scripts/check-token-strategy.ts";
import { executeCheckTypecheckCommand } from "../scripts/check-typecheck.ts";
import { executeCheckValidationCommandDocumentationCommand } from "../scripts/check-validation-command-documentation.ts";
import { executeCheckVerificationOutputCommand } from "../scripts/check-verification-output.ts";
import { executeCountLocCommand } from "../scripts/count-loc.ts";
import { executeDependencyGraphCommand } from "../scripts/dependency-graph.ts";
import { executeDryRunCommand } from "../scripts/dry-run.ts";
import { executeGenerateDiagnosisReportCommand } from "../scripts/generate-diagnosis-report.ts";
import { executeGenerateLoopContextCompressionPolicyCommand } from "../scripts/generate-loop-context-compression-policy.ts";
import { executeGenerateTokenStrategyCommand } from "../scripts/generate-token-strategy.ts";
import { executeHealthCheckCommand } from "../scripts/health-check.ts";
import { executeInspectFileTreeCommand } from "../scripts/inspect-file-tree.ts";
import { executeReviewEvidenceCommand } from "../scripts/review-evidence.ts";
import { executeMvpTestSuiteCommand } from "../scripts/run-mvp-tests.ts";
import { executeVerificationWorkflowRunnerCommand } from "../scripts/run-verification-workflow.ts";
import {
  writeContextStorageBoundaryArtifact,
  writeLoopContextCompressionPolicyArtifact,
  writeReviewEvidenceArtifact,
  writeTokenReductionStrategyArtifact,
} from "../src/index.ts";

type CapturedCommandResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
};

type CommandEntryCase = {
  modulePath: string;
  run: () => CapturedCommandResult | Promise<CapturedCommandResult>;
  runStandalone?: () => CapturedCommandResult;
  assertOutput?: (output: unknown) => void;
  assertStandaloneOutput?: (output: unknown, result: CapturedCommandResult) => void;
  allowEmptyStdout?: boolean;
  allowStandaloneEmptyStdout?: boolean;
};

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));

const commandEntryCases: CommandEntryCase[] = [
  {
    modulePath: "scripts/check-artifacts.ts",
    run: () => executeCheckArtifactsCommand(projectRoot),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-context-storage-boundary.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeContextStorageBoundaryCheckCommand(["--project-root", root]));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/check-context-storage-boundary.ts", [], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-decision-determinism.ts",
    run: () => executeCheckDecisionDeterminismCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-dry-run-contract.ts",
    run: () => executeCheckDryRunContractCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-environment-dependencies.ts",
    run: () => executeCheckEnvironmentDependenciesCommand(projectRoot),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-escalation-serialization.ts",
    run: () => executeCheckEscalationSerializationCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-final-output-schema.ts",
    run: () => executeCheckFinalOutputSchemaCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-final-synthesis-artifact.ts",
    run: () => executeCheckFinalSynthesisArtifactCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-final-synthesis-stability.ts",
    run: () => executeCheckFinalSynthesisStabilityCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-fixture-harness.ts",
    run: () => executeCheckFixtureHarnessCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-loop-context-compression-policy.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeLoopContextCompressionPolicyCheckCommand(["--project-root", root]));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/check-loop-context-compression-policy.ts", [], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-loop-context-compression-verification.ts",
    run: () => executeLoopContextCompressionVerificationCommand([]),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-meeting-loop-artifacts.ts",
    run: () => executeCheckMeetingLoopArtifactsCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-meeting-loop-routing.ts",
    run: () => executeCheckMeetingLoopRoutingCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-mvp-completion.ts",
    run: () =>
      executeMvpCompletionCheckCommand({
        checkRequirementGapMapping: () =>
          ({
            command: "ai-agent check-requirement-gap",
            artifact: {
              present: true,
              priorityOrderVerified: true,
              implementedCount: 6,
              missingCount: 0,
              capabilityIds: [
                "request-analysis-work-breakdown",
                "role-based-routing",
                "openclaw-hermes-meeting-loop",
                "final-synthesis",
                "escalation-artifact",
                "raw-storage-summary-context",
              ],
            },
          }) as ReturnType<typeof import("../scripts/check-requirement-gap.ts").checkRequirementGapMapping>,
      }),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-openclaw-hermes-loop.ts",
    run: () => executeCheckOpenClawHermesLoopCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-public-api.ts",
    run: async () => ({
      exitCode: 0,
      stdout: `${JSON.stringify(await checkPublicApi(), null, 2)}\n`,
      stderr: "",
    }),
    allowStandaloneEmptyStdout: true,
    assertOutput: (output) => assertJsonField(output, "modulePath", "ai-agent"),
    assertStandaloneOutput: () => assert.equal(existsSync(resolve(projectRoot, PUBLIC_API_ARTIFACT_PATH)), true),
  },
  {
    modulePath: "scripts/check-request-analysis.ts",
    run: () => executeCheckRequestAnalysisCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-requirement-gap.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => {
        const generation = executeGenerateDiagnosisReportCommand([
          "--project-root",
          root,
          "--output",
          "docs/generated/diagnosis-report.json",
          "--review-evidence-output",
          "docs/generated/review-evidence.json",
        ]);
        assertCommandSucceeded(generation);
        return executeRequirementGapCheckCommand(["--project-root", root]);
      });
    },
    runStandalone: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => {
        const generation = executeGenerateDiagnosisReportCommand([
          "--project-root",
          root,
          "--output",
          "docs/generated/diagnosis-report.json",
          "--review-evidence-output",
          "docs/generated/review-evidence.json",
        ]);
        assertCommandSucceeded(generation);
        return runStandalone("scripts/check-requirement-gap.ts", [], root);
      });
    },
    assertOutput: (output) => {
      const artifact = readObjectField(output, "artifact");
      assert.equal(artifact.present, true);
      assert.equal(artifact.missingCount, 0);
    },
  },
  {
    modulePath: "scripts/check-review-artifact-completeness.ts",
    run: () => executeCheckReviewArtifactCompletenessCommand(projectRoot),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-routing-assignment.ts",
    run: () => executeCheckRoutingAssignmentCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-task-decomposition-stability.ts",
    run: () => executeCheckTaskDecompositionStabilityCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-task-overlap.ts",
    run: () => executeCheckTaskOverlapCommand(),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-token-cost-control.ts",
    run: () => executeTokenCostControlCheckCommand([]),
    assertOutput: (output) => assertJsonField(output, "pass", true),
  },
  {
    modulePath: "scripts/check-token-reduction-savings-band.ts",
    run: () => executeTokenReductionSavingsBandCheckCommand([]),
    assertOutput: (output) => assertJsonField(output, "pass", true),
  },
  {
    modulePath: "scripts/check-token-strategy.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeTokenStrategyCheckCommand(["--project-root", root]));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/check-token-strategy.ts", [], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "command", "ai-agent check-token-strategy"),
  },
  {
    modulePath: "scripts/check-typecheck.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeCheckTypecheckCommand(root));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/check-typecheck.ts", [], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-validation-command-documentation.ts",
    run: () => executeCheckValidationCommandDocumentationCommand(projectRoot),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/check-verification-output.ts",
    run: () => executeCheckVerificationOutputCommand(projectRoot),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/dry-run.ts",
    run: () => executeDryRunCommand(["--request", "랜딩 페이지 제작 회의를 진행해줘."]),
    runStandalone: () => runStandalone("scripts/dry-run.ts", ["--request", "랜딩 페이지 제작 회의를 진행해줘."]),
    assertOutput: (output) => assertJsonField(output, "schemaVersion", "final-output-artifact.v1"),
  },
  {
    modulePath: "scripts/generate-diagnosis-report.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () =>
        executeGenerateDiagnosisReportCommand([
          "--project-root",
          root,
          "--output",
          "docs/generated/diagnosis-report.json",
          "--review-evidence-output",
          "docs/generated/review-evidence.json",
        ]),
      );
    },
    runStandalone: () =>
      runStandaloneWithFixture("scripts/generate-diagnosis-report.ts", [
        "--output",
        "docs/generated/diagnosis-report.json",
        "--review-evidence-output",
        "docs/generated/review-evidence.json",
      ]),
    assertOutput: (output) => assertJsonField(output, "command", "ai-agent generate-diagnosis-report"),
  },
  {
    modulePath: "scripts/generate-loop-context-compression-policy.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeGenerateLoopContextCompressionPolicyCommand(["--output", "docs/loop-context-compression-policy.md"]));
    },
    runStandalone: () =>
      runStandaloneWithFixture("scripts/generate-loop-context-compression-policy.ts", ["--output", "docs/loop-context-compression-policy.md"]),
    assertOutput: (output) => assertJsonField(output, "command", "ai-agent generate-loop-context-compression-policy"),
  },
  {
    modulePath: "scripts/generate-token-strategy.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeGenerateTokenStrategyCommand(["--output", "docs/token-reduction-strategy.md"]));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/generate-token-strategy.ts", ["--output", "docs/token-reduction-strategy.md"]),
    assertOutput: (output) => assertJsonField(output, "command", "ai-agent generate-token-strategy"),
  },
  {
    modulePath: "scripts/health-check.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeHealthCheckCommand([], root));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/health-check.ts", [], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "status", "ok"),
  },
  {
    modulePath: "scripts/count-loc.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeCountLocCommand({ projectRoot: root, format: "json" }));
    },
    runStandalone: () => runStandaloneWithFixture("scripts/count-loc.ts", ["--json"], writeFixtureProject()),
    assertOutput: (output) => assertJsonField(output, "schemaVersion", 1),
  },
  {
    modulePath: "scripts/dependency-graph.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeDependencyGraphCommand([], root));
    },
    assertOutput: (output) => assertJsonField(output, "schemaVersion", "dependency-graph.v1"),
  },
  {
    modulePath: "scripts/inspect-file-tree.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeInspectFileTreeCommand([], root));
    },
    assertOutput: (output) => assertJsonField(output, "schemaVersion", 1),
  },
  {
    modulePath: "scripts/review-evidence.ts",
    run: () => {
      const root = writeFixtureProject();
      return usingFixture(root, () => executeReviewEvidenceCommand(["--project-root", root, "--output", "docs/generated/review-evidence.json"]));
    },
    runStandalone: () =>
      runStandaloneWithFixture("scripts/review-evidence.ts", [
        "--output",
        "docs/generated/review-evidence.json",
      ]),
    assertOutput: (output) => assertJsonField(output, "command", "ai-agent review-evidence"),
  },
  {
    modulePath: "scripts/ruff-check.mjs",
    run: () => {
      const root = mkdtempSync(join(tmpdir(), "ai-agent-ruff-entry-"));
      // Ensure at least one Python file exists so ruff does not emit a
      // warning about no Python files found on stderr.
      writeFileSync(join(root, "example.py"), "x = 1\n");
      writeFileSync(
        join(root, "pyproject.toml"),
        [
          "[project]",
          'name = "ruff-fixture"',
          'version = "0.1.0"',
          'requires-python = ">=3.11"',
          "",
          "[tool.ruff]",
          'target-version = "py311"',
          'line-length = 88',
          "",
          "[tool.ruff.lint]",
          "select = ['E', 'F', 'W']",
          "ignore = []",
          "",
        ].join("\n"),
      );
      return usingFixture(root, () => {
        const result = spawnSync(process.execPath, [resolve(projectRoot, "scripts/ruff-check.mjs")], {
          cwd: root,
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
        });
        return {
          exitCode: typeof result.status === "number" ? result.status : 1,
          stdout: result.stdout,
          stderr: result.stderr,
        };
      });
    },
    assertOutput: (output) => assert.equal(typeof output, "string"),
    allowEmptyStdout: true,
  },
  {
    modulePath: "scripts/run-mvp-tests.ts",
    run: () => {
      const root = writeMvpTestFixtureProject();
      return usingFixture(root, () =>
        executeMvpTestSuiteCommand(root, {
          spawnNode: () =>
            ({
              status: 0,
              stdout: "fixture mvp tests passed\n",
              stderr: "",
              error: undefined,
            }) as ReturnType<typeof spawnSync>,
        }),
      );
    },
    runStandalone: () => runStandaloneWithFixture("scripts/run-mvp-tests.ts", [], writeMvpTestFixtureProject()),
    allowStandaloneEmptyStdout: true,
    assertOutput: (output) => assert.equal(typeof output, "string"),
  },
  {
    modulePath: "scripts/run-verification-workflow.ts",
    run: () =>
      executeVerificationWorkflowRunnerCommand({
        projectRoot,
        runCommand: () => ({ exitCode: 0, stdout: "ok\n", stderr: "" }),
      }),
    assertOutput: (output) => assertJsonField(output, "status", "passed"),
  },
  {
    modulePath: "scripts/typecheck-python.mjs",
    run: () => {
      const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-python-entry-"));
      mkdirSync(join(root, "src", "shared"), { recursive: true });
      writeFileSync(join(root, "src", "shared", "__init__.py"), '"""test."""\n');
      writeFileSync(join(root, "src", "shared", "example.py"), "def add(a: int, b: int) -> int:\n    return a + b\n");
      return usingFixture(root, () => {
        const result = spawnSync(process.execPath, [resolve(projectRoot, "scripts/typecheck-python.mjs")], {
          cwd: root,
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
        });
        return {
          exitCode: typeof result.status === "number" ? result.status : 1,
          stdout: result.stdout,
          stderr: result.stderr,
        };
      });
    },
    runStandalone: () => {
      const root = mkdtempSync(join(tmpdir(), "ai-agent-typecheck-python-standalone-"));
      mkdirSync(join(root, "src", "shared"), { recursive: true });
      writeFileSync(join(root, "src", "shared", "__init__.py"), '"""test."""\n');
      writeFileSync(join(root, "src", "shared", "example.py"), "def add(a: int, b: int) -> int:\n    return a + b\n");
      return runStandaloneWithFixture("scripts/typecheck-python.mjs", [], root);
    },
    allowEmptyStdout: true,
    allowStandaloneEmptyStdout: true,
    assertOutput: (output) => assert.equal(typeof output, "string"),
  },
];

test("success-path coverage exists for every package-exposed CLI entry module", () => {
  const packageJson = JSON.parse(readFileSync(resolve(projectRoot, "package.json"), "utf8"));
  const exposedEntryModules = [
    ...new Set(
      Object.values(packageJson.scripts as Record<string, string>).flatMap((command) =>
        [...command.matchAll(/(?:^|\s)scripts\/[^\s&]+?\.(?:ts|mjs)(?=\s|$)/g)]
          .map((match) => match[0].trim())
          .filter((entryModule) => !entryModule.includes("*")),
      ),
    ),
  ].sort();
  const coveredEntryModules = commandEntryCases.map((entryCase) => entryCase.modulePath).sort();

  assert.deepEqual(coveredEntryModules, exposedEntryModules);
});

for (const entryCase of commandEntryCases) {
  test(`public CLI command entry module primary success path: ${entryCase.modulePath}`, async () => {
    const result = await entryCase.run();
    assertCommandSucceeded(result, { allowEmptyStdout: entryCase.allowEmptyStdout ?? false });
    entryCase.assertOutput?.(parseCommandOutput(result));
  });

  test(`standalone CLI script module primary success path: ${entryCase.modulePath}`, () => {
    const result = entryCase.runStandalone?.() ?? runStandalone(entryCase.modulePath);
    assertCommandSucceeded(result, {
      allowEmptyStdout: entryCase.allowStandaloneEmptyStdout ?? entryCase.allowEmptyStdout ?? false,
    });
    (entryCase.assertStandaloneOutput ?? entryCase.assertOutput)?.(parseCommandOutput(result), result);
  });
}

function assertCommandSucceeded(result: CapturedCommandResult, options: { allowEmptyStdout?: boolean } = {}): void {
  assert.equal(result.exitCode, 0, result.stderr || result.stdout);
  assert.equal(result.stderr, "");
  if (!options.allowEmptyStdout) {
    assert.notEqual(result.stdout, "");
  }
}

function parseCommandOutput(result: CapturedCommandResult): unknown {
  const trimmed = result.stdout.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return trimmed;
  return JSON.parse(trimmed);
}

function assertJsonField(output: unknown, field: string, expected: unknown): void {
  assert.equal(typeof output, "object");
  assert.notEqual(output, null);
  assert.equal((output as Record<string, unknown>)[field], expected);
}

function readObjectField(output: unknown, field: string): Record<string, unknown> {
  assert.equal(typeof output, "object");
  assert.notEqual(output, null);
  const value = (output as Record<string, unknown>)[field];
  assert.equal(typeof value, "object");
  assert.notEqual(value, null);
  return value as Record<string, unknown>;
}

function usingFixture<T>(root: string, run: () => T): T {
  const previousCwd = process.cwd();
  try {
    process.chdir(root);
    return run();
  } finally {
    process.chdir(previousCwd);
    rmSync(root, { recursive: true, force: true });
  }
}

function runStandaloneWithFixture(modulePath: string, args: string[] = [], root = writeFixtureProject()): CapturedCommandResult {
  return usingFixture(root, () => runStandalone(modulePath, args, root));
}

function runStandalone(modulePath: string, args: string[] = [], cwd = projectRoot): CapturedCommandResult {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-standalone-entry-"));
  const stdoutPath = join(root, "stdout.txt");
  const stderrPath = join(root, "stderr.txt");
  const command = [
    shellQuote(process.execPath),
    shellQuote(resolve(projectRoot, modulePath)),
    ...args.map(shellQuote),
    ">",
    shellQuote(stdoutPath),
    "2>",
    shellQuote(stderrPath),
  ].join(" ");
  const result = spawnSync("bash", ["-lc", command], {
    cwd,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    return {
      exitCode: typeof result.status === "number" ? result.status : 1,
      stdout: readFileSync(stdoutPath, "utf8"),
      stderr: `${readFileSync(stderrPath, "utf8")}${result.stderr}`,
    };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function writeFixtureProject(): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-cli-entry-"));
  mkdirSync(join(root, "src"), { recursive: true });
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "tests"), { recursive: true });
  mkdirSync(join(root, "docs", "generated"), { recursive: true });
  writeFileSync(
    join(root, "package.json"),
    `${JSON.stringify({ scripts: { typecheck: "node --check src/index.ts" } }, null, 2)}\n`,
  );
  writeFileSync(
    join(root, "README.md"),
    [
      "# AI_Agent",
      "",
      "## MVP 목표",
      "OpenClaw와 Hermes가 회의하고 escalation을 보존한다.",
      "",
      "## Public API",
      "",
      "```ts",
      'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
      "```",
      "",
    ].join("\n"),
  );
  writeFileSync(join(root, "src", "index.ts"), "export const ok = true;\n");
  writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
  writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
  writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
  writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
  writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
  writeFileSync(join(root, "tests", "planning.test.ts"), "import test from 'node:test';\n");
  writeReviewEvidenceArtifact({
    outputPath: join(root, "docs", "review-evidence.json"),
    inventory: [],
    findings: [
      {
        id: "finding:fixture:token-cost",
        sourceId: "fixture:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "high",
        category: "token_cost",
        title: "Loop context repeats raw full text",
        evidence: "The fixture keeps enough evidence text for redesign gating.",
        recommendation: "Separate raw storage from exposed summaries.",
      },
    ],
  });
  writeTokenReductionStrategyArtifact({ projectRoot: root });
  writeContextStorageBoundaryArtifact({ projectRoot: root });
  writeLoopContextCompressionPolicyArtifact({ projectRoot: root });
  writeText(join(root, "docs", "diagnosis-report.md"), [
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
    "The mapping is generated from README and project inventory.",
    "## Token Strategy",
    "Compressed context separates raw full text from exposed loop summaries.",
    "",
  ]);
  writeText(join(root, "docs", "refactoring-plan.md"), [
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
  ]);
  return root;
}

function writeMvpTestFixtureProject(): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mvp-entry-"));
  mkdirSync(join(root, "tests"), { recursive: true });
  writeFileSync(join(root, "tests", "mvp-smoke.test.ts"), "import test from 'node:test';\ntest('mvp smoke', () => {});\n");
  return root;
}

function writeText(path: string, lines: string[]): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${lines.join("\n")}\n`);
  assert.equal(existsSync(path), true);
}
