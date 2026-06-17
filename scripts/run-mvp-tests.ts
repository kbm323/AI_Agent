import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";

type CapturedCommandResult = { exitCode: number; stdout: string; stderr: string };
type SpawnNode = typeof spawnSync;

export interface MvpTestSuiteDeps {
  spawnNode?: SpawnNode;
}

export interface MvpTestSuiteDiscovery {
  schemaVersion: "mvp-test-suite-discovery.v1";
  testsDirectory: string;
  testFiles: string[];
  testCount: number;
  discoveryRules: {
    fileExtensions: [".test.ts"];
    contentKeywords: string[];
    filenameKeywords: string[];
  };
}

const contentKeywords = [
  "@mvp-suite",
];

const filenameKeywords = [
  "mvp",
  "readme",
  "orchestrator",
  "routing",
  "meeting",
  "final",
  "escalation",
  "token",
  "context",
  "verification",
  "typecheck",
  "dry-run",
  "diagnosis",
  "fixture",
  "planning",
  "evaluation",
  "public-api",
  "review-evidence",
];

export function discoverMvpTestSuite(projectRoot = process.cwd()): MvpTestSuiteDiscovery {
  const testsDirectory = resolve(projectRoot, "tests");
  if (!existsSync(testsDirectory)) {
    throw new Error("tests directory does not exist");
  }

  const testFiles = readdirSync(testsDirectory)
    .filter((entry) => entry.endsWith(".test.ts"))
    .map((entry) => resolve(testsDirectory, entry))
    .filter((filePath) => isMvpTestFile(filePath))
    .map((filePath) => relative(projectRoot, filePath))
    .sort();

  if (testFiles.length === 0) {
    throw new Error("MVP test suite discovery matched no tests");
  }

  return {
    schemaVersion: "mvp-test-suite-discovery.v1",
    testsDirectory,
    testFiles,
    testCount: testFiles.length,
    discoveryRules: {
      fileExtensions: [".test.ts"],
      contentKeywords,
      filenameKeywords,
    },
  };
}

export function buildMvpTestCommand(projectRoot = process.cwd()): string[] {
  const discovery = discoverMvpTestSuite(projectRoot);
  return ["--test", "--test-concurrency=1", ...discovery.testFiles];
}

export function executeMvpTestSuiteCommand(
  projectRoot = process.cwd(),
  deps: MvpTestSuiteDeps = {},
): CapturedCommandResult {
  try {
    const discovery = discoverMvpTestSuite(projectRoot);
    const args = ["--test", "--test-concurrency=1", ...discovery.testFiles];
    const result = (deps.spawnNode ?? spawnSync)(process.execPath, args, {
      cwd: projectRoot,
      encoding: "utf8",
      env: buildIsolatedNodeTestEnv(),
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    });

    if (result.error) {
      throw result.error;
    }

    return {
      exitCode: typeof result.status === "number" ? result.status : 1,
      stdout: typeof result.stdout === "string" ? result.stdout : "",
      stderr: typeof result.stderr === "string" ? result.stderr : "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown MVP test suite failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "mvp_test_suite_failed", message }, null, 2)}\n`,
    };
  }
}

function isMvpTestFile(filePath: string): boolean {
  const lowerBasename = basename(filePath).toLowerCase();
  if (filenameKeywords.some((keyword) => lowerBasename.includes(keyword))) {
    return true;
  }

  const content = readFileSync(filePath, "utf8");
  const lowerContent = content.toLowerCase();
  return contentKeywords.some((keyword) => lowerContent.includes(keyword.toLowerCase()));
}

function buildIsolatedNodeTestEnv(): NodeJS.ProcessEnv {
  return Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("NODE_TEST")),
  );
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeMvpTestSuiteCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
