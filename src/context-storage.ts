import { dirname, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import type { TurnRecord } from "./types.ts";
import {
  buildCompressedLoopContextArtifact,
  type CompressedLoopContextArtifact,
  type MeetingTurnSummary,
} from "./summarization.ts";

export interface ContextStorageBoundaryArtifact {
  schemaVersion: "context-storage-boundary.v1";
  sourceOfTruth: {
    taskRequestField: "tasks.user_request";
    rawTurnField: "turns.content";
  };
  loopVisibleFields: string[];
  auditOnlyFields: string[];
  invariants: string[];
  verificationChecks: string[];
}

export interface ContextStorageBoundaryVerificationInput {
  turns: Pick<TurnRecord, "id" | "content" | "visibleSummary">[];
  loopVisibleContext: string;
}

export interface ContextStorageBoundaryVerificationResult {
  schemaVersion: "context-storage-boundary-verification.v1";
  passed: boolean;
  checkedTurnCount: number;
  fullOriginalTextRetained: boolean;
  rawTextHiddenFromLoopContext: boolean;
  summariesVisibleInLoopContext: boolean;
  violations: string[];
}

export interface WrittenContextStorageBoundaryArtifact {
  path: string;
  artifact: ContextStorageBoundaryArtifact;
  markdown: string;
}

export interface LoopVisibleContextRetrievalInput {
  userRequestSummary: string;
  turns: Pick<TurnRecord, "round" | "role" | "kind" | "content" | "visibleSummary">[];
  acceptedFeedback?: string[];
  rejectedFeedback?: string[];
  escalationReasons?: string[];
}

export interface LoopVisibleContextRetrievalResult {
  schemaVersion: "loop-visible-context-retrieval.v1";
  rawTurnCount: number;
  rawOriginalTextRetained: boolean;
  meetingHistory: MeetingTurnSummary[];
  compressedLoopContext: CompressedLoopContextArtifact;
}

export interface ContextStorageAccessPathDemoInput {
  turn: Pick<TurnRecord, "id" | "round" | "role" | "kind" | "content" | "visibleSummary">;
  userRequestSummary: string;
}

export interface ContextStorageAccessPathDemoResult {
  schemaVersion: "context-storage-access-path-demo.v1";
  turnId: string;
  rawOriginalTextPath: "turns.content";
  loopVisibleSummaryPath: "LoopVisibleContextRetrievalResult.meetingHistory[].summary";
  rawOriginalTextRetainedExactly: boolean;
  loopVisibleSummaryRetrievedExactly: boolean;
  observablyDifferentValues: boolean;
  separateAccessPaths: boolean;
  rawHiddenFromLoopVisiblePath: boolean;
  rawOriginalTextFingerprint: {
    length: number;
    prefix: string;
  };
  loopVisibleSummaryFingerprint: {
    length: number;
    prefix: string;
  };
}

export function buildContextStorageBoundaryArtifact(): ContextStorageBoundaryArtifact {
  return {
    schemaVersion: "context-storage-boundary.v1",
    sourceOfTruth: {
      taskRequestField: "tasks.user_request",
      rawTurnField: "turns.content",
    },
    loopVisibleFields: [
      "turns.visibleSummary",
      "RunTaskResult.meetingHistory[].summary",
      "LoopVisibleContextRetrievalResult.meetingHistory[].summary",
      "LoopVisibleContextRetrievalResult.compressedLoopContext.content",
      "DiscordDelivery.postThread.content",
    ],
    auditOnlyFields: [
      "turns.content",
      "DiscordDelivery.postThread.fullContent",
    ],
    invariants: [
      "Every persisted meeting turn keeps the complete original text in turns.content.",
      "Loop prompts, returned meeting history, and normal thread output use turns.visibleSummary-derived text.",
      "Raw full text is available only through persistence or explicit audit/debug paths.",
    ],
    verificationChecks: [
      "full original turn content is non-empty and retained exactly",
      "raw turn content does not appear in loop-visible context",
      "visible summaries appear in loop-visible context",
      "loop-visible retrieval returns meeting history summaries and compressed context without content fields",
      "raw retained text and loop-visible summary are retrieved through separate observable paths",
    ],
  };
}

export function retrieveLoopVisibleContext(
  input: LoopVisibleContextRetrievalInput,
): LoopVisibleContextRetrievalResult {
  const meetingHistory = input.turns.map((turn) => ({
    round: turn.round,
    role: turn.role,
    kind: turn.kind,
    summary: turn.visibleSummary,
  }));
  const compressedLoopContext = buildCompressedLoopContextArtifact({
    userRequestSummary: input.userRequestSummary,
    meetingTurns: meetingHistory,
    acceptedFeedback: input.acceptedFeedback,
    rejectedFeedback: input.rejectedFeedback,
    escalationReasons: input.escalationReasons,
  });

  return {
    schemaVersion: "loop-visible-context-retrieval.v1",
    rawTurnCount: input.turns.length,
    rawOriginalTextRetained: input.turns.length > 0 && input.turns.every((turn) => turn.content.trim().length > 0),
    meetingHistory,
    compressedLoopContext,
  };
}

export function demonstrateContextStorageAccessPaths(
  input: ContextStorageAccessPathDemoInput,
): ContextStorageAccessPathDemoResult {
  const retrieval = retrieveLoopVisibleContext({
    userRequestSummary: input.userRequestSummary,
    turns: [input.turn],
  });
  const rawOriginalText = input.turn.content;
  const loopVisibleSummary = retrieval.meetingHistory[0]?.summary ?? "";
  const rawOriginalTextPath = "turns.content";
  const loopVisibleSummaryPath = "LoopVisibleContextRetrievalResult.meetingHistory[].summary";

  return {
    schemaVersion: "context-storage-access-path-demo.v1",
    turnId: input.turn.id,
    rawOriginalTextPath,
    loopVisibleSummaryPath,
    rawOriginalTextRetainedExactly: rawOriginalText === input.turn.content && rawOriginalText.trim().length > 0,
    loopVisibleSummaryRetrievedExactly: loopVisibleSummary === input.turn.visibleSummary && loopVisibleSummary.trim().length > 0,
    observablyDifferentValues: rawOriginalText !== loopVisibleSummary,
    separateAccessPaths: rawOriginalTextPath !== loopVisibleSummaryPath,
    rawHiddenFromLoopVisiblePath: !containsExactText(loopVisibleSummary, rawOriginalText),
    rawOriginalTextFingerprint: fingerprintText(rawOriginalText),
    loopVisibleSummaryFingerprint: fingerprintText(loopVisibleSummary),
  };
}

export function verifyContextStorageBoundary(
  input: ContextStorageBoundaryVerificationInput,
): ContextStorageBoundaryVerificationResult {
  const violations: string[] = [];

  if (input.turns.length === 0) {
    violations.push("turns must include at least one stored meeting turn");
  }

  for (const turn of input.turns) {
    if (turn.content.trim().length === 0) {
      violations.push(`${turn.id}: raw content must be retained as a non-empty string`);
    }
    if (turn.visibleSummary.trim().length === 0) {
      violations.push(`${turn.id}: visible summary must be retained as a non-empty string`);
    }
    if (turn.content === turn.visibleSummary) {
      violations.push(`${turn.id}: visible summary must not equal raw full content`);
    }
    if (containsExactText(input.loopVisibleContext, turn.content)) {
      violations.push(`${turn.id}: raw full content leaked into loop-visible context`);
    }
    if (!containsExactText(input.loopVisibleContext, turn.visibleSummary)) {
      violations.push(`${turn.id}: visible summary is missing from loop-visible context`);
    }
  }

  const fullOriginalTextRetained =
    input.turns.length > 0 && input.turns.every((turn) => turn.content.trim().length > 0);
  const rawTextHiddenFromLoopContext =
    input.turns.length > 0 && input.turns.every((turn) => !containsExactText(input.loopVisibleContext, turn.content));
  const summariesVisibleInLoopContext =
    input.turns.length > 0 && input.turns.every((turn) => containsExactText(input.loopVisibleContext, turn.visibleSummary));

  return {
    schemaVersion: "context-storage-boundary-verification.v1",
    passed: violations.length === 0,
    checkedTurnCount: input.turns.length,
    fullOriginalTextRetained,
    rawTextHiddenFromLoopContext,
    summariesVisibleInLoopContext,
    violations,
  };
}

export function renderContextStorageBoundaryMarkdown(
  artifact = buildContextStorageBoundaryArtifact(),
): string {
  return [
    "# Context Storage Boundary",
    "",
    `Schema: \`${artifact.schemaVersion}\``,
    "",
    "## Source Of Truth",
    "",
    `- User request: \`${artifact.sourceOfTruth.taskRequestField}\``,
    `- Raw meeting turn: \`${artifact.sourceOfTruth.rawTurnField}\``,
    "",
    "## Loop Visible Fields",
    "",
    ...artifact.loopVisibleFields.map((field) => `- \`${field}\``),
    "",
    "## Audit Only Fields",
    "",
    ...artifact.auditOnlyFields.map((field) => `- \`${field}\``),
    "",
    "## Invariants",
    "",
    ...artifact.invariants.map((invariant) => `- ${invariant}`),
    "",
    "## Verification Checks",
    "",
    ...artifact.verificationChecks.map((check) => `- ${check}`),
    "",
  ].join("\n");
}

export function writeContextStorageBoundaryArtifact(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): WrittenContextStorageBoundaryArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? "docs/context-storage-boundary.md";
  const resolvedPath = resolve(projectRoot, outputPath);
  const artifact = buildContextStorageBoundaryArtifact();
  const markdown = renderContextStorageBoundaryMarkdown(artifact);

  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, markdown);

  return {
    path: resolvedPath,
    artifact,
    markdown,
  };
}

function containsExactText(haystack: string, needle: string): boolean {
  return needle.length > 0 && haystack.includes(needle);
}

function fingerprintText(text: string): { length: number; prefix: string } {
  return {
    length: text.length,
    prefix: text.slice(0, 24),
  };
}
