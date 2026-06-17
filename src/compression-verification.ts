import {
  retrieveLoopVisibleContext,
  type LoopVisibleContextRetrievalResult,
} from "./context-storage.ts";
import {
  buildRepresentativeLoopContextInput,
  estimateCompressedContextSavings,
  generateRepresentativeCompressedLoopContextArtifact,
  type RepresentativeLoopTurn,
} from "./token-baseline.ts";

export type MeetingPreservationElementName =
  | "user_request"
  | "task_breakdown"
  | "role_routes"
  | "openclaw_outputs"
  | "hermes_reviews"
  | "meeting_history"
  | "final_synthesis"
  | "escalation";

export interface MeetingPreservationElementCheck {
  name: MeetingPreservationElementName;
  available: boolean;
  source: string;
  evidence: string;
}

export interface LoopContextCompressionVerificationResult {
  schemaVersion: "loop-context-compression-verification.v1";
  status: "passed" | "failed";
  comparison: {
    originalLoopContextTokens: number;
    compressedLoopContextTokens: number;
    savedTokens: number;
    savingsPercent: number;
    meetsFortyPercentTarget: boolean;
  };
  rawExposure: {
    rawFullTextHiddenFromCompressedContext: boolean;
    meetingHistorySummaryOnly: boolean;
    rawFullTextRetainedOutsideLoopContext: boolean;
  };
  preservation: {
    requiredElements: MeetingPreservationElementCheck[];
    allRequiredElementsAvailable: boolean;
  };
}

export function verifyRepresentativeLoopContextCompression(
  turns: RepresentativeLoopTurn[] = buildRepresentativeLoopContextInput().turns,
): LoopContextCompressionVerificationResult {
  const generated = generateRepresentativeCompressedLoopContextArtifact({ turns });
  const retrieval = retrieveLoopVisibleContext({
    userRequestSummary: generated.artifact.requestSummary,
    turns,
    acceptedFeedback: generated.artifact.acceptedFeedback,
    rejectedFeedback: generated.artifact.rejectedFeedback,
    escalationReasons: generated.artifact.escalationReasons,
  });
  const savings = estimateCompressedContextSavings({
    baselineContext: turns.map((turn) => turn.content),
    proposedCompressedContext: generated.artifact.content,
  });
  const rawFullTextHiddenFromCompressedContext = turns.every(
    (turn) => !generated.artifact.content.includes(turn.content),
  );
  const meetingHistorySummaryOnly = retrieval.meetingHistory.every((turn) => !Object.hasOwn(turn, "content"));
  const rawFullTextRetainedOutsideLoopContext = retrieval.rawOriginalTextRetained;
  const requiredElements = buildMeetingPreservationChecks(turns, retrieval);
  const allRequiredElementsAvailable = requiredElements.every((element) => element.available);
  const passed =
    savings.meetsFortyPercentTarget &&
    rawFullTextHiddenFromCompressedContext &&
    meetingHistorySummaryOnly &&
    rawFullTextRetainedOutsideLoopContext &&
    allRequiredElementsAvailable;

  return {
    schemaVersion: "loop-context-compression-verification.v1",
    status: passed ? "passed" : "failed",
    comparison: {
      originalLoopContextTokens: savings.baselineTokens,
      compressedLoopContextTokens: savings.proposedCompressedTokens,
      savedTokens: savings.savedTokens,
      savingsPercent: savings.savingsPercent,
      meetsFortyPercentTarget: savings.meetsFortyPercentTarget,
    },
    rawExposure: {
      rawFullTextHiddenFromCompressedContext,
      meetingHistorySummaryOnly,
      rawFullTextRetainedOutsideLoopContext,
    },
    preservation: {
      requiredElements,
      allRequiredElementsAvailable,
    },
  };
}

function buildMeetingPreservationChecks(
  turns: RepresentativeLoopTurn[],
  retrieval: LoopVisibleContextRetrievalResult,
): MeetingPreservationElementCheck[] {
  const summaries = retrieval.meetingHistory.map((turn) => turn.summary);
  const latestOpenClaw = retrieval.compressedLoopContext.latestOpenClawSummary;
  const latestHermes = retrieval.compressedLoopContext.latestHermesSummary;

  return [
    {
      name: "user_request",
      available: retrieval.compressedLoopContext.requestSummary.trim().length > 0,
      source: "compressedLoopContext.requestSummary",
      evidence: retrieval.compressedLoopContext.requestSummary,
    },
    {
      name: "task_breakdown",
      available: summaries.some((summary) => summary.includes("task-001") && summary.includes("task-004")),
      source: "meetingHistory[].summary",
      evidence: findSummary(summaries, "task-001"),
    },
    {
      name: "role_routes",
      available:
        retrieval.meetingHistory.some((turn) => turn.role === "openclaw-owner") &&
        retrieval.meetingHistory.some((turn) => turn.role === "hermes-reviewer") &&
        retrieval.meetingHistory.some((turn) => turn.role === "openclaw-finalizer"),
      source: "meetingHistory[].role",
      evidence: [...new Set(retrieval.meetingHistory.map((turn) => turn.role))].join(", "),
    },
    {
      name: "openclaw_outputs",
      available: latestOpenClaw.trim().length > 0 && summaries.some((summary) => summary.includes("OpenClaw draft")),
      source: "compressedLoopContext.latestOpenClawSummary",
      evidence: latestOpenClaw,
    },
    {
      name: "hermes_reviews",
      available: latestHermes.trim().length > 0 && retrieval.compressedLoopContext.latestHermesVerdict !== "unknown",
      source: "compressedLoopContext.latestHermesSummary",
      evidence: `${retrieval.compressedLoopContext.latestHermesVerdict}: ${latestHermes}`,
    },
    {
      name: "meeting_history",
      available:
        retrieval.meetingHistory.length === turns.length &&
        retrieval.meetingHistory.every((turn) => turn.summary.trim().length > 0),
      source: "meetingHistory[]",
      evidence: `${retrieval.meetingHistory.length} summary-only turns`,
    },
    {
      name: "final_synthesis",
      available: retrieval.meetingHistory.some(
        (turn) => turn.kind === "final_synthesis" && turn.summary.includes("Final synthesis"),
      ),
      source: "meetingHistory[kind=final_synthesis].summary",
      evidence: findSummary(summaries, "Final synthesis"),
    },
    {
      name: "escalation",
      available: Array.isArray(retrieval.compressedLoopContext.escalationReasons),
      source: "compressedLoopContext.escalationReasons",
      evidence:
        retrieval.compressedLoopContext.escalationReasons.length > 0
          ? retrieval.compressedLoopContext.escalationReasons.join(" | ")
          : "no escalation reasons for converged representative loop",
    },
  ];
}

function findSummary(summaries: string[], needle: string): string {
  return summaries.find((summary) => summary.includes(needle)) ?? "";
}
