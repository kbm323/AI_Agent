import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkDiagnosticArtifacts,
  type DiagnosticArtifactCheckResult,
} from "../src/artifact-check.ts";

export function checkArtifacts(projectRoot = process.cwd()): DiagnosticArtifactCheckResult {
  return checkDiagnosticArtifacts(projectRoot);
}

export function executeCheckArtifactsCommand(projectRoot = process.cwd()): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  const result = checkArtifacts(projectRoot);
  return {
    exitCode: result.status === "passed" ? 0 : 1,
    stdout: `${JSON.stringify(result, null, 2)}\n`,
    stderr: "",
  };
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeCheckArtifactsCommand();
  process.stdout.write(result.stdout);
  process.exitCode = result.exitCode;
}
