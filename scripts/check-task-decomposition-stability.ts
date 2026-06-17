import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { parseTaskBreakdownFromAnalysisArtifact, type StructuredAnalysisArtifact, type TaskBreakdownItem } from "../src/index.ts";

interface TaskDecompositionStabilityRun {
  run: number;
  taskCount: number;
  outputSha256: string;
  taskBreakdown: TaskBreakdownItem[];
}

interface TaskDecompositionStabilityCheckResult {
  command: "ai-agent check-task-decomposition-stability";
  status: "passed";
  scenario: "fixed_request_analysis_artifact_repeated_runs";
  deterministic: true;
  sourceInput: {
    artifactPath: string;
    schemaVersion: "request-analysis.v1";
    userRequestSummary: string;
  };
  runs: TaskDecompositionStabilityRun[];
  artifact: {
    stableOutputSha256: string;
    taskBreakdown: TaskBreakdownItem[];
  };
}

const fixturePath = "tests/fixtures/request-analysis-output.json";
const repeatCount = 5;

export function checkTaskDecompositionStability(): TaskDecompositionStabilityCheckResult {
  const artifact = loadRequestAnalysisFixture();
  const runs = Array.from({ length: repeatCount }, (_, index) => {
    const taskBreakdown = parseTaskBreakdownFromAnalysisArtifact(artifact);
    const serialized = JSON.stringify(taskBreakdown);

    return {
      run: index + 1,
      taskCount: taskBreakdown.length,
      outputSha256: sha256(serialized),
      taskBreakdown,
    };
  });

  const [firstRun, ...remainingRuns] = runs;
  assert.ok(firstRun, "at least one decomposition stability run must be present");
  for (const run of remainingRuns) {
    assert.deepEqual(run.taskBreakdown, firstRun.taskBreakdown, "task decomposition output must be stable across repeated runs");
    assert.equal(run.outputSha256, firstRun.outputSha256, "task decomposition artifact hash must be stable across repeated runs");
  }
  assert.deepEqual(
    firstRun.taskBreakdown.map((task) => `${task.id}:${task.title}`),
    [
      "task-001:요청 의도와 성공 기준 정리",
      "task-002:OpenClaw 실행 초안 작성",
      "task-003:Hermes 리뷰와 수렴 판단",
      "task-004:최종 합성 또는 escalation",
    ],
  );

  return {
    command: "ai-agent check-task-decomposition-stability",
    status: "passed",
    scenario: "fixed_request_analysis_artifact_repeated_runs",
    deterministic: true,
    sourceInput: {
      artifactPath: fixturePath,
      schemaVersion: artifact.schemaVersion,
      userRequestSummary: artifact.userRequestSummary,
    },
    runs,
    artifact: {
      stableOutputSha256: firstRun.outputSha256,
      taskBreakdown: firstRun.taskBreakdown,
    },
  };
}

export function executeCheckTaskDecompositionStabilityCommand(): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkTaskDecompositionStability();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown task decomposition stability check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "task_decomposition_stability_check_failed", message }, null, 2)}\n`,
    };
  }
}

function loadRequestAnalysisFixture(): StructuredAnalysisArtifact {
  const fixtureUrl = new URL("../tests/fixtures/request-analysis-output.json", import.meta.url);
  return JSON.parse(readFileSync(fixtureUrl, "utf8")) as StructuredAnalysisArtifact;
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

const invokedAsScript = process.argv[1]?.endsWith("check-task-decomposition-stability.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckTaskDecompositionStabilityCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
