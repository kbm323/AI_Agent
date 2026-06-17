import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  buildPreservedMeetingTranscriptArtifact,
  defaultPreservedMeetingTranscriptPath,
  writePreservedMeetingTranscriptArtifact,
} from "../src/meeting-transcript.ts";
import { executeCheckMeetingLoopRoutingCommand } from "./check-meeting-loop-routing.ts";

interface MeetingLoopArtifactContractResult {
  command: "ai-agent check-meeting-loop-artifacts";
  status: "passed";
  contract: {
    schemaVersion: "meeting-loop-artifact-contract.v1";
    deterministic: true;
    sourceCommand: "npm run check:meeting-loop-routing";
    requiredFields: string[];
    expectedTurnOrder: Array<{ id: string; order: number; round: number; role: string; kind: string }>;
    retentionChecks: {
      rawContextStoredAfterCompletion: true;
      summaryArtifactOnly: true;
      rawSentinelHiddenFromArtifact: true;
      ownerDraftSummaryCompressed: true;
    };
    transcriptArtifact: {
      schemaVersion: "preserved-meeting-transcript.v1";
      path: string;
      stableWrite: true;
      iterationStatus: {
        status: "finalized";
        round: 1;
        openclawCompletedDraft: true;
        hermesCompletedReview: true;
        hermesVerdict: "agree";
        converged: true;
      };
      participantOutputs: Array<{
        role: "openclaw-owner" | "hermes-reviewer";
        kind: "owner_draft" | "review";
        turnId: "turn-002:owner_draft" | "turn-004:review";
        summaryPresent: true;
      }>;
      executionTurnId: "turn-002:owner_draft";
      reviewTurnId: "turn-004:review";
      hermesReviewedOpenClawDraft: true;
      transcriptSummaryOnly: true;
    };
  };
}

const requiredFields = [
  "artifact.schemaVersion",
  "artifact.meetingProcessId",
  "artifact.taskId",
  "artifact.threadId",
  "artifact.status",
  "artifact.taskMetadata",
  "artifact.routingMetadata",
  "artifact.meetingTurns",
  "artifact.meetingTurns[].id",
  "artifact.meetingTurns[].order",
  "artifact.meetingTurns[].round",
  "artifact.meetingTurns[].role",
  "artifact.meetingTurns[].kind",
  "artifact.meetingTurns[].summary",
  "artifact.retentionEvidence.rawContextStoredAfterCompletion",
  "artifact.retentionEvidence.summaryArtifactOnly",
  "artifact.retentionEvidence.rawSentinelHiddenFromArtifact",
  "artifact.retentionEvidence.ownerDraftSummaryCompressed",
  "artifact.personaLoopIteration.id",
  "artifact.personaLoopIteration.openclawRole",
  "artifact.personaLoopIteration.hermesRole",
  "artifact.personaLoopIteration.hermesReviewedOpenClawDraft",
  "artifact.discussionCycle.initializedFromTaskInput",
  "artifact.discussionCycle.inputTask.taskId",
  "artifact.discussionCycle.inputTask.assignedRole",
  "artifact.discussionCycle.selectedFirstAgent",
  "artifact.discussionCycle.expectedFirstAgent",
  "artifact.discussionCycle.firstAgentMatchedRoute",
  "artifact.discussionCycle.firstTurn",
  "artifact.discussionCycle.nextTurn",
  "artifact.discussionCycle.sequenceProof.valid",
] as const;

const expectedTurnOrder = [
  { id: "turn-001:request_analysis", order: 1, round: 0, role: "openclaw-owner", kind: "request_analysis" },
  { id: "turn-002:owner_draft", order: 2, round: 1, role: "openclaw-owner", kind: "owner_draft" },
  { id: "turn-003:review_request", order: 3, round: 1, role: "openclaw-owner", kind: "review_request" },
  { id: "turn-004:review", order: 4, round: 1, role: "hermes-reviewer", kind: "review" },
  { id: "turn-005:final_synthesis", order: 5, round: 5, role: "openclaw-finalizer", kind: "final_synthesis" },
] as const;

