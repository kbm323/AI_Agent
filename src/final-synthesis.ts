import type { AgentRole, TurnKind } from "./types.ts";

export interface MinimumMeetingLoopTurn {
  id: string;
  order: number;
  round: number;
  role: AgentRole;
  kind: TurnKind;
  summary: string;
  content?: never;
  fullContent?: never;
}

export interface MinimumMeetingLoopArtifact {
  schemaVersion: "meeting-process-artifact.v1";
  meetingProcessId: string;
  taskId: string;
  threadId: string;
  status: "finalized";
  meetingTurns: MinimumMeetingLoopTurn[];
  retentionEvidence: {
    rawContextStoredAfterCompletion: boolean;
    summaryArtifactOnly: boolean;
    rawSentinelHiddenFromArtifact: boolean;
    ownerDraftSummaryCompressed: boolean;
  };
  personaLoopIteration: {
    openclawRole: "openclaw-owner";
    hermesRole: "hermes-reviewer";
    openclawCompletedDraft: boolean;
    hermesCompletedReview: boolean;
    hermesVerdict: "agree" | "agree_with_changes";
    hermesReviewedOpenClawDraft: boolean;
  };
}

export interface FinalSynthesisInputContract {
  taskId: string;
  threadId: string;
  finalTurn: MinimumMeetingLoopTurn;
  acceptedTurnKinds: TurnKind[];
}

export interface SynthesisReadyMeetingLoopInput extends FinalSynthesisInputContract {
  meetingProcessId: string;
  meetingTurns: MinimumMeetingLoopTurn[];
  retentionEvidence: MinimumMeetingLoopArtifact["retentionEvidence"];
  personaLoopIteration: MinimumMeetingLoopArtifact["personaLoopIteration"];
}

export interface GeneratedFinalSynthesis {
  taskId: string;
  threadId: string;
  content: string;
  acceptedTurnKinds: TurnKind[];
}

export interface ConsolidatedFinalResponse extends GeneratedFinalSynthesis {
  sourceMeetingProcessId: string;
  sections: {
    requestAnalysis: string;
    openclawDraft: string;
    hermesReview: string;
    finalResponse: string;
    escalation: "none";
  };
}

export interface FinalSynthesisArtifact {
  schemaVersion: "final-synthesis-artifact.v1";
  scenario: "minimum";
  sourceArtifact: {
    schemaVersion: MinimumMeetingLoopArtifact["schemaVersion"];
    meetingProcessId: string;
    taskId: string;
    threadId: string;
    status: MinimumMeetingLoopArtifact["status"];
  };
  acceptedTurnKinds: TurnKind[];
  finalSynthesis: GeneratedFinalSynthesis;
  structure: {
    hasFinalSynthesisContent: true;
    includesAcceptedMeetingLoop: true;
    includesContextPolicy: true;
    summaryOnlyMeetingTurns: true;
  };
  retentionEvidence: MinimumMeetingLoopArtifact["retentionEvidence"];
  personaLoopIteration: MinimumMeetingLoopArtifact["personaLoopIteration"];
}

const MINIMUM_TURN_SEQUENCE: Array<Pick<MinimumMeetingLoopTurn, "role" | "kind">> = [
  { role: "openclaw-owner", kind: "request_analysis" },
  { role: "openclaw-owner", kind: "owner_draft" },
  { role: "openclaw-owner", kind: "review_request" },
  { role: "hermes-reviewer", kind: "review" },
  { role: "openclaw-finalizer", kind: "final_synthesis" },
];

const AGENT_ROLES: AgentRole[] = ["openclaw-owner", "hermes-reviewer", "openclaw-finalizer"];
const TURN_KINDS: TurnKind[] = ["request_analysis", "owner_draft", "review_request", "review", "final_synthesis", "escalation"];

export function adaptMeetingLoopOutputForFinalSynthesis(input: unknown): SynthesisReadyMeetingLoopInput {
  const artifact = normalizeMeetingLoopOutput(input);
  const contract = validateFinalSynthesisMeetingLoopArtifact(artifact);

  return {
    meetingProcessId: artifact.meetingProcessId,
    taskId: contract.taskId,
    threadId: contract.threadId,
    finalTurn: contract.finalTurn,
    acceptedTurnKinds: contract.acceptedTurnKinds,
    meetingTurns: artifact.meetingTurns,
    retentionEvidence: artifact.retentionEvidence,
    personaLoopIteration: artifact.personaLoopIteration,
  };
}

