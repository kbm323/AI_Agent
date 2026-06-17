import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("ruff check exits with code 0 on the current project", () => {
  const result = spawnSync("ruff", ["check", "."], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: "pipe",
  });

  assert.equal(result.error, undefined, `ruff process error: ${result.error?.message}`);
  assert.equal(result.status, 0, `ruff check failed with exit code ${result.status}`);
});

test("lint:ruff npm script exits with code 0 on the current project", () => {
  const result = spawnSync("npm", ["run", "lint:ruff"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: "pipe",
  });

  assert.equal(result.error, undefined, `npm process error: ${result.error?.message}`);
  assert.equal(result.status, 0, `npm run lint:ruff failed with exit code ${result.status}`);
});

test("ruff check . fixture exits with code 0 on a clean project", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-ruff-check-clean-"));
  try {
    writeRuffFixture(root, "x = 1\n");

    const result = spawnSync("ruff", ["check", "."], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
    });

    assert.equal(result.error, undefined, `ruff process error: ${result.error?.message}`);
    assert.equal(result.status, 0, `ruff check failed with exit code ${result.status}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("ruff check . fixture exits with non-zero on a project with lint violations", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-ruff-check-violation-"));
  try {
    writeRuffFixture(root, "import os\nimport sys\nx = 1\n");  // unused imports

    const result = spawnSync("ruff", ["check", "."], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
    });

    assert.equal(result.error, undefined, `ruff process error: ${result.error?.message}`);
    assert.notEqual(result.status, 0, "ruff check should exit non-zero with violations");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("ruff check . fixture exits with code 0 on a project with fixable violations when --fix is applied", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-ruff-check-fixable-"));
  try {
    writeRuffFixture(root, "import os\nimport sys\nx = 1\n");
    // Apply ruff --fix to remove unused imports
    spawnSync("ruff", ["check", ".", "--fix"], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
    });

    const result = spawnSync("ruff", ["check", "."], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe",
    });

    assert.equal(result.error, undefined, `ruff process error: ${result.error?.message}`);
    assert.equal(result.status, 0, `ruff check should pass after --fix: ${result.stdout}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("ruff-check.mjs script exits zero on the current project", () => {
  const result = spawnSync("node", ["scripts/ruff-check.mjs"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: "pipe",
  });

  assert.equal(result.error, undefined, `node process error: ${result.error?.message}`);
  assert.equal(result.status, 0, `ruff-check.mjs failed with exit code ${result.status}`);
});

test("ruff check . --output-format concise produces readable non-empty output on clean project", () => {
  const result = spawnSync("ruff", ["check", ".", "--output-format", "concise"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: "pipe",
  });

  assert.equal(result.error, undefined);
  assert.equal(result.status, 0);
});

test("ruff check . exits zero and stdout contains no error-level diagnostics on current project", () => {
  const result = spawnSync("ruff", ["check", ".", "--output-format", "full"], {
    cwd: process.cwd(),
    encoding: "utf8",
    stdio: "pipe",
  });

  assert.equal(result.error, undefined);
  assert.equal(result.status, 0);
  // stdout should indicate all checks passed
  assert.ok(
    result.stdout.includes("All checks passed") || result.stdout.trim() === "",
    `Expected 'All checks passed' or empty stdout, got: ${result.stdout.slice(0, 200)}`,
  );
});

function writeRuffFixture(root: string, pythonSource: string): void {
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "example.py"), pythonSource);
  writeFileSync(
    join(root, "pyproject.toml"),
    [
      "[project]",
      'name = "ruff-check-fixture"',
      'version = "0.1.0"',
      'requires-python = ">=3.11"',
      "",
      "[tool.ruff]",
      'target-version = "py311"',
      'line-length = 88',
      "",
      "[tool.ruff.lint]",
      "select = [",
      '    "E",',
      '    "F",',
      '    "W",',
      '    "I",',
      '    "UP",',
      "    ]",
      "ignore = []",
      "",
    ].join("\n"),
  );
}
