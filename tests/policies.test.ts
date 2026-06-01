import test from "node:test";
import assert from "node:assert/strict";
import { createDefaultEscalationPolicy } from "../src/policies.ts";

test("default escalation does not pause ordinary candidate review tasks", () => {
  const policy = createDefaultEscalationPolicy();
  const reasons = policy.requiresUserDecision({
    userRequest: "뮤직비디오 주제 후보를 만들고 리뷰해서 최종안을 정리해줘",
    draft: "후보 A는 비용이 낮고 되돌릴 수 없는 선택처럼 보이지 않는다.",
    review: "후보를 비교했다. 사용자가 원하면 선택하면 된다.",
    reviewerVerdict: "agree_with_changes",
  });

  assert.deepEqual(reasons, []);
});

test("default escalation pauses explicit external publishing request", () => {
  const policy = createDefaultEscalationPolicy();
  const reasons = policy.requiresUserDecision({
    userRequest: "완성본을 외부 공개하고 실제 게시해줘",
    draft: "게시 계획",
    review: "가능",
    reviewerVerdict: "agree",
  });

  assert.ok(reasons.includes("brand_or_public_release"));
});
