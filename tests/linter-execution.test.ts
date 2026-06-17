/**
 * Tests for unified linter execution (Sub-AC 2.1).
 *
 * Validates:
 *  - Each configured linter executes and produces output presence
 *  - Raw results are written to the deterministic artifact path
 *  - Exit codes are captured correctly for pass and fail scenarios
 *  - Unavailable/missing linters are handled gracefully
 *  - Invalid input produces stable non-zero failure
 */

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

import {
  runAllLinters,
  writeResults,
  LINT_RESULTS_PATH,
  SCHEMA_VERSION,
} from "../scripts/run-linters.mjs";

// ── Helpers ────────────────────────────────────────────────────────────

/**
 * Write a minimal fixture project with the given source files.
 * @param {string} root - Temp directory root.
 * @param {{ python?: string; typescript?: string; pyproject?: string }} sources
 */
function writeFixtureProject(root, sources = {}) {
  if (sources.pyproject) {
    writeFileSync(join(root, "pyproject.toml"), sources.pyproject);
  }
  if (sources.python) {
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(join(root, "src", "app.py"), sources.python);
  }
  if (sources.typescript) {
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(join(root, "src", "index.ts"), sources.typescript);
  }
  // Always write a minimal package.json for project detection
  writeFileSync(
    join(root, "package.json"),
    JSON.stringify({ private: true, type: "module" }, null, 2) + "\n",
  );
}

// ── Tests: runAllLinters() ─────────────────────────────────────────────

test("runAllLinters returns correct schema version and structure on current project", () => {
  const results = runAllLinters(process.cwd());

  assert.equal(results.schemaVersion, SCHEMA_VERSION);
  assert.equal(typeof results.projectRoot, "string");
  assert.equal(typeof results.timestamp, "string");
  assert.ok(results.summary.total >= 1, "at least one linter should be configured");
  assert.ok(Array.isArray(results.results));
  assert.ok(results.results.length > 0);
});

test("runAllLinters: ruff produces output on current project", () => {
  const results = runAllLinters(process.cwd());
  const ruffResult = results.results.find((r) => r.linter === "ruff");

  assert.ok(ruffResult, "ruff result must be present");
  assert.equal(ruffResult.available, true);
  assert.equal(ruffResult.exitCode, 0);
  assert.equal(ruffResult.passed, true);
  assert.ok(
    ruffResult.stdout.includes("All checks passed") || ruffResult.stdout.trim() === "",
    `ruff stdout should indicate pass, got: "${ruffResult.stdout.slice(0, 200)}"`,
  );
});

test("runAllLinters: mypy produces output on current project", () => {
  const results = runAllLinters(process.cwd());
  const mypyResult = results.results.find((r) => r.linter === "mypy");

  assert.ok(mypyResult, "mypy result must be present");
  assert.equal(mypyResult.available, true);
  assert.equal(mypyResult.exitCode, 0);
  assert.equal(mypyResult.passed, true);
  assert.ok(
    mypyResult.stdout.includes("no issues found"),
    `mypy stdout should indicate success, got: "${mypyResult.stdout.slice(0, 200)}"`,
  );
});

test("runAllLinters: node-check produces output on current project", () => {
  const results = runAllLinters(process.cwd());
  const ncResult = results.results.find((r) => r.linter === "node-check");

  assert.ok(ncResult, "node-check result must be present");
  assert.equal(ncResult.available, true);
  assert.equal(ncResult.exitCode, 0);
  assert.equal(ncResult.passed, true);
});

