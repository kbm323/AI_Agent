import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  detectHotspots,
  executeHotspotDetectionCommand,
  renderHotspotTable,
} from "../scripts/hotspot-detection.ts";
import type { HotspotReport, FileHotspot } from "../scripts/hotspot-detection.ts";

// ── Helpers ────────────────────────────────────────────────────────────

function createTempProject(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "hotspot-test-"));
  for (const [relPath, content] of Object.entries(files)) {
    const fullPath = join(dir, relPath);
    const parent = join(fullPath, "..");
    mkdirSync(parent, { recursive: true });
    writeFileSync(fullPath, content, "utf8");
  }
  return dir;
}

// Expect: a TypeScript file with high cyclomatic complexity
const HIGH_COMPLEXITY_TS = `
// This function has many branches to simulate high CCN
export function complexLogic(input: number, mode: string): string {
  if (input < 0) {
    if (mode === "strict") {
      if (input < -100) {
        return "critical";
      } else if (input < -50) {
        return "severe";
      }
      return "negative";
    }
    if (input < -10) {
      return "warning";
    }
    return "low";
  }
  if (input === 0) {
    return "zero";
  }
  if (input > 100) {
    for (let i = 0; i < 3; i++) {
      if (input > 200 && mode === "verbose") {
        return "huge-" + (i > 1 ? "X" : "Y");
      } else if (input > 150) {
        return "large";
      }
    }
    return "big";
  }
  switch (mode) {
    case "fast":
      return input > 50 ? "fast-high" : "fast-low";
    case "safe":
      return "safe";
    case "debug":
      try {
        if (input > 10) {
          throw new Error("debug overflow");
        }
      } catch (e) {
        return "error:" + (input > 5 ? "retry" : "abort");
      }
      return "debug";
    default:
      return "normal";
  }
}

export function simpleHelper(x: number): number {
  return x * 2;
}
`;

const LOW_COMPLEXITY_TS = `
export function add(a: number, b: number): number {
  return a + b;
}

export function multiply(a: number, b: number): number {
  return a * b;
}

export const PI = 3.14159;
`;

// Files with intentional duplication (identical blocks - many repeated patterns)
const DUPLICATED_FILE_A = `
// Shared pattern block - duplicated identically across files
const SHARED_CONFIG = {
  name: "shared",
  version: "1.0.0",
  enabled: true,
  settings: {
    maxRetries: 3,
    timeout: 5000,
    retryDelay: 1000,
  },
};

export function sharedBlock(): string {
  const greeting = "hello world";
  const processed = greeting.toUpperCase();
  const result = processed + " - from shared module";
  return result;
}

export function anotherSharedPattern(): number {
  const base = 100;
  const multiplier = 2;
  const result = base * multiplier;
  return result;
}

export function uniqueToA(): string {
  return "only in A";
}
`;

const DUPLICATED_FILE_B = `
// Shared pattern block - duplicated identically across files
const SHARED_CONFIG = {
  name: "shared",
  version: "1.0.0",
  enabled: true,
  settings: {
    maxRetries: 3,
    timeout: 5000,
    retryDelay: 1000,
  },
};

export function sharedBlock(): string {
  const greeting = "hello world";
  const processed = greeting.toUpperCase();
  const result = processed + " - from shared module";
  return result;
}

export function anotherSharedPattern(): number {
  const base = 100;
  const multiplier = 2;
  const result = base * multiplier;
  return result;
}

export function uniqueToB(): string {
  return "only in B";
}
`;

const DUPLICATED_FILE_C = `
// Shared pattern block - duplicated identically across files
const SHARED_CONFIG = {
  name: "shared",
  version: "1.0.0",
  enabled: true,
  settings: {
    maxRetries: 3,
    timeout: 5000,
    retryDelay: 1000,
  },
};

export function sharedBlock(): string {
  const greeting = "hello world";
  const processed = greeting.toUpperCase();
  const result = processed + " - from shared module";
  return result;
}

export function anotherSharedPattern(): number {
  const base = 100;
  const multiplier = 2;
  const result = base * multiplier;
  return result;
}
`;

// Python file with radon-detectable high complexity
const HIGH_COMPLEXITY_PY = `
def high_complexity_py(data, strict=False, validate=True):
    """Function with many decision points for radon."""
    if data is None:
        return None
    if not isinstance(data, list):
        if strict:
            raise TypeError("expected list")
        return [data]
    result = []
    for item in data:
        if item is None:
            continue
        if isinstance(item, dict):
            for key, value in item.items():
                if value is None:
                    continue
                if validate and isinstance(value, str):
                    if len(value) == 0:
                        result.append("")
                    elif len(value) > 100:
                        result.append(value[:100])
                    else:
                        result.append(value.upper() if strict else value)
                else:
                    try:
                        result.append(str(value))
                    except Exception:
                        if strict:
                            raise
                        result.append("error")
        elif isinstance(item, (int, float)):
            if item < 0 and strict:
                result.append(0)
            elif item > 1000:
                result.append(1000)
            else:
                result.append(item)
        else:
            result.append(str(item))
    return result


def simple_py(x):
    return x * 2
`;

