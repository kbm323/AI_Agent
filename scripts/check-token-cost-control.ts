import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  verifyRepresentativeTokenCostControl,
  writeTokenCostControlVerificationArtifact,
} from "../src/token-baseline.ts";

export type TokenCostControlCheckCommandResult = ReturnType<typeof verifyRepresentativeTokenCostControl> & {
  command: "ai-agent check-token-cost-control";
  artifactPath?: string;
};

export function checkTokenCostControl(): TokenCostControlCheckCommandResult {
  return {
    command: "ai-agent check-token-cost-control",
    ...verifyRepresentativeTokenCostControl(),
  };
}

export function executeTokenCostControlCheckCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const options = readOptions(args);
    const result = checkTokenCostControl();
    if (options.writeArtifact) {
      const written = writeTokenCostControlVerificationArtifact({
        projectRoot: process.cwd(),
        outputPath: options.outputPath,
      });
      result.artifactPath = written.path;
    }

    return {
      exitCode: result.pass ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown token-cost-control check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

interface TokenCostControlCheckOptions {
  writeArtifact: boolean;
  outputPath?: string;
}

function readOptions(args: string[]): TokenCostControlCheckOptions {
  const options: TokenCostControlCheckOptions = { writeArtifact: false };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--write-artifact") {
      options.writeArtifact = true;
      continue;
    }
    if (arg === "--artifact") {
      const outputPath = args[index + 1] ?? "";
      if (outputPath.trim().length === 0) {
        throw new TypeError("--artifact must be followed by a non-empty value");
      }
      options.outputPath = outputPath;
      index += 1;
      continue;
    }
    throw new TypeError(`unexpected argument: ${arg}`);
  }

  return options;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeTokenCostControlCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
