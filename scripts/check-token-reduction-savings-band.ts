import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { measureCurrentTokenBaseline, measureTokenReductionSavings } from "../src/token-baseline.ts";
import type { TokenBaselineInput, TokenBaselineMeasurement, TokenReductionSavingsMeasurement } from "../src/token-baseline.ts";

export interface TokenReductionSavingsBandCheckResult {
  command: "ai-agent check-token-reduction-savings-band";
  fixture: {
    path: string;
    present: boolean;
    name: string | null;
  };
  measurement: TokenBaselineMeasurement;
  savings: TokenReductionSavingsMeasurement;
  pass: boolean;
}

interface TokenReductionStrategyFixture {
  name?: unknown;
  input?: unknown;
}

const defaultFixturePath = "tests/fixtures/token-baseline-fixture.json";

export function checkTokenReductionSavingsBand(
  projectRoot = process.cwd(),
  fixturePath = defaultFixturePath,
): TokenReductionSavingsBandCheckResult {
  const resolvedPath = resolve(projectRoot, fixturePath);
  if (!existsSync(resolvedPath)) {
    throw new Error(`token reduction strategy fixture not found: ${resolvedPath}`);
  }

  const fixture = parseFixture(readFileSync(resolvedPath, "utf8"), resolvedPath);
  const input = validateFixtureInput(fixture.input);
  const measurement = measureCurrentTokenBaseline(input);
  const savings = measureTokenReductionSavings({
    baseline: measurement.rawFullTextTokens,
    reduced: measurement.exposedLoopContextTokens,
  });

  return {
    command: "ai-agent check-token-reduction-savings-band",
    fixture: {
      path: resolvedPath,
      present: true,
      name: typeof fixture.name === "string" ? fixture.name : null,
    },
    measurement,
    savings,
    pass: savings.withinTargetRange,
  };
}

export function executeTokenReductionSavingsBandCheckCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const fixturePath = readArgValue(args, "--fixture") ?? defaultFixturePath;
    const result = checkTokenReductionSavingsBand(process.cwd(), fixturePath);

    return {
      exitCode: result.pass ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown token reduction savings band check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function parseFixture(rawJson: string, path: string): TokenReductionStrategyFixture {
  try {
    const parsed = JSON.parse(rawJson) as TokenReductionStrategyFixture;
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("fixture root must be an object");
    }
    return parsed;
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown JSON parse failure";
    throw new Error(`invalid token reduction strategy fixture ${path}: ${message}`);
  }
}

function validateFixtureInput(input: unknown): TokenBaselineInput {
  if (typeof input !== "object" || input === null) {
    throw new Error("token reduction strategy fixture input must be an object");
  }

  const candidate = input as Partial<TokenBaselineInput>;
  if (!Array.isArray(candidate.turns) || candidate.turns.length === 0) {
    throw new Error("token reduction strategy fixture input.turns must be a non-empty array");
  }

  for (const [index, turn] of candidate.turns.entries()) {
    if (typeof turn !== "object" || turn === null) {
      throw new Error(`token reduction strategy fixture turn ${index} must be an object`);
    }
    const candidateTurn = turn as Record<string, unknown>;
    if (typeof candidateTurn.round !== "number") {
      throw new Error(`token reduction strategy fixture turn ${index}.round must be a number`);
    }
    if (typeof candidateTurn.role !== "string") {
      throw new Error(`token reduction strategy fixture turn ${index}.role must be a string`);
    }
    if (typeof candidateTurn.kind !== "string") {
      throw new Error(`token reduction strategy fixture turn ${index}.kind must be a string`);
    }
    if (typeof candidateTurn.content !== "string") {
      throw new Error(`token reduction strategy fixture turn ${index}.content must be a string`);
    }
    if (typeof candidateTurn.visibleSummary !== "string") {
      throw new Error(`token reduction strategy fixture turn ${index}.visibleSummary must be a string`);
    }
  }

  if (candidate.compressedContext !== undefined && typeof candidate.compressedContext !== "string") {
    throw new Error("token reduction strategy fixture input.compressedContext must be a string when present");
  }

  return candidate as TokenBaselineInput;
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
  const result = executeTokenReductionSavingsBandCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