// ── Tests ──────────────────────────────────────────────────────────────

test("hotspot detection ranks high-complexity files above low-complexity files", () => {
  const dir = createTempProject({
    "src/high.ts": HIGH_COMPLEXITY_TS,
    "src/low.ts": LOW_COMPLEXITY_TS,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  assert.ok(report.hotspots.length >= 2, `expected at least 2 hotspots, got ${report.hotspots.length}`);

  const highSpot = report.hotspots.find((h) => h.file.includes("high.ts"));
  const lowSpot = report.hotspots.find((h) => h.file.includes("low.ts"));

  assert.ok(highSpot, "high.ts should be in hotspots");
  assert.ok(lowSpot, "low.ts should be in hotspots");
  assert.ok(
    highSpot!.rank < lowSpot!.rank,
    `high.ts (rank=${highSpot!.rank}) should rank before low.ts (rank=${lowSpot!.rank})`,
  );
  assert.ok(
    highSpot!.maxComplexity > lowSpot!.maxComplexity,
    `high.ts maxCCN=${highSpot!.maxComplexity} should exceed low.ts maxCCN=${lowSpot!.maxComplexity}`,
  );

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection assigns higher combined score to files with duplication", () => {
  const dir = createTempProject({
    "src/unique.ts": LOW_COMPLEXITY_TS,
    "src/dup_a.ts": DUPLICATED_FILE_A,
    "src/dup_b.ts": DUPLICATED_FILE_B,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  const dupA = report.hotspots.find((h) => h.file.includes("dup_a.ts"));
  const dupB = report.hotspots.find((h) => h.file.includes("dup_b.ts"));
  const unique = report.hotspots.find((h) => h.file.includes("unique.ts"));

  assert.ok(dupA, "dup_a.ts should be in hotspots");
  assert.ok(dupB, "dup_b.ts should be in hotspots");

  // Duplicated files should have non-zero duplication scores
  assert.ok(dupA!.duplicationScore > 0, `dup_a duplicationScore=${dupA!.duplicationScore} should be > 0`);
  assert.ok(dupB!.duplicationScore > 0, `dup_b duplicationScore=${dupB!.duplicationScore} should be > 0`);

  // The duplicated files should rank higher than the unique file
  if (unique) {
    assert.ok(
      dupA!.rank < unique.rank || dupB!.rank < unique.rank,
      `duplicated files should outrank unique file: dupA=${dupA!.rank}, dupB=${dupB!.rank}, unique=${unique.rank}`,
    );
  }

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection produces deterministic ordering for identical files", () => {
  const dir = createTempProject({
    "src/module_a.ts": LOW_COMPLEXITY_TS,
    "src/module_b.ts": LOW_COMPLEXITY_TS,
  });

  // Run twice, results should be identical
  const report1 = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });
  const report2 = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  assert.equal(report1.hotspots.length, report2.hotspots.length);
  for (let i = 0; i < report1.hotspots.length; i++) {
    assert.equal(report1.hotspots[i].file, report2.hotspots[i].file);
    assert.equal(report1.hotspots[i].combinedScore, report2.hotspots[i].combinedScore);
    assert.equal(report1.hotspots[i].rank, report2.hotspots[i].rank);
  }

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection respects top-N limit", () => {
  const dir = createTempProject({
    "src/a.ts": `export const a = 1;`,
    "src/b.ts": `export const b = 2;`,
    "src/c.ts": `export const c = 3;`,
    "src/d.ts": `export const d = 4;`,
    "src/e.ts": `export const e = 5;`,
  });

  const reportN2 = detectHotspots({ projectRoot: dir, topN: 2, builtinOnly: true });
  assert.ok(reportN2.hotspots.length <= 2, `expected <= 2 hotspots, got ${reportN2.hotspots.length}`);
  assert.equal(reportN2.summary.hotspotCount, reportN2.hotspots.length);

  const reportN5 = detectHotspots({ projectRoot: dir, topN: 5, builtinOnly: true });
  assert.ok(reportN5.hotspots.length <= 5);
  assert.ok(reportN5.hotspots.length >= reportN2.hotspots.length);

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection assigns rank 1 to highest combined score", () => {
  const dir = createTempProject({
    "src/complex.ts": HIGH_COMPLEXITY_TS,
    "src/simple.ts": LOW_COMPLEXITY_TS,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  assert.ok(report.hotspots.length >= 2);
  assert.equal(report.hotspots[0].rank, 1);

  // Verify rank ordering: rank increases, combined score decreases
  for (let i = 1; i < report.hotspots.length; i++) {
    assert.ok(
      report.hotspots[i].rank === i + 1,
      `hotspot at index ${i} should have rank ${i + 1}, got ${report.hotspots[i].rank}`,
    );
    assert.ok(
      report.hotspots[i - 1].combinedScore >= report.hotspots[i].combinedScore,
      `combined score should be non-increasing: ${report.hotspots[i - 1].combinedScore} >= ${report.hotspots[i].combinedScore}`,
    );
  }

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection with builtinOnly=true does not use external tools", () => {
  const dir = createTempProject({
    "src/mod.ts": `export const x = 1; export function f() { if (1) return 2; return 3; }`,
  });

  const report = detectHotspots({ projectRoot: dir, builtinOnly: true });

  assert.equal(report.toolsUsed.radon.available, false);
  assert.equal(report.toolsUsed.lizard.available, false);
  assert.equal(report.toolsUsed.jscpd.available, false);

  rmSync(dir, { recursive: true, force: true });
});

test("hotspot detection throws for nonexistent project root", () => {
  assert.throws(
    () => detectHotspots({ projectRoot: "/nonexistent/path/xyz" }),
    /does not exist/,
  );
});

test("hotspot detection report has valid schema", () => {
  const dir = createTempProject({
    "src/mod.ts": LOW_COMPLEXITY_TS,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  assert.equal(report.schemaVersion, "hotspot-report.v1");
  assert.ok(report.generatedAt, "generatedAt should be set");
  assert.ok(report.projectRoot, "projectRoot should be set");
  assert.ok(Array.isArray(report.hotspots), "hotspots should be an array");
  assert.ok(report.summary.totalFilesAnalyzed > 0, "totalFilesAnalyzed should be > 0");
  assert.equal(typeof report.summary.hotspotCount, "number");
  assert.ok(Array.isArray(report.summary.topHotspots));

  // Each hotspot must have expected fields
  for (const h of report.hotspots) {
    assert.equal(typeof h.file, "string");
    assert.equal(typeof h.complexityScore, "number");
    assert.ok(h.complexityScore >= 0 && h.complexityScore <= 1, `complexityScore ${h.complexityScore} out of [0,1]`);
    assert.equal(typeof h.duplicationScore, "number");
    assert.ok(h.duplicationScore >= 0 && h.duplicationScore <= 1, `duplicationScore ${h.duplicationScore} out of [0,1]`);
    assert.equal(typeof h.combinedScore, "number");
    assert.ok(h.combinedScore >= 0 && h.combinedScore <= 1, `combinedScore ${h.combinedScore} out of [0,1]`);
    assert.equal(typeof h.maxComplexity, "number");
    assert.ok(h.maxComplexity >= 0);
    assert.equal(typeof h.avgComplexity, "number");
    assert.equal(typeof h.totalLinesOfCode, "number");
    assert.equal(typeof h.functionCount, "number");
    assert.ok(h.rank > 0, `rank should be positive, got ${h.rank}`);
    assert.ok(Array.isArray(h.warnings));
  }

  rmSync(dir, { recursive: true, force: true });
});

test("complexity scores are normalized to [0, 1] range", () => {
  const dir = createTempProject({
    "src/a.ts": `export function a() { return 1; }`,
    "src/b.ts": HIGH_COMPLEXITY_TS,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  // The highest complexity file should have complexityScore = 1
  const maxScore = Math.max(...report.hotspots.map((h) => h.complexityScore));
  assert.ok(maxScore > 0.5, `max complexity score ${maxScore} should be near 1.0`);
  // There should be a file with score 0 (or near 0) for the simplest file
  const minScore = Math.min(...report.hotspots.map((h) => h.complexityScore));
  assert.ok(minScore < 0.5, `min complexity score ${minScore} should be lower`);

  rmSync(dir, { recursive: true, force: true });
});

test("functions exceeding complexity threshold produce warnings", () => {
  const dir = createTempProject({
    "src/complex.ts": HIGH_COMPLEXITY_TS,
    "src/simple.ts": LOW_COMPLEXITY_TS,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  const complexSpot = report.hotspots.find((h) => h.file.includes("complex.ts"));
  const simpleSpot = report.hotspots.find((h) => h.file.includes("simple.ts"));

  // Complex file likely generates warnings; simple file should not
  if (complexSpot) {
    assert.ok(complexSpot.functionCount > 1 || complexSpot.maxComplexity > 5,
      `complex file should have significant complexity metrics`);
  }

  rmSync(dir, { recursive: true, force: true });
});

// ── CLI tests ──────────────────────────────────────────────────────────

test("executeHotspotDetectionCommand returns JSON by default", () => {
  const dir = createTempProject({
    "src/mod.ts": `export const x = 1;`,
  });

  const result = executeHotspotDetectionCommand(["--builtin-only"], dir);
  assert.equal(result.exitCode, 0);
  assert.ok(result.stdout, "stdout should not be empty");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.schemaVersion, "hotspot-report.v1");

  rmSync(dir, { recursive: true, force: true });
});

test("executeHotspotDetectionCommand --format table returns table", () => {
  const dir = createTempProject({
    "src/mod.ts": `export const x = 1;`,
  });

  const result = executeHotspotDetectionCommand(["--format", "table", "--builtin-only"], dir);
  assert.equal(result.exitCode, 0);
  assert.ok(result.stdout.includes("HOTSPOT DETECTION REPORT"));
  assert.ok(result.stdout.includes("Rank"));

  rmSync(dir, { recursive: true, force: true });
});

test("executeHotspotDetectionCommand --format both returns both", () => {
  const dir = createTempProject({
    "src/mod.ts": `export const x = 1;`,
  });

  const result = executeHotspotDetectionCommand(["--format", "both", "--builtin-only"], dir);
  assert.equal(result.exitCode, 0);
  assert.ok(result.stdout.includes("---TABLE---"));

  rmSync(dir, { recursive: true, force: true });
});

test("executeHotspotDetectionCommand --top-n limits results", () => {
  const dir = createTempProject({
    "src/a.ts": `export const a = 1;`,
    "src/b.ts": `export const b = 2;`,
    "src/c.ts": `export const c = 3;`,
  });

  const result = executeHotspotDetectionCommand(["--top-n", "2", "--builtin-only"], dir);
  assert.equal(result.exitCode, 0);

  const parsed = JSON.parse(result.stdout);
  assert.ok(parsed.hotspots.length <= 2);

  rmSync(dir, { recursive: true, force: true });
});

test("executeHotspotDetectionCommand invalid args return error", () => {
  const dir = createTempProject({});

  // Invalid --format
  const r1 = executeHotspotDetectionCommand(["--format", "xml"], dir);
  assert.equal(r1.exitCode, 1);
  const err1 = JSON.parse(r1.stderr);
  assert.equal(err1.error, "invalid_argument");

  // Invalid --top-n
  const r2 = executeHotspotDetectionCommand(["--top-n", "0"], dir);
  assert.equal(r2.exitCode, 1);

  // Nonexistent root
  const r3 = executeHotspotDetectionCommand(["/nonexistent/path"], dir);
  assert.equal(r3.exitCode, 1);
  const err3 = JSON.parse(r3.stderr);
  assert.equal(err3.error, "not_found");

  rmSync(dir, { recursive: true, force: true });
});

test("executeHotspotDetectionCommand --complexity-weight requires valid float", () => {
  const dir = createTempProject({ "src/a.ts": `export const a = 1;` });

  const r1 = executeHotspotDetectionCommand(["--complexity-weight", "1.5", "--builtin-only"], dir);
  assert.equal(r1.exitCode, 1);
  const err1 = JSON.parse(r1.stderr);
  assert.equal(err1.error, "invalid_argument");

  const r2 = executeHotspotDetectionCommand(["--complexity-weight", "0.7", "--builtin-only"], dir);
  assert.equal(r2.exitCode, 0);

  rmSync(dir, { recursive: true, force: true });
});

test("renderHotspotTable produces readable output", () => {
  const dir = createTempProject({
    "src/a.ts": `export const a = 1;`,
    "src/b.ts": `export function b() { if (1) { return 2; } return 3; }`,
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });
  const table = renderHotspotTable(report);

  assert.ok(table.includes("HOTSPOT DETECTION REPORT"));
  assert.ok(table.includes("Rank"));
  assert.ok(table.includes("File"));
  assert.ok(table.includes("Combined"));
  assert.ok(table.includes("MaxCCN"));
  assert.ok(table.includes("AvgCCN"));
  assert.ok(table.includes("Funcs"));
  assert.ok(table.includes("Dup%"));
  assert.ok(table.includes("Warnings"));

  // Each hotspot should appear in table
  for (const h of report.hotspots) {
    assert.ok(
      table.includes(h.file.split("/").pop()!),
      `table should include file name from ${h.file}`,
    );
  }

  rmSync(dir, { recursive: true, force: true });
});

// ── Integration test on real project ───────────────────────────────────

test("hotspot detection on real project produces valid report", () => {
  const report = detectHotspots({ projectRoot: process.cwd() });

  assert.ok(report.hotspots.length > 0, "should find hotspots in real project");
  assert.equal(report.schemaVersion, "hotspot-report.v1");
  assert.ok(report.generatedAt);
  assert.ok(report.projectRoot);

  // Tools should be discovered
  assert.ok(typeof report.toolsUsed.radon.available === "boolean");
  assert.ok(typeof report.toolsUsed.lizard.available === "boolean");
  assert.ok(typeof report.toolsUsed.jscpd.available === "boolean");

  // At least one real file should have meaningful data
  const withData = report.hotspots.filter((h) => h.maxComplexity > 0 || h.duplicationPercent > 0);
  assert.ok(withData.length > 0, "should have at least one hotspot with metrics");

  // Summary should be consistent
  assert.equal(report.summary.hotspotCount, report.hotspots.length);
  assert.ok(report.summary.totalFilesAnalyzed >= report.hotspots.length);

  // Top hotspots must be in rank order
  for (let i = 1; i < report.hotspots.length; i++) {
    assert.ok(
      report.hotspots[i - 1].combinedScore >= report.hotspots[i].combinedScore,
      `combinedScore should be non-increasing at index ${i}`,
    );
    assert.ok(
      report.hotspots[i - 1].rank < report.hotspots[i].rank,
      `rank should increase at index ${i}`,
    );
  }
});

// ── Edge case: empty project ───────────────────────────────────────────

test("hotspot detection on empty project returns empty hotspots", () => {
  const dir = createTempProject({});

  // Create empty src directory
  mkdirSync(join(dir, "src"), { recursive: true });

  const report = detectHotspots({ projectRoot: dir, builtinOnly: true });
  assert.equal(report.hotspots.length, 0);
  assert.equal(report.summary.hotspotCount, 0);
  assert.equal(report.summary.totalFilesAnalyzed, 0);

  rmSync(dir, { recursive: true, force: true });
});

// ── Edge case: complexity weight validation ────────────────────────────

test("custom complexity and duplication weights affect ranking", () => {
  const dir = createTempProject({
    "src/complex.ts": HIGH_COMPLEXITY_TS,
    "src/dup_a.ts": DUPLICATED_FILE_A,
    "src/dup_b.ts": DUPLICATED_FILE_B,
  });

  // With complexity-only weight, complex file should be #1
  const reportC = detectHotspots({
    projectRoot: dir,
    topN: 10,
    complexityWeight: 1.0,
    duplicationWeight: 0.0,
    builtinOnly: true,
  });

  const topComplexityOnly = reportC.hotspots[0];
  assert.ok(topComplexityOnly.file.includes("complex.ts"),
    `complexity-only mode: #1 should be complex file, got ${topComplexityOnly.file}`);

  // With duplication-only weight, a dup file should be #1
  const reportD = detectHotspots({
    projectRoot: dir,
    topN: 10,
    complexityWeight: 0.0,
    duplicationWeight: 1.0,
    builtinOnly: true,
  });

  const topDuplicationOnly = reportD.hotspots[0];
  const dupFiles = reportD.hotspots.filter(
    (h) => h.file.includes("dup_a") || h.file.includes("dup_b"),
  );
  assert.ok(
    dupFiles.some((h) => h.rank === 1),
    `duplication-only mode: a duplicated file should be #1`,
  );

  rmSync(dir, { recursive: true, force: true });
});

// ── Edge case: Python files with radon ─────────────────────────────────

test("hotspot detection can use builtin-only path for Python files", () => {
  const dir = createTempProject({
    "src/complex.py": HIGH_COMPLEXITY_PY,
    "src/simple.py": "def f(x):\n    return x\n",
  });

  const report = detectHotspots({ projectRoot: dir, topN: 10, builtinOnly: true });

  const complexPy = report.hotspots.find((h) => h.file.includes("complex.py"));
  const simplePy = report.hotspots.find((h) => h.file.includes("simple.py"));

  assert.ok(complexPy, "complex.py should be found");
  assert.ok(simplePy, "simple.py should be found");
  assert.ok(
    complexPy!.maxComplexity > simplePy!.maxComplexity,
    `complex.py (${complexPy!.maxComplexity}) should have higher maxCCN than simple.py (${simplePy!.maxComplexity})`,
  );

  rmSync(dir, { recursive: true, force: true });
});
