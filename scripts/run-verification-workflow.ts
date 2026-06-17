import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  defaultVerificationWorkflowResultPath,
  runVerificationWorkflow,
  type VerificationWorkflowRunnerResult,
  writeVerificationWorkflowResult,
} from "../src/verification-workflow-runner.ts";

interface ExecuteVerificationWorkflowRunnerCommandOptions {
  projectRoot?: string;
  runner?: () => Promise<VerificationWorkflowRunnerResult>;
}

export async function executeVerificationWorkflowRunnerCommand(
  projectRootOrOptions: string | ExecuteVerificationWorkflowRunnerCommandOptions = process.cwd(),
): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  const options = typeof projectRootOrOptions === "string"
    ? { projectRoot: projectRootOrOptions }
    : projectRootOrOptions;
  const projectRoot = options.projectRoot ?? process.cwd();
  const runner = options.runner;
  try {
    const written = runner
      ? await writeVerificationWorkflowResult({
          projectRoot,
          outputPath: defaultVerificationWorkflowResultPath,
          runner,
        })
      : await writeVerificationWorkflowResult({ projectRoot });
    const output = {
      command: "ai-agent run-verification-workflow",
      status: written.result.status,
      artifact: {
        path: written.path,
        schemaVersion: written.result.schemaVersion,
        caseCount: written.result.summary.caseCount,
        passedCaseCount: written.result.summary.passedCaseCount,
      },
    };
    return {
      exitCode: written.result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(output, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown verification workflow failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "verification_workflow_failed", message }, null, 2)}\n`,
    };
  }
}

export async function executeVerificationWorkflowRunnerApi(): Promise<VerificationWorkflowRunnerResult> {
  return runVerificationWorkflow();
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = await executeVerificationWorkflowRunnerCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
