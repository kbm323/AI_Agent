import test from "node:test";
import assert from "node:assert/strict";
import { buildEscalationMessage, emitEscalationNotification, serializeEscalationResult } from "../src/orchestrator.ts";
import { createDefaultEscalationPolicy, summarizeForThread } from "../src/policies.ts";

test("createDefaultEscalationPolicy returns a runnable policy method for user-decision reasons", () => {
  const policy = createDefaultEscalationPolicy();

  assert.equal(typeof policy.requiresUserDecision, "function");
  assert.deepEqual(
    policy.requiresUserDecision({
      userRequest: "외부 공개 전 브랜드 승인과 법무 검토가 필요해.",
      draft: "OpenClaw draft: git push 전에 비용과 rollback risk를 확인한다.",
      review: "Hermes review: 사용자 선택이 필요하다.",
      reviewerVerdict: "needs_user_decision",
    }),
    [
      "budget_or_payment",
      "legal_or_ip",
      "brand_or_public_release",
      "subjective_choice",
      "source_control_or_production",
      "reviewer_requested_user_decision",
    ],
  );
});

test("requiresUserDecision de-duplicates reasons across request, draft, and review text", () => {
  const policy = createDefaultEscalationPolicy();

  assert.deepEqual(
    policy.requiresUserDecision({
      userRequest: "게시 전 브랜드 승인 필요",
      draft: "브랜드 공개 리스크를 다시 검토한다.",
      review: "외부 공개 전 승인 필요.",
      reviewerVerdict: "agree",
    }),
    ["brand_or_public_release"],
  );
});

test("buildEscalationMessage emits stable observable artifact for convergence failure", () => {
  assert.equal(
    buildEscalationMessage(["max_rounds_without_agreement"]),
    [
      "User decision required",
      "",
      "Reasons:",
      "- max_rounds_without_agreement",
    ].join("\n"),
  );
});

test("buildEscalationMessage emits stable observable artifact when strong user input is required", () => {
  const policy = createDefaultEscalationPolicy();
  const reasons = policy.requiresUserDecision({
    userRequest: "두 구현 경로의 장단점을 정리해줘.",
    draft: "OpenClaw draft: path A and path B are both viable.",
    review: "Hermes review: both options are viable; user input is required before proceeding.",
    reviewerVerdict: "needs_user_decision",
  });

  assert.deepEqual(reasons, ["reviewer_requested_user_decision"]);
  assert.equal(
    buildEscalationMessage(reasons),
    [
      "User decision required",
      "",
      "Reasons:",
      "- reviewer_requested_user_decision",
    ].join("\n"),
  );
});

test("serializeEscalationResult emits deterministic structured output for escalation handoff", () => {
  const serialized = serializeEscalationResult({
    reasons: ["reviewer_requested_user_decision", "budget_or_payment"],
    triggerType: "meeting_loop",
    nextRequiredAction: "Ask the user to choose a budget range before continuing.",
  });

  assert.equal(
    serialized,
    [
      "{",
      '  "schemaVersion": "escalation-result.v1",',
      '  "escalation": {',
      '    "required": true,',
      '    "reasons": [',
      '      "reviewer_requested_user_decision",',
      '      "budget_or_payment"',
      "    ],",
      '    "triggerType": "meeting_loop",',
      '    "nextRequiredAction": "Ask the user to choose a budget range before continuing."',
      "  }",
      "}",
      "",
    ].join("\n"),
  );

  const parsed = JSON.parse(serialized);
  assert.equal(parsed.escalation.reasons[0], "reviewer_requested_user_decision");
  assert.equal(parsed.escalation.triggerType, "meeting_loop");
  assert.equal(parsed.escalation.nextRequiredAction, "Ask the user to choose a budget range before continuing.");
  assert.equal(
    serializeEscalationResult({
      reasons: ["reviewer_requested_user_decision", "budget_or_payment"],
      triggerType: "meeting_loop",
      nextRequiredAction: "Ask the user to choose a budget range before continuing.",
    }),
    serialized,
  );
});

test("emitEscalationNotification emits observable status for reviewer user-decision escalation", () => {
  assert.deepEqual(
    emitEscalationNotification({
      reasons: ["reviewer_requested_user_decision"],
      triggerType: "meeting_loop",
      taskId: "task-review",
      threadId: "thread-review",
    }),
    {
      schemaVersion: "escalation-notification.v1",
      event: "escalation_required",
      status: "waiting_for_user",
      escalation: {
        required: true,
        reasons: ["reviewer_requested_user_decision"],
        triggerType: "meeting_loop",
      },
      task: {
        id: "task-review",
        threadId: "thread-review",
      },
    },
  );
});

test("emitEscalationNotification emits observable status for convergence escalation", () => {
  const notification = emitEscalationNotification({
    reasons: ["max_rounds_without_agreement"],
    triggerType: "convergence_failure",
  });

  assert.equal(notification.event, "escalation_required");
  assert.equal(notification.status, "waiting_for_user");
  assert.deepEqual(notification.escalation, {
    required: true,
    reasons: ["max_rounds_without_agreement"],
    triggerType: "convergence_failure",
  });
  assert.equal("task" in notification, false);
});

test("summarizeForThread normalizes whitespace and keeps short content unchanged", () => {
  assert.equal(summarizeForThread("  Request analysis\n\n\n\nOpenClaw draft  ", 80), "Request analysis\n\nOpenClaw draft");
});

test("summarizeForThread truncates long visible context deterministically", () => {
  assert.equal(summarizeForThread("abcdef", 4), "abc…");
});
