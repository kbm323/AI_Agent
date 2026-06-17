import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import {
  buildDependencyGraph,
  renderDOT,
  renderJSON,
  executeDependencyGraphCommand,
} from "../scripts/dependency-graph.ts";

// ── Helpers ────────────────────────────────────────────────────────────

function createTempProject(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "dep-graph-test-"));
  for (const [relPath, content] of Object.entries(files)) {
    const fullPath = join(dir, relPath);
    const parent = dirname(fullPath);
    mkdirSync(parent, { recursive: true });
    writeFileSync(fullPath, content, "utf8");
  }
  return dir;
}

// ── Tests ──────────────────────────────────────────────────────────────

test("buildDependencyGraph builds nodes and edges for simple project", () => {
  const dir = createTempProject({
    "src/a.ts": `import { helper } from "./b.ts";\nexport function run() { return helper(); }`,
    "src/b.ts": `export function helper() { return 42; }`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });

  assert.equal(graph.nodes.length, 2);
  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].from, "a");
  assert.equal(graph.edges[0].to, "b");

  const a = graph.nodes.find((n) => n.module === "a");
  const b = graph.nodes.find((n) => n.module === "b");
  assert.ok(a);
  assert.ok(b);
  assert.deepEqual(a.imports, ["b"]);
  assert.deepEqual(a.importedBy, []);
  assert.deepEqual(b.imports, []);
  assert.deepEqual(b.importedBy, ["a"]);

  rmSync(dir, { recursive: true, force: true });
});

test("buildDependencyGraph detects type imports", () => {
  const dir = createTempProject({
    "src/a.ts": `import type { Kind } from "./types.ts";\nexport const x: Kind = "value";`,
    "src/types.ts": `export type Kind = "value" | "other";`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });

  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].kind, "type");

  rmSync(dir, { recursive: true, force: true });
});

test("buildDependencyGraph detects mixed imports (value + type)", () => {
  const dir = createTempProject({
    "src/a.ts": `import { helper, type Kind } from "./b.ts";`,
    "src/b.ts": `export function helper() { return 42; }\nexport type Kind = "value";`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });

  assert.equal(graph.edges.length, 1);
  assert.equal(graph.edges[0].kind, "mixed");

  rmSync(dir, { recursive: true, force: true });
});

test("buildDependencyGraph reports orphans correctly", () => {
  const dir = createTempProject({
    "src/standalone.ts": `export const x = 1;`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });

  assert.equal(graph.nodes.length, 1);
  assert.equal(graph.edges.length, 0);
  assert.equal(graph.summary.orphanModules, 1);

  rmSync(dir, { recursive: true, force: true });
});

test("buildDependencyGraph reports module categories correctly", () => {
  const dir = createTempProject({
    "src/a.ts": `export function f() {}`,
    "scripts/check.ts": `import "../src/a.ts";`,
  });
  mkdirSync(join(dir, "scripts"), { recursive: true });
  // scripts/check.ts was created above
  const graph = buildDependencyGraph(dir, { scope: ["src", "scripts"] });

  assert.equal(graph.summary.modulesByCategory.src, 1);
  assert.equal(graph.summary.modulesByCategory.scripts, 1);

  rmSync(dir, { recursive: true, force: true });
});

test("buildDependencyGraph throws for nonexistent root", () => {
  assert.throws(() => {
    buildDependencyGraph("/nonexistent/path/xyz");
  }, /does not exist/);
});

test("buildDependencyGraph throws for non-directory root", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
  });
  // Pass a file as the root, not a directory
  const filePath = join(dir, "src", "a.ts");
  assert.throws(() => {
    buildDependencyGraph(filePath);
  }, /Not a directory/);

  rmSync(dir, { recursive: true, force: true });
});

// ── JSON output tests ──────────────────────────────────────────────────

test("renderJSON produces valid JSON with all required fields", () => {
  const dir = createTempProject({
    "src/mod.ts": `export default 1;`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });
  const json = renderJSON(graph);
  const parsed = JSON.parse(json);

  assert.equal(parsed.schemaVersion, "dependency-graph.v1");
  assert.ok(parsed.generatedAt);
  assert.ok(parsed.projectRoot);
  assert.ok(Array.isArray(parsed.nodes));
  assert.ok(Array.isArray(parsed.edges));
  assert.ok(parsed.summary);
  assert.equal(typeof parsed.summary.totalModules, "number");
  assert.equal(typeof parsed.summary.totalEdges, "number");

  rmSync(dir, { recursive: true, force: true });
});

// ── DOT output tests ───────────────────────────────────────────────────

test("renderDOT produces valid DOT format", () => {
  const dir = createTempProject({
    "src/a.ts": `import { b } from "./b.ts";`,
    "src/b.ts": `export const b = 2;`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });
  const dot = renderDOT(graph);

  assert.ok(dot.startsWith("digraph AI_Agent_Dependencies {"));
  assert.ok(dot.includes('"a" -> "b"'));
  assert.ok(dot.endsWith("}\n"));

  rmSync(dir, { recursive: true, force: true });
});

