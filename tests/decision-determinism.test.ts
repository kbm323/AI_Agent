import test from "node:test";
import assert from "node:assert/strict";
import {
  checkDecisionDeterminism,
  executeCheckDecisionDeterminismCommand,
} from "../scripts/check-decision-determinism.ts";

test("decision determinism API check returns stable repeated-run evidence", async () => {
  const result = await checkDecisionDeterminism();

  assert.equal(result.command, "ai-agent check-decision-determinism");
  assert.equal(result.status, "passed");
  assert.deepEqual(result.fixedInput, {
    userRequest: "뮤직비디오 오프닝 아이디어를 회의해줘.",
    inputIdentifier: "request:f42143edc0867a0d",
    executionId: "run:5f605735bb696dec",
  });
  assert.deepEqual(result.determinism, {
    deterministic: true,
    stdoutEqual: true,
    selectedDecisionEqual: true,
    diagnosisEqual: true,
    decisionDiagnosticSectionsEqual: true,
  });
  assert.deepEqual([...result.allowedDecisionResults], ["Keep", "partial redesign", "full replan"]);
  assert.equal(result.runs[0].stdoutSha256, result.runs[1].stdoutSha256);
  assert.deepEqual(result.runs[0].decisionResult, result.runs[1].decisionResult);
  assert.equal((result.runs[0].decisionResult.selectedDecision as any).outcome, "partial_redesign");
  assert.equal((result.runs[0].decisionResult.diagnosis as any).decision, "partial_redesign");
});

test("decision determinism command emits parseable success output", async () => {
  const result = await executeCheckDecisionDeterminismCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.command, "ai-agent check-decision-determinism");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.scenario, "fixed_input_repository_state_repeated_runs");
  assert.equal(parsed.fixedInput.inputIdentifier, "request:f42143edc0867a0d");
  assert.deepEqual(parsed.determinism, {
    deterministic: true,
    stdoutEqual: true,
    selectedDecisionEqual: true,
    diagnosisEqual: true,
    decisionDiagnosticSectionsEqual: true,
  });
  assert.deepEqual(parsed.allowedDecisionResults, ["Keep", "partial redesign", "full replan"]);
  assert.deepEqual(parsed.runs[0].decisionResult, parsed.runs[1].decisionResult);
});
