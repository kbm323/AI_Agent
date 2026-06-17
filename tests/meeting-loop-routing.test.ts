import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { executeCheckMeetingLoopArtifactsCommand } from "../scripts/check-meeting-loop-artifacts.ts";
import {
  checkMeetingLoopRouting,
  executeCheckMeetingLoopRoutingCommand,
  validateRoutedOpenClawHermesTurnSequence,
} from "../scripts/check-meeting-loop-routing.ts";
import { checkOpenClawHermesLoop, executeCheckOpenClawHermesLoopCommand } from "../scripts/check-openclaw-hermes-loop.ts";

test("OpenClaw/Hermes loop check proves one execution step followed by one review step", async () => {
  const result = await checkOpenClawHermesLoop();

  assert.equal(result.command, "ai-agent check-openclaw-hermes-loop");
  assert.equal(result.status, "passed");
  assert.equal(result.scenario, "single_openclaw_step_followed_by_single_hermes_review");
  assert.deepEqual(result.proof, {
    taskId: "task-openclaw-hermes-loop-1",
    threadId: "thread-openclaw-hermes-loop-1",
    executionStep: {
      order: 2,
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      calledOnce: true,
    },
    reviewStep: {
      order: 4,
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      calledOnce: true,
    },
    hermesReviewedOpenClawDraft: true,
    adjacentExecutionThenReview: true,
  });
});

test("OpenClaw/Hermes loop check command returns stable JSON output", async () => {
  const first = await executeCheckOpenClawHermesLoopCommand();
  const second = await executeCheckOpenClawHermesLoopCommand();

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(first.stdout, second.stdout);

  const payload = JSON.parse(first.stdout);
  assert.equal(payload.command, "ai-agent check-openclaw-hermes-loop");
  assert.equal(payload.status, "passed");
  assert.deepEqual(payload.proof.executionStep, {
    order: 2,
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    calledOnce: true,
  });
  assert.deepEqual(payload.proof.reviewStep, {
    order: 4,
    round: 1,
    role: "hermes-reviewer",
    kind: "review",
    calledOnce: true,
  });
  assert.equal(payload.proof.hermesReviewedOpenClawDraft, true);
  assert.equal(payload.proof.adjacentExecutionThenReview, true);
});

