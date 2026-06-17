import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const ENVIRONMENT_DEPENDENCY_DOCUMENT_PATH = "docs/environment-dependency-verification.md";

export interface EnvironmentCommandSpec {
  id: "node_version" | "npm_version" | "health_check" | "typecheck_check";
  command: string;
  expectedOutput: string;
  packageScript?: string;
}

export interface EnvironmentDependencyCheckResult {
  schemaVersion: "environment-dependency-check.v1";
  command: "ai-agent check-environment-dependencies";
  status: "passed" | "failed";
  document: {
    path: string;
    commandsMatchedSpecification: boolean;
    documentedCommandCount: number;
  };
  checks: EnvironmentDependencyCommandCheck[];
  summary: {
    requiredCommandCount: number;
    presentCommandCount: number;
    executableCommandCount: number;
    failedCheckIds: string[];
  };
}

export interface EnvironmentDependencyCommandCheck {
  id: EnvironmentCommandSpec["id"];
  command: string;
  expectedOutput: string;
  packageScript?: string;
  present: boolean;
  executable: boolean;
  exitCode: number | null;
  outputMatchesExpectation: boolean;
  stdoutSummary: string;
  failureReason?: string;
}

interface CommandRunResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export type EnvironmentCommandRunner = (command: string, projectRoot: string) => CommandRunResult;

export const documentedEnvironmentCommandSpecs: EnvironmentCommandSpec[] = [
  {
    id: "node_version",
    command: "node --version",
    expectedOutput: "v24.x or newer",
  },
  {
    id: "npm_version",
    command: "npm --version",
    expectedOutput: "semver version string",
  },
  {
    id: "health_check",
    command: "npm run health-check --silent",
    expectedOutput: 'JSON with `schemaVersion: "health-check.v1"` and `status: "ok"`',
    packageScript: "health-check",
  },
  {
    id: "typecheck_check",
    command: "npm run check:typecheck --silent",
    expectedOutput: 'JSON with `schemaVersion: "typecheck-command-check.v1"` and `status: "passed"`',
    packageScript: "check:typecheck",
  },
];

export function checkEnvironmentDependencies(input: {
  projectRoot?: string;
  documentPath?: string;
  runCommand?: EnvironmentCommandRunner;
} = {}): EnvironmentDependencyCheckResult {
  const projectRoot = input.projectRoot ?? process.cwd();
  const documentPath = input.documentPath ?? ENVIRONMENT_DEPENDENCY_DOCUMENT_PATH;
  const runCommand = input.runCommand ?? runShellCommand;
  const documentedCommands = readDocumentedCommands(resolve(projectRoot, documentPath));
  const commandsMatchedSpecification = commandRowsMatchSpecs(documentedCommands, documentedEnvironmentCommandSpecs);
  const packageScripts = readPackageScripts(projectRoot);

  const checks = documentedEnvironmentCommandSpecs.map((spec) => {
    const packageScriptPresent = spec.packageScript === undefined || Object.hasOwn(packageScripts, spec.packageScript);
    if (!commandsMatchedSpecification) {
      return buildSkippedCheck(spec, "documented command table does not match executable specification");
    }
    if (!packageScriptPresent) {
      return buildSkippedCheck(spec, `package.json scripts.${spec.packageScript} is missing`);
    }

    const result = runCommand(spec.command, projectRoot);
    const outputMatchesExpectation = commandOutputMatchesExpectation(spec, result);
    const executable = result.exitCode === 0 && outputMatchesExpectation;
    return {
      id: spec.id,
      command: spec.command,
      expectedOutput: spec.expectedOutput,
      packageScript: spec.packageScript,
      present: true,
      executable,
      exitCode: result.exitCode,
      outputMatchesExpectation,
      stdoutSummary: summarizeStdout(result.stdout),
      failureReason: executable ? undefined : buildCommandFailureReason(result, outputMatchesExpectation),
    };
  });

  const failedCheckIds = checks.filter((check) => !check.present || !check.executable).map((check) => check.id);

  return {
    schemaVersion: "environment-dependency-check.v1",
    command: "ai-agent check-environment-dependencies",
    status: commandsMatchedSpecification && failedCheckIds.length === 0 ? "passed" : "failed",
    document: {
      path: resolve(projectRoot, documentPath),
      commandsMatchedSpecification,
      documentedCommandCount: documentedCommands.length,
    },
    checks,
    summary: {
      requiredCommandCount: documentedEnvironmentCommandSpecs.length,
      presentCommandCount: checks.filter((check) => check.present).length,
      executableCommandCount: checks.filter((check) => check.executable).length,
      failedCheckIds,
    },
  };
}