export function validateFinalSynthesisMeetingLoopArtifact(artifact: MinimumMeetingLoopArtifact): FinalSynthesisInputContract {
  if (artifact.schemaVersion !== "meeting-process-artifact.v1") {
    throw new Error("final synthesis input requires meeting-process-artifact.v1");
  }
  if (artifact.status !== "finalized") {
    throw new Error("final synthesis input requires finalized meeting status");
  }
  if (!artifact.taskId.trim() || !artifact.threadId.trim()) {
    throw new Error("final synthesis input requires taskId and threadId");
  }
  if (artifact.meetingTurns.length < MINIMUM_TURN_SEQUENCE.length) {
    throw new Error("final synthesis input requires request analysis, owner draft, Hermes review, and final synthesis turns");
  }

  const orderedTurns = [...artifact.meetingTurns].sort((left, right) => left.order - right.order);
  MINIMUM_TURN_SEQUENCE.forEach((expected, index) => {
    const turn = orderedTurns[index];
    if (!turn || turn.role !== expected.role || turn.kind !== expected.kind) {
      throw new Error(`final synthesis input turn ${index + 1} must be ${expected.role}:${expected.kind}`);
    }
    if (!turn.id.trim() || !Number.isInteger(turn.order) || !Number.isInteger(turn.round) || !turn.summary.trim()) {
      throw new Error(`final synthesis input turn ${index + 1} is missing required identity or summary fields`);
    }
    if ("content" in turn || "fullContent" in turn) {
      throw new Error("final synthesis input must expose summaries only, not raw full text");
    }
  });

  if (
    !artifact.retentionEvidence.rawContextStoredAfterCompletion ||
    !artifact.retentionEvidence.summaryArtifactOnly ||
    !artifact.retentionEvidence.rawSentinelHiddenFromArtifact ||
    !artifact.retentionEvidence.ownerDraftSummaryCompressed
  ) {
    throw new Error("final synthesis input requires raw-storage and compressed-summary retention evidence");
  }
  if (
    artifact.personaLoopIteration.openclawRole !== "openclaw-owner" ||
    artifact.personaLoopIteration.hermesRole !== "hermes-reviewer" ||
    !artifact.personaLoopIteration.openclawCompletedDraft ||
    !artifact.personaLoopIteration.hermesCompletedReview ||
    !artifact.personaLoopIteration.hermesReviewedOpenClawDraft
  ) {
    throw new Error("final synthesis input requires a completed OpenClaw/Hermes review loop");
  }

  const finalTurn = orderedTurns[MINIMUM_TURN_SEQUENCE.length - 1];
  return {
    taskId: artifact.taskId,
    threadId: artifact.threadId,
    finalTurn,
    acceptedTurnKinds: orderedTurns.slice(0, MINIMUM_TURN_SEQUENCE.length).map((turn) => turn.kind),
  };
}

export function generateFinalSynthesisFromMeetingLoopArtifact(artifact: MinimumMeetingLoopArtifact): GeneratedFinalSynthesis {
  const contract = validateFinalSynthesisMeetingLoopArtifact(artifact);
  const orderedTurns = [...artifact.meetingTurns].sort((left, right) => left.order - right.order).slice(0, MINIMUM_TURN_SEQUENCE.length);
  const content = [
    "Final synthesis",
    "",
    `Task: ${contract.taskId}`,
    `Thread: ${contract.threadId}`,
    "",
    "Accepted meeting loop:",
    ...orderedTurns.map((turn) => `- ${turn.order}. ${turn.role}:${turn.kind} - ${turn.summary}`),
    "",
    "Result:",
    contract.finalTurn.summary,
    "",
    "Context policy: raw full text remained in storage; only compressed summaries entered final synthesis.",
  ].join("\n");

  return {
    taskId: contract.taskId,
    threadId: contract.threadId,
    content,
    acceptedTurnKinds: contract.acceptedTurnKinds,
  };
}

