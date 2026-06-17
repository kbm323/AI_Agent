/**
 * Unified linter execution script for the AI_Agent project.
 *
 * Invokes each configured linter (ruff, mypy, node --check) across the
 * codebase, captures stdout/stderr, and writes raw results to a
 * deterministic file path.
 *
 * Sub-AC 2.1: Linter execution & raw output capture.
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

// ── Deterministic output path ──────────────────────────────────────────

/** @type {string} */
const LINT_RESULTS_PATH = "docs/generated/lint-results.json";

/** @type {string} */
const SCHEMA_VERSION = "lint-execution.v1";

// ── Helpers ────────────────────────────────────────────────────────────

/**
 * Run a single linter and capture its output.
 * @param {string} name - Human-readable linter name.
 * @param {string} command - Executable name.
 * @param {string[]} args - CLI arguments.
 * @param {string} projectRoot - Project root directory.
 * @returns {object} Structured result with stdout, stderr, exitCode, and passed flag.
 */
function runLinter(name, command, args, projectRoot) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 120_000,
  });

  if (result.error) {
    const errCode = /** @type {NodeJS.ErrnoException} */ (result.error).code;
    return {
      linter: name,
      available: errCode !== "ENOENT",
      command: `${command} ${args.join(" ")}`,
      exitCode: result.status ?? 1,
      stdout: result.stdout ?? "",
      stderr: result.error.message,
      passed: false,
    };
  }

  const exitCode = typeof result.status === "number" ? result.status : 1;
  return {
    linter: name,
    available: true,
    command: `${command} ${args.join(" ")}`,
    exitCode,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    passed: exitCode === 0,
  };
}

/**
 * Recursively check whether a directory tree contains any file matching
 * the given predicate (extension or exact filename).
 * @param {string} dir - Directory to scan.
 * @param {Set<string>} ignoredDirs - Directory names to skip.
 * @param {(entry: string, stat: import('fs').Stats) => boolean} predicate - Match function.
 * @returns {boolean}
 */
function treeContains(dir, ignoredDirs, predicate) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return false;
  }

  for (const entry of entries) {
    if (ignoredDirs.has(entry)) continue;

    const path = join(dir, entry);
    let stat;
    try {
      stat = statSync(path);
    } catch {
      continue;
    }

    if (stat.isDirectory()) {
      if (treeContains(path, ignoredDirs, predicate)) return true;
    } else if (predicate(entry, stat)) {
      return true;
    }
  }
  return false;
}

/** Directories to skip during file tree traversal. */
const IGNORED_DIRS = new Set([
  ".git",
  ".mypy_cache",
  ".ruff_cache",
  "node_modules",
  "dist",
  "build",
  "coverage",
  "__pycache__",
  ".venv",
]);

/**
 * Check whether the project contains Python source files.
 * @param {string} projectRoot
 * @returns {boolean}
 */
function hasPythonFiles(projectRoot) {
  return treeContains(
    projectRoot,
    IGNORED_DIRS,
    (entry) => entry.endsWith(".py") || entry === "pyproject.toml" || entry === "ruff.toml",
  );
}

/**
 * Check whether the project contains TypeScript source files.
 * @param {string} projectRoot
 * @returns {boolean}
 */
function hasTypeScriptFiles(projectRoot) {
  return treeContains(
    projectRoot,
    IGNORED_DIRS,
    (entry) => entry.endsWith(".ts") || entry === "package.json",
  );
}

// ── Main ────────────────────────────────────────────────────────────────

/**
 * Run all configured linters and return aggregated results.
 * @param {string} projectRoot - Project root directory.
 * @returns {object} Aggregated lint results with per-linter details.
 */
