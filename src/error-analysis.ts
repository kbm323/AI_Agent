/**
 * Error frequency & type distribution analysis (Sub-AC 2.2).
 *
 * Parses raw linter output (ruff, mypy, node --check) into structured error
 * records, groups by error code/type, computes frequency counts and percentage
 * distribution, and emits a typed summary as JSON.
 */

// ── Linter error record ────────────────────────────────────────────────

/** A single parsed linter error record. */
export interface LinterErrorRecord {
  /** Source linter name (ruff, mypy, node-check). */
  linter: string;
  /** File path relative to project root. */
  file: string;
  /** 1-indexed line number where the error occurs. */
  line: number;
  /** 1-indexed column number where the error occurs (0 if unknown). */
  column: number;
  /** Error code or rule identifier (e.g. "F401", "SyntaxError"). */
  code: string;
  /** Human-readable error message text. */
  message: string;
  /** Classification bucket for grouping. */
  type: LinterErrorType;
}

/** Classification bucket derived from the error code. */
export type LinterErrorType =
  | "unused_import"
  | "unused_variable"
  | "import_order"
  | "style_formatting"
  | "type_error"
  | "syntax_error"
  | "undefined_name"
  | "line_too_long"
  | "missing_whitespace"
  | "missing_blank_line"
  | "comprehension_style"
  | "simplify_expression"
  | "bugbear_warning"
  | "naming_convention"
  | "pyupgrade_suggestion"
  | "other";

// ── Frequency distribution ─────────────────────────────────────────────

/** A single frequency bucket for one error code/type. */
export interface ErrorFrequencyBucket {
  /** The error code or rule identifier. */
  code: string;
  /** The classification type. */
  type: LinterErrorType;
  /** Number of occurrences. */
  count: number;
  /** Percentage of total errors. */
  percent: number;
}

/** Linter-level frequency summary. */
export interface LinterFrequencySummary {
  /** Linter name. */
  linter: string;
  /** Total error count for this linter. */
  totalErrors: number;
  /** Number of distinct error codes. */
  distinctCodes: number;
  /** Frequency buckets sorted by count descending. */
  buckets: ErrorFrequencyBucket[];
  /** Error counts by type category. */
  byType: Record<LinterErrorType, number>;
}

/** Top-level typed summary merging all linters. */
export interface ErrorFrequencySummary {
  schemaVersion: "error-frequency.v1";
  /** ISO 8601 timestamp of analysis. */
  timestamp: string;
  /** Total errors across all linters. */
  totalErrors: number;
  /** Total distinct error codes across all linters. */
  distinctCodes: number;
  /** Per-linter breakdowns. */
  linters: Record<string, LinterFrequencySummary>;
  /** Global type distribution (aggregated across linters). */
  globalTypeDistribution: Record<string, number>;
}

// ── Ruff code → type classification ────────────────────────────────────

/**
 * Map a ruff error code prefix to a classification bucket.
 *
 * Ruff codes are in the form "F401", "E302", "W291", "I001", "UP004", etc.
 */
function classifyRuffCode(code: string): LinterErrorType {
  const prefix = code.replace(/[0-9]/g, "").toUpperCase();
  switch (prefix) {
    case "F": {
      // pyflakes
      if (code === "F401") return "unused_import";
      if (code === "F841") return "unused_variable";
      if (code === "F821") return "undefined_name";
      return "bugbear_warning";
    }
    case "E": {
      // pycodestyle errors
      if (code === "E302" || code === "E305") return "missing_blank_line";
      if (code === "E501") return "line_too_long";
      return "style_formatting";
    }
    case "W": {
      // pycodestyle warnings
      if (code === "W291" || code === "W293") return "missing_whitespace";
      return "style_formatting";
    }
    case "I":
      return "import_order";
    case "N":
      return "naming_convention";
    case "UP":
      return "pyupgrade_suggestion";
    case "B":
      return "bugbear_warning";
    case "C":
      return "comprehension_style";
    case "SIM":
      return "simplify_expression";
    default:
      return "other";
  }
}

// ── Ruff output parser ──────────────────────────────────────────────────

/**
 * Ruff error line format:
 *   file:line:col: CODE message
 * e.g.:
 *   src/app.py:1:8: F401 [*] `os` imported but unused
 */
const RUFF_LINE_RE =
  /^(?<file>.+?):(?<line>\d+):(?<col>\d+): (?<code>[A-Z]+\d+) (?<rest>.+)$/;

// ── Mypy output parser ─────────────────────────────────────────────────

/**
 * Mypy error line format:
 *   file:line: (error|note|warning): message  [error-code]
 * e.g.:
 *   src/app.py:5: error: Incompatible types in assignment
 *       (expression has type "int", variable has type "str")  [assignment]
 */
const MYPY_LINE_RE =
  /^(?<file>.+?):(?<line>\d+): (?<severity>error|note|warning): (?<message>.+?)(?:\s+\[(?<code>[^\]]+)\])?\s*$/;

// ── Node --check output parser ─────────────────────────────────────────

