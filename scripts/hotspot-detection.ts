import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve, relative, basename, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

// ── Types ──────────────────────────────────────────────────────────────

export interface ComplexityEntry {
  /** File path relative to project root */
  file: string;
  /** Function or method name */
  functionName: string;
  /** Cyclomatic complexity number (CCN) */
  complexity: number;
  /** Non-comment lines of code */
  linesOfCode: number;
  /** Radon rank (A-F), or undefined for lizard results */
  rank?: string;
  /** Which tool produced this entry */
  toolSource: "radon" | "lizard";
}

export interface FileComplexitySummary {
  file: string;
  functionCount: number;
  maxComplexity: number;
  avgComplexity: number;
  totalLinesOfCode: number;
  warnings: string[];
  entries: ComplexityEntry[];
}

export interface DuplicationEntry {
  file: string;
  duplicatedLines: number;
  duplicatedTokens: number;
  totalLines: number;
  totalTokens: number;
  /** Line-based duplication percentage */
  percentage: number;
  /** Token-based duplication percentage */
  percentageTokens: number;
  clones: number;
}

export interface FileHotspot {
  file: string;
  complexityScore: number;
  duplicationScore: number;
  combinedScore: number;
  maxComplexity: number;
  avgComplexity: number;
  totalLinesOfCode: number;
  duplicatedLines: number;
  duplicationPercent: number;
  functionCount: number;
  warnings: string[];
  /** The top-N rank (1-based, 1 = hottest) */
  rank: number;
}

export interface ToolAvailability {
  available: boolean;
  version?: string;
  path?: string;
  error?: string;
}

export interface HotspotReport {
  schemaVersion: "hotspot-report.v1";
  generatedAt: string;
  projectRoot: string;
  hotspots: FileHotspot[];
  toolsUsed: {
    radon: ToolAvailability;
    lizard: ToolAvailability;
    jscpd: ToolAvailability;
  };
  summary: {
    totalFilesAnalyzed: number;
    hotspotCount: number;
    topHotspots: FileHotspot[];
  };
}

export interface HotspotDetectionOptions {
  projectRoot?: string;
  /** File globs to include (passed to tools) */
  include?: string[];
  /** Limit output to top N hotspots (default 20) */
  topN?: number;
  /** Complexity weight in combined score (default 0.6) */
  complexityWeight?: number;
  /** Duplication weight in combined score (default 0.4) */
  duplicationWeight?: number;
  /** If true, use only TypeScript built-in analysis (no external tools) */
  builtinOnly?: boolean;
}

// ── Constants ──────────────────────────────────────────────────────────

const DEFAULT_TOP_N = 20;
const DEFAULT_COMPLEXITY_WEIGHT = 0.6;
const DEFAULT_DUPLICATION_WEIGHT = 0.4;

const TS_EXTENSIONS = new Set([".ts", ".tsx", ".mts", ".cts"]);
const PY_EXTENSIONS = new Set([".py", ".pyi"]);
const JS_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs"]);

// ── Tool discovery ─────────────────────────────────────────────────────

function resolveTool(command: string): ToolAvailability {
  try {
    const result = execFileSync("which", [command], { encoding: "utf8", timeout: 5000 }).trim();
    if (!result) {
      return { available: false, path: undefined };
    }
    let version: string;
    try {
      version = execFileSync(command, ["--version"], { encoding: "utf8", timeout: 5000 }).trim().split("\n")[0];
    } catch {
      version = undefined;
    }
    return { available: true, path: result, version };
  } catch {
    return { available: false, path: undefined };
  }
}

// ── radon (Python complexity tool) ─────────────────────────────────────

function runRadon(projectRoot: string, files: string[]): ComplexityEntry[] {
  const pyFiles = files.filter((f) => PY_EXTENSIONS.has(extname(f)));
  if (pyFiles.length === 0) return [];

  // radon cc -j <paths>
  let raw: string;
  try {
    raw = execFileSync("radon", ["cc", "-j", ...pyFiles], {
      encoding: "utf8",
      timeout: 60000,
      cwd: projectRoot,
    }).trim();
  } catch {
    return []; // Tool failure → no complexity data from radon
  }

  if (!raw) return [];

  const data: Record<string, Array<{
    type: string;
    rank: string;
    endline: number;
    name: string;
    complexity: number;
    lineno: number;
    closures: unknown[];
  }>> = JSON.parse(raw);

  const entries: ComplexityEntry[] = [];
  for (const [filePath, funcs] of Object.entries(data)) {
    for (const func of funcs) {
      entries.push({
        file: filePath,
        functionName: func.name,
        complexity: func.complexity,
        linesOfCode: func.endline - func.lineno + 1,
        rank: func.rank,
        toolSource: "radon",
      });
    }
  }

  return entries;
}

