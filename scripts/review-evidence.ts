import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildInspectionInventory,
  extractReviewFindings,
  writeReviewEvidenceArtifact,
  type InspectedModuleInput,
  type ReviewEvidenceArtifact,
} from "../src/inspection.ts";

export interface ReviewEvidenceCommandResult {
  command: "ai-agent review-evidence";
  artifact: {
    path: string;
    schemaVersion: ReviewEvidenceArtifact["schemaVersion"];
    inspectedModules: number;
    findingCount: number;
    recommendation: ReviewEvidenceArtifact["summary"]["recommendation"];
  };
}

export function generateReviewEvidence(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): { artifact: ReviewEvidenceArtifact; artifactPath: string } {
  const projectRoot = resolve(input.projectRoot ?? process.cwd());
  const artifactPath = resolve(projectRoot, input.outputPath ?? "docs/review-evidence.json");
  const excludedGeneratedArtifactPaths = new Set(
    ["docs/review-evidence.json", toProjectRelativePath(projectRoot, artifactPath)].filter(Boolean),
  );
  const inventory = buildInspectionInventory(projectRoot).filter(
    (entry) => !excludedGeneratedArtifactPaths.has(entry.relativePath),
  );
  const inspectedModules: InspectedModuleInput[] = inventory.map((entry) => ({
    ...entry,
    content: readInspectedContent(projectRoot, entry.relativePath),
  }));
  const findings = extractReviewFindings(inspectedModules);
  const artifact = writeReviewEvidenceArtifact({
    outputPath: artifactPath,
    inventory,
    findings,
  });

  return { artifact, artifactPath };
}

export function runReviewEvidenceCommand(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): ReviewEvidenceCommandResult {
  const { artifact, artifactPath } = generateReviewEvidence(input);

  return {
    command: "ai-agent review-evidence",
    artifact: {
      path: artifactPath,
      schemaVersion: artifact.schemaVersion,
      inspectedModules: artifact.summary.inspectedModules,
      findingCount: artifact.summary.findingCount,
      recommendation: artifact.summary.recommendation,
    },
  };
}

export function executeReviewEvidenceCommand(args: string[]): { exitCode: number; stdout: string; stderr: string } {
  try {
    const options = parseArgs(args);
    const result = runReviewEvidenceCommand(options);
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown review-evidence command failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function parseArgs(args: string[]): { projectRoot?: string; outputPath?: string } {
  const options: { projectRoot?: string; outputPath?: string } = {};

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--project-root") {
      options.projectRoot = readRequiredFlagValue(args, index, arg);
      index += 1;
      continue;
    }
    if (arg === "--output") {
      options.outputPath = readRequiredFlagValue(args, index, arg);
      index += 1;
      continue;
    }
    throw new TypeError(`unknown review-evidence argument: ${arg}`);
  }

  return options;
}

function readRequiredFlagValue(args: string[], index: number, flag: string): string {
  const value = args[index + 1] ?? "";
  if (value.trim() === "" || value.startsWith("--")) {
    throw new TypeError(`${flag} requires a non-empty value`);
  }
  return value;
}

function readInspectedContent(projectRoot: string, relativePath: string): string {
  const path = resolve(projectRoot, relativePath);
  if (!existsSync(path)) {
    return "";
  }
  return readFileSync(path, "utf8");
}

function toProjectRelativePath(projectRoot: string, path: string): string | undefined {
  const absolutePath = isAbsolute(path) ? resolve(path) : resolve(projectRoot, path);
  const relativePath = relative(projectRoot, absolutePath);
  if (relativePath === "" || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    return undefined;
  }
  return relativePath.split(sep).join("/");
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeReviewEvidenceCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
