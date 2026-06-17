import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  executeVerificationWorkflowRunnerApi,
  executeVerificationWorkflowRunnerCommand,
} from "../scripts/run-verification-workflow.ts";
import {
  defaultVerificationWorkflowResultPath,
  runVerificationWorkflow,
  type VerificationWorkflowRunnerResult,
  writeVerificationWorkflowResult,
} from "../src/verification-workflow-runner.ts";
import {
  defaultVerificationWorkflowResultPath as publicDefaultVerificationWorkflowResultPath,
  runVerificationWorkflow as publicRunVerificationWorkflow,
  type VerificationWorkflowCaseResult as PublicVerificationWorkflowCaseResult,
  type VerificationWorkflowRunnerResult as PublicVerificationWorkflowRunnerResult,
  type WrittenVerificationWorkflowResult as PublicWrittenVerificationWorkflowResult,
  writeVerificationWorkflowResult as publicWriteVerificationWorkflowResult,
} from "../src/index.ts";

test("verification workflow runner executes deterministic MVP success and escalation cases", async () => {
  const result = await runVerificationWorkflow();

  assert.equal(result.schemaVersion, "verification-workflow-runner.v1");
  assert.equal(result.command, "ai-agent run-verification-workflow");
  assert.equal(result.status, "passed");
  assert.deepEqual(result.summary, {
    caseCount: 2,
    passedCaseCount: 2,
    failedCaseCount: 0,
    mvpWorkflowExecuted: true,
    escalationWorkflowExecuted: true,
    rawStorageSeparatedFromLoopContext: true,
  });
  assert.deepEqual(
    result.cases.map((entry) => [entry.name, entry.status]),
    [
      ["finalized_meeting_loop", "passed"],
      ["ambiguous_request_escalation", "passed"],
    ],
  );

  const finalized = result.cases.find((entry) => entry.name === "finalized_meeting_loop");
  assert.equal(finalized?.observed.status, "finalized");
  assert.equal(finalized?.observed.finalSynthesisCreated, true);
  assert.equal(finalized?.observed.rawSentinelStored, true);
  assert.equal(finalized?.observed.rawSentinelHiddenFromSummaries, true);
  assert.deepEqual(finalized?.observed.ownerCallRounds, ["1", "2"]);
  assert.deepEqual(finalized?.observed.reviewerCallRounds, ["1", "2"]);

  const escalation = result.cases.find((entry) => entry.name === "ambiguous_request_escalation");
  assert.equal(escalation?.observed.status, "waiting_for_user");
  assert.equal(escalation?.observed.ownerCalled, false);
  assert.equal(escalation?.observed.reviewerCalled, false);
  assert.equal(escalation?.observed.finalizerCalled, false);
});

test("verification workflow runner generates identical output for identical deterministic inputs", async () => {
  const first = await runVerificationWorkflow();
  const second = await runVerificationWorkflow();

  assert.deepEqual(second, first);
  assert.equal(JSON.stringify(second, null, 2), JSON.stringify(first, null, 2));
});

