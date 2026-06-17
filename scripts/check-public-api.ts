import assert from "node:assert/strict";
import { mkdirSync, readdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface PublicApiCheckResult {
  modulePath: string;
  verifiedSymbols: string[];
  exportedSymbols: string[];
  undocumentedRuntimeSymbols: string[];
  verifiedClassSymbols: string[];
  verifiedFunctionSymbols: string[];
  importSideEffects: {
    stdoutBytes: 0;
    stderrBytes: 0;
    createdFiles: [];
  };
}

export const PUBLIC_API_ARTIFACT_PATH = "docs/public-api-symbols.json";

function readDocumentedPublicApi(): { modulePath: string; symbols: string[] } {
  const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
  const publicApiSection = readme.match(/## Public API\n\n```ts\n(?<code>[\s\S]*?)\n```/);
  assert.ok(publicApiSection?.groups?.code, "README.md must document a TypeScript Public API import block");

  const imports = [...publicApiSection.groups.code.matchAll(/import\s+\{\s*(?<symbols>[^}]+?)\s*\}\s+from\s+"(?<modulePath>[^"]+)";/g)];
  assert.ok(imports.length > 0, "Public API block must import named symbols");

  // Collect imports from the primary package entry ("ai-agent") only.
  // Sub-package imports (e.g. "ai-agent/execution-persona") are verified
  // separately by tests/public-api-entry-modules.test.ts.
  const primaryImports = imports.filter((entry) => entry.groups?.modulePath === "ai-agent");
  assert.ok(primaryImports.length > 0, "Public API block must include imports from the primary package entry (ai-agent)");

  const modulePath = "ai-agent";
  return {
    modulePath,
    symbols: [
      ...new Set(
        primaryImports.flatMap((entry) => {
          assert.ok(entry.groups?.symbols, "Public API block imports must include named symbols");
          return entry.groups.symbols
            .split(",")
            .map((symbol) => symbol.trim())
            .filter(Boolean);
        }),
      ),
    ],
  };
}

function writePublicApiArtifact(result: PublicApiCheckResult): void {
  const artifactPath = resolve(PUBLIC_API_ARTIFACT_PATH);
  mkdirSync(dirname(artifactPath), { recursive: true });
  writeFileSync(`${artifactPath}.tmp`, `${JSON.stringify(result, null, 2)}\n`);
  renameSync(`${artifactPath}.tmp`, artifactPath);
}

export async function checkPublicApi(): Promise<PublicApiCheckResult> {
  const documentedApi = readDocumentedPublicApi();
  const beforeFiles = collectProjectFiles(process.cwd());
  const captured = await captureImportOutput(() => import(documentedApi.modulePath));
  const afterFiles = collectProjectFiles(process.cwd());
  const createdFiles = afterFiles.filter((path) => !beforeFiles.includes(path));

  assert.deepEqual(
    captured,
    { api: captured.api, stdoutBytes: 0, stderrBytes: 0 },
    `Importing ${documentedApi.modulePath} should not write stdout or stderr`,
  );
  assert.deepEqual(createdFiles, [], `Importing ${documentedApi.modulePath} should not create project files`);
  const api = captured.api;
  const exportedSymbols = Object.keys(api).sort();
  const documentedSymbols = [...documentedApi.symbols].sort();
  const undocumentedRuntimeSymbols = exportedSymbols.filter((symbol) => !documentedSymbols.includes(symbol));

  assert.deepEqual(
    exportedSymbols,
    documentedSymbols,
    `${documentedApi.modulePath} should only expose README-documented public symbols`,
  );

  const verifiedClassSymbols: string[] = [];
  const verifiedFunctionSymbols: string[] = [];

  for (const symbol of documentedApi.symbols) {
    assert.equal(typeof api[symbol], "function", `${symbol} should be exported from ${documentedApi.modulePath}`);
    if (isClassExport(api[symbol])) {
      verifiedClassSymbols.push(symbol);
    } else {
      verifiedFunctionSymbols.push(symbol);
    }
  }
  assert.ok(
    verifiedFunctionSymbols.length > 0,
    `Public API documentation should include at least one function export from ${documentedApi.modulePath}`,
  );

  const result = {
    modulePath: documentedApi.modulePath,
    verifiedSymbols: documentedApi.symbols,
    exportedSymbols,
    undocumentedRuntimeSymbols,
    verifiedClassSymbols,
    verifiedFunctionSymbols,
    importSideEffects: {
      stdoutBytes: 0,
      stderrBytes: 0,
      createdFiles: [],
    },
  };
  writePublicApiArtifact(result);
  return result;
}

function isClassExport(value: unknown): boolean {
  return typeof value === "function" && Function.prototype.toString.call(value).startsWith("class ");
}

async function captureImportOutput(load: () => Promise<Record<string, unknown>>): Promise<{
  api: Record<string, unknown>;
  stdoutBytes: number;
  stderrBytes: number;
}> {
  const stdoutWrite = process.stdout.write;
  const stderrWrite = process.stderr.write;
  let stdoutBytes = 0;
  let stderrBytes = 0;

  process.stdout.write = ((chunk: string | Uint8Array, ...args: any[]) => {
    stdoutBytes += Buffer.byteLength(chunk);
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string | Uint8Array, ...args: any[]) => {
    stderrBytes += Buffer.byteLength(chunk);
    return true;
  }) as typeof process.stderr.write;

  try {
    return {
      api: await load(),
      stdoutBytes,
      stderrBytes,
    };
  } finally {
    process.stdout.write = stdoutWrite;
    process.stderr.write = stderrWrite;
  }
}

function collectProjectFiles(root: string): string[] {
  return collectFiles(root, root).sort();
}

function collectFiles(root: string, current: string): string[] {
  const stats = statSync(current);
  if (stats.isFile()) return [current.slice(root.length + 1)];
  if (!stats.isDirectory()) return [];
  return readdirSync(current)
    .filter((entry) => ![".git", "node_modules"].includes(entry))
    .flatMap((entry) => collectFiles(root, join(current, entry)));
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  await checkPublicApi();
}
