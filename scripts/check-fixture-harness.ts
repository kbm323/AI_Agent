import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { executeDryRunCommand } from "./dry-run.ts";
import { normalizeCapturedResponse } from "../src/output-normalization.ts";
import type { NormalizedCapturedResponse } from "../src/output-normalization.ts";

type FixtureInvocation = "api";
type FixtureStream = "stdout" | "stderr";

interface FixtureExpectedOutput {
  exitCode: number;
  stream: FixtureStream;
  jsonStatus?: string;
  jsonError?: string;
}

interface FixtureDefinition {
  name: string;
  invocation: FixtureInvocation;
  args: string[];
  repetitions: number;
  expected: FixtureExpectedOutput;
}

interface CapturedFixtureRun {
  run: number;
  exitCode: number;
  stdout: string;
  stderr: string;
  normalized: NormalizedCapturedResponse;
}

interface CapturedFixtureSummary {
  run: number;
  exitCode: number;
  stream: FixtureStream;
  parseableJson: boolean;
  stdoutSha256: string;
  stderrSha256: string;
  normalizedSha256: string;
  stdoutBytes: number;
  stderrBytes: number;
  jsonStatus?: string;
  jsonError?: string;
}

interface FixtureHarnessCaseResult {
  name: string;
  invocation: FixtureInvocation;
  repetitions: number;
  deterministic: true;
  normalizedEqual: true;
  stdoutEqual: true;
  stderrEqual: true;
  exitCodesEqual: true;
  expected: FixtureExpectedOutput;
  captures: CapturedFixtureSummary[];
}

interface FixtureHarnessResult {
  command: "ai-agent check-fixture-harness";
  status: "passed";
  harness: {
    schemaVersion: "fixture-harness.v1";
    fixturePath: string;
    cases: FixtureHarnessCaseResult[];
  };
}

const defaultFixturePath = "tests/fixtures/dry-run-harness-fixtures.json";

export async function checkFixtureHarness(fixturePath = defaultFixturePath): Promise<FixtureHarnessResult> {
  const fixtures = readFixtureDefinitions(fixturePath);
  assert.equal(fixtures.length > 0, true, "fixture harness requires at least one fixture");

  const cases: FixtureHarnessCaseResult[] = [];
  for (const fixture of fixtures) {
    cases.push(await runFixtureCase(fixture));
  }

  return {
    command: "ai-agent check-fixture-harness",
    status: "passed",
    harness: {
      schemaVersion: "fixture-harness.v1",
      fixturePath,
      cases,
    },
  };
}