// ── lizard (multi-language complexity tool) ────────────────────────────

function runLizard(projectRoot: string, files: string[]): ComplexityEntry[] {
  const targetFiles = files.filter(
    (f) => TS_EXTENSIONS.has(extname(f)) || JS_EXTENSIONS.has(extname(f)) || PY_EXTENSIONS.has(extname(f)),
  );
  if (targetFiles.length === 0) return [];

  // Lizard exits with code 1 when there are warnings.
  // Capture stdout from both success and error cases.
  let raw: string;
  try {
    raw = execFileSync("lizard", targetFiles, {
      encoding: "utf8",
      timeout: 60000,
      cwd: projectRoot,
    });
  } catch (err) {
    // Lizard wrote stdout before exiting non-zero; extract it.
    const execErr = err as { stdout?: string; stderr?: string };
    raw = execErr.stdout ?? "";
  }

  return parseLizardOutput(raw, projectRoot);
}

function parseLizardOutput(raw: string, projectRoot: string): ComplexityEntry[] {
  const entries: ComplexityEntry[] = [];
  const lines = raw.split("\n");

  // The function table comes after a header line, a separator, then data rows.
  // The table ends at "N file(s) analyzed." or at a section boundary.
  // There is a second table in the "Warnings" section — we parse both.
  // Structure:
  //   "  NLOC    CCN   token  PARAM  length  location  "   <- header
  //   "------------------------------------------------"   <- separator (skip)
  //   "      21      1    109      0      24 funcName@..." <- data
  //   ...
  //   "2 file analyzed."                                   <- end of table

  let inFunctionTable = false;
  let pastSeparator = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // Detect table header
    if (trimmed.includes("NLOC") && trimmed.includes("CCN") && trimmed.includes("location")) {
      inFunctionTable = true;
      pastSeparator = false;
      continue;
    }

    // Separator line (dashes) — skip but stay in table
    if (inFunctionTable && !pastSeparator && trimmed.match(/^-{10,}$/)) {
      pastSeparator = true;
      continue;
    }

    // Separator line (equals) at section boundaries
    if (trimmed.match(/^={10,}$/)) {
      inFunctionTable = false;
      pastSeparator = false;
      continue;
    }

    // End of table markers
    if (
      inFunctionTable &&
      pastSeparator &&
      (trimmed.match(/^\d+\s+file(s?) analyzed/) ||
        trimmed.match(/^No thresholds exceeded/) ||
        trimmed.match(/^!!!! Warnings/) ||
        trimmed.match(/^Total nloc/))
    ) {
      inFunctionTable = false;
      pastSeparator = false;
      continue;
    }

    if (!inFunctionTable || !pastSeparator) continue;

    // Parse: NLOC CCN token PARAM length location
    const match = trimmed.match(/^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+)$/);
    if (!match) continue;

    const nloc = Number.parseInt(match[1], 10);
    const ccn = Number.parseInt(match[2], 10);
    const location = match[6];
    // location format: functionName@start-end@filepath
    const atIndex = location.lastIndexOf("@");
    const funcName = atIndex >= 0 ? location.slice(0, atIndex) : location;
    const filePath = atIndex >= 0 ? location.slice(atIndex + 1) : "";

    if (!filePath) continue;

    // Make path relative to project root
    const absPath = resolve(projectRoot, filePath);
    const relPath = relative(projectRoot, absPath);

    entries.push({
      file: relPath,
      functionName: funcName.split("@")[0], // strip @start-end if present
      complexity: ccn,
      linesOfCode: nloc,
      toolSource: "lizard",
    });
  }

  // Deduplicate by file+functionName (warnings section may repeat entries)
  const seen = new Set<string>();
  return entries.filter((e) => {
    const key = `${e.file}::${e.functionName}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ── jscpd (copy-paste detection) ───────────────────────────────────────

function runJscpd(projectRoot: string, files: string[]): DuplicationEntry[] {
  const targetFiles = files.filter(
    (f) => TS_EXTENSIONS.has(extname(f)) || JS_EXTENSIONS.has(extname(f)) || PY_EXTENSIONS.has(extname(f)),
  );
  if (targetFiles.length === 0) return [];

  // jscpd needs a temp directory for its report
  const tmpDir = mkdtempSync(join(tmpdir(), "jscpd-"));
  try {
    // Write .jscpd.json config to tmpDir
    const config = {
      minLines: 5,
      maxLines: 200,
      minTokens: 50,
      reporters: ["json"],
      output: tmpDir,
      path: targetFiles.map((f) => resolve(projectRoot, f)),
      // jscpd has a path scanning issue with absolute paths; use process options instead
    };

    // jscpd can accept paths as positional args
    execFileSync(
      "jscpd",
      [
        ...targetFiles.map((f) => resolve(projectRoot, f)),
        "--min-lines",
        "5",
        "--max-lines",
        "200",
        "--min-tokens",
        "50",
        "--reporters",
        "json",
        "--output",
        tmpDir,
        "--silent",
      ],
      { encoding: "utf8", timeout: 120000, cwd: projectRoot },
    );
  } catch {
    // jscpd exits with non-zero sometimes but still produces output
  }

  // Read the report
  const reportPath = join(tmpDir, "jscpd-report.json");
  let entries: DuplicationEntry[] = [];

  if (existsSync(reportPath)) {
    try {
      const raw = execFileSync("cat", [reportPath], { encoding: "utf8", timeout: 5000 });
      const report = JSON.parse(raw);
      const stats = report?.statistics;
      if (stats?.formats) {
        for (const [format, formatStats] of Object.entries(stats.formats)) {
          const fmt = formatStats as {
            sources?: Record<string, {
              lines: number; tokens: number; clones: number;
              duplicatedLines: number; duplicatedTokens: number;
              percentage: number; percentageTokens: number;
            }>;
            total?: { lines: number; tokens: number; clones: number };
          };

          if (fmt.sources) {
            for (const [filePath, fileStats] of Object.entries(fmt.sources)) {
              // Normalize to relative path
              const absPath = resolve(projectRoot, filePath);
              const relPath = relative(projectRoot, absPath);
              entries.push({
                file: relPath,
                duplicatedLines: fileStats.duplicatedLines ?? 0,
                duplicatedTokens: fileStats.duplicatedTokens ?? 0,
                totalLines: fileStats.lines ?? 0,
                totalTokens: fileStats.tokens ?? 0,
                percentage: fileStats.percentage ?? 0,
                percentageTokens: fileStats.percentageTokens ?? 0,
                clones: fileStats.clones ?? 0,
              });
            }
          }
        }
      }
    } catch {
      // Report parsing failed
    }
  }

  // Cleanup
  try { rmSync(tmpDir, { recursive: true, force: true }); } catch { /* ignore */ }
  return entries;
}

// ── Built-in complexity analysis (fallback) ────────────────────────────

function extname(filePath: string): string {
  const lastDot = filePath.lastIndexOf(".");
  return lastDot >= 0 ? filePath.slice(lastDot) : "";
}

function computeBuiltinComplexity(files: string[], projectRoot: string): ComplexityEntry[] {
  const entries: ComplexityEntry[] = [];
  for (const file of files) {
    const fullPath = join(projectRoot, file);
    let content: string;
    try {
      content = execFileSync("cat", [fullPath], { encoding: "utf8", timeout: 10000 });
    } catch {
      continue;
    }

    const lines = content.split("\n");
    let functionCount = 0;
    let totalComplexity = 0;
    let maxComplexity = 0;

    // Count functions (TypeScript/JavaScript and Python patterns)
    const ext = extname(file);
    let funcMatches: RegExpMatchArray | null;

    if (PY_EXTENSIONS.has(ext)) {
      // Python: def funcname( or async def funcname(
      funcMatches = content.match(/^\s*(async\s+)?def\s+\w+/gm);
    } else {
      // TypeScript/JavaScript: function, arrow, method patterns
      funcMatches = content.match(/\b(function\s+\w+|=>|async\s+function|\w+\s*=\s*(async\s+)?\([^)]*\)\s*=>)/g);
    }
    functionCount = funcMatches ? funcMatches.length : 0;

    if (functionCount === 0) continue;

    // Count total branches with language-specific patterns
    let totalBranches = 0;

    if (PY_EXTENSIONS.has(ext)) {
      // Python-specific branch detection
      // Count: if, elif, for, while, except, with, and, or, ternary (x if cond else y)
      const pyIfs = (content.match(/\bif\s+.+:/g) || []).length;
      const pyElifs = (content.match(/\belif\s+.+:/g) || []).length;
      const pyFors = (content.match(/\bfor\s+.+:/g) || []).length;
      const pyWhiles = (content.match(/\bwhile\s+.+:/g) || []).length;
      const pyExcepts = (content.match(/\bexcept\b/g) || []).length;
      const pyWiths = (content.match(/\bwith\s+.+:/g) || []).length;
      // Ternary: x if cond else y  — count ' if ' occurrences that are likely ternary
      const pyTernaries = (content.match(/\bif\s+\S.+\s+else\b/g) || []).length;
      // Logical operators: ' and ' and ' or ' are branch points in comprehensions/conditions
      const pyAnds = (content.match(/\band\b/g) || []).length;
      const pyOrs = (content.match(/\bor\b/g) || []).length;
      totalBranches = pyIfs + pyElifs + pyFors + pyWhiles + pyExcepts + pyWiths + pyTernaries
        + Math.floor(pyAnds / 2) + Math.floor(pyOrs / 2);
    } else {
      // JS/TS branch patterns
      const branchPatterns = [
        /\bif\s*\(/g,
        /\belse\s+if\b/g,
        /\bfor\s*\(/g,
        /\bwhile\s*\(/g,
        /\bcase\s+/g,
        /\bcatch\s*\(/g,
        /\?\s*[^:]+:/g,     // ternary (approximate)
      ];
      for (const pattern of branchPatterns) {
        const matches = content.match(pattern);
        if (matches) totalBranches += matches.length;
      }

      // Also count && and || as branch points
      const logicalOps = content.match(/&&|\|\|/g);
      const logicalCount = logicalOps ? Math.floor(logicalOps.length / 2) : 0;
      totalBranches += logicalCount;
    }

    // Complexity per function approximation
    // CCN ≈ 1 + branches_per_function
    const avgComplexity = functionCount > 0 ? 1 + Math.round(totalBranches / functionCount) : 1;
    // Estimate max as proportionally higher than average for file-level summary
    maxComplexity = functionCount > 1
      ? Math.round(avgComplexity * 1.5)
      : avgComplexity;

    entries.push({
      file,
      functionName: "(file-level)",
      complexity: maxComplexity,
      linesOfCode: lines.filter((l) => l.trim() && !l.trim().startsWith("//") && !l.trim().startsWith("/*") && !l.trim().startsWith("*")).length,
      toolSource: "lizard", // mark as lizard for compatibility
    });
  }

  return entries;
}

// ── Built-in duplication detection (fallback) ──────────────────────────

function computeBuiltinDuplication(files: string[], projectRoot: string): DuplicationEntry[] {
  // Simple token-based fingerprinting
  const entries: DuplicationEntry[] = [];
  const fingerprints = new Map<string, string[]>(); // fingerprint -> files

  for (const file of files) {
    const fullPath = join(projectRoot, file);
    let content: string;
    try {
      content = execFileSync("cat", [fullPath], { encoding: "utf8", timeout: 10000 });
    } catch {
      continue;
    }

    const lines = content.split("\n");
    const codeLines = lines.filter((l) => {
      const t = l.trim();
      return t && !t.startsWith("//") && !t.startsWith("/*") && !t.startsWith("*") && !t.startsWith("import");
    });

    for (let i = 0; i < codeLines.length - 2; i++) {
      const block = codeLines.slice(i, i + 3).join("\n").replace(/\s+/g, " ").trim().toLowerCase();
      if (block.length < 20) continue;

      const existing = fingerprints.get(block);
      if (existing) {
        existing.push(file);
      } else {
        fingerprints.set(block, [file]);
      }
    }
  }

  // Calculate per-file duplication
  const fileDupLines = new Map<string, number>();

  for (const [, dupFiles] of fingerprints) {
    if (dupFiles.length < 2) continue; // no duplication
    for (const f of dupFiles) {
      fileDupLines.set(f, (fileDupLines.get(f) ?? 0) + 3); // each block = 3 lines
    }
  }

  for (const file of files) {
    const fullPath = join(projectRoot, file);
    let totalLines = 0;
    try {
      const content = execFileSync("cat", [fullPath], { encoding: "utf8", timeout: 5000 });
      totalLines = content.split("\n").length;
    } catch {
      totalLines = 0;
    }

    const dupLines = fileDupLines.get(file) ?? 0;
    entries.push({
      file,
      duplicatedLines: Math.min(dupLines, totalLines),
      duplicatedTokens: 0,
      totalLines,
      totalTokens: 0,
      percentage: totalLines > 0 ? (dupLines / totalLines) * 100 : 0,
      percentageTokens: 0,
      clones: 0,
    });
  }

  return entries;
}

// ── Hotspot aggregation ────────────────────────────────────────────────

function aggregateComplexity(entries: ComplexityEntry[]): Map<string, FileComplexitySummary> {
  const map = new Map<string, FileComplexitySummary>();

  for (const entry of entries) {
    let summary = map.get(entry.file);
    if (!summary) {
      summary = {
        file: entry.file,
        functionCount: 0,
        maxComplexity: 0,
        avgComplexity: 0,
        totalLinesOfCode: 0,
        warnings: [],
        entries: [],
      };
      map.set(entry.file, summary);
    }

    summary.functionCount++;
    summary.maxComplexity = Math.max(summary.maxComplexity, entry.complexity);
    summary.totalLinesOfCode += entry.linesOfCode;
    summary.entries.push(entry);

    if (entry.complexity > 15) {
      summary.warnings.push(`high complexity: ${entry.functionName} (CCN=${entry.complexity})`);
    }
  }

  // Compute averages
  for (const summary of map.values()) {
    summary.avgComplexity =
      summary.functionCount > 0
        ? Math.round((summary.entries.reduce((s, e) => s + e.complexity, 0) / summary.functionCount) * 10) / 10
        : 0;
  }

  return map;
}

function computeHotspots(
  complexityMap: Map<string, FileComplexitySummary>,
  duplicationEntries: DuplicationEntry[],
  options: Required<Pick<HotspotDetectionOptions, "topN" | "complexityWeight" | "duplicationWeight">>,
): FileHotspot[] {
  const dupMap = new Map<string, DuplicationEntry>();
  for (const dup of duplicationEntries) {
    dupMap.set(dup.file, dup);
  }

  // Collect all unique files
  const allFiles = new Set<string>();
  for (const f of complexityMap.keys()) allFiles.add(f);
  for (const f of dupMap.keys()) allFiles.add(f);

  // Compute raw scores for normalization
  const fileScores: Array<{
    file: string;
    rawComplexity: number;
    rawDuplication: number;
    complexity: FileComplexitySummary | undefined;
    duplication: DuplicationEntry | undefined;
  }> = [];

  for (const file of allFiles) {
    const complexity = complexityMap.get(file);
    const duplication = dupMap.get(file);

    // Complexity score: max(CCN) weighted by function count and avg
    const rawComplexity = complexity
      ? complexity.maxComplexity * Math.log2(Math.max(complexity.functionCount, 1) + 1) + complexity.avgComplexity
      : 0;

    // Duplication score: percentage * (1 + log of dup lines)
    const rawDuplication = duplication
      ? (duplication.percentage / 100) * (1 + Math.log2(Math.max(duplication.duplicatedLines, 1)))
      : 0;

    fileScores.push({
      file,
      rawComplexity,
      rawDuplication,
      complexity,
      duplication,
    });
  }

  // Normalize scores to [0, 1]
  const maxRawComplexity = Math.max(...fileScores.map((s) => s.rawComplexity), 1);
  const maxRawDuplication = Math.max(...fileScores.map((s) => s.rawDuplication), 1);

  const hotspots: FileHotspot[] = fileScores.map((fs) => {
    const complexityScore = maxRawComplexity > 0 ? fs.rawComplexity / maxRawComplexity : 0;
    const duplicationScore = maxRawDuplication > 0 ? fs.rawDuplication / maxRawDuplication : 0;
    const combinedScore =
      complexityScore * options.complexityWeight + duplicationScore * options.duplicationWeight;

    return {
      file: fs.file,
      complexityScore: Math.round(complexityScore * 1000) / 1000,
      duplicationScore: Math.round(duplicationScore * 1000) / 1000,
      combinedScore: Math.round(combinedScore * 1000) / 1000,
      maxComplexity: fs.complexity?.maxComplexity ?? 0,
      avgComplexity: fs.complexity?.avgComplexity ?? 0,
      totalLinesOfCode: fs.complexity?.totalLinesOfCode ?? 0,
      duplicatedLines: fs.duplication?.duplicatedLines ?? 0,
      duplicationPercent: Math.round((fs.duplication?.percentage ?? 0) * 10) / 10,
      functionCount: fs.complexity?.functionCount ?? 0,
      warnings: fs.complexity?.warnings ?? [],
      rank: 0,
    };
  });

  // Sort by combined score descending
  hotspots.sort((a, b) => b.combinedScore - a.combinedScore);

  // Assign ranks
  for (let i = 0; i < hotspots.length; i++) {
    hotspots[i].rank = i + 1;
  }

  // Return top N
  return hotspots.slice(0, options.topN);
}

// ── Main entry point ───────────────────────────────────────────────────

export function detectHotspots(options: HotspotDetectionOptions = {}): HotspotReport {
  const projectRoot = resolve(options.projectRoot ?? process.cwd());
  const topN = options.topN ?? DEFAULT_TOP_N;
  const complexityWeight = options.complexityWeight ?? DEFAULT_COMPLEXITY_WEIGHT;
  const duplicationWeight = options.duplicationWeight ?? DEFAULT_DUPLICATION_WEIGHT;
  const builtinOnly = options.builtinOnly ?? false;

  if (!existsSync(projectRoot)) {
    throw new Error(`Project root does not exist: ${projectRoot}`);
  }

  // Discover tool availability
  const radonAvail = builtinOnly ? { available: false } : resolveTool("radon");
  const lizardAvail = builtinOnly ? { available: false } : resolveTool("lizard");
  const jscpdAvail = builtinOnly ? { available: false } : resolveTool("jscpd");

  // Collect files
  const files = collectSourceFiles(projectRoot);

  // ── Complexity analysis ──────────────────────────────────────────────
  const complexityEntries: ComplexityEntry[] = [];

  if (radonAvail.available) {
    try {
      const pyFiles = files.filter((f) => PY_EXTENSIONS.has(extname(f)));
      complexityEntries.push(...runRadon(projectRoot, pyFiles));
    } catch { /* fall through */ }
  }

  if (lizardAvail.available) {
    try {
      complexityEntries.push(...runLizard(projectRoot, files));
    } catch { /* fall through */ }
  }

  // Built-in fallback for files not covered by radon or lizard
  // Always run on files that neither tool produced entries for
  const coveredFiles = new Set(complexityEntries.map((e) => e.file));
  const uncoveredFiles = files.filter((f) => !coveredFiles.has(f) && (TS_EXTENSIONS.has(extname(f)) || JS_EXTENSIONS.has(extname(f)) || PY_EXTENSIONS.has(extname(f))));
  if (uncoveredFiles.length > 0) {
    complexityEntries.push(...computeBuiltinComplexity(uncoveredFiles, projectRoot));
  }

  const complexityMap = aggregateComplexity(complexityEntries);

  // ── Duplication analysis ─────────────────────────────────────────────
  let duplicationEntries: DuplicationEntry[] = [];

  if (jscpdAvail.available) {
    try {
      duplicationEntries = runJscpd(projectRoot, files);
    } catch { /* fall through */ }
  }

  if (!jscpdAvail.available || duplicationEntries.length === 0) {
    duplicationEntries = computeBuiltinDuplication(files, projectRoot);
  }

  // ── Compute hotspots ─────────────────────────────────────────────────
  const hotspots = computeHotspots(complexityMap, duplicationEntries, {
    topN,
    complexityWeight,
    duplicationWeight,
  });

  // ── Build report ─────────────────────────────────────────────────────
  return {
    schemaVersion: "hotspot-report.v1",
    generatedAt: new Date().toISOString(),
    projectRoot,
    hotspots,
    toolsUsed: {
      radon: radonAvail,
      lizard: lizardAvail,
      jscpd: jscpdAvail,
    },
    summary: {
      totalFilesAnalyzed: files.length,
      hotspotCount: hotspots.length,
      topHotspots: hotspots.slice(0, Math.min(10, hotspots.length)),
    },
  };
}

// ── File discovery ─────────────────────────────────────────────────────

function collectSourceFiles(projectRoot: string): string[] {
  const sources = ["src", "scripts"];
  const results: string[] = [];

  for (const dir of sources) {
    const fullDir = join(projectRoot, dir);
    if (!existsSync(fullDir)) continue;
    results.push(...walkDir(fullDir, projectRoot));
  }

  return results.sort();
}

function walkDir(dirPath: string, projectRoot: string): string[] {
  const results: string[] = [];
  let entries: string[];

  try {
    // Use ls -A within a subprocess to avoid readdirSync TS issues
    const raw = execFileSync("ls", ["-A", dirPath], { encoding: "utf8", timeout: 5000 }).trim();
    entries = raw ? raw.split("\n") : [];
  } catch {
    return results;
  }

  for (const name of entries) {
    if (!name) continue;
    const fullPath = join(dirPath, name);
    const relPath = relative(projectRoot, fullPath);

    // Skip common non-source directories
    if (["node_modules", ".git", "generated", "fixtures", "__pycache__", ".mypy_cache", ".ruff_cache", ".code-review-graph"].includes(name)) {
      continue;
    }

    let isDir = false;
    try { isDir = execFileSync("test", ["-d", fullPath], { timeout: 3000 })?.toString()?.trim() === "" ? false : false; } catch { isDir = false; }
    // Better approach: check with ls -ld
    try {
      execFileSync("test", ["-d", fullPath], { timeout: 3000 });
      isDir = true;
    } catch {
      isDir = false;
    }

    if (isDir) {
      results.push(...walkDir(fullPath, projectRoot));
    } else {
      const ext = extname(name);
      if (TS_EXTENSIONS.has(ext) || JS_EXTENSIONS.has(ext) || PY_EXTENSIONS.has(ext)) {
        results.push(relPath);
      }
    }
  }

  return results;
}

// ── CLI interface ──────────────────────────────────────────────────────

export interface HotspotDetectionCommandOptions {
  projectRoot?: string;
  format?: "json" | "table" | "both";
  topN?: number;
  complexityWeight?: number;
  duplicationWeight?: number;
  builtinOnly?: boolean;
}

export function executeHotspotDetectionCommand(
  args: string[],
  defaultProjectRoot: string = process.cwd(),
): { exitCode: number; stdout: string; stderr: string } {
  try {
    let projectRoot = defaultProjectRoot;
    let format: "json" | "table" | "both" = "json";
    let topN = DEFAULT_TOP_N;
    let complexityWeight = DEFAULT_COMPLEXITY_WEIGHT;
    let duplicationWeight = DEFAULT_DUPLICATION_WEIGHT;
    let builtinOnly = false;

    let i = 0;
    while (i < args.length) {
      switch (args[i]) {
        case "--format":
        case "-f":
          if (args[i + 1] && ["json", "table", "both"].includes(args[i + 1])) {
            format = args[i + 1] as "json" | "table" | "both";
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({ error: "invalid_argument", message: "--format requires json, table, or both" }, null, 2) + "\n",
            };
          }
          break;
        case "--top-n":
        case "-n":
          if (args[i + 1]) {
            topN = Number.parseInt(args[i + 1], 10);
            if (Number.isNaN(topN) || topN < 1) {
              return {
                exitCode: 1,
                stdout: "",
                stderr: JSON.stringify({ error: "invalid_argument", message: "--top-n must be a positive integer" }, null, 2) + "\n",
              };
            }
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({ error: "invalid_argument", message: "--top-n requires a number" }, null, 2) + "\n",
            };
          }
          break;
        case "--complexity-weight":
        case "-cw":
          if (args[i + 1]) {
            complexityWeight = Number.parseFloat(args[i + 1]);
            if (Number.isNaN(complexityWeight) || complexityWeight < 0 || complexityWeight > 1) {
              return {
                exitCode: 1,
                stdout: "",
                stderr: JSON.stringify({ error: "invalid_argument", message: "--complexity-weight must be between 0 and 1" }, null, 2) + "\n",
              };
            }
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({ error: "invalid_argument", message: "--complexity-weight requires a number" }, null, 2) + "\n",
            };
          }
          break;
        case "--duplication-weight":
        case "-dw":
          if (args[i + 1]) {
            duplicationWeight = Number.parseFloat(args[i + 1]);
            if (Number.isNaN(duplicationWeight) || duplicationWeight < 0 || duplicationWeight > 1) {
              return {
                exitCode: 1,
                stdout: "",
                stderr: JSON.stringify({ error: "invalid_argument", message: "--duplication-weight must be between 0 and 1" }, null, 2) + "\n",
              };
            }
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({ error: "invalid_argument", message: "--duplication-weight requires a number" }, null, 2) + "\n",
            };
          }
          break;
        case "--builtin-only":
          builtinOnly = true;
          i++;
          break;
        default:
          // Positional: project root
          const candidate = resolve(args[i]);
          if (!existsSync(candidate)) {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({ error: "not_found", message: `Project root does not exist: ${candidate}` }, null, 2) + "\n",
            };
          }
          projectRoot = candidate;
          i++;
          break;
      }
    }

    const report = detectHotspots({
      projectRoot,
      topN,
      complexityWeight,
      duplicationWeight,
      builtinOnly,
    });

    let stdout = "";
    if (format === "json" || format === "both") {
      stdout += JSON.stringify(report, null, 2) + "\n";
    }
    if (format === "table" || format === "both") {
      if (format === "both") stdout += "\n---TABLE---\n";
      stdout += renderHotspotTable(report);
    }

    return { exitCode: 0, stdout, stderr: "" };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    return {
      exitCode: 2,
      stdout: "",
      stderr: JSON.stringify({ error: "failure", message }, null, 2) + "\n",
    };
  }
}

export function renderHotspotTable(report: HotspotReport): string {
  const lines: string[] = [];
  lines.push("=".repeat(100));
  lines.push(`  HOTSPOT DETECTION REPORT — ${report.summary.totalFilesAnalyzed} files analyzed`);
  lines.push(`  Tools: radon=${report.toolsUsed.radon.available}/${report.toolsUsed.lizard.available}/${report.toolsUsed.jscpd.available}`);
  lines.push("=".repeat(100));
  lines.push("");
  lines.push(
    "Rank".padEnd(6) +
    "File".padEnd(40) +
    "Combined".padEnd(10) +
    "MaxCCN".padEnd(8) +
    "AvgCCN".padEnd(8) +
    "Funcs".padEnd(7) +
    "LOC".padEnd(8) +
    "Dup%".padEnd(7) +
    "DupLines".padEnd(9) +
    "Warnings",
  );
  lines.push("-".repeat(100));

  for (const h of report.hotspots) {
    const warnings = h.warnings.length > 0 ? `${h.warnings.length} warning(s)` : "";
    lines.push(
      `#${String(h.rank).padEnd(4)}` +
      `${h.file.slice(0, 38).padEnd(40)}` +
      `${h.combinedScore.toFixed(3).padEnd(10)}` +
      `${String(h.maxComplexity).padEnd(8)}` +
      `${h.avgComplexity.toFixed(1).padEnd(8)}` +
      `${String(h.functionCount).padEnd(7)}` +
      `${String(h.totalLinesOfCode).padEnd(8)}` +
      `${h.duplicationPercent.toFixed(1).padEnd(7)}` +
      `${String(h.duplicatedLines).padEnd(9)}` +
      warnings,
    );
  }

  lines.push("-".repeat(100));
  lines.push(`  Top ${report.hotspots.length} hotspots shown (of ${report.summary.totalFilesAnalyzed} files)`);
  return lines.join("\n") + "\n";
}

// ── CLI entry ──────────────────────────────────────────────────────────

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeHotspotDetectionCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
