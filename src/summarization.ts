import { summarizeForThread } from "./policies.ts";
import type { AgentRole, TurnKind } from "./types.ts";

export interface SummarizableMeetingTurn {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  content: string;
}

export interface MeetingTurnSummary {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  summary: string;
}

export interface CompressedLoopContextInput {
  userRequestSummary: string;
  meetingTurns: MeetingTurnSummary[];
  acceptedFeedback?: string[];
  rejectedFeedback?: string[];
  escalationReasons?: string[];
}

export interface CompressedLoopContextArtifact {
  schemaVersion: "compressed-loop-context.v1";
  requestSummary: string;
  latestOpenClawSummary: string;
  latestHermesSummary: string;
  latestHermesVerdict: "agree" | "agree_with_changes" | "disagree" | "needs_user_decision" | "unknown";
  acceptedFeedback: string[];
  rejectedFeedback: string[];
  escalationReasons: string[];
  content: string;
}

export type PromptContextMessageKind =
  | "system"
  | "user_request"
  | "task_breakdown"
  | "role_route"
  | "meeting_turn"
  | "raw_prompt_echo"
  | "scratchpad"
  | "final_synthesis"
  | "escalation";

export interface PromptContextMessage {
  id: string;
  kind: PromptContextMessageKind;
  content: string;
  round?: number;
  role?: AgentRole;
}

export type CompactedPromptContextDisposition = "retained" | "compressed";

export interface CompactedPromptContextMessage {
  id: string;
  kind: PromptContextMessageKind;
  disposition: CompactedPromptContextDisposition;
  content: string;
  originalChars: number;
  compactedChars: number;
  round?: number;
  role?: AgentRole;
}

export interface PromptContextCompactionOptions {
  maxSummaryChars?: number;
  compressKinds?: PromptContextMessageKind[];
  dropKinds?: PromptContextMessageKind[];
}

export interface PromptContextCompactionResult {
  schemaVersion: "prompt-context-compaction.v1";
  messages: CompactedPromptContextMessage[];
  removedMessageIds: string[];
  compressedMessageIds: string[];
  originalMessageCount: number;
  compactedMessageCount: number;
  originalCharCount: number;
  compactedCharCount: number;
}

const defaultCompressKinds: PromptContextMessageKind[] = ["meeting_turn"];
const defaultDropKinds: PromptContextMessageKind[] = ["raw_prompt_echo", "scratchpad"];

export function summarizeMeetingTurn(input: SummarizableMeetingTurn, maxChars = 1200): MeetingTurnSummary {
  return {
    round: input.round,
    role: input.role,
    kind: input.kind,
    summary: summarizeForThread(input.content, maxChars),
  };
}

export function summarizeMeetingHistory(turns: SummarizableMeetingTurn[], maxChars = 1200): MeetingTurnSummary[] {
  return turns.map((turn) => summarizeMeetingTurn(turn, maxChars));
}

export function compactPromptContext(
  history: PromptContextMessage[],
  options: PromptContextCompactionOptions = {},
): PromptContextCompactionResult {
  const maxSummaryChars = options.maxSummaryChars ?? 240;
  const compressKinds = new Set(options.compressKinds ?? defaultCompressKinds);
  const dropKinds = new Set(options.dropKinds ?? defaultDropKinds);
  const removedMessageIds: string[] = [];
  const compressedMessageIds: string[] = [];
  const messages: CompactedPromptContextMessage[] = [];
  let originalCharCount = 0;
  let compactedCharCount = 0;

  for (const message of history) {
    const normalizedContent = message.content.trim();
    originalCharCount += normalizedContent.length;

    if (dropKinds.has(message.kind)) {
      removedMessageIds.push(message.id);
      continue;
    }

    const shouldCompress = compressKinds.has(message.kind);
    const compactedContent = shouldCompress ? summarizeForThread(normalizedContent, maxSummaryChars) : normalizedContent;

    if (shouldCompress && compactedContent !== normalizedContent) {
      compressedMessageIds.push(message.id);
    }

    compactedCharCount += compactedContent.length;
    messages.push({
      id: message.id,
      kind: message.kind,
      disposition: shouldCompress ? "compressed" : "retained",
      content: compactedContent,
      originalChars: normalizedContent.length,
      compactedChars: compactedContent.length,
      ...(message.round === undefined ? {} : { round: message.round }),
      ...(message.role === undefined ? {} : { role: message.role }),
    });
  }

  return {
    schemaVersion: "prompt-context-compaction.v1",
    messages,
    removedMessageIds,
    compressedMessageIds,
    originalMessageCount: history.length,
    compactedMessageCount: messages.length,
    originalCharCount,
    compactedCharCount,
  };
}

export function buildCompressedLoopContextArtifact(input: CompressedLoopContextInput): CompressedLoopContextArtifact {
  const requestSummary = summarizeForThread(input.userRequestSummary, 240);
  if (!requestSummary) {
    throw new TypeError("userRequestSummary must be a non-empty string");
  }

  const latestOpenClawSummary = findLatestSummary(input.meetingTurns, "openclaw-owner");
  const latestHermesSummary = findLatestSummary(input.meetingTurns, "hermes-reviewer");
  const latestHermesVerdict = inferHermesVerdict(latestHermesSummary);
  const acceptedFeedback = summarizeList(input.acceptedFeedback ?? []);
  const rejectedFeedback = summarizeList(input.rejectedFeedback ?? []);
  const escalationReasons = summarizeList(input.escalationReasons ?? []);

  return {
    schemaVersion: "compressed-loop-context.v1",
    requestSummary,
    latestOpenClawSummary,
    latestHermesSummary,
    latestHermesVerdict,
    acceptedFeedback,
    rejectedFeedback,
    escalationReasons,
    content: [
      "Compressed loop context",
      `- request_summary: ${requestSummary}`,
      `- latest_openclaw: ${latestOpenClawSummary || "none"}`,
      `- latest_hermes_verdict: ${latestHermesVerdict}`,
      `- latest_hermes: ${latestHermesSummary || "none"}`,
      `- accepted_feedback: ${formatList(acceptedFeedback)}`,
      `- rejected_feedback: ${formatList(rejectedFeedback)}`,
      `- escalation_reasons: ${formatList(escalationReasons)}`,
    ].join("\n"),
  };
}

function findLatestSummary(turns: MeetingTurnSummary[], role: AgentRole): string {
  return [...turns].reverse().find((turn) => turn.role === role)?.summary ?? "";
}

function inferHermesVerdict(summary: string): CompressedLoopContextArtifact["latestHermesVerdict"] {
  const normalized = summary.toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized.includes("needs_user_decision")) return "needs_user_decision";
  if (normalized.includes("agree_with_changes")) return "agree_with_changes";
  if (/(^|[^a-z])disagree($|[^a-z])/.test(normalized)) return "disagree";
  if (/(^|[^a-z])agree($|[^a-z])/.test(normalized)) return "agree";
  return "unknown";
}

function summarizeList(values: string[]): string[] {
  return values.map((value) => summarizeForThread(value, 160)).filter(Boolean);
}

function formatList(values: string[]): string {
  return values.length > 0 ? values.join(" | ") : "none";
}
