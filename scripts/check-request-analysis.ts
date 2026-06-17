import assert from "node:assert/strict";
import { analyzeUserRequest } from "../src/index.ts";

interface RequestAnalysisCheckResult {
  command: "ai-agent check-request-analysis";
  status: "passed";
  scenario: "minimum";
  deterministic: boolean;
  artifact: {
    schemaVersion: "request-analysis.v1";
    intent: {
      summary: string;
      primaryGoal: string;
      meetingSystem: string;
      workflow: string;
    };
    constraints: Array<{ id: string; source: string; description: string }>;
    requiredOutputs: Array<{ id: string; producedBy: string; description: string }>;
    userRequestSummary: string;
    taskBreakdown: Array<{ id: string; title: string }>;
    roleRoutes: Array<{ taskId: string; role: string }>;
    loopContextSummary: string;
    tokenStrategy: {
      rawStorage: string;
      exposedLoopContext: string;
      compressionPolicy: string;
      targetReduction: string;
    };
    ambiguitySignals: string[];
  };
}

const minimumScenarioRequest = "브랜드 영상 제작 회의를 진행하고 최종 산출물을 합성해줘.";

export function checkRequestAnalysis(): RequestAnalysisCheckResult {
  const first = analyzeUserRequest(minimumScenarioRequest);
  const second = analyzeUserRequest(minimumScenarioRequest);
  assert.deepEqual(first, second, "minimum request analysis artifact must be deterministic");

  const artifact = {
    schemaVersion: "request-analysis.v1" as const,
    intent: first.intent,
    constraints: first.constraints,
    requiredOutputs: first.requiredOutputs,
    userRequestSummary: first.userRequestSummary,
    taskBreakdown: first.taskBreakdown.map((task) => ({ id: task.id, title: task.title })),
    roleRoutes: first.roleRoutes.map((route) => ({ taskId: route.taskId, role: route.role })),
    loopContextSummary: first.loopContextSummary,
    tokenStrategy: first.tokenStrategy,
    ambiguitySignals: first.ambiguitySignals,
  };

  assert.deepEqual(artifact.taskBreakdown, [
    { id: "task-001", title: "요청 의도와 성공 기준 정리" },
    { id: "task-002", title: "OpenClaw 실행 초안 작성" },
    { id: "task-003", title: "Hermes 리뷰와 수렴 판단" },
    { id: "task-004", title: "최종 합성 또는 escalation" },
  ]);
  assert.deepEqual(artifact.roleRoutes, [
    { taskId: "task-001", role: "openclaw-owner" },
    { taskId: "task-002", role: "openclaw-owner" },
    { taskId: "task-003", role: "hermes-reviewer" },
    { taskId: "task-004", role: "openclaw-finalizer" },
  ]);
  assert.equal(artifact.userRequestSummary, minimumScenarioRequest);
  assert.deepEqual(artifact.intent, {
    summary: minimumScenarioRequest,
    primaryGoal: "Run the requested work through the MVP virtual-company multi-agent meeting flow.",
    meetingSystem: "virtual-company-multi-agent-meeting",
    workflow: "analysis-routing-openclaw-hermes-synthesis",
  });
  assert.deepEqual(
    artifact.constraints.map((constraint) => `${constraint.source}:${constraint.id}`),
    ["mvp-default:mvp-flow-required", "mvp-default:compressed-loop-context"],
  );
  assert.deepEqual(
    artifact.requiredOutputs.map((output) => output.id),
    ["task_breakdown", "role_routes", "meeting_loop_result", "final_synthesis", "escalation"],
  );
  assert.equal(artifact.ambiguitySignals.length, 0);
  assert.match(artifact.loopContextSummary, /analysis -> routing -> OpenClaw draft -> Hermes review -> final synthesis\/escalation/);
  assert.match(artifact.tokenStrategy.targetReduction, /40-50%/);

  return {
    command: "ai-agent check-request-analysis",
    status: "passed",
    scenario: "minimum",
    deterministic: true,
    artifact,
  };
}

export function executeCheckRequestAnalysisCommand(): { exitCode: number; stdout: string; stderr: string } {
  try {
    const result = checkRequestAnalysis();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown request analysis check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "request_analysis_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-request-analysis.ts") ?? false;
if (invokedAsScript) {
  const result = executeCheckRequestAnalysisCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
