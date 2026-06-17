import test from "node:test";
import assert from "node:assert/strict";
import { detectStrongUserInputRequired } from "../src/user-input-required.ts";

test("detectStrongUserInputRequired identifies states that cannot proceed without explicit user decision", () => {
  assert.deepEqual(
    detectStrongUserInputRequired({
      taskStatus: "waiting_for_user",
      reviewerVerdict: "needs_user_decision",
      requestAmbiguitySignals: ["underspecified_preference", "unclear_success_criteria"],
      policyReasons: ["budget_or_payment", "budget_or_payment"],
      decisionRequiresUserDecision: true,
      decisionReasons: ["legal_or_ip"],
      latestTurnKind: "escalation",
      latestTurnSummary: "User decision required\n\nReasons:\n- budget_or_payment",
    }),
    {
      required: true,
      status: "waiting_for_user",
      triggers: [
        "task_waiting_for_user",
        "request_ambiguity",
        "policy_reason",
        "reviewer_verdict",
        "decision_record",
        "escalation_turn",
      ],
      reasons: [
        "task_waiting_for_user",
        "underspecified_preference",
        "unclear_success_criteria",
        "budget_or_payment",
        "reviewer_requested_user_decision",
        "legal_or_ip",
        "escalation_turn_requires_user_decision",
      ],
      nextAction: {
        type: "user_input_required",
        prompt: "Pause execution and request an explicit user decision before continuing.",
      },
    },
  );
});

test("detectStrongUserInputRequired returns continue for finalized or reviewable states", () => {
  assert.deepEqual(
    detectStrongUserInputRequired({
      taskStatus: "reviewed",
      reviewerVerdict: "agree_with_changes",
      requestAmbiguitySignals: [],
      policyReasons: [],
      decisionRequiresUserDecision: false,
      decisionReasons: [],
      latestTurnKind: "review",
      latestTurnSummary: "Hermes agreed with changes.",
    }),
    {
      required: false,
      status: "clear",
      triggers: [],
      reasons: [],
      nextAction: {
        type: "continue",
        prompt: "No explicit user decision is required for the current state.",
      },
    },
  );
});
