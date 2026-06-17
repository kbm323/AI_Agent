import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkEnvironmentDependencies,
  type EnvironmentDependencyCheckResult,
} from "../src/environment-dependency-verification.ts";

export function checkEnvironmentDependencyCommands(projectRoot = process.cwd()): EnvironmentDependencyCheckResult {
  return checkEnvironmentDependencies({ projectRoot });
}

export function executeCheckEnvironmentDependenciesCommand(projectRoot = process.cwd()): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const result = checkEnvironmentDependencyCommands(projectRoot);
    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown environment dependency check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "environment_dependency_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeCheckEnvironmentDependenciesCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
