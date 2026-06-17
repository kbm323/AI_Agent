import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateCompletedReviewArtifacts } from "../src/review-artifact-completeness.ts";

export function checkReviewArtifactCompleteness(projectRoot = process.cwd()) {
  return validateCompletedReviewArtifacts(projectRoot);
}

export function executeCheckReviewArtifactCompletenessCommand(
  projectRoot = process.cwd(),
): { exitCode: number; stdout: string; stderr: string } {
  const result = checkReviewArtifactCompleteness(projectRoot);
  return {
    exitCode: result.status === "passed" ? 0 : 1,
    stdout: `${JSON.stringify(result, null, 2)}\n`,
    stderr: "",
  };
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeCheckReviewArtifactCompletenessCommand();
  process.stdout.write(result.stdout);
  process.exitCode = result.exitCode;
}
