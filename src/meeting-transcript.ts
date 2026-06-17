import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

export interface PreservedMeetingTranscriptTurn {
  id: string;
  order: number;
  round: number;
  role: string;
  kind: string;
  summary: string;
}

export interface PreservedMeetingTranscriptArtifact {
  schemaVersion: "preserved-meeting-transcript.v1";
  meetingProcessId: string;
  taskId: string;
  threadId: string;
  status: string;
  iterationStatus: {
    status: string;
    round: number;
    openclawCompletedDraft: boolean;
    hermesCompletedReview: boolean;
    hermesVerdict: string;
    converged: boolean;
  };
  participantOutputs: Array<{
    role: "openclaw-owner" | "hermes-reviewer";
    kind: "owner_draft" | "review";
    turnId: string;
    summary: string;
  }>;
  preservedLoop: {
    iterationId: string;
    round: number;
    openclawRole: "openclaw-owner";
    hermesRole: "hermes-reviewer";
    executionTurnId: string;
    reviewTurnId: string;
    hermesReviewedOpenClawDraft: boolean;
  };
  meetingTurns: PreservedMeetingTranscriptTurn[];
  retentionEvidence: {
    rawContextStoredAfterCompletion: boolean;
    transcriptSummaryOnly: true;
    rawSentinelHiddenFromTranscript: boolean;
    ownerDraftSummaryCompressed: boolean;
  };
}

export interface WritePreservedMeetingTranscriptInput {
  projectRoot?: string;
  outputPath?: string;
  artifact: PreservedMeetingTranscriptArtifact;
}

export interface WrittenPreservedMeetingTranscriptArtifact {
  path: string;
  artifact: PreservedMeetingTranscriptArtifact;
}

export const defaultPreservedMeetingTranscriptPath = "docs/generated/meeting-loop-transcript.json";

export function buildPreservedMeetingTranscriptArtifact(input: {
  meetingProcessId: string;
  taskId: string;
  threadId: string;
  status: string;
  meetingTurns: PreservedMeetingTranscriptTurn[];
  retentionEvidence: {
    rawContextStoredAfterCompletion: boolean;
    summaryArtifactOnly: boolean;
    rawSentinelHiddenFromArtifact: boolean;
    ownerDraftSummaryCompressed: boolean;
  };
  personaLoopIteration: {
    id: string;
    round: number;
    openclawRole: "openclaw-owner";
    hermesRole: "hermes-reviewer";
    openclawCompletedDraft?: boolean;
    hermesCompletedReview?: boolean;
    hermesVerdict?: string;
    hermesReviewedOpenClawDraft: boolean;
  };
}): PreservedMeetingTranscriptArtifact {
  const executionTurn = input.meetingTurns.find(
    (turn) => turn.round === input.personaLoopIteration.round && turn.role === input.personaLoopIteration.openclawRole && turn.kind === "owner_draft",
  );
  const reviewTurn = input.meetingTurns.find(
    (turn) => turn.round === input.personaLoopIteration.round && turn.role === input.personaLoopIteration.hermesRole && turn.kind === "review",
  );

  return {
    schemaVersion: "preserved-meeting-transcript.v1",
    meetingProcessId: input.meetingProcessId,
    taskId: input.taskId,
    threadId: input.threadId,
    status: input.status,
    iterationStatus: {
      status: input.status,
      round: input.personaLoopIteration.round,
      openclawCompletedDraft: input.personaLoopIteration.openclawCompletedDraft ?? executionTurn !== undefined,
      hermesCompletedReview: input.personaLoopIteration.hermesCompletedReview ?? reviewTurn !== undefined,
      hermesVerdict: input.personaLoopIteration.hermesVerdict ?? "unknown",
      converged: input.status === "finalized" && input.personaLoopIteration.hermesReviewedOpenClawDraft,
    },
    participantOutputs: [
      ...(executionTurn
        ? [
            {
              role: input.personaLoopIteration.openclawRole,
              kind: "owner_draft" as const,
              turnId: executionTurn.id,
              summary: executionTurn.summary,
            },
          ]
        : []),
      ...(reviewTurn
        ? [
            {
              role: input.personaLoopIteration.hermesRole,
              kind: "review" as const,
              turnId: reviewTurn.id,
              summary: reviewTurn.summary,
            },
          ]
        : []),
    ],
    preservedLoop: {
      iterationId: input.personaLoopIteration.id,
      round: input.personaLoopIteration.round,
      openclawRole: input.personaLoopIteration.openclawRole,
      hermesRole: input.personaLoopIteration.hermesRole,
      executionTurnId: executionTurn?.id ?? "",
      reviewTurnId: reviewTurn?.id ?? "",
      hermesReviewedOpenClawDraft: input.personaLoopIteration.hermesReviewedOpenClawDraft,
    },
    meetingTurns: input.meetingTurns.map((turn) => ({ ...turn })),
    retentionEvidence: {
      rawContextStoredAfterCompletion: input.retentionEvidence.rawContextStoredAfterCompletion,
      transcriptSummaryOnly: input.retentionEvidence.summaryArtifactOnly,
      rawSentinelHiddenFromTranscript: input.retentionEvidence.rawSentinelHiddenFromArtifact,
      ownerDraftSummaryCompressed: input.retentionEvidence.ownerDraftSummaryCompressed,
    },
  };
}

export function writePreservedMeetingTranscriptArtifact(input: WritePreservedMeetingTranscriptInput): WrittenPreservedMeetingTranscriptArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? defaultPreservedMeetingTranscriptPath;
  const resolvedPath = resolve(projectRoot, outputPath);
  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, `${JSON.stringify(input.artifact, null, 2)}\n`, "utf8");
  return { path: resolvedPath, artifact: input.artifact };
}