export function produceConsolidatedFinalResponseFromNormalizedMeetingOutputs(
  input: SynthesisReadyMeetingLoopInput,
): ConsolidatedFinalResponse {
  const artifact = synthesisReadyInputToArtifact(input);
  const contract = validateFinalSynthesisMeetingLoopArtifact(artifact);
  const orderedTurns = artifact.meetingTurns.slice(0, MINIMUM_TURN_SEQUENCE.length);
  const requestAnalysis = orderedTurns[0].summary;
  const openclawDraft = orderedTurns[1].summary;
  const hermesReview = orderedTurns[3].summary;
  const finalResponse = contract.finalTurn.summary;
  const content = [
    "Consolidated final response",
    "",
    `Task: ${contract.taskId}`,
    `Thread: ${contract.threadId}`,
    "",
    "Request analysis:",
    requestAnalysis,
    "",
    "OpenClaw execution:",
    openclawDraft,
    "",
    "Hermes review:",
    hermesReview,
    "",
    "Final response:",
    finalResponse,
    "",
    "Escalation: none",
    "Context policy: raw full text remained in storage; only compressed summaries entered final response synthesis.",
  ].join("\n");

  return {
    taskId: contract.taskId,
    threadId: contract.threadId,
    sourceMeetingProcessId: input.meetingProcessId,
    content,
    acceptedTurnKinds: contract.acceptedTurnKinds,
    sections: {
      requestAnalysis,
      openclawDraft,
      hermesReview,
      finalResponse,
      escalation: "none",
    },
  };
}

export function buildFinalSynthesisArtifactFromMeetingLoopArtifact(artifact: MinimumMeetingLoopArtifact): FinalSynthesisArtifact {
  const finalSynthesis = generateFinalSynthesisFromMeetingLoopArtifact(artifact);
  const summaryOnlyMeetingTurns = artifact.meetingTurns.every((turn) => !("content" in turn) && !("fullContent" in turn));
  const hasFinalSynthesisContent = finalSynthesis.content.trim().length > 0;
  const includesAcceptedMeetingLoop = finalSynthesis.content.includes("Accepted meeting loop:");
  const includesContextPolicy = finalSynthesis.content.includes("only compressed summaries entered final synthesis");

  if (!hasFinalSynthesisContent || !includesAcceptedMeetingLoop || !includesContextPolicy || !summaryOnlyMeetingTurns) {
    throw new Error("final synthesis artifact structure proof failed");
  }

  return {
    schemaVersion: "final-synthesis-artifact.v1",
    scenario: "minimum",
    sourceArtifact: {
      schemaVersion: artifact.schemaVersion,
      meetingProcessId: artifact.meetingProcessId,
      taskId: artifact.taskId,
      threadId: artifact.threadId,
      status: artifact.status,
    },
    acceptedTurnKinds: finalSynthesis.acceptedTurnKinds,
    finalSynthesis,
    structure: {
      hasFinalSynthesisContent: true,
      includesAcceptedMeetingLoop: true,
      includesContextPolicy: true,
      summaryOnlyMeetingTurns: true,
    },
    retentionEvidence: artifact.retentionEvidence,
    personaLoopIteration: {
      openclawRole: artifact.personaLoopIteration.openclawRole,
      hermesRole: artifact.personaLoopIteration.hermesRole,
      openclawCompletedDraft: artifact.personaLoopIteration.openclawCompletedDraft,
      hermesCompletedReview: artifact.personaLoopIteration.hermesCompletedReview,
      hermesVerdict: artifact.personaLoopIteration.hermesVerdict,
      hermesReviewedOpenClawDraft: artifact.personaLoopIteration.hermesReviewedOpenClawDraft,
    },
  };
}

function normalizeMeetingLoopOutput(input: unknown): MinimumMeetingLoopArtifact {
  const artifact = expectRecord(input, "final synthesis input must be an object");
  const schemaVersion = expectLiteral(
    artifact.schemaVersion,
    "meeting-process-artifact.v1",
    "final synthesis input requires meeting-process-artifact.v1",
  );
  const status = expectLiteral(artifact.status, "finalized", "final synthesis input requires finalized meeting status");
  const meetingProcessId = expectNonEmptyString(artifact.meetingProcessId, "final synthesis input requires meetingProcessId");
  const taskId = expectNonEmptyString(artifact.taskId, "final synthesis input requires taskId");
  const threadId = expectNonEmptyString(artifact.threadId, "final synthesis input requires threadId");
  if (!Array.isArray(artifact.meetingTurns)) {
    throw new Error("final synthesis input requires meetingTurns array");
  }

  return {
    schemaVersion,
    meetingProcessId,
    taskId,
    threadId,
    status,
    meetingTurns: artifact.meetingTurns.map((turn, index) => normalizeMeetingLoopTurn(turn, index)).sort((left, right) => left.order - right.order),
    retentionEvidence: normalizeRetentionEvidence(artifact.retentionEvidence),
    personaLoopIteration: normalizePersonaLoopIteration(artifact.personaLoopIteration),
  };
}

