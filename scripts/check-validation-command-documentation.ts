import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkValidationCommandDocumentation,
  type ValidationCommandDocumentationCheckResult,
} from "../src/validation-command-documentation.ts";

export function checkValidationCommandDocumentationCommand(
  projectRoot = process.cwd(),
): ValidationCommandDocumentationCheckResult {
  return checkValidationCommandDocumentation({ projectRoot });
}

export function executeCheckValidationCommandDocumentationCommand(projectRoot = process.cwd()): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const result = checkValidationCommandDocumentationCommand(projectRoot);
    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown validation command documentation check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "validation_command_documentation_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeCheckValidationCommandDocumentationCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
