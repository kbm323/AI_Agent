import assert from "node:assert/strict";
import { generateFinalSynthesisFromMeetingLoopArtifact } from "../src/final-synthesis.ts";
import type { GeneratedFinalSynthesis, MinimumMeetingLoopArtifact } from "../src/final-synthesis.ts";
import { formatStableJsonForComparison } from "../src/output-normalization.ts";
import { checkMeetingLoopRouting } from "./check-meeting-loop-routing.ts";

interface FinalSynthesisStabilityCheckResult {
  command: "ai-agent check-final-synthesis-stability";
  status: "passed";
  scenario: "minimum_final_synthesis_repeated_runs";
  sourceCommand: "npm run check:meeting-loop-routing";
  runs: [
    {
      run: 1;
      exitCode: 0;
      synthesis: GeneratedFinalSynthesis;
    },
    {
      run: 2;
      exitCode: 0;
      synthesis: GeneratedFinalSynthesis;
    },
  ];
  stability: {
    deterministic: true;
    stdoutEqual: true;
    acceptedTurnKindsEqual: true;
    finalSynthesisEqual: true;
  };
}

export async function checkFinalSynthesisStability(): Promise<FinalSynthesisStabilityCheckResult> {
  const firstSource = await checkMeetingLoopRouting();
  const secondSource = await checkMeetingLoopRouting();
  const first = generateFinalSynthesisFromMeetingLoopArtifact(firstSource.artifact as MinimumMeetingLoopArtifact);
  const second = generateFinalSynthesisFromMeetingLoopArtifact(secondSource.artifact as MinimumMeetingLoopArtifact);
  const firstStdout = formatStableJsonForComparison(first);
  const secondStdout = formatStableJsonForComparison(second);

  assert.equal(firstStdout, secondStdout, "minimum final synthesis snapshot must be stable across repeated runs");
  assert.deepEqual(first.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.deepEqual(second.acceptedTurnKinds, first.acceptedTurnKinds);
  assert.equal(first.taskId, "task-meeting-loop-routing-1");
  assert.equal(first.threadId, "thread-routing-1");
  assert.match(first.content, /Final synthesis accepted from routed meeting loop/);
  assert.match(first.content, /Context policy: raw full text remained in storage; only compressed summaries entered final synthesis\./);
  assert.equal(first.content, second.content);

  return {
    command: "ai-agent check-final-synthesis-stability",
    status: "passed",
    scenario: "minimum_final_synthesis_repeated_runs",
    sourceCommand: "npm run check:meeting-loop-routing",
    runs: [
      { run: 1, exitCode: 0, synthesis: first },
      { run: 2, exitCode: 0, synthesis: second },
    ],
    stability: {
      deterministic: true,
      stdoutEqual: true,
      acceptedTurnKindsEqual: true,
      finalSynthesisEqual: true,
    },
  };
}

export async function executeCheckFinalSynthesisStabilityCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkFinalSynthesisStability();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown final synthesis stability check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "final_synthesis_stability_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-final-synthesis-stability.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckFinalSynthesisStabilityCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
