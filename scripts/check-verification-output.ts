import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildVerificationOutputCheckResult,
  buildVerificationOutputDocument,
  validateVerificationOutputCheckResult,
  type VerificationOutputCheckResult,
} from "../src/verification-output.ts";

export function checkVerificationOutput(projectRoot = process.cwd()): VerificationOutputCheckResult {
  const document = buildVerificationOutputDocument(projectRoot);
  const result = buildVerificationOutputCheckResult({ projectRoot, document });
  const validation = validateVerificationOutputCheckResult(result);
  if (!validation.valid) {
    throw new Error(`verification output check result schema validation failed: ${validation.errors.join("; ")}`);
  }
  return result;
}

export function executeCheckVerificationOutputCommand(
  projectRoot = process.cwd(),
): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkVerificationOutput(projectRoot);
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown verification output check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "verification_output_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeCheckVerificationOutputCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