/**
 * Node --check error format (compact single-line form):
 *   file:line
 *   message
 *   ^^^^^^
 *
 * We detect the `SyntaxError` keyword to extract the code.
 */
const NODE_SYNTAX_RE = /^(?<file>[^:]+):(?<line>\d+)\s*$/;

// ── Single-result parsers ──────────────────────────────────────────────

function parseRuffOutput(stdout: string): LinterErrorRecord[] {
  const records: LinterErrorRecord[] = [];
  const lines = stdout.split("\n");
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (line.length === 0) continue;
    // Skip summary lines like "Found 3 errors."
    if (/^(Found|All checks passed)/.test(line)) continue;
    const match = line.match(RUFF_LINE_RE);
    if (!match) continue;
    const { file, code } = match.groups!;
    const lineNum = Number.parseInt(match.groups!.line, 10);
    const colNum = Number.parseInt(match.groups!.col, 10);
    const message = match.groups!.rest.trim();
    records.push({
      linter: "ruff",
      file,
      line: lineNum,
      column: colNum,
      code,
      message,
      type: classifyRuffCode(code),
    });
  }
  return records;
}

function parseMypyOutput(stdout: string, stderr: string): LinterErrorRecord[] {
  const records: LinterErrorRecord[] = [];
  const combined = [stdout, stderr].filter(Boolean).join("\n");
  const lines = combined.split("\n");
  let currentFile = "";
  let currentLine = 0;
  let currentMessage = "";
  let currentCode = "type-error";
  let pending = false;

  function flushRecord(): void {
    if (!pending || currentFile.length === 0) return;
    records.push({
      linter: "mypy",
      file: currentFile,
      line: currentLine,
      column: 0,
      code: currentCode,
      message: currentMessage,
      type: classifyMypyCode(currentCode),
    });
    pending = false;
    currentMessage = "";
    currentCode = "type-error";
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    // Main error line: file:line: severity: message [code]
    const match = line.match(MYPY_LINE_RE);
    if (match && match.groups!.severity === "error") {
      flushRecord();
      const { file, message, code } = match.groups!;
      currentFile = file!;
      currentLine = Number.parseInt(match.groups!.line, 10);
      currentMessage = message!.trim();
      currentCode = code ?? "type-error";
      pending = true;
      continue;
    }

    // Continuation lines (indented)
    if (pending && /^\s{4,}/.test(line)) {
      const noteMatch = line.match(/^\s+(?<note>.+)$/);
      if (noteMatch && noteMatch.groups!.note.trim().length > 0) {
        // Append note to message if it looks like detail
        const note = noteMatch.groups!.note.trim();
        if (!currentMessage.includes(note)) {
          currentMessage += ` (${note})`;
        }
      }
      continue;
    }

    // "Success: no issues found" or similar summaries → skip
    if (/^(Success|Found \d+ error)/i.test(line)) {
      continue;
    }

    // Any non-continuation, non-error line flushes the pending record
    if (pending) {
      flushRecord();
    }
  }

  flushRecord();
  return records;
}

function classifyMypyCode(code: string): LinterErrorType {
  switch (code) {
    case "assignment":
    case "arg-type":
    case "return-value":
    case "attr-defined":
    case "union-attr":
    case "index":
    case "operator":
    case "comparison-overlap":
    case "misc":
      return "type_error";
    case "name-defined":
      return "undefined_name";
    case "syntax":
      return "syntax_error";
    case "call-overload":
    case "valid-type":
    case "var-annotated":
      return "type_error";
    default:
      return "type_error"; // all mypy issues are fundamentally type-related
  }
}

function parseNodeCheckOutput(
  stdout: string,
  stderr: string,
): LinterErrorRecord[] {
  const records: LinterErrorRecord[] = [];
  const combined = [stdout, stderr].filter(Boolean).join("\n");
  const lines = combined.split("\n");

  let currentFile = "";
  let currentLine = 0;
  let messageLines: string[] = [];

  function flushNodeError(): void {
    if (currentFile.length === 0) return;
    const message = messageLines.join(" ").trim() || "Unknown syntax error";
    records.push({
      linter: "node-check",
      file: currentFile,
      line: currentLine,
      column: 0,
      code: "SyntaxError",
      message,
      type: "syntax_error",
    });
    messageLines = [];
    currentFile = "";
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    if (line.length === 0) continue;

    // Match "file:line" pattern
    const match = line.match(NODE_SYNTAX_RE);
    if (match) {
      flushNodeError();
      currentFile = match.groups!.file;
      currentLine = Number.parseInt(match.groups!.line, 10);
      messageLines = [];
      continue;
    }

    // If it starts with ^^^^ (caret line), skip
    if (/^\^+/.test(line)) continue;

    // Otherwise it's a message line
    if (currentFile.length > 0) {
      messageLines.push(line.trim());
      // Flush on next blank or at end
    }
  }

  flushNodeError();
  return records;
}

// ── Frequency analysis ─────────────────────────────────────────────────

/**
 * Parse a single linter's raw stdout/stderr into structured error records.
 */
