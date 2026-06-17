import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { resolve, relative, basename, dirname, join, extname } from "node:path";
import { fileURLToPath } from "node:url";

// ── Types ──────────────────────────────────────────────────────────────

export interface DependencyEdge {
  from: string; // importing module name
  to: string;   // imported module name
  /** Import kind */
  kind: "value" | "type" | "mixed";
}

export interface DependencyNode {
  module: string;      // module name (filename without extension)
  file: string;        // relative path from project root
  category: "src" | "scripts" | "tests";
  imports: string[];   // imported module names
  importedBy: string[]; // modules that import this one
}

export interface DependencyGraph {
  schemaVersion: "dependency-graph.v1";
  generatedAt: string;
  projectRoot: string;
  nodes: DependencyNode[];
  edges: DependencyEdge[];
  summary: {
    totalModules: number;
    totalEdges: number;
    orphanModules: number;      // modules with no imports and not imported
    leafModules: number;         // modules that import but nothing imports them
    rootModules: number;         // modules that are imported but import nothing
    modulesByCategory: Record<DependencyNode["category"], number>;
  };
}

// ── Constants ──────────────────────────────────────────────────────────

const SRC_DIRS = ["src", "scripts", "tests"] as const;

// ── Helpers ────────────────────────────────────────────────────────────

function stripExtension(filePath: string): string {
  return filePath.replace(/\.(ts|js|mjs|cjs|tsx|jsx)$/, "");
}

function resolveImportPath(
  importPath: string,
  currentFileDir: string,
  projectRoot: string,
): string | null {
  // Relative import: ./foo/bar or ../foo/bar
  const resolved = resolve(currentFileDir, importPath);

  // Try with extensions in order of preference
  const extensions = [".ts", ".js", ".mjs", "/index.ts", "/index.js"];
  for (const ext of extensions) {
    const candidate = resolved + ext;
    if (existsSync(candidate)) {
      return relative(projectRoot, candidate);
    }
  }

  return null;
}

function extractImports(filePath: string, content: string): { module: string; kind: "value" | "type" | "mixed" }[] {
  const results: { module: string; kind: "value" | "type" | "mixed" }[] = [];
  const seen = new Set<string>();

  const valueImportPaths = new Set<string>();
  const typeImportPaths = new Set<string>();

  const lines = content.split("\n");
  for (const rawLine of lines) {
    const line = rawLine.trim();

    // Skip comments
    if (line.startsWith("//") || line.startsWith("/*") || line.startsWith("*")) continue;
    if (!line.startsWith("import")) continue;

    // Extract the from-path: everything between "from" and the closing quote
    // Match: from "./path" or from './path' or from "../path"
    const fromMatch = line.match(/from\s+['"]([./][^'"]+)['"]/);
    if (!fromMatch) continue;

    const importPath = fromMatch[1];

    // Type-only import: "import type ..."
    const isTypeOnly = /^import\s+type\s/.test(line);

    // Mixed import: has inline "type" keyword inside import specifiers
    // e.g., "import { helper, type Kind } from './b.ts'"
    const hasInlineType = !isTypeOnly && /\btype\s+\w+/.test(
      line.slice(0, line.lastIndexOf("from"))
    );

    if (isTypeOnly) {
      typeImportPaths.add(importPath);
    } else if (hasInlineType) {
      // Mark as both value and type (mixed)
      valueImportPaths.add(importPath);
      typeImportPaths.add(importPath);
    } else {
      valueImportPaths.add(importPath);
    }
  }

  // Merge: determine kind per import path
  for (const imp of valueImportPaths) {
    if (!seen.has(imp)) {
      const modName = stripExtension(basename(imp));
      results.push({ module: modName, kind: typeImportPaths.has(imp) ? "mixed" : "value" });
      seen.add(imp);
    }
  }

  for (const imp of typeImportPaths) {
    if (!seen.has(imp)) {
      const modName = stripExtension(basename(imp));
      results.push({ module: modName, kind: "type" });
      seen.add(imp);
    }
  }

  return results;
}

function classifyPath(relativePath: string): DependencyNode["category"] {
  const top = relativePath.split("/")[0] ?? "";
  if (top === "src") return "src";
  if (top === "scripts") return "scripts";
  if (top === "tests") return "tests";
  return "src"; // default fallback
}

function collectTypeScriptFiles(dirPath: string, projectRoot: string): string[] {
  const results: string[] = [];
  if (!existsSync(dirPath)) return results;

  const entries = readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = resolve(dirPath, entry.name);
    const relativePath = relative(projectRoot, fullPath);

    if (entry.isDirectory()) {
      // Skip common non-source directories
      if (["node_modules", ".git", "generated", "fixtures", ".ouroboros", ".code-review-graph"].includes(entry.name)) {
        continue;
      }
      results.push(...collectTypeScriptFiles(fullPath, projectRoot));
    } else if (entry.isFile() && /\.(ts|mts)$/.test(entry.name)) {
      results.push(relativePath);
    }
  }

  return results;
}

