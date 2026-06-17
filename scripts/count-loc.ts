import { resolve, relative, basename, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";

// ── Types ──────────────────────────────────────────────────────────────

interface LocStats {
  /** Module name (filename without extension) */
  module: string;
  /** Relative path from project root */
  path: string;
  /** Category: src, scripts, tests */
  category: ModuleCategory;
  /** Total lines in file */
  totalLines: number;
  /** Lines containing actual code (non-blank, non-comment) */
  codeLines: number;
  /** Lines that are entirely comments (// ... or part of /* block) */
  commentLines: number;
  /** Blank / whitespace-only lines */
  blankLines: number;
}

type ModuleCategory = "src" | "scripts" | "tests";

interface LocReport {
  schemaVersion: 1;
  generatedAt: string;
  projectRoot: string;
  modules: LocStats[];
  summary: {
    totalFiles: number;
    totalLines: number;
    totalCodeLines: number;
    totalCommentLines: number;
    totalBlankLines: number;
    byCategory: Record<ModuleCategory, {
      files: number;
      totalLines: number;
      codeLines: number;
      commentLines: number;
      blankLines: number;
    }>;
  };
}

// ── Constants ──────────────────────────────────────────────────────────

const SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  ".ouroboros",
  ".ouroboros_eval",
  "dist",
  ".nyc_output",
  "coverage",
]);

const COUNTED_EXTENSIONS = new Set([".ts", ".js", ".mjs", ".cjs"]);

// ── Single-file parse/import result (Sub-AC 2.2) ────────────────────────

/** Deterministic per-file status for a single source file parse. */
export type SourceFileStatus = "success" | "failure" | "skip";

/** Result of parsing a single source file. */
export interface SourceFileResult {
  /** The file path that was parsed. */
  filePath: string;
  /** Deterministic status: success (parsed), failure (error), skip (unsupported). */
  status: SourceFileStatus;
  /** Accurate total line count (only meaningful when status === "success"). */
  lineCount: number;
  /** Code lines (only meaningful when status === "success"). */
  codeLines: number;
  /** Comment lines (only meaningful when status === "success"). */
  commentLines: number;
  /** Blank lines (only meaningful when status === "success"). */
  blankLines: number;
  /** Error message when status === "failure", empty otherwise. */
  error: string;
}

/**
 * Parse a single source file and return deterministic per-file status
 * (success/failure/skip) plus an accurate line count.
 *
 * - **success**: file exists, has a counted extension, and was parsed without error.
 * - **failure**: file exists but could not be read or parsed (e.g. permission denied,
 *   encoding issue, I/O error returned as a structured message).
 * - **skip**: file is not a counted source file (wrong extension, binary, directory).
 *
 * The function is deterministic: the same file in the same state always
 * produces the same result. Non-existent files return `skip` (not `failure`)
 * because the caller should decide whether a missing file is an error.
 */
