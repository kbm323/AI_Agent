import assert from "node:assert/strict";
import { AiAgentDatabase, buildRoleRoutingMetadata, CompanyOrchestrator } from "../src/index.ts";
import type { AgentRole, DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor, TurnKind } from "../src/index.ts";

type MeetingLoopTurnSummary = { order: number; round: number; role: string; kind: string };

export interface RoutedTurnSequenceProof {
  expectedSequence: Array<{ role: AgentRole; kind: TurnKind }>;
  observedSequence: Array<{ order: number; role: string; kind: string }>;
  missingParticipants: string[];
  duplicatedParticipants: string[];
  orderAdvanced: boolean;
  noSkippedParticipants: boolean;
  noDuplicatedParticipants: boolean;
  valid: boolean;
}

interface MeetingLoopRoutingCheckResult {
  command: "ai-agent check-meeting-loop-routing";
  status: "passed";
  scenario: "routed_tasks_enter_meeting_loop";
  artifact: {
    schemaVersion: "meeting-process-artifact.v1";
    meetingProcessId: string;
    taskId: string;
    threadId: string;
    status: string;
    taskMetadata: Array<{ taskId: string; title: string; assignedRole: string; responsibility: string }>;
    routingMetadata: ReturnType<typeof buildRoleRoutingMetadata>;
    meetingTurns: Array<{ id: string; order: number; round: number; role: string; kind: string; summary: string }>;
    retentionEvidence: {
      rawContextStoredAfterCompletion: boolean;
      summaryArtifactOnly: boolean;
      rawSentinelHiddenFromArtifact: boolean;
      ownerDraftSummaryCompressed: boolean;
    };
    personaLoopIteration: {
      id: "loop-001";
      round: number;
      routedTaskId: "task-002";
      openclawRole: "openclaw-owner";
      hermesRole: "hermes-reviewer";
      openclawCompletedDraft: boolean;
      hermesCompletedReview: boolean;
      hermesVerdict: "agree";
      hermesReviewedOpenClawDraft: boolean;
    };
    discussionCycle: {
      initializedFromTaskInput: true;
      inputTask: {
        taskId: "task-002";
        title: string;
        assignedRole: "openclaw-owner";
        responsibility: string;
      };
      selectedFirstAgent: "openclaw-owner";
      expectedFirstAgent: "openclaw-owner";
      firstAgentMatchedRoute: boolean;
      firstTurn: { order: number; round: number; role: string; kind: string };
      nextTurn: { order: number; round: number; role: string; kind: string };
      sequenceProof: RoutedTurnSequenceProof;
    };
  };
}

export function validateRoutedOpenClawHermesTurnSequence(turns: MeetingLoopTurnSummary[], round: number): RoutedTurnSequenceProof {
  const expectedSequence: RoutedTurnSequenceProof["expectedSequence"] = [
    { role: "openclaw-owner", kind: "owner_draft" },
    { role: "openclaw-owner", kind: "review_request" },
    { role: "hermes-reviewer", kind: "review" },
  ];
  const loopTurns = turns
    .filter((turn) => turn.round === round && expectedSequence.some((expected) => expected.role === turn.role && expected.kind === turn.kind))
    .map((turn) => ({ order: turn.order, role: turn.role, kind: turn.kind }));
  const observedKeys = loopTurns.map((turn) => `${turn.role}:${turn.kind}`);
  const expectedKeys = expectedSequence.map((turn) => `${turn.role}:${turn.kind}`);
  const missingParticipants = expectedKeys.filter((expected) => !observedKeys.includes(expected));
  const duplicatedParticipants = Array.from(new Set(observedKeys.filter((key, index) => observedKeys.indexOf(key) !== index)));
  const orderAdvanced =
    loopTurns.length === expectedSequence.length &&
    loopTurns.every((turn, index) => turn.role === expectedSequence[index]?.role && turn.kind === expectedSequence[index]?.kind) &&
    loopTurns.every((turn, index) => index === 0 || turn.order > loopTurns[index - 1].order);
  const noSkippedParticipants = missingParticipants.length === 0;
  const noDuplicatedParticipants = duplicatedParticipants.length === 0 && loopTurns.length === expectedSequence.length;

  return {
    expectedSequence,
    observedSequence: loopTurns,
    missingParticipants,
    duplicatedParticipants,
    orderAdvanced,
    noSkippedParticipants,
    noDuplicatedParticipants,
    valid: orderAdvanced && noSkippedParticipants && noDuplicatedParticipants,
  };
}