function readDocumentedCommands(documentPath: string): string[] {
  const markdown = readFileSync(documentPath, "utf8");
  return markdown
    .split("\n")
    .map((line) => line.match(/^\|\s*[^|]+\s*\|\s*`(?<command>[^`]+)`\s*\|/)?.groups?.command)
    .filter((command): command is string => command !== undefined);
}

function commandRowsMatchSpecs(documentedCommands: string[], specs: EnvironmentCommandSpec[]): boolean {
  return (
    documentedCommands.length === specs.length &&
    documentedCommands.every((command, index) => command === specs[index]?.command)
  );
}

function readPackageScripts(projectRoot: string): Record<string, string> {
  const packageJson = JSON.parse(readFileSync(resolve(projectRoot, "package.json"), "utf8"));
  const scripts = packageJson.scripts;
  if (scripts === null || typeof scripts !== "object" || Array.isArray(scripts)) {
    return {};
  }
  return scripts;
}

function runShellCommand(command: string, projectRoot: string): CommandRunResult {
  const [executable, ...args] = command.split(" ");
  const result = spawnSync(executable, args, {
    cwd: projectRoot,
    encoding: "utf8",
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });

  if (result.error) {
    return {
      exitCode: 127,
      stdout: "",
      stderr: result.error.message,
    };
  }

  return {
    exitCode: typeof result.status === "number" ? result.status : 1,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}

function commandOutputMatchesExpectation(spec: EnvironmentCommandSpec, result: CommandRunResult): boolean {
  if (result.exitCode !== 0) return false;
  const stdout = result.stdout.trim();

  if (stdout.length === 0 && spec.command.startsWith("npm ")) {
    return true;
  }
  if (spec.id === "node_version") {
    const major = Number(stdout.match(/^v(?<major>\d+)\./)?.groups?.major);
    return Number.isInteger(major) && major >= 24;
  }
  if (spec.id === "npm_version") {
    return /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(stdout);
  }

  const parsed = parseJson(stdout);
  if (spec.id === "health_check") {
    return parsed?.schemaVersion === "health-check.v1" && parsed.status === "ok";
  }
  if (spec.id === "typecheck_check") {
    return parsed?.typecheck?.schemaVersion === "typecheck-command-check.v1" && parsed.status === "passed";
  }
  return false;
}

function parseJson(value: string): any {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

function summarizeStdout(stdout: string): string {
  const trimmed = stdout.trim();
  if (trimmed.length === 0) return "stdout not captured; exit code used";

  const parsed = parseJson(trimmed);
  if (parsed?.schemaVersion && parsed?.status) {
    return `${parsed.schemaVersion}:${parsed.status}`;
  }
  if (parsed?.typecheck?.schemaVersion && parsed?.status) {
    return `${parsed.typecheck.schemaVersion}:${parsed.status}`;
  }
  return trimmed.split("\n")[0] ?? "";
}

function buildSkippedCheck(spec: EnvironmentCommandSpec, failureReason: string): EnvironmentDependencyCommandCheck {
  return {
    id: spec.id,
    command: spec.command,
    expectedOutput: spec.expectedOutput,
    packageScript: spec.packageScript,
    present: false,
    executable: false,
    exitCode: null,
    outputMatchesExpectation: false,
    stdoutSummary: "",
    failureReason,
  };
}

function buildCommandFailureReason(result: CommandRunResult, outputMatchesExpectation: boolean): string {
  if (result.exitCode !== 0) {
    return `command exited with ${result.exitCode}${result.stderr.trim() ? `: ${result.stderr.trim()}` : ""}`;
  }
  if (!outputMatchesExpectation) {
    return "command output did not match documented expectation";
  }
  return "command was not executable";
}