export async function checkMeetingLoopArtifacts(): Promise<MeetingLoopArtifactContractResult> {
  const first = await executeCheckMeetingLoopRoutingCommand();
  const second = await executeCheckMeetingLoopRoutingCommand();

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(first.stdout, second.stdout, "meeting loop artifact stdout must be deterministic");
  assert.equal(first.stderr, second.stderr, "meeting loop artifact stderr must be deterministic");

  const payload = parseJson(first.stdout);
  assert.equal(payload.command, "ai-agent check-meeting-loop-routing");
  assert.equal(payload.status, "passed");
  assert.equal(payload.artifact.schemaVersion, "meeting-process-artifact.v1");
  assert.equal(payload.artifact.meetingProcessId, "meeting-process:task-meeting-loop-routing-1");
  assert.equal(payload.artifact.taskId, "task-meeting-loop-routing-1");
  assert.equal(payload.artifact.threadId, "thread-routing-1");
  assert.equal(payload.artifact.status, "finalized");

  for (const field of requiredFields) {
    assertHasRequiredField(payload, field);
  }
  assert.deepEqual(
    payload.artifact.meetingTurns.map(({ id, order, round, role, kind }: any) => ({ id, order, round, role, kind })),
    expectedTurnOrder,
  );
  assert.equal(payload.artifact.meetingTurns.every((turn: any) => typeof turn.summary === "string" && turn.summary.length > 0), true);
  assert.equal(payload.artifact.meetingTurns.every((turn: any) => turn.content === undefined && turn.fullContent === undefined), true);
  assert.equal(JSON.stringify(payload.artifact.meetingTurns).includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
  assert.deepEqual(payload.artifact.retentionEvidence, {
    rawContextStoredAfterCompletion: true,
    summaryArtifactOnly: true,
    rawSentinelHiddenFromArtifact: true,
    ownerDraftSummaryCompressed: true,
  });
  assert.deepEqual(payload.artifact.personaLoopIteration, {
    id: "loop-001",
    round: 1,
    routedTaskId: "task-002",
    openclawRole: "openclaw-owner",
    hermesRole: "hermes-reviewer",
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree",
    hermesReviewedOpenClawDraft: true,
  });
  assert.deepEqual(payload.artifact.discussionCycle, {
    initializedFromTaskInput: true,
    inputTask: {
      taskId: "task-002",
      title: "OpenClaw 실행 초안 작성",
      assignedRole: "openclaw-owner",
      responsibility: "실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
    },
    selectedFirstAgent: "openclaw-owner",
    expectedFirstAgent: "openclaw-owner",
    firstAgentMatchedRoute: true,
    firstTurn: {
      order: 2,
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
    },
    nextTurn: {
      order: 4,
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
    },
    sequenceProof: {
      expectedSequence: [
        { role: "openclaw-owner", kind: "owner_draft" },
        { role: "openclaw-owner", kind: "review_request" },
        { role: "hermes-reviewer", kind: "review" },
      ],
      observedSequence: [
        { order: 2, role: "openclaw-owner", kind: "owner_draft" },
        { order: 3, role: "openclaw-owner", kind: "review_request" },
        { order: 4, role: "hermes-reviewer", kind: "review" },
      ],
      missingParticipants: [],
      duplicatedParticipants: [],
      orderAdvanced: true,
      noSkippedParticipants: true,
      noDuplicatedParticipants: true,
      valid: true,
    },
  });

  const transcript = buildPreservedMeetingTranscriptArtifact(payload.artifact);
  const written = writePreservedMeetingTranscriptArtifact({ artifact: transcript });
  const firstFile = readFileSync(written.path, "utf8");
  const secondWritten = writePreservedMeetingTranscriptArtifact({ artifact: transcript });
  const secondFile = readFileSync(secondWritten.path, "utf8");
  assert.equal(firstFile, secondFile, "preserved meeting transcript artifact writes must be deterministic");
  assert.deepEqual(JSON.parse(firstFile), transcript);
  assert.equal(transcript.preservedLoop.executionTurnId, "turn-002:owner_draft");
  assert.equal(transcript.preservedLoop.reviewTurnId, "turn-004:review");
  assert.equal(transcript.preservedLoop.hermesReviewedOpenClawDraft, true);
  assert.deepEqual(transcript.iterationStatus, {
    status: "finalized",
    round: 1,
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree",
    converged: true,
  });
  assert.deepEqual(
    transcript.participantOutputs.map((output) => ({
      role: output.role,
      kind: output.kind,
      turnId: output.turnId,
      summaryPresent: output.summary.length > 0,
    })),
    [
      { role: "openclaw-owner", kind: "owner_draft", turnId: "turn-002:owner_draft", summaryPresent: true },
      { role: "hermes-reviewer", kind: "review", turnId: "turn-004:review", summaryPresent: true },
    ],
  );
  assert.equal(transcript.retentionEvidence.transcriptSummaryOnly, true);
  assert.equal(JSON.stringify(transcript).includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
  assert.equal(transcript.meetingTurns.every((turn: any) => turn.content === undefined && turn.fullContent === undefined), true);

  return {
    command: "ai-agent check-meeting-loop-artifacts",
    status: "passed",
    contract: {
      schemaVersion: "meeting-loop-artifact-contract.v1",
      deterministic: true,
      sourceCommand: "npm run check:meeting-loop-routing",
      requiredFields: [...requiredFields],
      expectedTurnOrder: expectedTurnOrder.map((turn) => ({ ...turn })),
      retentionChecks: {
        rawContextStoredAfterCompletion: true,
        summaryArtifactOnly: true,
        rawSentinelHiddenFromArtifact: true,
        ownerDraftSummaryCompressed: true,
      },
      transcriptArtifact: {
        schemaVersion: transcript.schemaVersion,
        path: resolve(defaultPreservedMeetingTranscriptPath),
        stableWrite: firstFile === secondFile,
        iterationStatus: {
          status: transcript.iterationStatus.status as "finalized",
          round: transcript.iterationStatus.round as 1,
          openclawCompletedDraft: transcript.iterationStatus.openclawCompletedDraft,
          hermesCompletedReview: transcript.iterationStatus.hermesCompletedReview,
          hermesVerdict: transcript.iterationStatus.hermesVerdict as "agree",
          converged: transcript.iterationStatus.converged,
        },
        participantOutputs: transcript.participantOutputs.map((output) => ({
          role: output.role,
          kind: output.kind,
          turnId: output.turnId as "turn-002:owner_draft" | "turn-004:review",
          summaryPresent: output.summary.length > 0,
        })),
        executionTurnId: transcript.preservedLoop.executionTurnId as "turn-002:owner_draft",
        reviewTurnId: transcript.preservedLoop.reviewTurnId as "turn-004:review",
        hermesReviewedOpenClawDraft: transcript.preservedLoop.hermesReviewedOpenClawDraft,
        transcriptSummaryOnly: transcript.retentionEvidence.transcriptSummaryOnly,
      },
    },
  };
}

export async function executeCheckMeetingLoopArtifactsCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkMeetingLoopArtifacts();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown meeting loop artifact check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "meeting_loop_artifact_check_failed", message }, null, 2)}\n`,
    };
  }
}

function parseJson(value: string): Record<string, any> {
  const parsed = JSON.parse(value);
  assert.equal(typeof parsed, "object");
  assert.notEqual(parsed, null);
  return parsed;
}

function assertHasRequiredField(payload: Record<string, any>, field: string): void {
  if (field === "artifact.meetingTurns[]") {
    assert.equal(Array.isArray(payload.artifact.meetingTurns), true);
    return;
  }
  if (field.startsWith("artifact.meetingTurns[].")) {
    const property = field.replace("artifact.meetingTurns[].", "");
    assert.equal(
      payload.artifact.meetingTurns.every((turn: Record<string, unknown>) => turn[property] !== undefined),
      true,
      `${field} must be present on every meeting turn`,
    );
    return;
  }

  const value = field.split(".").reduce((current: any, key) => current?.[key], payload);
  assert.notEqual(value, undefined, `${field} must be present`);
}

const invokedAsScript = process.argv[1]?.endsWith("check-meeting-loop-artifacts.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckMeetingLoopArtifactsCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
