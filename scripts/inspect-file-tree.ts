import { resolve, relative, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { readdirSync, statSync, existsSync } from "node:fs";

// ── Types ──────────────────────────────────────────────────────────────

interface TreeNode {
  name: string;
  type: "file" | "directory";
  /** Absolute path */
  path: string;
  /** Relative path from project root */
  relativePath: string;
  /** Only for directories — sorted children */
  children?: TreeNode[];
}

interface ModuleEntry {
  /** Module name derived from filename (without extension) */
  name: string;
  /** Relative path from project root */
  relativePath: string;
  /** Absolute path */
  path: string;
  /** Category: src, scripts, tests, docs, config, other */
  category: ModuleCategory;
}

type ModuleCategory = "src" | "scripts" | "tests" | "docs" | "config" | "other";

interface FileTreeArtifact {
  schemaVersion: 1;
  generatedAt: string;
  projectRoot: string;
  tree: TreeNode;
  modules: ModuleEntry[];
  summary: {
    totalFiles: number;
    totalDirectories: number;
    totalModules: number;
    modulesByCategory: Record<ModuleCategory, number>;
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

const SOURCE_EXTENSIONS = new Set([".ts", ".js", ".mjs", ".cjs", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"]);

const MODULE_EXTENSIONS = new Set([".ts", ".js", ".mjs"]);

// ── Helpers ────────────────────────────────────────────────────────────

function classifyCategory(relativePath: string): ModuleCategory {
  const topDir = relativePath.split("/")[0] ?? "";
  switch (topDir) {
    case "src": return "src";
    case "scripts": return "scripts";
    case "tests": return "tests";
    case "docs": return "docs";
    default:
      if (["package.json", "tsconfig.json", ".gitignore", "README.md"].includes(basename(relativePath))) {
        return "config";
      }
      return "other";
  }
}

function moduleNameFromPath(filePath: string): string {
  return basename(filePath).replace(/\.(ts|js|mjs|cjs)$/, "");
}

function walkTree(absPath: string, root: string): TreeNode {
  const name = basename(absPath);
  const rel = relative(root, absPath) || ".";
  const st = statSync(absPath);

  if (!st.isDirectory()) {
    return { name, type: "file", path: absPath, relativePath: rel };
  }

  const entries = readdirSync(absPath, { withFileTypes: true });
  const children: TreeNode[] = [];

  for (const entry of entries) {
    if (entry.isDirectory() && SKIP_DIRS.has(entry.name)) continue;
    const childPath = resolve(absPath, entry.name);
    children.push(walkTree(childPath, root));
  }

  children.sort((a, b) => {
    // directories first, then files; alphabetical within each group
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return { name, type: "directory", path: absPath, relativePath: rel, children };
}

function collectModules(node: TreeNode, modules: ModuleEntry[]): void {
  if (node.type === "file") {
    const ext = node.name.includes(".") ? "." + (node.name.split(".").pop() ?? "") : "";
    if (MODULE_EXTENSIONS.has(ext)) {
      modules.push({
        name: moduleNameFromPath(node.name),
        relativePath: node.relativePath,
        path: node.path,
        category: classifyCategory(node.relativePath),
      });
    }
  }

  if (node.children) {
    for (const child of node.children) {
      collectModules(child, modules);
    }
  }
}

function countFiles(node: TreeNode): number {
  if (node.type === "file") return 1;
  let count = 0;
  if (node.children) {
    for (const child of node.children) {
      count += countFiles(child);
    }
  }
  return count;
}

function countDirs(node: TreeNode): number {
  if (node.type === "file") return 0;
  let count = 1;
  if (node.children) {
    for (const child of node.children) {
      count += countDirs(child);
    }
  }
  return count;
}

// ── Main ───────────────────────────────────────────────────────────────

export function buildFileTreeArtifact(projectRoot: string): FileTreeArtifact {
  const absRoot = resolve(projectRoot);

  if (!existsSync(absRoot)) {
    throw new Error(`Project root does not exist: ${absRoot}`);
  }

  const tree = walkTree(absRoot, absRoot);
  const modules: ModuleEntry[] = [];
  collectModules(tree, modules);

  const modulesByCategory: Record<ModuleCategory, number> = {
    src: 0,
    scripts: 0,
    tests: 0,
    docs: 0,
    config: 0,
    other: 0,
  };

  for (const m of modules) {
    modulesByCategory[m.category] = (modulesByCategory[m.category] ?? 0) + 1;
  }

  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    projectRoot: absRoot,
    tree,
    modules: modules.sort((a, b) => {
      if (a.category !== b.category) return a.category.localeCompare(b.category);
      return a.relativePath.localeCompare(b.relativePath);
    }),
    summary: {
      totalFiles: countFiles(tree),
      totalDirectories: countDirs(tree),
      totalModules: modules.length,
      modulesByCategory,
    },
  };
}

export function executeInspectFileTreeCommand(
  args: string[],
  projectRoot: string = process.cwd(),
): { exitCode: number; stdout: string; stderr: string } {
  try {
    // Optional custom project root from argv[1]
    const customRoot = args[0] ? resolve(args[0]) : projectRoot;

    if (!existsSync(customRoot)) {
      return {
        exitCode: 1,
        stdout: "",
        stderr: JSON.stringify({ error: "not_found", message: `Path does not exist: ${customRoot}` }, null, 2) + "\n",
      };
    }

    // Validate it's a directory
    if (!statSync(customRoot).isDirectory()) {
      return {
        exitCode: 1,
        stdout: "",
        stderr: JSON.stringify({ error: "invalid_input", message: `Not a directory: ${customRoot}` }, null, 2) + "\n",
      };
    }

    const artifact = buildFileTreeArtifact(customRoot);
    return {
      exitCode: 0,
      stdout: JSON.stringify(artifact, null, 2) + "\n",
      stderr: "",
    };
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
  const result = executeInspectFileTreeCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
