import assert from "node:assert/strict";
import { assessTaskDecompositionOverlap, decomposeUserRequest } from "../src/index.ts";

interface TaskOverlapCheckResult {
  command: "ai-agent check-task-overlap";
  status: "passed";
  scenario: "minimum";
  deterministic: boolean;
  artifact: ReturnType<typeof assessTaskDecompositionOverlap>;
}

const minimumScenarioRequest = "브랜드 영상 제작 회의를 진행하고 최종 산출물을 합성해줘.";

export function checkTaskOverlap(): TaskOverlapCheckResult {
  const first = assessTaskDecompositionOverlap(decomposeUserRequest(minimumScenarioRequest));
  const second = assessTaskDecompositionOverlap(decomposeUserRequest(minimumScenarioRequest));

  assert.deepEqual(first, second, "task overlap report must be deterministic");
  assert.equal(first.nonOverlapping, true, "emitted task decomposition units must not overlap");
  assert.deepEqual(first.overlaps, []);
  assert.deepEqual(first.checkedRules, [
    "duplicate_task_id",
    "duplicate_title_fingerprint",
    "duplicate_workflow_scope",
    "unknown_workflow_scope",
  ]);

  return {
    command: "ai-agent check-task-overlap",
    status: "passed",
    scenario: "minimum",
    deterministic: true,
    artifact: first,
  };
}

export function executeCheckTaskOverlapCommand(): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkTaskOverlap();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown task overlap check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "task_overlap_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-task-overlap.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckTaskOverlapCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
