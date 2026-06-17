import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { normalizeCapturedResponse, type NormalizedCapturedResponse } from "../src/output-normalization.ts";

export const TYPECHECK_CHECK_ARTIFACT_PATH = "docs/generated/typecheck-check-result.json";
export const TYPECHECK_INVOKED_COMMAND = "resolved package.json scripts.typecheck command";
export const TYPECHECK_DOCUMENTED_FALLBACK_COMMAND =
  "node --check src/*.ts && node --check scripts/*.ts && node --check tests/*.ts";
export const TYPECHECK_UNSUPPORTED_COMMAND_ERROR = "unsupported_typecheck_command_form";

interface TypecheckCommandCheckResult {
  command: "ai-agent check:typecheck";
  status: "passed";
  typecheck: {
    schemaVersion: "typecheck-command-check.v1";
    scriptName: "typecheck";
    configuredCommand: string;
    invokedCommand: string;
    exitCode: 0;
    exitsWithCodeZero: true;
    artifactPath?: string;
  };
}

interface FailedTypecheckCommandCheckResult {
  command: "ai-agent check:typecheck";
  status: "failed";
  typecheck: {
    schemaVersion: "typecheck-command-check.v1";
    scriptName: "typecheck";
    configuredCommand: string;
    invokedCommand: string;
    exitCode: number;
    exitsWithCodeZero: false;
    artifactPath?: string;
  };
}

type CapturedCommandResult = { exitCode: number; stdout: string; stderr: string };
type TypecheckCommandOutput = TypecheckCommandCheckResult | FailedTypecheckCommandCheckResult;

export interface TypecheckProofArtifact {
  schemaVersion: "typecheck-proof-artifact.v1";
  command: "ai-agent check:typecheck";
  status: "passed" | "failed";
  typecheck: {
    scriptName: "typecheck";
    configuredCommand: string;
    invokedCommand: string;
    exitCode: number;
    exitsWithCodeZero: boolean;
  };
  capturedResult: NormalizedCapturedResponse;
}

export function checkTypecheckCommand(projectRoot = process.cwd()): TypecheckCommandCheckResult {
  const configuredCommand = readConfiguredTypecheckCommand(projectRoot);
  const result = runConfiguredTypecheck(projectRoot);

  assert.equal(
    result.exitCode,
    0,
    `configured typecheck command must exit with code 0; received ${result.exitCode}`,
  );

  return {
    command: "ai-agent check:typecheck",
    status: "passed",
    typecheck: {
      schemaVersion: "typecheck-command-check.v1",
      scriptName: "typecheck",
      configuredCommand,
      invokedCommand: configuredCommand,
      exitCode: 0,
      exitsWithCodeZero: true,
    },
  };
}

