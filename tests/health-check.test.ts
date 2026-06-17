import test from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { executeHealthCheckCommand } from "../scripts/health-check.ts";
import { buildHealthCheckOutput } from "../src/index.ts";

test("health-check output builder returns stable baseline and strategy metadata", () => {
  const projectRoot = "/tmp/ai-agent-health-check-test";
  const output = buildHealthCheckOutput({ projectRoot });

  assert.equal(output.schemaVersion, "health-check.v1");
  assert.equal(output.status, "ok");
  assert.equal(typeof output.baseline, "object");
  assert.equal(output.baseline.method, "deterministic-local-estimate-v1");
  assert.equal(typeof output.baseline.turnCount, "number");
  assert.equal(typeof output.baseline.rawFullTextTokens, "number");
  assert.equal(typeof output.baseline.exposedLoopContextTokens, "number");
  assert.equal(typeof output.baseline.compressedLoopContextTokens, "number");
  assert.equal(typeof output.projectedSavingsPercentage, "number");
  assert.equal(typeof output.strategyArtifactPath, "string");

  assert.deepEqual(output.baseline, {
    method: "deterministic-local-estimate-v1",
    turnCount: 7,
    rawFullTextTokens: 734,
    exposedLoopContextTokens: 260,
    compressedLoopContextTokens: 67,
  });
  assert.equal(output.projectedSavingsPercentage, 90.9);
  assert.equal(output.strategyArtifactPath, join(projectRoot, "docs/token-reduction-strategy.md"));
});

test("health-check output builder accepts a custom strategy artifact path", () => {
  const output = buildHealthCheckOutput({
    projectRoot: "/tmp/ai-agent-health-check-test",
    strategyArtifactPath: "artifact/token-strategy.md",
  });

  assert.equal(output.strategyArtifactPath, "/tmp/ai-agent-health-check-test/artifact/token-strategy.md");
});

test("health-check command exposes the builder schema without wrapping it", () => {
  const projectRoot = "/tmp/ai-agent-health-check-command-test";
  const result = executeHealthCheckCommand([], projectRoot);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.deepEqual(JSON.parse(result.stdout), buildHealthCheckOutput({ projectRoot }));
});