export async function executeCheckFixtureHarnessCommand(args: string[] = []): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const fixturePath = readFixturePathArg(args) ?? defaultFixturePath;
    const result = await checkFixtureHarness(fixturePath);
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown fixture harness failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "fixture_harness_failed", message }, null, 2)}\n`,
    };
  }
}

async function runFixtureCase(fixture: FixtureDefinition): Promise<FixtureHarnessCaseResult> {
  validateFixtureDefinition(fixture);
  const captures: CapturedFixtureRun[] = [];

  for (let index = 0; index < fixture.repetitions; index += 1) {
    captures.push(await invokeFixture(fixture));
  }

  const first = captures[0];
  assert.notEqual(first, undefined, `fixture ${fixture.name} did not run`);
  for (const capture of captures) {
    assert.equal(capture.exitCode, first.exitCode, `fixture ${fixture.name} exit code must be deterministic`);
    assert.deepEqual(capture.normalized, first.normalized, `fixture ${fixture.name} normalized output must be deterministic`);
    assert.equal(capture.stdout, first.stdout, `fixture ${fixture.name} stdout must be deterministic`);
    assert.equal(capture.stderr, first.stderr, `fixture ${fixture.name} stderr must be deterministic`);
    assert.equal(capture.exitCode, fixture.expected.exitCode, `fixture ${fixture.name} exit code must match expected`);
    assertExpectedJson(fixture, capture);
  }

  return {
    name: fixture.name,
    invocation: fixture.invocation,
    repetitions: fixture.repetitions,
    deterministic: true,
    normalizedEqual: true,
    stdoutEqual: true,
    stderrEqual: true,
    exitCodesEqual: true,
    expected: fixture.expected,
    captures: captures.map((capture, index) => summarizeCapture(index + 1, fixture.expected.stream, capture)),
  };
}

async function invokeFixture(fixture: FixtureDefinition): Promise<CapturedFixtureRun> {
  if (fixture.invocation === "api") {
    const response = await executeDryRunCommand(fixture.args);
    return {
      ...response,
      run: 0,
      normalized: normalizeCapturedResponse(response),
    };
  }
  throw new TypeError(`unsupported fixture invocation: ${fixture.invocation}`);
}

function assertExpectedJson(fixture: FixtureDefinition, capture: CapturedFixtureRun): void {
  const streamContent = fixture.expected.stream === "stdout" ? capture.stdout : capture.stderr;
  const parsed = parseJson(streamContent, fixture.name);
  if (fixture.expected.jsonStatus !== undefined) {
    assert.equal(parsed.status, fixture.expected.jsonStatus, `fixture ${fixture.name} JSON status must match expected`);
  }
  if (fixture.expected.jsonError !== undefined) {
    assert.equal(parsed.error, fixture.expected.jsonError, `fixture ${fixture.name} JSON error must match expected`);
  }
}

function summarizeCapture(run: number, stream: FixtureStream, capture: CapturedFixtureRun): CapturedFixtureSummary {
  const parsed = parseJson(stream === "stdout" ? capture.stdout : capture.stderr, `run ${run}`);
  return {
    run,
    exitCode: capture.exitCode,
    stream,
    parseableJson: true,
    stdoutSha256: sha256(capture.stdout),
    stderrSha256: sha256(capture.stderr),
    normalizedSha256: sha256(JSON.stringify(capture.normalized)),
    stdoutBytes: Buffer.byteLength(capture.stdout),
    stderrBytes: Buffer.byteLength(capture.stderr),
    jsonStatus: typeof parsed.status === "string" ? parsed.status : undefined,
    jsonError: typeof parsed.error === "string" ? parsed.error : undefined,
  };
}

function readFixtureDefinitions(fixturePath: string): FixtureDefinition[] {
  const raw = JSON.parse(readFileSync(fixturePath, "utf8")) as unknown;
  assert.equal(Array.isArray(raw), true, "fixture file must contain an array");
  return raw as FixtureDefinition[];
}

function validateFixtureDefinition(fixture: FixtureDefinition): void {
  assert.equal(typeof fixture.name, "string", "fixture name must be a string");
  assert.equal(fixture.name.trim().length > 0, true, "fixture name must be non-empty");
  assert.equal(fixture.invocation, "api", `fixture ${fixture.name} invocation must be api`);
  assert.equal(Array.isArray(fixture.args), true, `fixture ${fixture.name} args must be an array`);
  assert.equal(Number.isInteger(fixture.repetitions), true, `fixture ${fixture.name} repetitions must be an integer`);
  assert.equal(fixture.repetitions >= 2, true, `fixture ${fixture.name} repetitions must be at least 2`);
  assert.equal(typeof fixture.expected, "object", `fixture ${fixture.name} expected output is required`);
  assert.equal(Number.isInteger(fixture.expected.exitCode), true, `fixture ${fixture.name} expected exitCode must be an integer`);
  assert.equal(["stdout", "stderr"].includes(fixture.expected.stream), true, `fixture ${fixture.name} expected stream must be stdout or stderr`);
}

function parseJson(content: string, label: string): Record<string, unknown> {
  try {
    return JSON.parse(content) as Record<string, unknown>;
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown JSON parse failure";
    throw new TypeError(`${label} did not emit parseable JSON: ${message}`);
  }
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function readFixturePathArg(args: string[]): string | undefined {
  const fixtureFlagIndex = args.indexOf("--fixture");
  if (fixtureFlagIndex === -1) return undefined;
  const fixturePath = args[fixtureFlagIndex + 1] ?? "";
  if (fixturePath.trim().length === 0) {
    throw new TypeError("fixture path must be non-empty");
  }
  return fixturePath;
}

const invokedAsScript = process.argv[1]?.endsWith("check-fixture-harness.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckFixtureHarnessCommand(process.argv.slice(2));
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