test("runAllLinters: ruff detects violations in a fixture with lint errors", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-violation-"));
  try {
    writeFixtureProject(root, {
      python: "import os\nimport sys\nx = 1\n",  // unused imports
      pyproject: [
        "[project]",
        'name = "fixture"',
        'version = "0.1.0"',
        'requires-python = ">=3.11"',
        "",
        "[tool.ruff]",
        'target-version = "py311"',
        "[tool.ruff.lint]",
        'select = ["E", "F", "W", "I", "UP"]',
      ].join("\n"),
    });

    const results = runAllLinters(root);
    const ruffResult = results.results.find((r) => r.linter === "ruff");

    assert.ok(ruffResult, "ruff result must be present");
    assert.equal(ruffResult.available, true);
    assert.notEqual(ruffResult.exitCode, 0, "ruff should fail on unused imports");
    assert.equal(ruffResult.passed, false);
    assert.ok(ruffResult.stdout.length > 0, "ruff should produce violation output");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("runAllLinters: mypy detects type errors in a fixture with type violations", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-mypy-violation-"));
  try {
    writeFixtureProject(root, {
      python: "def add(a: int, b: int) -> int:\n    return a + b\n\nresult: str = add(1, 2)\n",  // type mismatch
      pyproject: [
        "[project]",
        'name = "fixture"',
        'version = "0.1.0"',
        'requires-python = ">=3.11"',
      ].join("\n"),
    });

    const results = runAllLinters(root);
    const mypyResult = results.results.find((r) => r.linter === "mypy");

    assert.ok(mypyResult, "mypy result must be present");
    assert.equal(mypyResult.available, true);
    assert.notEqual(mypyResult.exitCode, 0, "mypy should fail on type mismatch");
    assert.equal(mypyResult.passed, false);
    assert.ok(
      mypyResult.stdout.length > 0,
      `mypy should produce error output, got: "${mypyResult.stdout.slice(0, 200)}"`,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("runAllLinters: node-check detects syntax errors in a fixture with broken TS", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-nodecheck-violation-"));
  try {
    writeFixtureProject(root, {
      typescript: "const x =\n",  // incomplete statement
    });

    const results = runAllLinters(root);
    const ncResult = results.results.find((r) => r.linter === "node-check");

    assert.ok(ncResult, "node-check result must be present");
    assert.equal(ncResult.available, true);
    assert.notEqual(ncResult.exitCode, 0, "node-check should fail on syntax error");
    assert.equal(ncResult.passed, false);
    assert.ok(ncResult.stderr.length > 0, "node-check should produce error output");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("runAllLinters: unavailable linters are marked as unavailable gracefully", () => {
  // Create a fixture with ONLY TypeScript files, no Python at all
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-nopython-"));
  try {
    writeFixtureProject(root, {
      typescript: "export const ok = true;\n",
    });

    const results = runAllLinters(root);
    const ruffResult = results.results.find((r) => r.linter === "ruff");
    const mypyResult = results.results.find((r) => r.linter === "mypy");
    const ncResult = results.results.find((r) => r.linter === "node-check");

    assert.ok(ruffResult, "ruff result must be present");
    assert.equal(ruffResult.available, false, "ruff should be unavailable without Python files");
    assert.ok(ruffResult.stderr.includes("skipped"), "ruff should report skip reason");

    assert.ok(mypyResult, "mypy result must be present");
    assert.equal(mypyResult.available, false, "mypy should be unavailable without Python files");
    assert.ok(mypyResult.stderr.includes("skipped"), "mypy should report skip reason");

    assert.ok(ncResult, "node-check result must be present");
    assert.equal(ncResult.available, true, "node-check should be available with TS files");
    assert.equal(ncResult.passed, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// ── Tests: writeResults() ──────────────────────────────────────────────

test("writeResults writes raw results to deterministic file path", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-write-"));
  try {
    writeFixtureProject(root, {
      python: "x = 1\n",
      pyproject: [
        "[project]",
        'name = "fixture"',
        'version = "0.1.0"',
        'requires-python = ">=3.11"',
        "[tool.ruff]",
        'target-version = "py311"',
      ].join("\n"),
    });

    const results = runAllLinters(root);
    const artifactPath = writeResults(root, results);
    const expectedPath = join(root, LINT_RESULTS_PATH);

    assert.equal(artifactPath, expectedPath);
    assert.ok(existsSync(artifactPath), "artifact file must exist");

    const raw = readFileSync(artifactPath, "utf8");
    const parsed = JSON.parse(raw);

    assert.equal(parsed.schemaVersion, SCHEMA_VERSION);
    assert.equal(parsed.projectRoot, root);
    assert.ok(Array.isArray(parsed.results));
    assert.ok(parsed.results.length > 0);
    assert.equal(typeof parsed.summary, "object");
    assert.equal(typeof parsed.timestamp, "string");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("writeResults overwrites existing artifact deterministically", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-overwrite-"));
  try {
    writeFixtureProject(root, {
      python: "x = 1\n",
      pyproject: [
        "[project]",
        'name = "fixture"',
        'version = "0.1.0"',
        'requires-python = ">=3.11"',
        "[tool.ruff]",
        'target-version = "py311"',
      ].join("\n"),
    });

    const firstRun = runAllLinters(root);
    const path1 = writeResults(root, firstRun);
    const ts1 = JSON.parse(readFileSync(path1, "utf8")).timestamp;

    // Second run should overwrite
    const secondRun = runAllLinters(root);
    const path2 = writeResults(root, secondRun);
    const ts2 = JSON.parse(readFileSync(path2, "utf8")).timestamp;

    assert.equal(path1, path2, "same path should be used");
    assert.notEqual(ts1, ts2, "timestamp should differ between runs");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// ── Tests: summary correctness ─────────────────────────────────────────

test("summary reflects correct pass/fail/unavailable counts", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-summary-"));
  try {
    writeFixtureProject(root, {
      python: "import os\nimport sys\nx = 1\n",  // has ruff violations
      pyproject: [
        "[project]",
        'name = "fixture"',
        'version = "0.1.0"',
        'requires-python = ">=3.11"',
        "[tool.ruff]",
        'target-version = "py311"',
        "[tool.ruff.lint]",
        'select = ["E", "F", "W", "I", "UP"]',
      ].join("\n"),
    });

    const results = runAllLinters(root);
    const { summary } = results;

    assert.equal(summary.total, 3, "total linters should be 3");
    assert.equal(
      summary.available,
      summary.passed + summary.failed,
      "available = passed + failed",
    );
    assert.equal(
      summary.total,
      summary.available + summary.unavailable,
      "total = available + unavailable",
    );
    assert.ok(summary.failed >= 1, "at least ruff should fail with violations");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// ── Tests: npm script integration ──────────────────────────────────────

test("npm run lint:all exits 0 on current project", () => {
  const result = spawnSync("npm", ["run", "lint:all"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  assert.equal(result.error, undefined, `npm process error: ${result.error?.message}`);
  assert.equal(result.status, 0, `npm run lint:all should exit 0, got ${result.status}, stderr: ${result.stderr?.slice(0, 300)}`);
});

test("npm run lint:all writes artifact to deterministic path", () => {
  const result = spawnSync("npm", ["run", "lint:all"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  assert.equal(result.status, 0);
  const artifactPath = join(process.cwd(), LINT_RESULTS_PATH);
  assert.ok(existsSync(artifactPath), `artifact must exist at ${LINT_RESULTS_PATH}`);

  const raw = readFileSync(artifactPath, "utf8");
  const parsed = JSON.parse(raw);

  assert.equal(parsed.schemaVersion, SCHEMA_VERSION);
  assert.ok(Array.isArray(parsed.results));
  assert.ok(parsed.results.length >= 3);
});

// ── Tests: invalid input handling ──────────────────────────────────────

test("runAllLinters on empty directory marks all linters unavailable", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-empty-"));
  try {
    const results = runAllLinters(root);
    const { summary } = results;

    assert.equal(summary.available, 0, "no linters should be available on empty dir");
    assert.equal(summary.unavailable, summary.total);
    assert.equal(summary.failed, 0);
    assert.equal(summary.passed, 0);

    for (const r of results.results) {
      assert.equal(r.available, false, `${r.linter} should be unavailable`);
      assert.ok(r.stderr.includes("skipped"), `${r.linter} should report skip reason`);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("runAllLinters returns valid structured result even on empty/missing project", () => {
  // Verify that runAllLinters always returns a well-structured object,
  // even when there are no source files at all (worst-case input).
  const root = mkdtempSync(join(tmpdir(), "ai-agent-lint-empty2-"));
  try {
    // No files at all — not even package.json
    const results = runAllLinters(root);

    assert.equal(results.schemaVersion, SCHEMA_VERSION);
    assert.equal(typeof results.projectRoot, "string");
    assert.equal(typeof results.timestamp, "string");
    assert.ok(Array.isArray(results.results));
    assert.ok(results.results.length > 0);

    const { summary } = results;
    assert.equal(summary.available, 0, "no linters should be available");
    assert.equal(summary.unavailable, summary.total);
    assert.equal(summary.failed, 0);

    for (const r of results.results) {
      assert.equal(r.available, false);
      assert.equal(typeof r.linter, "string");
      assert.equal(typeof r.command, "string");
      assert.equal(typeof r.stdout, "string");
      assert.equal(typeof r.stderr, "string");
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// ── Tests: each linter has stdout or stderr presence ───────────────────

test("each available linter result has non-null stdout and stderr strings", () => {
  const results = runAllLinters(process.cwd());

  for (const r of results.results) {
    assert.equal(typeof r.linter, "string", `${r.linter}: linter name must be string`);
    assert.equal(typeof r.available, "boolean", `${r.linter}: available must be boolean`);
    assert.equal(typeof r.exitCode, "number", `${r.linter}: exitCode must be number`);
    assert.equal(typeof r.passed, "boolean", `${r.linter}: passed must be boolean`);
    assert.equal(typeof r.stdout, "string", `${r.linter}: stdout must be string`);
    assert.equal(typeof r.stderr, "string", `${r.linter}: stderr must be string`);
    assert.equal(typeof r.command, "string", `${r.linter}: command must be string`);
  }
});
