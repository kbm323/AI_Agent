import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { verifyRepresentativeLoopContextCompression } from "../src/compression-verification.ts";

export type LoopContextCompressionVerificationCommandResult = ReturnType<
  typeof verifyRepresentativeLoopContextCompression
> & {
  command: "ai-agent check-loop-context-compression-verification";
};

export function executeLoopContextCompressionVerificationCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    rejectUnknownArgs(args);
    const result: LoopContextCompressionVerificationCommandResult = {
      command: "ai-agent check-loop-context-compression-verification",
      ...verifyRepresentativeLoopContextCompression(),
    };

    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown compression verification failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function rejectUnknownArgs(args: string[]): void {
  if (args.length > 0) {
    throw new TypeError(`unexpected argument: ${args[0]}`);
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeLoopContextCompressionVerificationCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