test("routed task metadata and role assignments are accepted by the meeting loop", async () => {
  const result = await checkMeetingLoopRouting();

  assert.equal(result.status, "passed");
  assert.deepEqual(
    result.artifact.taskMetadata.map((task) => `${task.taskId}:${task.assignedRole}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.deepEqual(result.artifact.routingMetadata.workflowOrder, [
    "task-001:openclaw-owner",
    "task-002:openclaw-owner",
    "task-003:hermes-reviewer",
    "task-004:openclaw-finalizer",
  ]);
  assert.equal(result.artifact.schemaVersion, "meeting-process-artifact.v1");
  assert.equal(result.artifact.meetingProcessId, "meeting-process:task-meeting-loop-routing-1");
  assert.equal(result.artifact.taskId, "task-meeting-loop-routing-1");
  assert.equal(result.artifact.threadId, "thread-routing-1");
  assert.deepEqual(result.artifact.meetingTurns, [
    {
      id: "turn-001:request_analysis",
      order: 1,
      round: 0,
      role: "openclaw-owner",
      kind: "request_analysis",
      summary: result.artifact.meetingTurns[0].summary,
    },
    {
      id: "turn-002:owner_draft",
      order: 2,
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      summary: result.artifact.meetingTurns[1].summary,
    },
    {
      id: "turn-003:review_request",
      order: 3,
      round: 1,
      role: "openclaw-owner",
      kind: "review_request",
      summary: result.artifact.meetingTurns[2].summary,
    },
    {
      id: "turn-004:review",
      order: 4,
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      summary: result.artifact.meetingTurns[3].summary,
    },
    {
      id: "turn-005:final_synthesis",
      order: 5,
      round: 5,
      role: "openclaw-finalizer",
      kind: "final_synthesis",
      summary: result.artifact.meetingTurns[4].summary,
    },
  ]);
  assert.equal(result.artifact.meetingTurns.every((turn) => turn.summary.length > 0), true);
  assert.deepEqual(result.artifact.retentionEvidence, {
    rawContextStoredAfterCompletion: true,
    summaryArtifactOnly: true,
    rawSentinelHiddenFromArtifact: true,
    ownerDraftSummaryCompressed: true,
  });
  assert.deepEqual(result.artifact.personaLoopIteration, {
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
  assert.deepEqual(result.artifact.discussionCycle, {
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
});

test("routed OpenClaw/Hermes sequence proof rejects skipped or duplicated participants", () => {
  const validTurns = [
    { order: 1, round: 0, role: "openclaw-owner", kind: "request_analysis" },
    { order: 2, round: 1, role: "openclaw-owner", kind: "owner_draft" },
    { order: 3, round: 1, role: "openclaw-owner", kind: "review_request" },
    { order: 4, round: 1, role: "hermes-reviewer", kind: "review" },
    { order: 5, round: 5, role: "openclaw-finalizer", kind: "final_synthesis" },
  ];

  assert.deepEqual(validateRoutedOpenClawHermesTurnSequence(validTurns, 1), {
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
  });

  const skippedHermes = validateRoutedOpenClawHermesTurnSequence(validTurns.slice(0, 3), 1);
  assert.equal(skippedHermes.valid, false);
  assert.equal(skippedHermes.noSkippedParticipants, false);
  assert.deepEqual(skippedHermes.missingParticipants, ["hermes-reviewer:review"]);

  const duplicatedOpenClawDraft = validateRoutedOpenClawHermesTurnSequence(
    [
      { order: 2, round: 1, role: "openclaw-owner", kind: "owner_draft" },
      { order: 3, round: 1, role: "openclaw-owner", kind: "owner_draft" },
      { order: 4, round: 1, role: "openclaw-owner", kind: "review_request" },
      { order: 5, round: 1, role: "hermes-reviewer", kind: "review" },
    ],
    1,
  );
  assert.equal(duplicatedOpenClawDraft.valid, false);
  assert.equal(duplicatedOpenClawDraft.noDuplicatedParticipants, false);
  assert.deepEqual(duplicatedOpenClawDraft.duplicatedParticipants, ["openclaw-owner:owner_draft"]);
});

test("meeting loop routing check command returns stable JSON output", async () => {
  const command = await executeCheckMeetingLoopRoutingCommand();
  const payload = JSON.parse(command.stdout);

  assert.equal(command.exitCode, 0);
  assert.equal(command.stderr, "");
  assert.equal(payload.command, "ai-agent check-meeting-loop-routing");
  assert.equal(payload.status, "passed");
  assert.equal(payload.scenario, "routed_tasks_enter_meeting_loop");
  assert.equal(payload.artifact.meetingProcessId, "meeting-process:task-meeting-loop-routing-1");
  assert.deepEqual(
    payload.artifact.meetingTurns.map(({ id, order, round, role, kind }: any) => ({ id, order, round, role, kind })),
    [
      { id: "turn-001:request_analysis", order: 1, round: 0, role: "openclaw-owner", kind: "request_analysis" },
      { id: "turn-002:owner_draft", order: 2, round: 1, role: "openclaw-owner", kind: "owner_draft" },
      { id: "turn-003:review_request", order: 3, round: 1, role: "openclaw-owner", kind: "review_request" },
      { id: "turn-004:review", order: 4, round: 1, role: "hermes-reviewer", kind: "review" },
      { id: "turn-005:final_synthesis", order: 5, round: 5, role: "openclaw-finalizer", kind: "final_synthesis" },
    ],
  );
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
});

test("meeting loop artifact contract command verifies deterministic identifiers and required fields", async () => {
  const first = await executeCheckMeetingLoopArtifactsCommand();
  const second = await executeCheckMeetingLoopArtifactsCommand();

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(first.stdout, second.stdout);

  const payload = JSON.parse(first.stdout);
  assert.deepEqual(payload, {
    command: "ai-agent check-meeting-loop-artifacts",
    status: "passed",
    contract: {
      schemaVersion: "meeting-loop-artifact-contract.v1",
      deterministic: true,
      sourceCommand: "npm run check:meeting-loop-routing",
      requiredFields: [
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
      ],
      expectedTurnOrder: [
        { id: "turn-001:request_analysis", order: 1, round: 0, role: "openclaw-owner", kind: "request_analysis" },
        { id: "turn-002:owner_draft", order: 2, round: 1, role: "openclaw-owner", kind: "owner_draft" },
        { id: "turn-003:review_request", order: 3, round: 1, role: "openclaw-owner", kind: "review_request" },
        { id: "turn-004:review", order: 4, round: 1, role: "hermes-reviewer", kind: "review" },
        { id: "turn-005:final_synthesis", order: 5, round: 5, role: "openclaw-finalizer", kind: "final_synthesis" },
      ],
      retentionChecks: {
        rawContextStoredAfterCompletion: true,
        summaryArtifactOnly: true,
        rawSentinelHiddenFromArtifact: true,
        ownerDraftSummaryCompressed: true,
      },
      transcriptArtifact: {
        schemaVersion: "preserved-meeting-transcript.v1",
        path: resolve("docs/generated/meeting-loop-transcript.json"),
        stableWrite: true,
        iterationStatus: {
          status: "finalized",
          round: 1,
          openclawCompletedDraft: true,
          hermesCompletedReview: true,
          hermesVerdict: "agree",
          converged: true,
        },
        participantOutputs: [
          {
            role: "openclaw-owner",
            kind: "owner_draft",
            turnId: "turn-002:owner_draft",
            summaryPresent: true,
          },
          {
            role: "hermes-reviewer",
            kind: "review",
            turnId: "turn-004:review",
            summaryPresent: true,
          },
        ],
        executionTurnId: "turn-002:owner_draft",
        reviewTurnId: "turn-004:review",
        hermesReviewedOpenClawDraft: true,
        transcriptSummaryOnly: true,
      },
    },
  });

  const transcript = JSON.parse(readFileSync(payload.contract.transcriptArtifact.path, "utf8"));
  assert.equal(transcript.schemaVersion, "preserved-meeting-transcript.v1");
  assert.deepEqual(transcript.preservedLoop, {
    iterationId: "loop-001",
    round: 1,
    openclawRole: "openclaw-owner",
    hermesRole: "hermes-reviewer",
    executionTurnId: "turn-002:owner_draft",
    reviewTurnId: "turn-004:review",
    hermesReviewedOpenClawDraft: true,
  });
  assert.deepEqual(transcript.iterationStatus, {
    status: "finalized",
    round: 1,
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree",
    converged: true,
  });
  assert.deepEqual(
    transcript.participantOutputs.map(({ role, kind, turnId, summary }: any) => ({ role, kind, turnId, summaryPresent: summary.length > 0 })),
    [
      { role: "openclaw-owner", kind: "owner_draft", turnId: "turn-002:owner_draft", summaryPresent: true },
      { role: "hermes-reviewer", kind: "review", turnId: "turn-004:review", summaryPresent: true },
    ],
  );
  assert.deepEqual(
    transcript.meetingTurns.map(({ id, order, round, role, kind }: any) => ({ id, order, round, role, kind })),
    payload.contract.expectedTurnOrder,
  );
  assert.equal(transcript.meetingTurns.every((turn: any) => turn.content === undefined && turn.fullContent === undefined), true);
  assert.equal(JSON.stringify(transcript).includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
});
