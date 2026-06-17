import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { writeLoopContextCompressionPolicyArtifact } from "../src/loop-context-compression-policy.ts";

export interface GenerateLoopContextCompressionPolicyCommandResult {
  command: "ai-agent generate-loop-context-compression-policy";
  artifact: {
    path: string;
    schemaVersion: string;
    sections: string[];
    retainedFieldCount: number;
    summarizedFieldCount: number;
    droppedFieldCount: number;
    iterationBoundaryCount: number;
  };
}

export function generateLoopContextCompressionPolicyArtifact(
  projectRoot = process.cwd(),
  outputPath?: string,
): GenerateLoopContextCompressionPolicyCommandResult {
  const written = writeLoopContextCompressionPolicyArtifact({ projectRoot, outputPath });

  return {
    command: "ai-agent generate-loop-context-compression-policy",
    artifact: {
      path: written.path,
      schemaVersion: written.artifact.schemaVersion,
      sections: written.artifact.validationSections,
      retainedFieldCount: written.artifact.retainedFields.length,
      summarizedFieldCount: written.artifact.summarizedFields.length,
      droppedFieldCount: written.artifact.droppedFields.length,
      iterationBoundaryCount: written.artifact.iterationBoundaries.length,
    },
  };
}

export function executeGenerateLoopContextCompressionPolicyCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const outputPath = readOutputArg(args);
    const result = generateLoopContextCompressionPolicyArtifact(process.cwd(), outputPath);

    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown loop-context compression policy generation failure";
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
  const result = executeGenerateLoopContextCompressionPolicyCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
