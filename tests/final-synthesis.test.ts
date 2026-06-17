import test from "node:test";
import assert from "node:assert/strict";
import { executeCheckFinalSynthesisArtifactCommand } from "../scripts/check-final-synthesis-artifact.ts";
import { executeCheckFinalSynthesisStabilityCommand } from "../scripts/check-final-synthesis-stability.ts";
import { checkMeetingLoopRouting } from "../scripts/check-meeting-loop-routing.ts";
import {
  adaptMeetingLoopOutputForFinalSynthesis,
  buildFinalSynthesisArtifactFromMeetingLoopArtifact,
  generateFinalSynthesisFromMeetingLoopArtifact,
  produceConsolidatedFinalResponseFromNormalizedMeetingOutputs,
  validateFinalSynthesisMeetingLoopArtifact,
} from "../src/final-synthesis.ts";
import type { MinimumMeetingLoopArtifact } from "../src/final-synthesis.ts";

const directSuccessArtifact: MinimumMeetingLoopArtifact = {
  schemaVersion: "meeting-process-artifact.v1",
  meetingProcessId: "meeting-direct-success-1",
  taskId: "task-direct-success-1",
  threadId: "thread-direct-success-1",
  status: "finalized",
  meetingTurns: [
    {
      id: "direct-turn-001",
      order: 1,
      round: 1,
      role: "openclaw-owner",
      kind: "request_analysis",
      summary: "OpenClaw identified the user request and decomposed it into routed work.",
    },
    {
      id: "direct-turn-002",
      order: 2,
      round: 2,
      role: "openclaw-owner",
      kind: "owner_draft",
      summary: "OpenClaw produced the primary execution draft from compressed loop context.",
    },
    {
      id: "direct-turn-003",
      order: 3,
      round: 3,
      role: "openclaw-owner",
      kind: "review_request",
      summary: "OpenClaw requested Hermes review against convergence and escalation criteria.",
    },
    {
      id: "direct-turn-004",
      order: 4,
      round: 4,
      role: "hermes-reviewer",
      kind: "review",
      summary: "Hermes accepted the draft with concrete final synthesis requirements.",
    },
    {
      id: "direct-turn-005",
      order: 5,
      round: 5,
      role: "openclaw-finalizer",
      kind: "final_synthesis",
      summary: "Final synthesis merged OpenClaw execution and Hermes review into the deliverable.",
    },
  ],
  retentionEvidence: {
    rawContextStoredAfterCompletion: true,
    summaryArtifactOnly: true,
    rawSentinelHiddenFromArtifact: true,
    ownerDraftSummaryCompressed: true,
  },
  personaLoopIteration: {
    openclawRole: "openclaw-owner",
    hermesRole: "hermes-reviewer",
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree_with_changes",
    hermesReviewedOpenClawDraft: true,
  },
};

test("final synthesis public functions produce stable primary success-path outputs", () => {
  const contract = validateFinalSynthesisMeetingLoopArtifact(directSuccessArtifact);
  const synthesis = generateFinalSynthesisFromMeetingLoopArtifact(directSuccessArtifact);

  assert.deepEqual(contract, {
    taskId: "task-direct-success-1",
    threadId: "thread-direct-success-1",
    finalTurn: directSuccessArtifact.meetingTurns[4],
    acceptedTurnKinds: ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"],
  });
  assert.deepEqual(synthesis, {
    taskId: "task-direct-success-1",
    threadId: "thread-direct-success-1",
    acceptedTurnKinds: ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"],
    content: [
      "Final synthesis",
      "",
      "Task: task-direct-success-1",
      "Thread: thread-direct-success-1",
      "",
      "Accepted meeting loop:",
      ...directSuccessArtifact.meetingTurns.map((turn) => `- ${turn.order}. ${turn.role}:${turn.kind} - ${turn.summary}`),
      "",
      "Result:",
      directSuccessArtifact.meetingTurns[4].summary,
      "",
      "Context policy: raw full text remained in storage; only compressed summaries entered final synthesis.",
    ].join("\n"),
  });
});

