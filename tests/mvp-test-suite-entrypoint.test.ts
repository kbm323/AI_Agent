import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildMvpTestCommand, discoverMvpTestSuite, executeMvpTestSuiteCommand } from "../scripts/run-mvp-tests.ts";

test("MVP test suite discovery finds MVP-related tests from concrete test artifacts", () => {
  const discovery = discoverMvpTestSuite();

  assert.equal(discovery.schemaVersion, "mvp-test-suite-discovery.v1");
  assert.equal(discovery.testCount > 0, true);
  assert.equal(discovery.testFiles.includes("tests/mvp-completion-check.test.ts"), true);
  assert.equal(discovery.testFiles.includes("tests/orchestrator.test.ts"), true);
  assert.equal(discovery.testFiles.includes("tests/meeting-loop-routing.test.ts"), true);
  assert.equal(discovery.testFiles.includes("tests/typecheck-command.test.ts"), true);
  assert.deepEqual(discovery.testFiles, [...discovery.testFiles].sort());
});

test("MVP test suite command uses node test with discovered files", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mvp-test-command-"));
  try {
    writeTestFile(root, "mvp-flow.test.ts", "import test from 'node:test';\ntest('MVP flow', () => {});\n");
    writeTestFile(root, "unit-only.test.ts", "import test from 'node:test';\ntest('ordinary helper', () => {});\n");

    assert.deepEqual(buildMvpTestCommand(root), ["--test", "--test-concurrency=1", "tests/mvp-flow.test.ts"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("MVP test suite command runs the discovered test files and returns the subprocess result", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mvp-test-entry-"));
  try {
    writeTestFile(root, "meeting-loop.test.ts", "import test from 'node:test';\ntest('Hermes review', () => {});\n");
    const calls: Array<{ command: string; args: string[]; cwd: string | undefined }> = [];

    const result = executeMvpTestSuiteCommand(root, {
      spawnNode(command, args, options) {
        calls.push({
          command,
          args: args as string[],
          cwd: typeof options?.cwd === "string" ? options.cwd : undefined,
        });
        return {
          status: 0,
          stdout: "mvp tests passed\n",
          stderr: "",
          pid: 1,
          output: ["", "mvp tests passed\n", ""],
          signal: null,
        };
      },
    });

    assert.equal(result.exitCode, 0);
    assert.equal(result.stdout, "mvp tests passed\n");
    assert.equal(result.stderr, "");
    assert.deepEqual(calls, [
      {
        command: process.execPath,
        args: ["--test", "--test-concurrency=1", "tests/meeting-loop.test.ts"],
        cwd: root,
      },
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("MVP test suite entrypoint exits nonzero when a core MVP behavior test fails", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mvp-test-failing-core-"));
  try {
    writeTestFile(
      root,
      "core-mvp-meeting-behavior.test.ts",
      [
        "import test from 'node:test';",
        "import assert from 'node:assert/strict';",
        "test('core MVP meeting flow analyzes, routes, reviews, and synthesizes', () => {",
        "  assert.equal('missing-final-synthesis', 'finalized');",
        "});",
        "",
      ].join("\n"),
    );

    const discovery = discoverMvpTestSuite(root);
    const result = executeMvpTestSuiteCommand(root);

    assert.deepEqual(discovery.testFiles, ["tests/core-mvp-meeting-behavior.test.ts"]);
    assert.notEqual(result.exitCode, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("MVP test suite command fails explicitly when discovery finds no MVP tests", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mvp-test-empty-"));
  try {
    writeTestFile(root, "unit-only.test.ts", "import test from 'node:test';\ntest('ordinary helper', () => {});\n");

    const result = executeMvpTestSuiteCommand(root);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.deepEqual(JSON.parse(result.stderr), {
      error: "mvp_test_suite_failed",
      message: "MVP test suite discovery matched no tests",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function writeTestFile(root: string, basename: string, content: string): void {
  mkdirSync(join(root, "tests"), { recursive: true });
  writeFileSync(join(root, "tests", basename), content);
}