test("renderDOT uses dashed style for type-only edges", () => {
  const dir = createTempProject({
    "src/a.ts": `import type { T } from "./types.ts";`,
    "src/types.ts": `export type T = number;`,
  });

  const graph = buildDependencyGraph(dir, { scope: ["src"] });
  const dot = renderDOT(graph);

  assert.ok(dot.includes("[style=dashed, color=gray]"));

  rmSync(dir, { recursive: true, force: true });
});

// ── CLI interface tests ────────────────────────────────────────────────

test("executeDependencyGraphCommand returns JSON by default", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
  });

  const result = executeDependencyGraphCommand([], dir);
  assert.equal(result.exitCode, 0);
  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.schemaVersion, "dependency-graph.v1");

  rmSync(dir, { recursive: true, force: true });
});

test("executeDependencyGraphCommand --format dot returns DOT", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
  });

  const result = executeDependencyGraphCommand(["--format", "dot"], dir);
  assert.equal(result.exitCode, 0);
  assert.ok(result.stdout.startsWith("digraph "));

  rmSync(dir, { recursive: true, force: true });
});

test("executeDependencyGraphCommand --format both returns both formats", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
  });

  const result = executeDependencyGraphCommand(["--format", "both"], dir);
  assert.equal(result.exitCode, 0);
  assert.ok(result.stdout.includes("---DOT---"));

  rmSync(dir, { recursive: true, force: true });
});

test("executeDependencyGraphCommand --scope filters directories", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
    "scripts/b.ts": `export const y = 2;`,
    "tests/c.test.ts": `import "../src/a.ts";`,
  });

  const result = executeDependencyGraphCommand(["--scope", "src"], dir);
  assert.equal(result.exitCode, 0);
  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.summary.modulesByCategory.src, 1);
  // scripts and tests should be 0 since we scoped only to src
  assert.equal(parsed.summary.modulesByCategory.scripts, 0);

  rmSync(dir, { recursive: true, force: true });
});

test("executeDependencyGraphCommand invalid project root returns error", () => {
  const result = executeDependencyGraphCommand(["/nonexistent/path"]);
  assert.equal(result.exitCode, 1);
  const err = JSON.parse(result.stderr);
  assert.equal(err.error, "not_found");
});

test("executeDependencyGraphCommand non-directory project root returns error", () => {
  const dir = createTempProject({
    "src/a.ts": `export const x = 1;`,
  });
  const filePath = join(dir, "src", "a.ts");

  const result = executeDependencyGraphCommand([filePath]);
  assert.equal(result.exitCode, 1);
  const err = JSON.parse(result.stderr);
  assert.equal(err.error, "invalid_input");

  rmSync(dir, { recursive: true, force: true });
});

test("executeDependencyGraphCommand invalid --format returns error", () => {
  const dir = createTempProject({});
  const result = executeDependencyGraphCommand(["--format", "xml"], dir);
  assert.equal(result.exitCode, 1);
  const err = JSON.parse(result.stderr);
  assert.equal(err.error, "invalid_argument");

  rmSync(dir, { recursive: true, force: true });
});

// ── Integration: real project ──────────────────────────────────────────

test("buildDependencyGraph on real project produces valid structure", () => {
  // Use current project as the real-world test
  const graph = buildDependencyGraph(process.cwd());

  // Sanity checks
  assert.ok(graph.nodes.length > 0);
  assert.ok(graph.edges.length > 0);
  assert.equal(typeof graph.summary.totalModules, "number");
  assert.equal(typeof graph.summary.totalEdges, "number");
  assert.equal(typeof graph.summary.orphanModules, "number");
  assert.equal(typeof graph.summary.leafModules, "number");
  assert.equal(typeof graph.summary.rootModules, "number");

  // Every edge's from/to must reference existing nodes
  const nodeNames = new Set(graph.nodes.map((n) => n.module));
  for (const edge of graph.edges) {
    assert.ok(nodeNames.has(edge.from), `Edge from "${edge.from}" not in nodes`);
    assert.ok(nodeNames.has(edge.to), `Edge to "${edge.to}" not in nodes`);
  }

  // Categories
  const cats = graph.summary.modulesByCategory;
  assert.ok(cats.src >= 0);
  assert.ok(cats.scripts >= 0);
  assert.ok(cats.tests >= 0);
  assert.equal(cats.src + cats.scripts + cats.tests, graph.summary.totalModules);

  // Orphans + leaf + root + internal <= total
  assert.ok(
    graph.summary.orphanModules + graph.summary.leafModules + graph.summary.rootModules <=
    graph.summary.totalModules,
  );
});
