import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildTokenReductionStrategyArtifact } from "../src/token-strategy-artifact.ts";

export interface TokenStrategyCheckResult {
  command: "ai-agent check-token-strategy";
  artifact: {
    path: string;
    present: boolean;
    schemaVersion: string;
    requiredSections: string[];
    missingSections: string[];
  };
}

export function checkTokenStrategyArtifact(
  projectRoot = process.cwd(),
  artifactPath = "docs/token-reduction-strategy.md",
): TokenStrategyCheckResult {
  const required = buildTokenReductionStrategyArtifact().validationSections;
  const resolvedPath = resolve(projectRoot, artifactPath);

  if (!existsSync(resolvedPath)) {
    return buildResult(resolvedPath, false, required, required);
  }

  const markdown = readFileSync(resolvedPath, "utf8");
  const missingSections = required.filter((section) => !hasMarkdownSection(markdown, section));

  return buildResult(resolvedPath, true, required, missingSections);
}

export function executeTokenStrategyCheckCommand(args: string[]): { exitCode: number; stdout: string; stderr: string } {
  try {
    const projectRoot = readArgValue(args, "--project-root") ?? process.cwd();
    const artifactPath = readArgValue(args, "--artifact") ?? "docs/token-reduction-strategy.md";
    const result = checkTokenStrategyArtifact(projectRoot, artifactPath);
    const exitCode = result.artifact.present && result.artifact.missingSections.length === 0 ? 0 : 1;

    return {
      exitCode,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown token-strategy check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function buildResult(
  path: string,
  present: boolean,
  requiredSections: string[],
  missingSections: string[],
): TokenStrategyCheckResult {
  return {
    command: "ai-agent check-token-strategy",
    artifact: {
      path,
      present,
      schemaVersion: "token-reduction-strategy.v1",
      requiredSections,
      missingSections,
    },
  };
}

function hasMarkdownSection(markdown: string, section: string): boolean {
  const escapedSection = section.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^##\\s+${escapedSection}\\s*$`, "m").test(markdown);
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
  const result = executeTokenStrategyCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