export async function checkMeetingLoopRouting(): Promise<MeetingLoopRoutingCheckResult> {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const rawSentinel = "RAW_CONTEXT_SENTINEL_SHOULD_NOT_APPEAR_IN_SUMMARY_ARTIFACT";
  const ownerDraft = [
    "Owner draft: define the internal plan, review checkpoints, and final handoff.",
    ...Array.from({ length: 34 }, (_, index) => `Detailed raw context note ${String(index + 1).padStart(2, "0")}: preserve this for audit, not loop exposure.`),
    rawSentinel,
  ].join("\n");
  const ownerCalls: Array<{ taskId: string; userRequest: string; round: number }> = [];
  const reviewerCalls: Array<{ taskId: string; userRequest: string; draft: string; round: number }> = [];
  const owner: OwnerExecutor = {
    async createDraft({ task, userRequest, round }) {
      ownerCalls.push({ taskId: task.id, userRequest, round });
      return ownerDraft;
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ task, userRequest, draft, round }) {
      reviewerCalls.push({ taskId: task.id, userRequest, draft, round });
      return {
        verdict: "agree",
        content: "Hermes review: agree. Routed internal tasks have a clear review checkpoint.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize({ draft, review }) {
      return `Final synthesis accepted from routed meeting loop.\n\n${draft}\n\n${review}`;
    },
  };

  try {
    const orchestrator = new CompanyOrchestrator({
      db,
      discord,
      owner,
      reviewer,
      finalizer,
      idFactory: () => "task-meeting-loop-routing-1",
    });

    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-routing-1", name: "routing-check" },
      userRequest: "내부 영상 제작 회의를 진행하고 최종 실행안을 합성해줘.",
    });

    const routingMetadata = buildRoleRoutingMetadata(result.requestAnalysis.roleRoutes);
    const taskMetadata = result.requestAnalysis.taskBreakdown.map((task, index) => {
      const route = result.requestAnalysis.roleRoutes[index];
      return {
        taskId: task.id,
        title: task.title,
        assignedRole: route.role,
        responsibility: route.responsibility,
      };
    });
    const turns = db.getTurns(result.task.id);
    const ownerDraftTurn = turns.find((turn) => turn.round === 1 && turn.role === "openclaw-owner" && turn.kind === "owner_draft");
    const hermesReviewTurn = turns.find((turn) => turn.round === 1 && turn.role === "hermes-reviewer" && turn.kind === "review");
    const firstOwnerCall = ownerCalls[0];
    const firstReviewerCall = reviewerCalls[0];

    assert.equal(result.status, "finalized");
    assert.deepEqual(
      taskMetadata.map((task) => `${task.taskId}:${task.assignedRole}`),
      [
        "task-001:openclaw-owner",
        "task-002:openclaw-owner",
        "task-003:hermes-reviewer",
        "task-004:openclaw-finalizer",
      ],
    );
    assert.deepEqual(routingMetadata, {
      routeCount: 4,
      roles: ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"],
      workflowOrder: [
        "task-001:openclaw-owner",
        "task-002:openclaw-owner",
        "task-003:hermes-reviewer",
        "task-004:openclaw-finalizer",
      ],
      hasHermesReview: true,
      hasFinalizer: true,
    });
    const storedRequestAnalysis = JSON.parse(turns[0]?.content ?? "{}") as {
      taskBreakdown?: Array<{ id: string }>;
      roleRoutes?: Array<{ taskId: string; role: string }>;
    };
    assert.deepEqual(storedRequestAnalysis.taskBreakdown?.map((task) => task.id), ["task-001", "task-002", "task-003", "task-004"]);
    assert.deepEqual(storedRequestAnalysis.roleRoutes?.map((route) => `${route.taskId}:${route.role}`), [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ]);
    assert.deepEqual(
      result.meetingHistory.map((turn) => ({ round: turn.round, role: turn.role, kind: turn.kind })),
      [
        { round: 0, role: "openclaw-owner", kind: "request_analysis" },
        { round: 1, role: "openclaw-owner", kind: "owner_draft" },
        { round: 1, role: "openclaw-owner", kind: "review_request" },
        { round: 1, role: "hermes-reviewer", kind: "review" },
        { round: 5, role: "openclaw-finalizer", kind: "final_synthesis" },
      ],
    );
    assert.deepEqual(ownerCalls, [{ taskId: result.task.id, userRequest: "내부 영상 제작 회의를 진행하고 최종 실행안을 합성해줘.", round: 1 }]);
    assert.deepEqual(reviewerCalls, [
      { taskId: result.task.id, userRequest: "내부 영상 제작 회의를 진행하고 최종 실행안을 합성해줘.", draft: ownerDraft, round: 1 },
    ]);
    assert.equal(ownerDraftTurn?.content, ownerDraft);
    assert.equal(ownerDraftTurn?.content.includes(rawSentinel), true);
    assert.match(hermesReviewTurn?.content ?? "", /Hermes review: agree/);

    const meetingTurns = result.meetingHistory.map((turn, index) => ({
      id: `turn-${String(index + 1).padStart(3, "0")}:${turn.kind}`,
      order: index + 1,
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      summary: turn.summary,
    }));
    const ownerDraftSummary = meetingTurns.find((turn) => turn.kind === "owner_draft")?.summary ?? "";
    const routedOpenClawTask = taskMetadata.find((task) => task.taskId === "task-002");
    const firstCycleTurn = meetingTurns.find((turn) => turn.round === 1 && turn.kind === "owner_draft");
    const nextCycleTurn = meetingTurns.find((turn) => turn.round === 1 && turn.kind === "review");
    const sequenceProof = validateRoutedOpenClawHermesTurnSequence(meetingTurns, 1);
    const artifactPreview = JSON.stringify({ meetingTurns });
    const summaryArtifactOnly = meetingTurns.every((turn) => !("content" in turn) && !("fullContent" in turn));
    const rawSentinelHiddenFromArtifact = !artifactPreview.includes(rawSentinel);

    assert.equal(result.status, "finalized");
    assert.equal(routedOpenClawTask?.assignedRole, "openclaw-owner");
    assert.equal(firstCycleTurn?.role, routedOpenClawTask?.assignedRole);
    assert.equal(firstCycleTurn?.kind, "owner_draft");
    assert.equal(nextCycleTurn?.role, "hermes-reviewer");
    assert.equal(nextCycleTurn?.kind, "review");
    assert.equal(sequenceProof.valid, true);
    assert.equal(ownerDraftSummary.includes(rawSentinel), false);
    assert.equal(ownerDraftSummary.length < ownerDraft.length, true);
    assert.equal(summaryArtifactOnly, true);
    assert.equal(rawSentinelHiddenFromArtifact, true);

    return {
      command: "ai-agent check-meeting-loop-routing",
      status: "passed",
      scenario: "routed_tasks_enter_meeting_loop",
      artifact: {
        schemaVersion: "meeting-process-artifact.v1",
        meetingProcessId: `meeting-process:${result.task.id}`,
        taskId: result.task.id,
        threadId: result.threadId,
        status: result.status,
        taskMetadata,
        routingMetadata,
        meetingTurns,
        retentionEvidence: {
          rawContextStoredAfterCompletion: ownerDraftTurn?.content === ownerDraft,
          summaryArtifactOnly,
          rawSentinelHiddenFromArtifact,
          ownerDraftSummaryCompressed: ownerDraftSummary.length < ownerDraft.length,
        },
        personaLoopIteration: {
          id: "loop-001",
          round: firstOwnerCall?.round ?? 0,
          routedTaskId: "task-002",
          openclawRole: "openclaw-owner",
          hermesRole: "hermes-reviewer",
          openclawCompletedDraft: ownerDraftTurn?.content === ownerDraft,
          hermesCompletedReview: hermesReviewTurn?.content.includes("Hermes review: agree") ?? false,
          hermesVerdict: "agree",
          hermesReviewedOpenClawDraft: firstReviewerCall?.draft === ownerDraft && firstReviewerCall.round === firstOwnerCall?.round,
        },
        discussionCycle: {
          initializedFromTaskInput: true,
          inputTask: {
            taskId: "task-002",
            title: routedOpenClawTask?.title ?? "",
            assignedRole: "openclaw-owner",
            responsibility: routedOpenClawTask?.responsibility ?? "",
          },
          selectedFirstAgent: "openclaw-owner",
          expectedFirstAgent: "openclaw-owner",
          firstAgentMatchedRoute: firstCycleTurn?.role === routedOpenClawTask?.assignedRole,
          firstTurn: {
            order: firstCycleTurn?.order ?? 0,
            round: firstCycleTurn?.round ?? 0,
            role: firstCycleTurn?.role ?? "",
            kind: firstCycleTurn?.kind ?? "",
          },
          nextTurn: {
            order: nextCycleTurn?.order ?? 0,
            round: nextCycleTurn?.round ?? 0,
            role: nextCycleTurn?.role ?? "",
            kind: nextCycleTurn?.kind ?? "",
          },
          sequenceProof,
        },
      },
    };
  } finally {
    db.close();
  }
}

export async function executeCheckMeetingLoopRoutingCommand(): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  try {
    const result = await checkMeetingLoopRouting();
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown meeting loop routing check failure";
    return {
      exitCode: 1,
      stdout: "",
      stderr: `${JSON.stringify({ error: "meeting_loop_routing_check_failed", message }, null, 2)}\n`,
    };
  }
}

function createFakeDiscord(): DiscordDelivery {
  return {
    async createThread() {
      return { threadId: "thread-routing-1", url: "https://discord.test/thread-routing-1" };
    },
    async postParent() {},
    async postThread() {},
  };
}

const invokedAsScript = process.argv[1]?.endsWith("check-meeting-loop-routing.ts") ?? false;
if (invokedAsScript) {
  const result = await executeCheckMeetingLoopRoutingCommand();
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