// ── Core Logic ─────────────────────────────────────────────────────────

export function buildDependencyGraph(
  projectRoot: string,
  options: { scope?: string[] } = {},
): DependencyGraph {
  const absRoot = resolve(projectRoot);

  if (!existsSync(absRoot)) {
    throw new Error(`Project root does not exist: ${absRoot}`);
  }

  if (!statSync(absRoot).isDirectory()) {
    throw new Error(`Not a directory: ${absRoot}`);
  }

  // Collect all TypeScript files
  const scope = options.scope ?? ["src", "scripts", "tests"];
  const files: string[] = [];
  for (const dir of scope) {
    files.push(...collectTypeScriptFiles(join(absRoot, dir), absRoot));
  }

  // Build node map: moduleName -> { file, category, imports }
  type ModuleInfo = {
    file: string;
    category: DependencyNode["category"];
    imports: { module: string; kind: "value" | "type" | "mixed" }[];
  };

  const moduleMap = new Map<string, ModuleInfo>();

  for (const file of files) {
    const fullPath = join(absRoot, file);
    let content: string;
    try {
      content = readFileSync(fullPath, "utf8");
    } catch {
      continue; // skip unreadable
    }

    const moduleName = stripExtension(basename(file));
    const imports = extractImports(file, content);

    moduleMap.set(moduleName, {
      file,
      category: classifyPath(file),
      imports,
    });
  }

  // Now, for imports, resolve the module name. When two modules have the same name
  // (e.g., both src/planning.ts and tests/planning.test.ts import a local module),
  // resolve by checking the import directory relative to the importing file.
  // For the dependency graph, we use the resolved module name against moduleMap.

  // Build resolved import references
  type ResolvedImport = { fromModule: string; toModule: string; kind: "value" | "type" | "mixed" };

  const resolvedEdges: ResolvedImport[] = [];

  for (const [fromModule, info] of moduleMap) {
    const importDir = dirname(join(absRoot, info.file));
    for (const imp of info.imports) {
      // The module name from the import is `imp.module`
      // Check if this name exists in moduleMap directly
      // If multiple modules share the name (e.g. planning.ts in src vs planning.test.ts in tests),
      // prefer same-category, then same-directory parent, then first match.
      if (moduleMap.has(imp.module)) {
        resolvedEdges.push({ fromModule, toModule: imp.module, kind: imp.kind });
      } else {
        // Try resolving by looking at filesystem from the import directory
        // Re-extract the raw import path from the file content
        const fullContent = readFileSync(join(absRoot, info.file), "utf8");
        // Find all import paths that match this module name
        const importPaths = findImportPathsForModule(fullContent, imp.module);
        for (const importPath of importPaths) {
          const resolvedAbs = resolve(importDir, importPath);
          for (const ext of [".ts", ".js", "/index.ts", "/index.js", ""]) {
            const candidate = resolvedAbs + ext;
            if (existsSync(candidate)) {
              const resolvedRel = relative(absRoot, candidate);
              const resolvedModule = stripExtension(basename(resolvedRel));
              if (moduleMap.has(resolvedModule)) {
                resolvedEdges.push({ fromModule, toModule: resolvedModule, kind: imp.kind });
              }
              break;
            }
          }
        }
      }
    }
  }

  // Deduplicate edges
  const edgeSet = new Set<string>();
  const edges: DependencyEdge[] = [];
  for (const e of resolvedEdges) {
    const key = `${e.fromModule}->${e.toModule}:${e.kind}`;
    if (!edgeSet.has(key)) {
      edgeSet.add(key);
      edges.push({ from: e.fromModule, to: e.toModule, kind: e.kind });
    }
  }

  // Build nodes
  const nodes: DependencyNode[] = [];
  const importedBy = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!importedBy.has(edge.to)) importedBy.set(edge.to, new Set());
    importedBy.get(edge.to)!.add(edge.from);
  }

  for (const [moduleName, info] of moduleMap) {
    const importSet = new Set(edges.filter(e => e.from === moduleName).map(e => e.to));
    nodes.push({
      module: moduleName,
      file: info.file,
      category: info.category,
      imports: [...importSet].sort(),
      importedBy: [...(importedBy.get(moduleName) ?? new Set())].sort(),
    });
  }

  // Sort nodes by category then name
  nodes.sort((a, b) => {
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.module.localeCompare(b.module);
  });

  // Summary
  const orphanModules = nodes.filter(n => n.imports.length === 0 && n.importedBy.length === 0).length;
  const leafModules = nodes.filter(n => n.imports.length > 0 && n.importedBy.length === 0).length;
  const rootModules = nodes.filter(n => n.imports.length === 0 && n.importedBy.length > 0).length;

  const modulesByCategory: Record<DependencyNode["category"], number> = {
    src: 0,
    scripts: 0,
    tests: 0,
  };
  for (const n of nodes) {
    modulesByCategory[n.category] = (modulesByCategory[n.category] ?? 0) + 1;
  }

  return {
    schemaVersion: "dependency-graph.v1",
    generatedAt: new Date().toISOString(),
    projectRoot: absRoot,
    nodes,
    edges,
    summary: {
      totalModules: nodes.length,
      totalEdges: edges.length,
      orphanModules,
      leafModules,
      rootModules,
      modulesByCategory,
    },
  };
}