test("final synthesis input adapter validates and normalizes meeting loop outputs", () => {
  const unnormalizedOutput = {
    ...directSuccessArtifact,
    meetingProcessId: "  meeting-direct-success-1  ",
    taskId: "  task-direct-success-1  ",
    threadId: "  thread-direct-success-1  ",
    meetingTurns: [
      { ...directSuccessArtifact.meetingTurns[4], id: "  direct-turn-005  ", summary: "  Final synthesis merged\n\nOpenClaw execution and Hermes review into the deliverable.  " },
      { ...directSuccessArtifact.meetingTurns[0], summary: "  OpenClaw identified the user request and decomposed it into routed work.  " },
      { ...directSuccessArtifact.meetingTurns[3], summary: "  Hermes accepted the draft with concrete final synthesis requirements.  " },
      { ...directSuccessArtifact.meetingTurns[1], summary: "  OpenClaw produced the primary execution draft from compressed loop context.  " },
      { ...directSuccessArtifact.meetingTurns[2], summary: "  OpenClaw requested Hermes review against convergence and escalation criteria.  " },
    ],
  };

  const readyInput = adaptMeetingLoopOutputForFinalSynthesis(unnormalizedOutput);

  assert.equal(readyInput.meetingProcessId, "meeting-direct-success-1");
  assert.equal(readyInput.taskId, "task-direct-success-1");
  assert.equal(readyInput.threadId, "thread-direct-success-1");
  assert.deepEqual(
    readyInput.meetingTurns.map((turn) => turn.kind),
    ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"],
  );
  assert.deepEqual(readyInput.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.equal(readyInput.finalTurn.id, "direct-turn-005");
  assert.equal(readyInput.finalTurn.summary, "Final synthesis merged OpenClaw execution and Hermes review into the deliverable.");
  assert.equal(readyInput.meetingTurns.every((turn) => !("content" in turn) && !("fullContent" in turn)), true);
});

test("final synthesis module produces a consolidated final response from normalized meeting outputs", () => {
  const normalizedMeetingOutputs = adaptMeetingLoopOutputForFinalSynthesis({
    ...directSuccessArtifact,
    meetingProcessId: "  meeting-normalized-success-1  ",
    meetingTurns: [
      { ...directSuccessArtifact.meetingTurns[3], summary: "  Hermes accepted the draft with concrete final synthesis requirements.  " },
      { ...directSuccessArtifact.meetingTurns[1], summary: "  OpenClaw produced the primary execution draft from compressed loop context.  " },
      { ...directSuccessArtifact.meetingTurns[4], summary: "  Final synthesis merged OpenClaw execution and Hermes review into the deliverable.  " },
      { ...directSuccessArtifact.meetingTurns[0], summary: "  OpenClaw identified the user request and decomposed it into routed work.  " },
      { ...directSuccessArtifact.meetingTurns[2], summary: "  OpenClaw requested Hermes review against convergence and escalation criteria.  " },
    ],
  });

  const response = produceConsolidatedFinalResponseFromNormalizedMeetingOutputs(normalizedMeetingOutputs);

  assert.deepEqual(response.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.equal(response.sourceMeetingProcessId, "meeting-normalized-success-1");
  assert.deepEqual(response.sections, {
    requestAnalysis: "OpenClaw identified the user request and decomposed it into routed work.",
    openclawDraft: "OpenClaw produced the primary execution draft from compressed loop context.",
    hermesReview: "Hermes accepted the draft with concrete final synthesis requirements.",
    finalResponse: "Final synthesis merged OpenClaw execution and Hermes review into the deliverable.",
    escalation: "none",
  });
  assert.equal(
    response.content,
    [
      "Consolidated final response",
      "",
      "Task: task-direct-success-1",
      "Thread: thread-direct-success-1",
      "",
      "Request analysis:",
      response.sections.requestAnalysis,
      "",
      "OpenClaw execution:",
      response.sections.openclawDraft,
      "",
      "Hermes review:",
      response.sections.hermesReview,
      "",
      "Final response:",
      response.sections.finalResponse,
      "",
      "Escalation: none",
      "Context policy: raw full text remained in storage; only compressed summaries entered final response synthesis.",
    ].join("\n"),
  );
  assert.equal(response.content.includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
});

test("final synthesis input adapter rejects malformed or raw meeting loop outputs", () => {
  const rawTextOutput = structuredClone(directSuccessArtifact) as unknown as {
    meetingTurns: Array<MinimumMeetingLoopArtifact["meetingTurns"][number] & { fullContent?: string }>;
  };
  rawTextOutput.meetingTurns[1].fullContent = "raw owner draft must not enter synthesis-ready loop input";

  assert.throws(() => adaptMeetingLoopOutputForFinalSynthesis(rawTextOutput), /must expose summaries only/);
  assert.throws(
    () => adaptMeetingLoopOutputForFinalSynthesis({ ...directSuccessArtifact, retentionEvidence: undefined }),
    /requires retentionEvidence object/,
  );
  assert.throws(
    () => adaptMeetingLoopOutputForFinalSynthesis({ ...directSuccessArtifact, meetingTurns: directSuccessArtifact.meetingTurns.slice(1) }),
    /requires request analysis, owner draft, Hermes review, and final synthesis turns/,
  );
});

test("final synthesis module accepts and validates the Minimum scenario meeting loop state as input", () => {
  const minimumScenarioMeetingLoopState: MinimumMeetingLoopArtifact = {
    schemaVersion: "meeting-process-artifact.v1",
    meetingProcessId: "meeting-minimum-scenario-1",
    taskId: "task-minimum-scenario-1",
    threadId: "thread-minimum-scenario-1",
    status: "finalized",
    meetingTurns: [
      {
        id: "minimum-turn-001",
        order: 1,
        round: 0,
        role: "openclaw-owner",
        kind: "request_analysis",
        summary: "OpenClaw analyzed the user request and produced the minimum work breakdown.",
      },
      {
        id: "minimum-turn-002",
        order: 2,
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        summary: "OpenClaw drafted the routed execution answer from compressed meeting context.",
      },
      {
        id: "minimum-turn-003",
        order: 3,
        round: 1,
        role: "openclaw-owner",
        kind: "review_request",
        summary: "OpenClaw requested Hermes review for convergence before final synthesis.",
      },
      {
        id: "minimum-turn-004",
        order: 4,
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        summary: "Hermes reviewed the OpenClaw draft and agreed it was ready to synthesize.",
      },
      {
        id: "minimum-turn-005",
        order: 5,
        round: 2,
        role: "openclaw-finalizer",
        kind: "final_synthesis",
        summary: "Final synthesis merged the OpenClaw draft and Hermes review into one answer.",
      },
    ],
    retentionEvidence: {
      rawContextStoredAfterCompletion: true,
      summaryArtifactOnly: true,
      rawSentinelHiddenFromArtifact: true,
      ownerDraftSummaryCompressed: true,
    },
    personaLoopIteration: {
      openclawRole: "openclaw-owner",
      hermesRole: "hermes-reviewer",
      openclawCompletedDraft: true,
      hermesCompletedReview: true,
      hermesVerdict: "agree",
      hermesReviewedOpenClawDraft: true,
    },
  };

  const contract = validateFinalSynthesisMeetingLoopArtifact(minimumScenarioMeetingLoopState);

  assert.equal(contract.taskId, "task-minimum-scenario-1");
  assert.equal(contract.threadId, "thread-minimum-scenario-1");
  assert.equal(contract.finalTurn, minimumScenarioMeetingLoopState.meetingTurns[4]);
  assert.deepEqual(contract.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.equal(minimumScenarioMeetingLoopState.meetingTurns.every((turn) => !("content" in turn) && !("fullContent" in turn)), true);
  assert.deepEqual(minimumScenarioMeetingLoopState.retentionEvidence, {
    rawContextStoredAfterCompletion: true,
    summaryArtifactOnly: true,
    rawSentinelHiddenFromArtifact: true,
    ownerDraftSummaryCompressed: true,
  });
  assert.deepEqual(minimumScenarioMeetingLoopState.personaLoopIteration, {
    openclawRole: "openclaw-owner",
    hermesRole: "hermes-reviewer",
    openclawCompletedDraft: true,
    hermesCompletedReview: true,
    hermesVerdict: "agree",
    hermesReviewedOpenClawDraft: true,
  });
});

test("final synthesis module accepts the minimum meeting-loop artifact contract", async () => {
  const result = await checkMeetingLoopRouting();

  const contract = validateFinalSynthesisMeetingLoopArtifact(result.artifact as MinimumMeetingLoopArtifact);

  assert.deepEqual(contract, {
    taskId: "task-meeting-loop-routing-1",
    threadId: "thread-routing-1",
    finalTurn: {
      id: "turn-005:final_synthesis",
      order: 5,
      round: 5,
      role: "openclaw-finalizer",
      kind: "final_synthesis",
      summary: result.artifact.meetingTurns[4].summary,
    },
    acceptedTurnKinds: ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"],
  });
  assert.match(contract.finalTurn.summary, /Final synthesis accepted from routed meeting loop/);
});

test("final synthesis module generates expected content from minimum meeting-loop artifacts", async () => {
  const result = await checkMeetingLoopRouting();
  const artifact = result.artifact as MinimumMeetingLoopArtifact;

  const synthesis = generateFinalSynthesisFromMeetingLoopArtifact(artifact);

  assert.deepEqual(synthesis.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.equal(synthesis.taskId, "task-meeting-loop-routing-1");
  assert.equal(synthesis.threadId, "thread-routing-1");
  assert.equal(
    synthesis.content,
    [
      "Final synthesis",
      "",
      "Task: task-meeting-loop-routing-1",
      "Thread: thread-routing-1",
      "",
      "Accepted meeting loop:",
      ...artifact.meetingTurns.map((turn) => `- ${turn.order}. ${turn.role}:${turn.kind} - ${turn.summary}`),
      "",
      "Result:",
      artifact.meetingTurns[4].summary,
      "",
      "Context policy: raw full text remained in storage; only compressed summaries entered final synthesis.",
    ].join("\n"),
  );
  assert.match(synthesis.content, /OpenClaw draft/);
  assert.match(synthesis.content, /Hermes review/);
  assert.match(synthesis.content, /Final synthesis accepted from routed meeting loop/);
});

test("final synthesis module returns the expected Minimum scenario artifact structure", async () => {
  const result = await checkMeetingLoopRouting();
  const artifact = buildFinalSynthesisArtifactFromMeetingLoopArtifact(result.artifact as MinimumMeetingLoopArtifact);

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
  assert.match(artifact.finalSynthesis.content, /Accepted meeting loop/);
  assert.match(artifact.finalSynthesis.content, /only compressed summaries entered final synthesis/);
  assert.deepEqual(artifact.structure, {
    hasFinalSynthesisContent: true,
    includesAcceptedMeetingLoop: true,
    includesContextPolicy: true,
    summaryOnlyMeetingTurns: true,
  });
  assert.equal(JSON.stringify(artifact).includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
});

test("final synthesis artifact command returns the expected Minimum scenario artifact structure", async () => {
  const result = await executeCheckFinalSynthesisArtifactCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.command, "ai-agent check-final-synthesis-artifact");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.scenario, "minimum");
  assert.equal(parsed.sourceCommand, "npm run check:meeting-loop-routing");
  assert.equal(parsed.artifact.schemaVersion, "final-synthesis-artifact.v1");
  assert.deepEqual(parsed.artifact.sourceArtifact, {
    schemaVersion: "meeting-process-artifact.v1",
    meetingProcessId: "meeting-process:task-meeting-loop-routing-1",
    taskId: "task-meeting-loop-routing-1",
    threadId: "thread-routing-1",
    status: "finalized",
  });
  assert.deepEqual(parsed.artifact.acceptedTurnKinds, [
    "request_analysis",
    "owner_draft",
    "review_request",
    "review",
    "final_synthesis",
  ]);
  assert.deepEqual(parsed.artifact.structure, {
    hasFinalSynthesisContent: true,
    includesAcceptedMeetingLoop: true,
    includesContextPolicy: true,
    summaryOnlyMeetingTurns: true,
  });
  assert.match(parsed.artifact.finalSynthesis.content, /Final synthesis accepted from routed meeting loop/);
  assert.equal(result.stdout.includes("RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT"), false);
});

test("final synthesis module emits deterministic content for the same Minimum scenario meeting loop state", async () => {
  const result = await checkMeetingLoopRouting();
  const artifact = result.artifact as MinimumMeetingLoopArtifact;

  const first = generateFinalSynthesisFromMeetingLoopArtifact(artifact);
  const second = generateFinalSynthesisFromMeetingLoopArtifact(artifact);

  assert.equal(first.content, second.content);
  assert.deepEqual(first, second);
  assert.deepEqual(first.acceptedTurnKinds, ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis"]);
  assert.equal(first.taskId, "task-meeting-loop-routing-1");
  assert.equal(first.threadId, "thread-routing-1");
  assert.match(first.content, /Final synthesis accepted from routed meeting loop/);
  assert.match(first.content, /only compressed summaries entered final synthesis/);
});

test("final synthesis command-level check proves repeated minimum-scenario runs are stable", async () => {
  const result = await executeCheckFinalSynthesisStabilityCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.command, "ai-agent check-final-synthesis-stability");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.scenario, "minimum_final_synthesis_repeated_runs");
  assert.deepEqual(parsed.stability, {
    deterministic: true,
    stdoutEqual: true,
    acceptedTurnKindsEqual: true,
    finalSynthesisEqual: true,
  });
  assert.deepEqual(parsed.runs[0], parsed.runs[1] && { ...parsed.runs[1], run: 1 });
  assert.deepEqual(parsed.runs[0].synthesis.acceptedTurnKinds, [
    "request_analysis",
    "owner_draft",
    "review_request",
    "review",
    "final_synthesis",
  ]);
  assert.match(parsed.runs[0].synthesis.content, /Final synthesis accepted from routed meeting loop/);
  assert.match(parsed.runs[0].synthesis.content, /only compressed summaries entered final synthesis/);
});

test("final synthesis module rejects meeting-loop artifacts that expose raw turn text", async () => {
  const result = await checkMeetingLoopRouting();
  const artifact = structuredClone(result.artifact) as MinimumMeetingLoopArtifact & {
    meetingTurns: Array<MinimumMeetingLoopArtifact["meetingTurns"][number] & { content?: string }>;
  };
  artifact.meetingTurns[1].content = "raw owner draft must stay out of final synthesis loop input";

  assert.throws(
    () => validateFinalSynthesisMeetingLoopArtifact(artifact as MinimumMeetingLoopArtifact),
    /must expose summaries only/,
  );
});
