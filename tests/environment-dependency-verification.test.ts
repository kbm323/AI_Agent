import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeCheckEnvironmentDependenciesCommand } from "../scripts/check-environment-dependencies.ts";
import {
  checkEnvironmentDependencies,
  documentedEnvironmentCommandSpecs,
  ENVIRONMENT_DEPENDENCY_DOCUMENT_PATH,
  type EnvironmentCommandRunner,
} from "../src/environment-dependency-verification.ts";

test("environment dependency check validates documented commands and executable outputs", () => {
  const root = buildEnvironmentFixture();
  try {
    const result = checkEnvironmentDependencies({
      projectRoot: root,
      runCommand: successfulRunner,
    });

    assert.equal(result.schemaVersion, "environment-dependency-check.v1");
    assert.equal(result.command, "ai-agent check-environment-dependencies");
    assert.equal(result.status, "passed");
    assert.equal(result.document.commandsMatchedSpecification, true);
    assert.equal(result.document.documentedCommandCount, documentedEnvironmentCommandSpecs.length);
    assert.deepEqual(result.summary, {
      requiredCommandCount: documentedEnvironmentCommandSpecs.length,
      presentCommandCount: documentedEnvironmentCommandSpecs.length,
      executableCommandCount: documentedEnvironmentCommandSpecs.length,
      failedCheckIds: [],
    });
    assert.equal(result.checks.every((check) => check.present), true);
    assert.equal(result.checks.every((check) => check.executable), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("environment dependency check fails when markdown command documentation drifts", () => {
  const root = buildEnvironmentFixture({
    commandOverride: "npm run health-check",
  });
  try {
    const result = checkEnvironmentDependencies({
      projectRoot: root,
      runCommand: successfulRunner,
    });

    assert.equal(result.status, "failed");
    assert.equal(result.document.commandsMatchedSpecification, false);
    assert.deepEqual(result.summary.failedCheckIds, ["node_version", "npm_version", "health_check", "typecheck_check"]);
    assert.equal(
      result.checks.every((check) => check.failureReason === "documented command table does not match executable specification"),
      true,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("environment dependency check fails when a documented package script is missing", () => {
  const root = buildEnvironmentFixture({ includeTypecheckScript: false });
  try {
    const result = checkEnvironmentDependencies({
      projectRoot: root,
      runCommand: successfulRunner,
    });

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.failedCheckIds, ["typecheck_check"]);
    assert.equal(result.checks.find((check) => check.id === "typecheck_check")?.failureReason, "package.json scripts.check:typecheck is missing");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("environment dependency command entry returns non-zero output for unexpected command output", () => {
  const root = buildEnvironmentFixture();
  try {
    const result = checkEnvironmentDependencies({
      projectRoot: root,
      runCommand: (command, projectRoot) => {
        if (command === "npm run health-check --silent") {
          return { exitCode: 0, stdout: '{"schemaVersion":"health-check.v1","status":"broken"}\n', stderr: "" };
        }
        return successfulRunner(command, projectRoot);
      },
    });

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.failedCheckIds, ["health_check"]);
    assert.equal(result.checks.find((check) => check.id === "health_check")?.failureReason, "command output did not match documented expectation");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("environment dependency command wrapper produces stable observable JSON", () => {
  const result = executeCheckEnvironmentDependenciesCommand(process.cwd());
  const parsed = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(parsed.schemaVersion, "environment-dependency-check.v1");
  assert.equal(parsed.command, "ai-agent check-environment-dependencies");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.summary.failedCheckIds.length, 0);
});

function buildEnvironmentFixture(input: {
  commandOverride?: string;
  includeTypecheckScript?: boolean;
} = {}): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-env-dependencies-"));
  mkdirSync(join(root, "docs"), { recursive: true });
  writeFileSync(
    join(root, "package.json"),
    `${JSON.stringify(
      {
        private: true,
        type: "module",
        scripts: {
          "health-check": "node scripts/health-check.ts",
          ...(input.includeTypecheckScript === false ? {} : { "check:typecheck": "node scripts/check-typecheck.ts" }),
        },
      },
      null,
      2,
    )}\n`,
  );
  writeFileSync(join(root, ENVIRONMENT_DEPENDENCY_DOCUMENT_PATH), buildDocument(input.commandOverride));
  return root;
}

function buildDocument(commandOverride?: string): string {
  return [
    "# Environment And Dependency Verification",
    "",
    "## Commands",
    "",
    "| id | command | expected output | purpose |",
    "| --- | --- | --- | --- |",
    ...documentedEnvironmentCommandSpecs.map((spec) => {
      const command = spec.id === "health_check" && commandOverride ? commandOverride : spec.command;
      return `| ${spec.id} | \`${command}\` | \`${spec.expectedOutput.replaceAll("`", "")}\` | test fixture |`;
    }),
    "",
  ].join("\n");
}

const successfulRunner: EnvironmentCommandRunner = (command) => {
  if (command === "node --version") return { exitCode: 0, stdout: "v24.1.0\n", stderr: "" };
  if (command === "npm --version") return { exitCode: 0, stdout: "11.0.0\n", stderr: "" };
  if (command === "npm run health-check --silent") {
    return { exitCode: 0, stdout: '{"schemaVersion":"health-check.v1","status":"ok"}\n', stderr: "" };
  }
  if (command === "npm run check:typecheck --silent") {
    return {
      exitCode: 0,
      stdout: '{"command":"ai-agent check:typecheck","status":"passed","typecheck":{"schemaVersion":"typecheck-command-check.v1"}}\n',
      stderr: "",
    };
  }
  return { exitCode: 127, stdout: "", stderr: `unexpected command: ${command}` };
};