function synthesisReadyInputToArtifact(input: SynthesisReadyMeetingLoopInput): MinimumMeetingLoopArtifact {
  return {
    schemaVersion: "meeting-process-artifact.v1",
    meetingProcessId: input.meetingProcessId,
    taskId: input.taskId,
    threadId: input.threadId,
    status: "finalized",
    meetingTurns: input.meetingTurns.map((turn) => ({ ...turn })).sort((left, right) => left.order - right.order),
    retentionEvidence: { ...input.retentionEvidence },
    personaLoopIteration: { ...input.personaLoopIteration },
  };
}

function normalizeMeetingLoopTurn(input: unknown, index: number): MinimumMeetingLoopTurn {
  const turn = expectRecord(input, `final synthesis input turn ${index + 1} must be an object`);
  if ("content" in turn || "fullContent" in turn) {
    throw new Error("final synthesis input must expose summaries only, not raw full text");
  }

  return {
    id: expectNonEmptyString(turn.id, `final synthesis input turn ${index + 1} requires id`),
    order: expectInteger(turn.order, `final synthesis input turn ${index + 1} requires integer order`),
    round: expectInteger(turn.round, `final synthesis input turn ${index + 1} requires integer round`),
    role: expectOneOf(turn.role, AGENT_ROLES, `final synthesis input turn ${index + 1} has unsupported role`),
    kind: expectOneOf(turn.kind, TURN_KINDS, `final synthesis input turn ${index + 1} has unsupported kind`),
    summary: expectNonEmptyString(turn.summary, `final synthesis input turn ${index + 1} requires summary`),
  };
}

function normalizeRetentionEvidence(input: unknown): MinimumMeetingLoopArtifact["retentionEvidence"] {
  const evidence = expectRecord(input, "final synthesis input requires retentionEvidence object");
  return {
    rawContextStoredAfterCompletion: expectBoolean(evidence.rawContextStoredAfterCompletion, "retentionEvidence.rawContextStoredAfterCompletion must be boolean"),
    summaryArtifactOnly: expectBoolean(evidence.summaryArtifactOnly, "retentionEvidence.summaryArtifactOnly must be boolean"),
    rawSentinelHiddenFromArtifact: expectBoolean(evidence.rawSentinelHiddenFromArtifact, "retentionEvidence.rawSentinelHiddenFromArtifact must be boolean"),
    ownerDraftSummaryCompressed: expectBoolean(evidence.ownerDraftSummaryCompressed, "retentionEvidence.ownerDraftSummaryCompressed must be boolean"),
  };
}

function normalizePersonaLoopIteration(input: unknown): MinimumMeetingLoopArtifact["personaLoopIteration"] {
  const iteration = expectRecord(input, "final synthesis input requires personaLoopIteration object");
  return {
    openclawRole: expectLiteral(iteration.openclawRole, "openclaw-owner", "personaLoopIteration.openclawRole must be openclaw-owner"),
    hermesRole: expectLiteral(iteration.hermesRole, "hermes-reviewer", "personaLoopIteration.hermesRole must be hermes-reviewer"),
    openclawCompletedDraft: expectBoolean(iteration.openclawCompletedDraft, "personaLoopIteration.openclawCompletedDraft must be boolean"),
    hermesCompletedReview: expectBoolean(iteration.hermesCompletedReview, "personaLoopIteration.hermesCompletedReview must be boolean"),
    hermesVerdict: expectOneOf(
      iteration.hermesVerdict,
      ["agree", "agree_with_changes"],
      "personaLoopIteration.hermesVerdict must be agree or agree_with_changes",
    ),
    hermesReviewedOpenClawDraft: expectBoolean(
      iteration.hermesReviewedOpenClawDraft,
      "personaLoopIteration.hermesReviewedOpenClawDraft must be boolean",
    ),
  };
}

function expectRecord(input: unknown, message: string): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error(message);
  }
  return input as Record<string, unknown>;
}

function expectNonEmptyString(input: unknown, message: string): string {
  if (typeof input !== "string" || input.trim().length === 0) {
    throw new Error(message);
  }
  return input.trim().replace(/\s+/g, " ");
}

function expectInteger(input: unknown, message: string): number {
  if (!Number.isInteger(input)) {
    throw new Error(message);
  }
  return input;
}

function expectBoolean(input: unknown, message: string): boolean {
  if (typeof input !== "boolean") {
    throw new Error(message);
  }
  return input;
}

function expectLiteral<const Expected extends string>(input: unknown, expected: Expected, message: string): Expected {
  if (input !== expected) {
    throw new Error(message);
  }
  return expected;
}

function expectOneOf<const Expected extends string>(input: unknown, allowed: readonly Expected[], message: string): Expected {
  if (typeof input !== "string" || !allowed.includes(input as Expected)) {
    throw new Error(message);
  }
  return input as Expected;
}
