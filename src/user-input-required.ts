import type { ReviewerVerdict, TaskStatus, TurnKind } from "./types.ts";

export type StrongUserInputRequiredTrigger =
  | "task_waiting_for_user"
  | "request_ambiguity"
  | "policy_reason"
  | "reviewer_verdict"
  | "decision_record"
  | "escalation_turn";

export interface StrongUserInputRequiredState {
  taskStatus?: TaskStatus;
  reviewerVerdict?: ReviewerVerdict;
  requestAmbiguitySignals?: string[];
  policyReasons?: string[];
  decisionRequiresUserDecision?: boolean;
  decisionReasons?: string[];
  latestTurnKind?: TurnKind;
  latestTurnSummary?: string;
}

export interface StrongUserInputRequiredDetection {
  required: boolean;
  status: "waiting_for_user" | "clear";
  triggers: StrongUserInputRequiredTrigger[];
  reasons: string[];
  nextAction: {
    type: "user_input_required" | "continue";
    prompt: string;
  };
}

export function detectStrongUserInputRequired(
  state: StrongUserInputRequiredState,
): StrongUserInputRequiredDetection {
  const reasons: string[] = [];
  const triggers: StrongUserInputRequiredTrigger[] = [];

  if (state.taskStatus === "waiting_for_user") {
    triggers.push("task_waiting_for_user");
    reasons.push("task_waiting_for_user");
  }

  appendReasons(reasons, triggers, "request_ambiguity", state.requestAmbiguitySignals);
  appendReasons(reasons, triggers, "policy_reason", state.policyReasons);

  if (state.reviewerVerdict === "needs_user_decision") {
    triggers.push("reviewer_verdict");
    reasons.push("reviewer_requested_user_decision");
  }

  if (state.decisionRequiresUserDecision === true) {
    triggers.push("decision_record");
    appendUnique(reasons, state.decisionReasons?.length ? state.decisionReasons : ["decision_record_requires_user"]);
  }

  if (
    state.latestTurnKind === "escalation" &&
    /user decision required|사용자 결정|사용자 입력|explicit user decision/i.test(state.latestTurnSummary ?? "")
  ) {
    triggers.push("escalation_turn");
    reasons.push("escalation_turn_requires_user_decision");
  }

  const uniqueReasons = uniqueNonEmpty(reasons);
  const uniqueTriggers = uniqueNonEmpty(triggers);
  const required = uniqueReasons.length > 0;

  return {
    required,
    status: required ? "waiting_for_user" : "clear",
    triggers: uniqueTriggers,
    reasons: uniqueReasons,
    nextAction: required
      ? {
          type: "user_input_required",
          prompt: "Pause execution and request an explicit user decision before continuing.",
        }
      : {
          type: "continue",
          prompt: "No explicit user decision is required for the current state.",
        },
  };
}

function appendReasons(
  reasons: string[],
  triggers: StrongUserInputRequiredTrigger[],
  trigger: StrongUserInputRequiredTrigger,
  values: string[] | undefined,
): void {
  const normalized = uniqueNonEmpty(values ?? []);
  if (normalized.length === 0) return;
  triggers.push(trigger);
  appendUnique(reasons, normalized);
}

function appendUnique(target: string[], values: string[]): void {
  for (const value of values) {
    const normalized = value.trim();
    if (normalized.length > 0 && !target.includes(normalized)) target.push(normalized);
  }
}

function uniqueNonEmpty<T extends string>(values: T[]): T[] {
  return [...new Set(values.map((value) => value.trim()).filter((value): value is T => value.length > 0))];
}
