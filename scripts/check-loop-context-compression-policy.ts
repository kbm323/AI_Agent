import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildLoopContextCompressionPolicyArtifact,
  validateLoopContextCompressionPolicyArtifact,
} from "../src/loop-context-compression-policy.ts";

export interface LoopContextCompressionPolicyCheckResult {
  command: "ai-agent check-loop-context-compression-policy";
  status: "passed" | "failed";
  artifact: {
    path: string;
    present: boolean;
    schemaVersion: string;
    requiredSections: string[];
    missingSections: string[];
  };
  schema: {
    retainedFieldCount: number;
    summarizedFieldCount: number;
    droppedFieldCount: number;
    iterationBoundaryCount: number;
    deterministicOrderingValid: boolean;
    iterationBoundariesValid: boolean;
    missingPolicyGroups: string[];
  };
}

export function checkLoopContextCompressionPolicyArtifact(
  projectRoot = process.cwd(),
  artifactPath = "docs/loop-context-compression-policy.md",
): LoopContextCompressionPolicyCheckResult {
  const artifact = buildLoopContextCompressionPolicyArtifact();
  const resolvedPath = resolve(projectRoot, artifactPath);
  const present = existsSync(resolvedPath);
  const markdown = present ? readFileSync(resolvedPath, "utf8") : "";
  const validation = validateLoopContextCompressionPolicyArtifact(artifact, markdown);
  const missingSections = present ? validation.missingSections : artifact.validationSections;
  const status =
    present &&
    missingSections.length === 0 &&
    validation.missingPolicyGroups.length === 0 &&
    validation.deterministicOrderingValid &&
    validation.iterationBoundariesValid
      ? "passed"
      : "failed";

  return {
    command: "ai-agent check-loop-context-compression-policy",
    status,
    artifact: {
      path: resolvedPath,
      present,
      schemaVersion: artifact.schemaVersion,
      requiredSections: artifact.validationSections,
      missingSections,
    },
    schema: {
      retainedFieldCount: artifact.retainedFields.length,
      summarizedFieldCount: artifact.summarizedFields.length,
      droppedFieldCount: artifact.droppedFields.length,
      iterationBoundaryCount: artifact.iterationBoundaries.length,
      deterministicOrderingValid: validation.deterministicOrderingValid,
      iterationBoundariesValid: validation.iterationBoundariesValid,
      missingPolicyGroups: validation.missingPolicyGroups,
    },
  };
}

export function executeLoopContextCompressionPolicyCheckCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const projectRoot = readArgValue(args, "--project-root") ?? process.cwd();
    const artifactPath = readArgValue(args, "--artifact") ?? "docs/loop-context-compression-policy.md";
    const result = checkLoopContextCompressionPolicyArtifact(projectRoot, artifactPath);

    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown loop-context compression policy check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function readArgValue(args: string[], flag: string): string | undefined {
  const flagIndex = args.indexOf(flag);
  if (flagIndex === -1) return undefined;
  const value = args[flagIndex + 1] ?? "";
  if (value.trim().length === 0) {
    throw new TypeError(`${flag} must be followed by a non-empty value`);
  }
  return value;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeLoopContextCompressionPolicyCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
