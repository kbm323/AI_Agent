export type AgentRole = "openclaw-owner" | "hermes-reviewer" | "openclaw-finalizer";

export type TeamRoute = "content" | "art" | "tech" | "marketing" | "executive";

export type TaskStatus =
  | "created"
  | "owner_drafted"
  | "review_requested"
  | "reviewed"
  | "finalized"
  | "waiting_for_user"
  | "failed";

export type TurnKind = "owner_draft" | "review_request" | "review" | "final_synthesis" | "escalation" | "user_decision";

export type ReviewerVerdict = "agree" | "partial_agree" | "disagree" | "needs_user_decision";

export interface ProjectRef {
  channelId: string;
  name?: string;
}

export interface TaskRecord {
  id: string;
  projectChannelId: string;
  threadId: string;
  userRequest: string;
  teamRoute: TeamRoute;
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
  postParent(input: { channelId: string; content: string }): Promise<void>;
  postThread(input: { threadId: string; content: string; fullContent?: string; role?: AgentRole; kind?: TurnKind }): Promise<void>;
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
  repeatIssueThreshold: number;
}