export function parseSourceFile(filePath: string): SourceFileResult {
  // ── Skip: non-existent / inaccessible path ──────────────────────────
  let stats;
  try {
    stats = statSync(filePath);
  } catch {
    return {
      filePath,
      status: "skip",
      lineCount: 0,
      codeLines: 0,
      commentLines: 0,
      blankLines: 0,
      error: "",
    };
  }

  // ── Skip: directories ──────────────────────────────────────────────
  if (stats.isDirectory()) {
    return {
      filePath,
      status: "skip",
      lineCount: 0,
      codeLines: 0,
      commentLines: 0,
      blankLines: 0,
      error: "",
    };
  }

  // ── Skip: unsupported extension ─────────────────────────────────────
  const ext = extname(filePath);
  if (!COUNTED_EXTENSIONS.has(ext)) {
    return {
      filePath,
      status: "skip",
      lineCount: 0,
      codeLines: 0,
      commentLines: 0,
      blankLines: 0,
      error: "",
    };
  }

  // ── Parse: read and count lines ─────────────────────────────────────
  try {
    const { totalLines, codeLines, commentLines, blankLines } = countLoc(filePath);
    return {
      filePath,
      status: "success",
      lineCount: totalLines,
      codeLines,
      commentLines,
      blankLines,
      error: "",
    };
  } catch (err) {
    return {
      filePath,
      status: "failure",
      lineCount: 0,
      codeLines: 0,
      commentLines: 0,
      blankLines: 0,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

// ── LOC Counter ────────────────────────────────────────────────────────

function countLoc(filePath: string): { totalLines: number; codeLines: number; commentLines: number; blankLines: number } {
  const content = readFileSync(filePath, "utf-8");
  const lines = content.split("\n");

  let codeLines = 0;
  let commentLines = 0;
  let blankLines = 0;
  let inBlockComment = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    // Handle blank lines
    if (line === "" || line === "\r") {
      blankLines++;
      continue;
    }

    // Handle block comments
    if (inBlockComment) {
      commentLines++;
      if (line.includes("*/")) {
        inBlockComment = false;
      }
      continue;
    }

    // Start of block comment
    if (line.startsWith("/*") || line.startsWith("/**")) {
      commentLines++;
      if (!line.includes("*/")) {
        inBlockComment = true;
      }
      // Check if the block comment also contains code on the same line (e.g. `/* comment */ code()`)
      // In that case we count it as a comment line, not code — consistent with standard LOC tools.
      continue;
    }

    // Full-line single-line comment
    // Covers: // ..., /// ...
    if (line.startsWith("//") || line.startsWith("///")) {
      commentLines++;
      continue;
    }

    // Everything else is a code line (including lines with trailing comments like `x = 1; // comment`)
    codeLines++;
  }

  const totalLines = lines.length;
  return { totalLines, codeLines, commentLines, blankLines };
}

// ── Module Discovery ────────────────────────────────────────────────────

function discoverModules(rootPath: string, relativePrefix: string, category: ModuleCategory): string[] {
  const absPath = resolve(rootPath, relativePrefix);
  if (!existsSync(absPath)) return [];
  if (!statSync(absPath).isDirectory()) return [];

  const files: string[] = [];
  const entries = readdirSync(absPath, { withFileTypes: true });

  for (const entry of entries) {
    if (entry.isDirectory() && SKIP_DIRS.has(entry.name)) continue;

    const childRel = relativePrefix ? `${relativePrefix}/${entry.name}` : entry.name;
    const childAbs = resolve(rootPath, childRel);

    if (entry.isDirectory()) {
      files.push(...discoverModules(rootPath, childRel, category));
    } else if (entry.isFile()) {
      const ext = extname(entry.name);
      if (COUNTED_EXTENSIONS.has(ext)) {
        files.push(childRel);
      }
    }
  }

  return files;
}

// ── Report Building ─────────────────────────────────────────────────────

export function buildLocReport(projectRoot: string): LocReport {
  const absRoot = resolve(projectRoot);

  if (!existsSync(absRoot)) {
    throw new Error(`Project root does not exist: ${absRoot}`);
  }

  const categories: { dir: string; category: ModuleCategory }[] = [
    { dir: "src", category: "src" },
    { dir: "scripts", category: "scripts" },
    { dir: "tests", category: "tests" },
  ];

  const modules: LocStats[] = [];

  for (const { dir, category } of categories) {
    const moduleFiles = discoverModules(absRoot, dir, category).sort();
    for (const relPath of moduleFiles) {
      const absPath = resolve(absRoot, relPath);
      const { totalLines, codeLines, commentLines, blankLines } = countLoc(absPath);
      modules.push({
        module: basename(relPath).replace(/\.[^.]+$/, ""),
        path: relPath,
        category,
        totalLines,
        codeLines,
        commentLines,
        blankLines,
      });
    }
  }

  // Build summary
  const byCategory: LocReport["summary"]["byCategory"] = {
    src: { files: 0, totalLines: 0, codeLines: 0, commentLines: 0, blankLines: 0 },
    scripts: { files: 0, totalLines: 0, codeLines: 0, commentLines: 0, blankLines: 0 },
    tests: { files: 0, totalLines: 0, codeLines: 0, commentLines: 0, blankLines: 0 },
  };

  let totalLines = 0;
  let totalCodeLines = 0;
  let totalCommentLines = 0;
  let totalBlankLines = 0;

  for (const m of modules) {
    const cat = byCategory[m.category];
    cat.files++;
    cat.totalLines += m.totalLines;
    cat.codeLines += m.codeLines;
    cat.commentLines += m.commentLines;
    cat.blankLines += m.blankLines;

    totalLines += m.totalLines;
    totalCodeLines += m.codeLines;
    totalCommentLines += m.commentLines;
    totalBlankLines += m.blankLines;
  }

  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    projectRoot: absRoot,
    modules,
    summary: {
      totalFiles: modules.length,
      totalLines,
      totalCodeLines,
      totalCommentLines,
      totalBlankLines,
      byCategory,
    },
  };
}

// ── Formatters ──────────────────────────────────────────────────────────

function formatTable(report: LocReport): string {
  const lines: string[] = [];

  // Header
  const hdr = "│ Module                    │ Total  │ Code   │ Comments │ Blank  │";
  const sep = "├───────────────────────────┼────────┼────────┼──────────┼────────┤";

  lines.push("┌─ LOC Report ──────────────────────────────────────────────────────────────┐");
  lines.push(`│ Project: ${report.projectRoot.padEnd(56)} │`);
  lines.push(`│ Generated: ${report.generatedAt.padEnd(53)} │`);
  lines.push("├───────────────────────────┬────────┬────────┬──────────┬────────┤");
  lines.push("│ Category / Module         │ Total  │ Code   │ Comments │ Blank  │");
  lines.push("├───────────────────────────┼────────┼────────┼──────────┼────────┤");

  // Group by category
  let lastCategory = "";
  for (const m of report.modules) {
    if (m.category !== lastCategory) {
      lastCategory = m.category;
      const catSummary = report.summary.byCategory[m.category];
      lines.push(`│ ▶ ${m.category.toUpperCase().padEnd(24)} │ ${String(catSummary.totalLines).padStart(6)} │ ${String(catSummary.codeLines).padStart(6)} │ ${String(catSummary.commentLines).padStart(8)} │ ${String(catSummary.blankLines).padStart(6)} │`);
      lines.push("├───────────────────────────┼────────┼────────┼──────────┼────────┤");
    }

    const name = m.module.length > 25 ? m.module.slice(0, 22) + "..." : m.module;
    lines.push(
      `│   ${name.padEnd(23)} │ ${String(m.totalLines).padStart(6)} │ ${String(m.codeLines).padStart(6)} │ ${String(m.commentLines).padStart(8)} │ ${String(m.blankLines).padStart(6)} │`,
    );
  }

  lines.push("├───────────────────────────┼────────┼────────┼──────────┼────────┤");
  lines.push(
    `│ TOTAL                     │ ${String(report.summary.totalLines).padStart(6)} │ ${String(report.summary.totalCodeLines).padStart(6)} │ ${String(report.summary.totalCommentLines).padStart(8)} │ ${String(report.summary.totalBlankLines).padStart(6)} │`,
  );
  lines.push("└───────────────────────────┴────────┴────────┴──────────┴────────┘");

  return lines.join("\n");
}

// ── CLI Entry ───────────────────────────────────────────────────────────

export function executeCountLocCommand(
  opts: { projectRoot?: string; format?: "table" | "json" } = {},
): { exitCode: number; stdout: string; stderr: string } {
  const format = opts.format ?? "table";
  const projectRoot = process.cwd();
  try {
    const customRoot = opts.projectRoot ? resolve(opts.projectRoot) : projectRoot;

    if (!existsSync(customRoot)) {
      return {
        exitCode: 1,
        stdout: "",
        stderr: JSON.stringify({ error: "not_found", message: `Path does not exist: ${customRoot}` }, null, 2) + "\n",
      };
    }

    if (!statSync(customRoot).isDirectory()) {
      return {
        exitCode: 1,
        stdout: "",
        stderr: JSON.stringify({ error: "invalid_input", message: `Not a directory: ${customRoot}` }, null, 2) + "\n",
      };
    }

    const report = buildLocReport(customRoot);

    if (format === "json") {
      return { exitCode: 0, stdout: JSON.stringify(report, null, 2) + "\n", stderr: "" };
    }

    return { exitCode: 0, stdout: formatTable(report) + "\n", stderr: "" };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    return {
      exitCode: 2,
      stdout: "",
      stderr: JSON.stringify({ error: "failure", message }, null, 2) + "\n",
    };
  }
}

// ── CLI entry point ─────────────────────────────────────────────────────

function parseCliArgs(rawArgs: string[]): { projectRoot?: string; format: "table" | "json" } {
  const result: { projectRoot?: string; format: "table" | "json" } = { format: "table" };
  for (const arg of rawArgs) {
    if (arg === "--json") {
      result.format = "json";
    } else if (!arg.startsWith("-")) {
      result.projectRoot = arg;
    }
  }
  return result;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const cliArgs = parseCliArgs(process.argv.slice(2));
  const result = executeCountLocCommand({
    projectRoot: cliArgs.projectRoot,
    format: cliArgs.format,
  });
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