export function parseLinterErrors(
  linter: string,
  stdout: string,
  stderr: string,
): LinterErrorRecord[] {
  switch (linter) {
    case "ruff":
      return parseRuffOutput(stdout);
    case "mypy":
      return parseMypyOutput(stdout, stderr);
    case "node-check":
      return parseNodeCheckOutput(stdout, stderr);
    default:
      return [];
  }
}

/**
 * Parse all linter results from a lint-execution.v1 artifact into error records.
 */
export function parseAllLinterErrors(results: Array<{
  linter: string;
  stdout: string;
  stderr: string;
  passed?: boolean;
}>): LinterErrorRecord[] {
  return results.flatMap((r) => parseLinterErrors(r.linter, r.stdout, r.stderr));
}

/**
 * Group error records by code/type and compute frequency distribution for a
 * single linter.
 */
export function computeLinterFrequency(
  records: LinterErrorRecord[],
): LinterFrequencySummary {
  const codeCounts = new Map<string, number>();
  const typeCounts: Record<LinterErrorType, number> = Object.fromEntries(
    TYPE_KEYS.map((k) => [k, 0]),
  ) as Record<LinterErrorType, number>;

  for (const record of records) {
    codeCounts.set(record.code, (codeCounts.get(record.code) ?? 0) + 1);
    typeCounts[record.type] = (typeCounts[record.type] ?? 0) + 1;
  }

  const totalErrors = records.length;
  const distinctCodes = codeCounts.size;

  const buckets: ErrorFrequencyBucket[] = [...codeCounts.entries()]
    .map(([code, count]) => {
      const record = records.find((r) => r.code === code)!;
      return {
        code,
        type: record.type,
        count,
        percent: totalErrors > 0 ? roundPercent((count / totalErrors) * 100) : 0,
      };
    })
    .sort((a, b) => b.count - a.count);

  const linter = records.length > 0 ? records[0].linter : "unknown";

  return { linter, totalErrors, distinctCodes, buckets, byType: typeCounts };
}

/**
 * Produce a full typed error frequency summary across all linter error records.
 */
export function buildErrorFrequencySummary(
  allRecords: LinterErrorRecord[],
): ErrorFrequencySummary {
  const linterGroups = new Map<string, LinterErrorRecord[]>();
  for (const record of allRecords) {
    const group = linterGroups.get(record.linter) ?? [];
    group.push(record);
    linterGroups.set(record.linter, group);
  }

  const linterSummaries: Record<string, LinterFrequencySummary> = {};
  const globalTypeDistribution: Record<string, number> = Object.fromEntries(
    TYPE_KEYS.map((k) => [k, 0]),
  );

  let totalErrors = 0;
  let distinctCodes = 0;

  const globalCodeSet = new Set<string>();

  for (const [linter, records] of linterGroups) {
    const summary = computeLinterFrequency(records);
    linterSummaries[linter] = summary;
    totalErrors += summary.totalErrors;
    for (const bucket of summary.buckets) {
      globalCodeSet.add(`${linter}:${bucket.code}`);
    }
    for (const type of TYPE_KEYS) {
      globalTypeDistribution[type] += summary.byType[type] ?? 0;
    }
  }

  distinctCodes = globalCodeSet.size;

  return {
    schemaVersion: "error-frequency.v1",
    timestamp: new Date().toISOString(),
    totalErrors,
    distinctCodes,
    linters: linterSummaries,
    globalTypeDistribution,
  };
}

/**
 * Parse a lint-results.json artifact (schemaVersion "lint-execution.v1") and
 * produce a full typed ErrorFrequencySummary.
 */
export function analyzeLintResultsArtifact(artifact: {
  schemaVersion: string;
  results: Array<{ linter: string; stdout: string; stderr: string }>;
}): ErrorFrequencySummary {
  if (artifact.schemaVersion !== "lint-execution.v1") {
    throw new TypeError(
      `expected lint-execution.v1 artifact, got ${artifact.schemaVersion}`,
    );
  }

  if (!Array.isArray(artifact.results)) {
    throw new TypeError("artifact.results must be an array");
  }

  const allRecords = parseAllLinterErrors(artifact.results);
  const summary = buildErrorFrequencySummary(allRecords);

  // Ensure every linter from the artifact has a summary entry, even with 0 errors
  for (const result of artifact.results) {
    if (!summary.linters[result.linter]) {
      summary.linters[result.linter] = {
        linter: result.linter,
        totalErrors: 0,
        distinctCodes: 0,
        buckets: [],
        byType: Object.fromEntries(
          TYPE_KEYS.map((k) => [k, 0]),
        ) as Record<LinterErrorType, number>,
      };
    }
  }

  return summary;
}

// ── Helpers ────────────────────────────────────────────────────────────

const TYPE_KEYS: LinterErrorType[] = [
  "unused_import",
  "unused_variable",
  "import_order",
  "style_formatting",
  "type_error",
  "syntax_error",
  "undefined_name",
  "line_too_long",
  "missing_whitespace",
  "missing_blank_line",
  "comprehension_style",
  "simplify_expression",
  "bugbear_warning",
  "naming_convention",
  "pyupgrade_suggestion",
  "other",
];

function roundPercent(value: number): number {
  return Math.round(value * 10) / 10;
}