function runAllLinters(projectRoot) {
  /** @type {Array<object>} */
  const results = [];

  // ── Ruff ─────────────────────────────────────────────────────────────

  if (hasPythonFiles(projectRoot)) {
    results.push(runLinter("ruff", "ruff", ["check", "."], projectRoot));
  } else {
    results.push({
      linter: "ruff",
      available: false,
      command: "ruff check .",
      exitCode: 0,
      stdout: "",
      stderr: "No Python files found; ruff skipped.",
      passed: true,
    });
  }

  // ── Mypy ─────────────────────────────────────────────────────────────

  if (hasPythonFiles(projectRoot)) {
    results.push(runLinter("mypy", "mypy", ["src/shared"], projectRoot));
  } else {
    results.push({
      linter: "mypy",
      available: false,
      command: "mypy src/shared",
      exitCode: 0,
      stdout: "",
      stderr: "No Python files found; mypy skipped.",
      passed: true,
    });
  }

  // ── Node --check (TypeScript syntax) ─────────────────────────────────
  // node --check doesn't expand globs itself, so we use sh -c for each glob.
  // We only run on globs whose parent directory actually exists.

  if (hasTypeScriptFiles(projectRoot)) {
    const tsGlobs = ["src/*.ts", "scripts/*.ts", "tests/*.ts"];
    let allPassed = true;
    const combinedStdout = [];
    const combinedStderr = [];
    let lastExitCode = 0;

    for (const glob of tsGlobs) {
      // Extract the directory part (e.g., "src" from "src/*.ts")
      const dir = glob.split("/")[0];
      let dirExists = false;
      try {
        const s = statSync(join(projectRoot, dir));
        dirExists = s.isDirectory();
      } catch {
        dirExists = false;
      }

      if (!dirExists) {
        // Directory doesn't exist — skip this glob gracefully
        continue;
      }

      // Use shell to expand the glob before passing to node --check
      const r = runLinter(
        `node-check:${glob}`,
        "sh",
        ["-c", `node --check ${glob}`],
        projectRoot,
      );

      if (r.stdout) combinedStdout.push(r.stdout);
      if (r.stderr) combinedStderr.push(r.stderr);
      if (!r.passed) {
        allPassed = false;
        lastExitCode = r.exitCode;
      }
    }

    results.push({
      linter: "node-check",
      available: true,
      command: `sh -c "node --check ${tsGlobs.join(" ")}"`,
      exitCode: allPassed ? 0 : lastExitCode,
      stdout: combinedStdout.join("\n"),
      stderr: combinedStderr.join("\n"),
      passed: allPassed,
    });
  } else {
    results.push({
      linter: "node-check",
      available: false,
      command: "node --check src/*.ts scripts/*.ts tests/*.ts",
      exitCode: 0,
      stdout: "",
      stderr: "No TypeScript files found; node-check skipped.",
      passed: true,
    });
  }

  // ── Aggregate ────────────────────────────────────────────────────────

  const availableResults = results.filter((r) => r.available);
  const passedResults = availableResults.filter((r) => r.passed);
  const failedResults = availableResults.filter((r) => !r.passed);
  const unavailableResults = results.filter((r) => !r.available);

  return {
    schemaVersion: SCHEMA_VERSION,
    projectRoot,
    timestamp: new Date().toISOString(),
    summary: {
      total: results.length,
      available: availableResults.length,
      passed: passedResults.length,
      failed: failedResults.length,
      unavailable: unavailableResults.length,
    },
    results,
  };
}

/**
 * Write lint results to the deterministic artifact path.
 * @param {string} projectRoot
 * @param {object} results
 * @returns {string} Absolute path to the written artifact.
 */
function writeResults(projectRoot, results) {
  const artifactPath = resolve(projectRoot, LINT_RESULTS_PATH);
  mkdirSync(dirname(artifactPath), { recursive: true });
  writeFileSync(artifactPath, `${JSON.stringify(results, null, 2)}\n`);
  return artifactPath;
}

// ── Entry point ─────────────────────────────────────────────────────────

const invokedAsScript =
  process.argv[1]?.endsWith("run-linters.mjs") ?? false;

if (invokedAsScript) {
  const projectRoot = process.cwd();
  const results = runAllLinters(projectRoot);
  const artifactPath = writeResults(projectRoot, results);

  const { summary } = results;
  console.log(JSON.stringify({ summary, artifactPath }, null, 2));

  // Exit 0 only if all available linters pass.
  process.exitCode = summary.failed > 0 ? 1 : 0;
}

// ── Public API exports ──────────────────────────────────────────────────

export { runAllLinters, writeResults, LINT_RESULTS_PATH, SCHEMA_VERSION };