export function executeCheckTypecheckCommand(projectRoot = process.cwd()): CapturedCommandResult {
  let configuredCommand = "";
  try {
    configuredCommand = readConfiguredTypecheckCommand(projectRoot);
    const typecheck = runConfiguredTypecheck(projectRoot);
    const artifactPath = resolve(projectRoot, TYPECHECK_CHECK_ARTIFACT_PATH);
    const output: TypecheckCommandOutput =
      typecheck.exitCode === 0
        ? {
            command: "ai-agent check:typecheck",
            status: "passed",
            typecheck: {
              schemaVersion: "typecheck-command-check.v1",
              scriptName: "typecheck",
              configuredCommand,
              invokedCommand: configuredCommand,
              exitCode: 0,
              exitsWithCodeZero: true,
              artifactPath,
            },
          }
        : {
            command: "ai-agent check:typecheck",
            status: "failed",
            typecheck: {
              schemaVersion: "typecheck-command-check.v1",
              scriptName: "typecheck",
              configuredCommand,
              invokedCommand: configuredCommand,
              exitCode: typecheck.exitCode,
              exitsWithCodeZero: false,
              artifactPath,
            },
          };
    const exitCode = typecheck.exitCode === 0 ? 0 : 1;
    const stdout = `${JSON.stringify(output, null, 2)}\n`;
    writeTypecheckProofArtifact(projectRoot, output, { exitCode, stdout, stderr: "" });

    return {
      exitCode,
      stdout,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown typecheck command check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify(
        {
          error: "typecheck_command_check_failed",
          message,
          configuredCommand,
        },
        null,
        2,
      )}\n`,
    };
  }
}

export function writeTypecheckProofArtifact(
  projectRoot: string,
  output: TypecheckCommandOutput,
  capturedResult: CapturedCommandResult,
): TypecheckProofArtifact {
  const artifact: TypecheckProofArtifact = {
    schemaVersion: "typecheck-proof-artifact.v1",
    command: "ai-agent check:typecheck",
    status: output.status,
    typecheck: {
      scriptName: "typecheck",
      configuredCommand: output.typecheck.configuredCommand,
      invokedCommand: output.typecheck.invokedCommand,
      exitCode: output.typecheck.exitCode,
      exitsWithCodeZero: output.typecheck.exitsWithCodeZero,
    },
    capturedResult: normalizeCapturedResponse(capturedResult),
  };
  const artifactPath = resolve(projectRoot, TYPECHECK_CHECK_ARTIFACT_PATH);
  mkdirSync(dirname(artifactPath), { recursive: true });
  writeFileSync(`${artifactPath}.tmp`, `${JSON.stringify(artifact, null, 2)}\n`);
  renameSync(`${artifactPath}.tmp`, artifactPath);
  return artifact;
}

function readConfiguredTypecheckCommand(projectRoot: string): string {
  const packageJsonPath = resolve(projectRoot, "package.json");
  const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  const configuredCommand = packageJson.scripts?.typecheck;

  if (configuredCommand === undefined) {
    return TYPECHECK_DOCUMENTED_FALLBACK_COMMAND;
  }
  if (typeof configuredCommand !== "string") {
    throw new TypeError("package.json scripts.typecheck must be a string when configured");
  }
  if (configuredCommand.trim() === "") {
    throw new TypeError("package.json scripts.typecheck must be non-empty");
  }
  return configuredCommand;
}

function runConfiguredTypecheck(projectRoot: string): { exitCode: number } {
  const configuredCommand = readConfiguredTypecheckCommand(projectRoot);
  validateSupportedTypecheckCommand(configuredCommand);
  const result = spawnSync(configuredCommand, {
    cwd: projectRoot,
    encoding: "utf8",
    shell: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error) {
    throw result.error;
  }
  return { exitCode: typeof result.status === "number" ? result.status : 1 };
}

function validateSupportedTypecheckCommand(configuredCommand: string): void {
  const segments = configuredCommand.split("&&").map((segment) => segment.trim());
  if (segments.length === 0 || segments.some((segment) => segment.length === 0)) {
    throw new TypeError(
      `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: scripts.typecheck must contain non-empty node command segments joined with &&`,
    );
  }

  for (const segment of segments) {
    validateSupportedTypecheckSegment(segment);
  }
}

function validateSupportedTypecheckSegment(segment: string): void {
  if (/(\|\||[;|`<>]|\$\(|\n|\r)/.test(segment)) {
    throw new TypeError(
      `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: scripts.typecheck segment uses unsupported shell syntax: ${segment}`,
    );
  }

  const tokens = segment.split(/\s+/);
  if (tokens[0] !== "node") {
    throw new TypeError(
      `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: scripts.typecheck segment must start with node: ${segment}`,
    );
  }

  if (tokens[1] === "--check") {
    const targets = tokens.slice(2);
    if (targets.length === 0) {
      throw new TypeError(
        `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: node --check segments must include at least one target: ${segment}`,
      );
    }
    for (const target of targets) {
      validateRelativeScriptTarget(target, segment);
    }
    return;
  }

  if (tokens.length === 2) {
    validateRelativeScriptTarget(tokens[1], segment);
    return;
  }

  throw new TypeError(
    `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: supported forms are "node --check <relative targets>" or "node <relative script>", joined with &&`,
  );
}

function validateRelativeScriptTarget(target: string, segment: string): void {
  if (target.startsWith("/") || target.startsWith("../") || target.includes("/../") || target === "..") {
    throw new TypeError(
      `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: scripts.typecheck target must stay within the project: ${segment}`,
    );
  }
  if (!/^[A-Za-z0-9_./*-]+$/.test(target)) {
    throw new TypeError(
      `${TYPECHECK_UNSUPPORTED_COMMAND_ERROR}: scripts.typecheck target contains unsupported characters: ${segment}`,
    );
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-typecheck.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckTypecheckCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
