import test from "node:test";
import assert from "node:assert/strict";
import { executeMvpCompletionCheckCommand } from "../scripts/check-mvp-completion.ts";

test("MVP completion command returns deterministic success status for the composed gate", async () => {
  const first = await executeMvpCompletionCheckCommand();
  const second = await executeMvpCompletionCheckCommand();

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(first.stdout, second.stdout);

  const output = JSON.parse(first.stdout);
  assert.deepEqual(
    {
      command: output.command,
      status: output.status,
      deterministic: output.deterministic,
      schemaVersion: output.schemaVersion,
      criteria: output.steps.map((step: any) => `${step.criterion}:${step.status}`),
    },
    {
      command: "ai-agent check:mvp-completion",
      status: "passed",
      deterministic: true,
      schemaVersion: "mvp-completion-check.v1",
      criteria: [
        "request_analysis_and_work_breakdown:passed",
        "role_based_routing:passed",
        "openclaw_hermes_preserved_loop:passed",
        "final_synthesis_and_escalation:passed",
        "diagnosis_and_requirement_gap:passed",
        "token_strategy_and_compression:passed",
        "invalid_input_contract:passed",
      ],
    },
  );
  const tokenStep = output.steps.find((step: any) => step.criterion === "token_strategy_and_compression");
  assert.deepEqual(tokenStep.evidence, {
    tokenCostPass: true,
    compressionStatus: "passed",
    rawFullTextHiddenFromCompressedContext: true,
    rawFullTextRetainedOutsideLoopContext: true,
    percentSavings: 74.3,
    minimumTargetSavingsPercent: 40,
  });
});

test("MVP completion command returns stable non-zero failure status when a composed check fails", async () => {
  const result = await executeMvpCompletionCheckCommand({
    checkRequestAnalysis() {
      throw new Error("simulated request analysis failure");
    },
  });

  assert.equal(result.exitCode, 1);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "mvp_completion_check_failed",
    message: "simulated request analysis failure",
  });
});

test("MVP completion command returns deterministic failure when completion evidence is missing", async () => {
  const deps = {
    checkRequirementGapMapping() {
      return {
        command: "ai-agent check:requirement-gap",
        artifact: {
          present: true,
          priorityOrderVerified: true,
          implementedCount: 6,
          missingCount: 0,
          capabilityIds: [],
        },
      } as any;
    },
  };

  const first = await executeMvpCompletionCheckCommand(deps);
  const second = await executeMvpCompletionCheckCommand(deps);

  assert.equal(first.exitCode, 1);
  assert.equal(first.stderr, "");
  assert.equal(first.stdout, second.stdout);

  const output = JSON.parse(first.stdout);
  const requirementGapStep = output.steps.find((step: any) => step.criterion === "diagnosis_and_requirement_gap");

  assert.equal(output.status, "failed");
  assert.deepEqual(requirementGapStep, {
    criterion: "diagnosis_and_requirement_gap",
    command: "ai-agent check:requirement-gap",
    status: "failed",
    evidence: {
      priorityOrderVerified: true,
      implementedCount: 6,
      missingCount: 0,
      capabilityIds: [],
    },
  });
});
