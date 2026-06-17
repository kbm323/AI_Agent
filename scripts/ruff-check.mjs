import { spawnSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const projectRoot = process.cwd();
const ignoredDirs = new Set([
  ".git",
  ".ruff_cache",
  "node_modules",
  "dist",
  "build",
  "coverage",
]);

function hasPythonFiles(dir) {
  for (const entry of readdirSync(dir)) {
    if (ignoredDirs.has(entry)) {
      continue;
    }

    const path = join(dir, entry);
    const stat = statSync(path);

    if (stat.isDirectory()) {
      if (hasPythonFiles(path)) {
        return true;
      }
      continue;
    }

    if (entry.endsWith(".py") || entry === "pyproject.toml" || entry === "ruff.toml") {
      return true;
    }
  }

  return false;
}

const result = spawnSync("ruff", ["check", "."], {
  cwd: projectRoot,
  encoding: "utf8",
  stdio: "pipe",
});

if (result.error?.code === "ENOENT" && !hasPythonFiles(projectRoot)) {
  console.log("ruff check .");
  console.log("No Python or Ruff configuration files found; Ruff check is not applicable.");
  process.exit(0);
}

if (result.stdout) {
  process.stdout.write(result.stdout);
}

if (result.stderr) {
  process.stderr.write(result.stderr);
}

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
