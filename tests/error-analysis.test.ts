/**
 * Tests for error frequency & type distribution analysis (Sub-AC 2.2).
 *
 * Validates:
 *  - Ruff output parsing into structured error records
 *  - Mypy output parsing into structured error records
 *  - Node --check output parsing into structured error records
 *  - Frequency counting and percentage distribution
 *  - Type bucket classification for ruff error codes
 *  - Full summary generation from a lint-results.json artifact
 *  - Empty / no-error input handled gracefully
 *  - Invalid input produces stable non-zero failure
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  parseLinterErrors,
  parseAllLinterErrors,
  computeLinterFrequency,
  buildErrorFrequencySummary,
  analyzeLintResultsArtifact,
  type LinterErrorRecord,
  type ErrorFrequencySummary,
} from "../src/error-analysis.ts";

// ── Fixtures ──────────────────────────────────────────────────────────────

const RUFF_THREE_ERRORS = [
  "src/app.py:1:8: F401 [*] `os` imported but unused",
  "src/app.py:2:8: F401 [*] `sys` imported but unused",
  "src/app.py:5:1: E302 [*] Expected 2 blank lines before class definition, found 1",
].join("\n");

const RUFF_MIXED_ERRORS = [
  "src/core.py:1:1: I001 [*] Import block is un-sorted or un-formatted",
  "src/core.py:1:8: F841 [*] Local variable `x` is assigned to but never used",
  "src/core.py:10:80: E501 Line too long (89 > 88)",
  "src/core.py:15:1: W293 [*] Blank line contains whitespace",
  "src/core.py:3:5: N801 Class name `myClass` should use CapWords convention",
  "src/core.py:4:1: UP004 [*] Class `MyError` inherits from `object`",
  "src/core.py:5:1: B006 [*] Do not use mutable data structures for argument defaults",
  "src/core.py:6:1: C400 [*] Unnecessary generator (rewrite as a `list` comprehension)",
  "src/core.py:7:1: SIM105 [*] Use `contextlib.suppress(Exception)` instead of `try`-`except`-`pass`",
  "src/core.py:8:8: F821 Undefined name `undefined_var`",
].join("\n");

const MYPY_TWO_ERRORS = [
  "src/app.py:5: error: Incompatible types in assignment (expression has type \"int\", variable has type \"str\")  [assignment]",
  "src/app.py:10: error: Argument 1 to \"add\" has incompatible type \"str\"; expected \"int\"  [arg-type]",
].join("\n");

const MYPY_ONE_ERROR_WITH_NOTE = [
  "src/types.py:5: error: Incompatible return value type (got \"int\", expected \"str\")  [return-value]",
  "src/types.py:3: note:     def add(a: int, b: int) -> int: ...",
  "Found 1 error in 1 file (checked 1 source file)",
].join("\n");

const NODE_CHECK_ONE_ERROR = [
  "/tmp/test/src/index.ts:3",
  "const x =",
  "",
  "^^^^^^",
  "",
  "SyntaxError: Unexpected token 'const'",
  "",
].join("\n");

const NODE_CHECK_TWO_ERRORS = [
  "/tmp/test/src/index.ts:3",
  "const x =",
  "",
  "^^^^^^",
  "",
  "SyntaxError: Unexpected token 'const'",
  "",
  "/tmp/test/src/helper.ts:8",
  "function bad(",
  "",
  "            ^",
  "",
  "SyntaxError: Unexpected end of input",
  "",
].join("\n");

// ── Tests: parseLinterErrors ────────────────────────────────────────────

test("parseLinterErrors: ruff parses three F401 + E302 errors", () => {
  const records = parseLinterErrors("ruff", RUFF_THREE_ERRORS, "");

  assert.equal(records.length, 3);
  assert.equal(records[0].linter, "ruff");
  assert.equal(records[0].file, "src/app.py");
  assert.equal(records[0].line, 1);
  assert.equal(records[0].column, 8);
  assert.equal(records[0].code, "F401");
  assert.equal(records[0].type, "unused_import");
  assert.ok(records[0].message.includes("unused"));

  assert.equal(records[1].code, "F401");
  assert.equal(records[2].code, "E302");
  assert.equal(records[2].type, "missing_blank_line");
});

test("parseLinterErrors: ruff parses mixed error codes with correct types", () => {
  const records = parseLinterErrors("ruff", RUFF_MIXED_ERRORS, "");

  assert.equal(records.length, 10);

  const typesByCode = new Map<string, string>();
  for (const r of records) {
    typesByCode.set(r.code, r.type);
  }

  assert.equal(typesByCode.get("I001"), "import_order");
  assert.equal(typesByCode.get("F841"), "unused_variable");
  assert.equal(typesByCode.get("E501"), "line_too_long");
  assert.equal(typesByCode.get("W293"), "missing_whitespace");
  assert.equal(typesByCode.get("N801"), "naming_convention");
  assert.equal(typesByCode.get("UP004"), "pyupgrade_suggestion");
  assert.equal(typesByCode.get("B006"), "bugbear_warning");
  assert.equal(typesByCode.get("C400"), "comprehension_style");
  assert.equal(typesByCode.get("SIM105"), "simplify_expression");
  assert.equal(typesByCode.get("F821"), "undefined_name");
});

test("parseLinterErrors: ruff handles empty output gracefully", () => {
  const records = parseLinterErrors("ruff", "", "");
  assert.equal(records.length, 0);
});

test("parseLinterErrors: ruff handles 'All checks passed' summary line", () => {
  const records = parseLinterErrors("ruff", "All checks passed!\n", "");
  assert.equal(records.length, 0);
});

test("parseLinterErrors: ruff handles 'Found N errors' summary line", () => {
  const output = [
    "src/app.py:1:8: F401 [*] `os` imported but unused",
    "Found 1 error.",
  ].join("\n");
  const records = parseLinterErrors("ruff", output, "");
  assert.equal(records.length, 1);
});

test("parseLinterErrors: mypy parses two type errors", () => {
  const records = parseLinterErrors("mypy", MYPY_TWO_ERRORS, "");

  assert.equal(records.length, 2);
  assert.equal(records[0].linter, "mypy");
  assert.equal(records[0].file, "src/app.py");
  assert.equal(records[0].line, 5);
  assert.equal(records[0].code, "assignment");
  assert.equal(records[0].type, "type_error");
  assert.ok(records[0].message.includes("Incompatible types"));

  assert.equal(records[1].code, "arg-type");
  assert.equal(records[1].type, "type_error");
});

test("parseLinterErrors: mypy parses error with trailing note line", () => {
  const records = parseLinterErrors("mypy", MYPY_ONE_ERROR_WITH_NOTE, "");

  assert.equal(records.length, 1);
  assert.equal(records[0].code, "return-value");
  assert.equal(records[0].file, "src/types.py");
  assert.equal(records[0].line, 5);
  assert.ok(records[0].message.includes("return value"));
});

test("parseLinterErrors: mypy handles success output gracefully", () => {
  const records = parseLinterErrors(
    "mypy",
    "Success: no issues found in 5 source files\n",
    "",
  );
  assert.equal(records.length, 0);
});

test("parseLinterErrors: mypy handles empty output gracefully", () => {
  const records = parseLinterErrors("mypy", "", "");
  assert.equal(records.length, 0);
});

test("parseLinterErrors: node-check parses one syntax error", () => {
  const records = parseLinterErrors("node-check", NODE_CHECK_ONE_ERROR, "");

  assert.equal(records.length, 1);
  assert.equal(records[0].linter, "node-check");
  assert.equal(records[0].file, "/tmp/test/src/index.ts");
  assert.equal(records[0].line, 3);
  assert.equal(records[0].code, "SyntaxError");
  assert.equal(records[0].type, "syntax_error");
  assert.ok(records[0].message.includes("Unexpected token"));
});

test("parseLinterErrors: node-check parses two syntax errors", () => {
  const records = parseLinterErrors("node-check", NODE_CHECK_TWO_ERRORS, "");

  assert.equal(records.length, 2);
  assert.equal(records[0].file, "/tmp/test/src/index.ts");
  assert.equal(records[1].file, "/tmp/test/src/helper.ts");
  assert.equal(records[1].line, 8);
});

test("parseLinterErrors: node-check handles empty output gracefully", () => {
  const records = parseLinterErrors("node-check", "", "");
  assert.equal(records.length, 0);
});

test("parseLinterErrors: node-check parses stderr as well as stdout", () => {
  const records = parseLinterErrors(
    "node-check",
    "",
    "/tmp/test/src/bad.ts:2\nconst x =\n\n^^^^^^\n\nSyntaxError: Unexpected token 'const'\n",
  );
  assert.equal(records.length, 1);
  assert.equal(records[0].file, "/tmp/test/src/bad.ts");
});

test("parseLinterErrors: unknown linter returns empty array", () => {
  const records = parseLinterErrors("unknown", "some output", "");
  assert.equal(records.length, 0);
});

// ── Tests: computeLinterFrequency ──────────────────────────────────────

test("computeLinterFrequency: computes correct counts for ruff errors", () => {
  const records = parseLinterErrors("ruff", RUFF_MIXED_ERRORS, "");
  const summary = computeLinterFrequency(records);

  assert.equal(summary.linter, "ruff");
  assert.equal(summary.totalErrors, 10);
  assert.equal(summary.distinctCodes, 10); // all distinct

  // Top bucket should be the most frequent (all have count=1 in this fixture)
  const topBucket = summary.buckets[0];
  assert.equal(topBucket.count, 1);
  assert.equal(topBucket.percent, 10);

  // Sum of all percents should be ~100
  const totalPercent = summary.buckets.reduce((sum, b) => sum + b.percent, 0);
  assert.ok(Math.abs(totalPercent - 100) <= 0.5, `total percent should be ~100, got ${totalPercent}`);

  // Type distribution: each type should be 1
  const nonZeroTypes = Object.entries(summary.byType).filter(
    ([, count]) => count > 0,
  );
  assert.equal(nonZeroTypes.length, 10); // one per unique type in this fixture
});

test("computeLinterFrequency: handles three F401 in a row", () => {
  const records = parseLinterErrors("ruff", RUFF_THREE_ERRORS, "");
  const summary = computeLinterFrequency(records);

  assert.equal(summary.totalErrors, 3);
  assert.equal(summary.distinctCodes, 2); // F401, E302

  const f401Bucket = summary.buckets.find((b) => b.code === "F401");
  if (!f401Bucket) throw new assert.AssertionError({ message: "F401 bucket missing" });
  assert.equal(f401Bucket.count, 2);
  assert.equal(f401Bucket.type, "unused_import");

  // 2/3 = 66.7%
  assert.ok(f401Bucket.percent >= 66.5 && f401Bucket.percent <= 67);

  const e302Bucket = summary.buckets.find((b) => b.code === "E302");
  if (!e302Bucket) throw new assert.AssertionError({ message: "E302 bucket missing" });
  assert.equal(e302Bucket.count, 1);
  assert.equal(e302Bucket.type, "missing_blank_line");

  assert.equal(summary.byType.unused_import, 2);
  assert.equal(summary.byType.missing_blank_line, 1);
});

test("computeLinterFrequency: handles empty records", () => {
  const summary = computeLinterFrequency([]);

  assert.equal(summary.totalErrors, 0);
  assert.equal(summary.distinctCodes, 0);
  assert.equal(summary.buckets.length, 0);
});

// ── Tests: buildErrorFrequencySummary ──────────────────────────────────

test("buildErrorFrequencySummary: produces aggregate summary across linters", () => {
  const ruffRecords = parseLinterErrors("ruff", RUFF_THREE_ERRORS, "");
  const mypyRecords = parseLinterErrors("mypy", MYPY_TWO_ERRORS, "");
  const allRecords = [...ruffRecords, ...mypyRecords];

  const summary = buildErrorFrequencySummary(allRecords);

  assert.equal(summary.schemaVersion, "error-frequency.v1");
  assert.equal(typeof summary.timestamp, "string");
  assert.equal(summary.totalErrors, 5);
  assert.equal(summary.distinctCodes, 4); // F401, E302, assignment, arg-type

  // Check linter summaries exist
  assert.ok(summary.linters.ruff);
  assert.ok(summary.linters.mypy);

  assert.equal(summary.linters.ruff.totalErrors, 3);
  assert.equal(summary.linters.mypy.totalErrors, 2);

  // Global type distribution
  assert.equal(summary.globalTypeDistribution.unused_import, 2);
  assert.equal(summary.globalTypeDistribution.missing_blank_line, 1);
  assert.equal(summary.globalTypeDistribution.type_error, 2);
});

test("buildErrorFrequencySummary: handles empty records gracefully", () => {
  const summary = buildErrorFrequencySummary([]);

  assert.equal(summary.schemaVersion, "error-frequency.v1");
  assert.equal(summary.totalErrors, 0);
  assert.equal(summary.distinctCodes, 0);
  assert.deepStrictEqual(summary.linters, {});
});

// ── Tests: analyzeLintResultsArtifact ──────────────────────────────────

test("analyzeLintResultsArtifact: produces summary from valid artifact", () => {
  const artifact = {
    schemaVersion: "lint-execution.v1",
    results: [
      {
        linter: "ruff",
        stdout: RUFF_THREE_ERRORS,
        stderr: "",
      },
      {
        linter: "mypy",
        stdout: MYPY_TWO_ERRORS,
        stderr: "",
      },
      {
        linter: "node-check",
        stdout: NODE_CHECK_ONE_ERROR,
        stderr: "",
      },
    ],
  };

  const summary = analyzeLintResultsArtifact(artifact);

  assert.equal(summary.schemaVersion, "error-frequency.v1");
  assert.equal(summary.totalErrors, 6); // 3 ruff + 2 mypy + 1 node

  assert.ok(summary.linters.ruff);
  assert.ok(summary.linters.mypy);
  assert.ok(summary.linters["node-check"]);

  assert.equal(summary.linters.ruff.totalErrors, 3);
  assert.equal(summary.linters.mypy.totalErrors, 2);
  assert.equal(summary.linters["node-check"].totalErrors, 1);

  // Verify global type distribution reflects all linters
  assert.equal(summary.globalTypeDistribution.unused_import, 2);
  assert.equal(summary.globalTypeDistribution.missing_blank_line, 1);
  assert.equal(summary.globalTypeDistribution.type_error, 2);
  assert.equal(summary.globalTypeDistribution.syntax_error, 1);
});

test("analyzeLintResultsArtifact: rejects wrong schema version", () => {
  assert.throws(
    () => {
      analyzeLintResultsArtifact({
        schemaVersion: "wrong-version",
        results: [],
      });
    },
    {
      name: "TypeError",
      message: /expected lint-execution\.v1/,
    },
  );
});

test("analyzeLintResultsArtifact: rejects missing results array", () => {
  assert.throws(
    () => {
      analyzeLintResultsArtifact({
        schemaVersion: "lint-execution.v1",
        results: null as unknown as [],
      });
    },
    { name: "TypeError" },
  );
});

test("analyzeLintResultsArtifact: handles empty results array gracefully", () => {
  const summary = analyzeLintResultsArtifact({
    schemaVersion: "lint-execution.v1",
    results: [],
  });

  assert.equal(summary.totalErrors, 0);
  assert.equal(summary.distinctCodes, 0);
});

// ── Tests: integration with current project ─────────────────────────────

test("integration: parses current lint-results.json artifact", async () => {
  const { readFileSync } = await import("node:fs");
  const { join } = await import("node:path");

  const artifactPath = join(
    process.cwd(),
    "docs/generated/lint-results.json",
  );
  const raw = readFileSync(artifactPath, "utf8");
  const artifact = JSON.parse(raw);

  assert.equal(artifact.schemaVersion, "lint-execution.v1");

  const summary = analyzeLintResultsArtifact(artifact);

  // Current project has all linters passing → 0 errors
  assert.equal(summary.schemaVersion, "error-frequency.v1");
  assert.equal(typeof summary.timestamp, "string");
  assert.ok(summary.totalErrors >= 0, "totalErrors should be non-negative");

  // Linter summaries should exist for all configured linters
  for (const result of artifact.results) {
    assert.ok(
      summary.linters[result.linter] !== undefined,
      `linter summary should exist for ${result.linter}`,
    );
  }
});

// ── Tests: type classification coverage ────────────────────────────────

test("type classification: classifies unknown ruff codes as 'other'", () => {
  // Create a ruff output with a hypothetical code prefix
  const output = "src/app.py:1:1: X999 Some future rule\n";
  const records = parseLinterErrors("ruff", output, "");
  assert.equal(records.length, 1);
  assert.equal(records[0].type, "other");
});

// ── Tests: parseAllLinterErrors ────────────────────────────────────────

test("parseAllLinterErrors: aggregates across multiple linter results", () => {
  const results = [
    { linter: "ruff", stdout: RUFF_THREE_ERRORS, stderr: "" },
    { linter: "mypy", stdout: MYPY_TWO_ERRORS, stderr: "" },
    { linter: "node-check", stdout: NODE_CHECK_ONE_ERROR, stderr: "" },
    { linter: "ruff", stdout: "", stderr: "" }, // empty
  ];

  const records = parseAllLinterErrors(results);
  assert.equal(records.length, 6);
});

// ── Tests: percentage distribution consistency ─────────────────────────

test("error frequency buckets sum to 100% within float tolerance", () => {
  const records = parseLinterErrors("ruff", RUFF_MIXED_ERRORS, "");
  const summary = computeLinterFrequency(records);

  const totalPercent = summary.buckets.reduce((sum, b) => sum + b.percent, 0);
  // With rounding to 1 decimal, 10 items at 10% each = 100%
  assert.ok(
    Math.abs(totalPercent - 100) <= 0.5,
    `total percent ${totalPercent} should be within 0.5 of 100`,
  );
});

test("error frequency buckets for uneven distribution sum to 100%", () => {
  // 2x F401, 1x E302 = 2/3=66.7% + 1/3=33.3%
  const records = parseLinterErrors("ruff", RUFF_THREE_ERRORS, "");
  const summary = computeLinterFrequency(records);

  const totalPercent = summary.buckets.reduce((sum, b) => sum + b.percent, 0);
  assert.ok(
    Math.abs(totalPercent - 100) <= 0.5,
    `total percent ${totalPercent} should be within 0.5 of 100`,
  );
});
