import assert from "node:assert/strict";
import { buildFinalSynthesisArtifactFromMeetingLoopArtifact } from "../src/final-synthesis.ts";
import type { FinalSynthesisArtifact, MinimumMeetingLoopArtifact } from "../src/final-synthesis.ts";
import { checkMeetingLoopRouting } from "./check-meeting-loop-routing.ts";

interface FinalSynthesisArtifactCheckResult {
  command: "ai-agent check-final-synthesis-artifact";
  status: "passed";
  scenario: "minimum";
  sourceCommand: "npm run check:meeting-loop-routing";
  artifact: FinalSynthesisArtifact;
}

export async function checkFinalSynthesisArtifact(): Promise<FinalSynthesisArtifactCheckResult> {
  const source = await checkMeetingLoopRouting();
  const artifact = buildFinalSynthesisArtifactFromMeetingLoopArtifact(source.artifact as MinimumMeetingLoopArtifact);

  assert.equal(artifact.schemaVersion, "final-synthesis-artifact.v1");
  assert.equal(artifact.scenario, "minimum");
  assert.deepEqual(artifact.sourceArtifact, {
    schemaVersion: "meeting-process-artifact.v1",
    meetingProcessId: "meeting-process:task-meeting-loop-routing-1",
    taskId: "task-meeting-loop-routing-1",
    threadId: "thread-routing-1",
    status: "finalized",
  });
  assert.deepEqual(artifact.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.deepEqual(artifact.finalSynthesis.acceptedTurnKinds, artifact.acceptedTurnKinds);
  assert.equal(artifact.finalSynthesis.taskId, "task-meeting-loop-routing-1");
  assert.equal(artifact.finalSynthesis.threadId, "thread-routing-1");
  assert.match(artifact.finalSynthesis.content, /Final synthesis accepted from routed meeting loop/);
  assert.deepEqual(artifact.structure, {
    hasFinalSynthesisContent: true,
    includesAcceptedMeetingLoop: true,
    includesContextPolicy: true,
    summaryOnlyMeetingTurns: true,
  });
  assert.deepEqual(artifact.retentionEvidence, {
    rawContextStoredAfterCompletion: true,
    summaryArtifactOnly: true,
    rawSentinelHiddenFromArtifact: true,
    ownerDraftSummaryCompressed: true,
  });
  assert.deepEqual(artifact.personaLoopIteration, {
    openclawRole: "openclaw-owner",
    hermesRole: "hermes-reviewer",
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree",
    hermesReviewedOpenClawDraft: true,
  });

  return {
    command: "ai-agent check-final-synthesis-artifact",
    status: "passed",
    scenario: "minimum",
    sourceCommand: "npm run check:meeting-loop-routing",
    artifact,
  };
}

export async function executeCheckFinalSynthesisArtifactCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkFinalSynthesisArtifact();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown final synthesis artifact check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "final_synthesis_artifact_check_failed", message }, null, 2)}\n`,
    };
  }
}

const invokedAsScript = process.argv[1]?.endsWith("check-final-synthesis-artifact.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckFinalSynthesisArtifactCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
