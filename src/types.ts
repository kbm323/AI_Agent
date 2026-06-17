export type AgentRole = "openclaw-owner" | "hermes-reviewer" | "openclaw-finalizer";

export type TaskStatus =
  | "created"
  | "owner_drafted"
  | "review_requested"
  | "reviewed"
  | "finalized"
  | "waiting_for_user"
  | "failed";

export type TurnKind = "request_analysis" | "owner_draft" | "review_request" | "review" | "final_synthesis" | "escalation";

export type ReviewerVerdict = "agree" | "agree_with_changes" | "disagree" | "needs_user_decision";

export interface ProjectRef {
  channelId: string;
  name?: string;
}

export interface TaskRecord {
  id: string;
  projectChannelId: string;
  threadId: string;
  userRequest: string;
  status: TaskStatus;
  createdAt: string;
  updatedAt: string;
}

export interface TurnRecord {
  id: string;
  taskId: string;
  round: number;
  role: AgentRole;
  kind: TurnKind;
  content: string;
  visibleSummary: string;
  createdAt: string;
}

export interface DecisionRecord {
  id: string;
  taskId: string;
  requiresUserDecision: boolean;
  reasons: string[];
  createdAt: string;
}

export interface DiscordDelivery {
  createThread(input: { parentChannelId: string; name: string; initialMessage: string }): Promise<{ threadId: string; url?: string }>;
  archiveThread(threadId: string): Promise<void>;
  getThread(threadId: string): Promise<{ threadId: string; name: string; archived: boolean }>;
  postParent(input: { channelId: string; content: string }): Promise<void>;
  postThread(input: { threadId: string; content: string; fullContent?: string }): Promise<void>;
}

export interface OwnerExecutor {
  createDraft(input: { task: TaskRecord; userRequest: string; round: number }): Promise<string>;
}

export interface ReviewerExecutor {
  review(input: { task: TaskRecord; userRequest: string; draft: string; round: number }): Promise<{ content: string; verdict: ReviewerVerdict }>;
}

export interface FinalizerExecutor {
  synthesize(input: {
    task: TaskRecord;
    userRequest: string;
    draft: string;
    review: string;
    reviewerVerdict: ReviewerVerdict;
    acceptedFeedback: string[];
    rejectedFeedback: string[];
  }): Promise<string>;
}

export interface EscalationPolicy {
  requiresUserDecision(input: { userRequest: string; draft: string; review: string; reviewerVerdict: ReviewerVerdict }): string[];
}

export interface OrchestratorConfig {
  maxRounds: number;
}
