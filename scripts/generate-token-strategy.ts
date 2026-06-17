import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { writeTokenReductionStrategyArtifact } from "../src/token-strategy-artifact.ts";

export interface GenerateTokenStrategyCommandResult {
  command: "ai-agent generate-token-strategy";
  artifact: {
    path: string;
    schemaVersion: string;
    targetSavingsPercent: string;
    exposedReductionPercent: number;
    compressedReductionPercent: number;
    sections: string[];
  };
}

export function generateTokenStrategyArtifact(projectRoot = process.cwd(), outputPath?: string): GenerateTokenStrategyCommandResult {
  const written = writeTokenReductionStrategyArtifact({ projectRoot, outputPath });

  return {
    command: "ai-agent generate-token-strategy",
    artifact: {
      path: written.path,
      schemaVersion: written.artifact.schemaVersion,
      targetSavingsPercent: `${written.artifact.targetSavings.minimumPercent}-${written.artifact.targetSavings.maximumPercent}`,
      exposedReductionPercent: written.artifact.baseline.exposedReductionPercent,
      compressedReductionPercent: written.artifact.baseline.compressedReductionPercent,
      sections: written.artifact.validationSections,
    },
  };
}

export function executeGenerateTokenStrategyCommand(args: string[]): { exitCode: number; stdout: string; stderr: string } {
  try {
    const outputPath = readOutputArg(args);
    const result = generateTokenStrategyArtifact(process.cwd(), outputPath);

    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown token-strategy generation failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function readOutputArg(args: string[]): string | undefined {
  const outputFlagIndex = args.indexOf("--output");
  if (outputFlagIndex === -1) return undefined;
  const outputPath = args[outputFlagIndex + 1] ?? "";
  if (outputPath.trim().length === 0) {
    throw new TypeError("output path must be a non-empty string");
  }
  return outputPath;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeGenerateTokenStrategyCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