test("verification workflow runner writes a stable concrete artifact", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-workflow-"));
  try {
    const first = await writeVerificationWorkflowResult({ projectRoot: root });
    const firstArtifact = readFileSync(join(root, defaultVerificationWorkflowResultPath), "utf8");
    const second = await writeVerificationWorkflowResult({ projectRoot: root });
    const secondArtifact = readFileSync(join(root, defaultVerificationWorkflowResultPath), "utf8");

    assert.equal(first.path, join(root, defaultVerificationWorkflowResultPath));
    assert.equal(existsSync(first.path), true);
    assert.deepEqual(second.result, first.result);
    assert.equal(secondArtifact, firstArtifact);
    assert.equal(JSON.parse(firstArtifact).status, "passed");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification workflow public exports cover primary success paths", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-workflow-public-"));
  try {
    assert.equal(publicDefaultVerificationWorkflowResultPath, defaultVerificationWorkflowResultPath);
    assert.equal(publicRunVerificationWorkflow, runVerificationWorkflow);
    assert.equal(publicWriteVerificationWorkflowResult, writeVerificationWorkflowResult);

    const result: PublicVerificationWorkflowRunnerResult = await publicRunVerificationWorkflow();
    const finalizedCase = result.cases.find((entry) => entry.name === "finalized_meeting_loop");
    assert.ok(finalizedCase);
    const typedCase: PublicVerificationWorkflowCaseResult = finalizedCase;
    assert.equal(result.status, "passed");
    assert.equal(typedCase.status, "passed");
    assert.equal(typedCase.observed.finalSynthesisCreated, true);

    const written: PublicWrittenVerificationWorkflowResult = await publicWriteVerificationWorkflowResult({
      projectRoot: root,
      runner: async () => result,
    });
    assert.equal(written.path, join(root, publicDefaultVerificationWorkflowResultPath));
    assert.deepEqual(written.result, result);
    assert.deepEqual(JSON.parse(readFileSync(written.path, "utf8")), result);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification workflow runner command returns observable artifact metadata", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-workflow-command-"));
  try {
    const first = await executeVerificationWorkflowRunnerCommand(root);
    const second = await executeVerificationWorkflowRunnerCommand(root);

    assert.equal(first.exitCode, 0);
    assert.equal(first.stderr, "");
    assert.equal(second.exitCode, 0);
    assert.equal(second.stderr, "");
    assert.equal(first.stdout, second.stdout);
    assert.deepEqual(JSON.parse(first.stdout), {
      command: "ai-agent run-verification-workflow",
      status: "passed",
      artifact: {
        path: join(root, defaultVerificationWorkflowResultPath),
        schemaVersion: "verification-workflow-runner.v1",
        caseCount: 2,
        passedCaseCount: 2,
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification workflow API endpoint returns the concrete runner result", async () => {
  const result = await executeVerificationWorkflowRunnerApi();

  assert.equal(result.schemaVersion, "verification-workflow-runner.v1");
  assert.equal(result.command, "ai-agent run-verification-workflow");
  assert.equal(result.status, "passed");
  assert.equal(result.summary.caseCount, 2);
});

test("verification workflow runner command maps failed verification to exit code 1", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-workflow-failed-command-"));
  try {
    const failedResult = buildFailedVerificationWorkflowResult();
    const result = await executeVerificationWorkflowRunnerCommand({
      projectRoot: root,
      async runner() {
        return failedResult;
      },
    });

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.deepEqual(JSON.parse(result.stdout), {
      command: "ai-agent run-verification-workflow",
      status: "failed",
      artifact: {
        path: join(root, defaultVerificationWorkflowResultPath),
        schemaVersion: "verification-workflow-runner.v1",
        caseCount: 1,
        passedCaseCount: 0,
      },
    });
    assert.deepEqual(JSON.parse(readFileSync(join(root, defaultVerificationWorkflowResultPath), "utf8")), failedResult);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("verification workflow runner command maps thrown failures to stable stderr", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-verification-workflow-thrown-command-"));
  try {
    const result = await executeVerificationWorkflowRunnerCommand({
      projectRoot: root,
      async runner() {
        throw new Error("synthetic runner failure");
      },
    });

    assert.equal(result.exitCode, 1);
    assert.equal(result.stdout, "");
    assert.deepEqual(JSON.parse(result.stderr), {
      error: "verification_workflow_failed",
      message: "synthetic runner failure",
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function buildFailedVerificationWorkflowResult(): VerificationWorkflowRunnerResult {
  return {
    schemaVersion: "verification-workflow-runner.v1",
    command: "ai-agent run-verification-workflow",
    status: "failed",
    deterministicInputs: {
      clearRequest: "clear",
      ambiguousRequest: "ambiguous",
      projectChannelId: "channel",
    },
    cases: [
      {
        name: "finalized_meeting_loop",
        status: "failed",
        observed: {
          finalSynthesisCreated: false,
        },
        failures: ["final synthesis created"],
      },
    ],
    summary: {
      caseCount: 1,
      passedCaseCount: 0,
      failedCaseCount: 1,
      mvpWorkflowExecuted: false,
      escalationWorkflowExecuted: false,
      rawStorageSeparatedFromLoopContext: false,
    },
    errors: ["final synthesis created"],
  };
}