// ── Helper: find import paths in file content that resolve to a module name ──

function findImportPathsForModule(content: string, targetModule: string): string[] {
  const results: string[] = [];
  const combinedRe = /import\s+(?:type\s+)?(?:\{[^}]*\}|[^{}\s,]+|(?:\*\s+as\s+\w+))\s*(?:,\s*(?:\{[^}]*\}|[^{}\s,]+|(?:\*\s+as\s+\w+)))*\s*from\s+['"]([./][^'"]+)['"]/g;

  let match: RegExpExecArray | null;
  while ((match = combinedRe.exec(content)) !== null) {
    const importPath = match[1];
    if (importPath && stripExtension(basename(importPath)) === targetModule) {
      results.push(importPath);
    }
  }

  return results;
}

// ── Output Formats ─────────────────────────────────────────────────────

export function renderDOT(graph: DependencyGraph): string {
  const lines: string[] = [];

  lines.push("digraph AI_Agent_Dependencies {");
  lines.push("  rankdir=LR;");
  lines.push("  node [shape=box, style=filled, fontname=\"monospace\"];");
  lines.push("");

  // Color by category
  const categoryColors: Record<string, string> = {
    src: "#e8f4fd",     // light blue
    scripts: "#fff3cd",  // light yellow
    tests: "#d4edda",    // light green
  };

  for (const node of graph.nodes) {
    const color = categoryColors[node.category] ?? "#ffffff";
    const label = `${node.module}\\n[${node.category}]`;
    lines.push(`  "${node.module}" [label="${label}", fillcolor="${color}"];`);
  }

  lines.push("");

  // Use explicit kind = "type" edges as dashed
  for (const edge of graph.edges) {
    const style = edge.kind === "type" ? " [style=dashed, color=gray]" : "";
    lines.push(`  "${edge.from}" -> "${edge.to}"${style};`);
  }

  lines.push("}");
  lines.push("");

  return lines.join("\n");
}

export function renderJSON(graph: DependencyGraph): string {
  return JSON.stringify(graph, null, 2) + "\n";
}

// ── CLI interface ──────────────────────────────────────────────────────

export interface DependencyGraphCommandOptions {
  projectRoot?: string;
  format?: "json" | "dot" | "both";
  scope?: string[];
  output?: string;
}

export function executeDependencyGraphCommand(
  args: string[],
  defaultProjectRoot: string = process.cwd(),
): { exitCode: number; stdout: string; stderr: string } {
  try {
    // Parse args
    let projectRoot = defaultProjectRoot;
    let format: "json" | "dot" | "both" = "json";
    let scope: string[] = ["src", "scripts", "tests"];

    // Positional project root: first non-flag argument
    const positional: string[] = [];
    let i = 0;
    while (i < args.length) {
      switch (args[i]) {
        case "--format":
        case "-f":
          if (args[i + 1] && ["json", "dot", "both"].includes(args[i + 1])) {
            format = args[i + 1] as "json" | "dot" | "both";
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({
                error: "invalid_argument",
                message: `--format requires json, dot, or both`,
              }, null, 2) + "\n",
            };
          }
          break;
        case "--scope":
        case "-s":
          if (args[i + 1]) {
            scope = args[i + 1].split(",").filter(Boolean);
            i += 2;
          } else {
            return {
              exitCode: 1,
              stdout: "",
              stderr: JSON.stringify({
                error: "invalid_argument",
                message: `--scope requires comma-separated directory list`,
              }, null, 2) + "\n",
            };
          }
          break;
        default:
          positional.push(args[i]);
          i++;
          break;
      }
    }

    if (positional.length > 0) {
      const candidate = resolve(positional[0]);
      if (!existsSync(candidate)) {
        return {
          exitCode: 1,
          stdout: "",
          stderr: JSON.stringify({
            error: "not_found",
            message: `Project root does not exist: ${candidate}`,
          }, null, 2) + "\n",
        };
      }
      if (!statSync(candidate).isDirectory()) {
        return {
          exitCode: 1,
          stdout: "",
          stderr: JSON.stringify({
            error: "invalid_input",
            message: `Not a directory: ${candidate}`,
          }, null, 2) + "\n",
        };
      }
      projectRoot = candidate;
    }

    const graph = buildDependencyGraph(projectRoot, { scope });

    let stdout = "";
    if (format === "json" || format === "both") {
      stdout += renderJSON(graph);
    }
    if (format === "dot" || format === "both") {
      if (format === "both") stdout += "\n---DOT---\n";
      stdout += renderDOT(graph);
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

// ── CLI entry ──────────────────────────────────────────────────────────

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeDependencyGraphCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
