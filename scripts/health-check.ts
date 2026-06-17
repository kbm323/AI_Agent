import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildHealthCheckOutput, type HealthCheckOutput } from "../src/index.ts";

export function runHealthCheck(projectRoot = process.cwd()): HealthCheckOutput {
  return buildHealthCheckOutput({ projectRoot });
}

export function executeHealthCheckCommand(args: string[], projectRoot = process.cwd()): { exitCode: number; stdout: string; stderr: string } {
  try {
    assertNoArgs(args);
    const output = runHealthCheck(projectRoot);

    return {
      exitCode: 0,
      stdout: `${JSON.stringify(output, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown health-check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function assertNoArgs(args: string[]): void {
  if (args.length > 0) {
    throw new TypeError("health-check does not accept arguments");
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeHealthCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
