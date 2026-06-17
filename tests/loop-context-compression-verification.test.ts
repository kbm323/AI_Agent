import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRepresentativeLoopContextInput,
  verifyRepresentativeLoopContextCompression,
} from "../src/index.ts";
import { executeLoopContextCompressionVerificationCommand } from "../scripts/check-loop-context-compression-verification.ts";

test("compression verification proves required meeting elements survive summary-only compression", () => {
  const result = verifyRepresentativeLoopContextCompression();

  assert.equal(result.schemaVersion, "loop-context-compression-verification.v1");
  assert.equal(result.status, "passed");
  assert.deepEqual(result.comparison, {
    originalLoopContextTokens: 734,
    compressedLoopContextTokens: 189,
    savedTokens: 545,
    savingsPercent: 74.3,
    meetsFortyPercentTarget: true,
  });
  assert.deepEqual(result.rawExposure, {
    rawFullTextHiddenFromCompressedContext: true,
    meetingHistorySummaryOnly: true,
    rawFullTextRetainedOutsideLoopContext: true,
  });
  assert.deepEqual(
    result.preservation.requiredElements.map((element) => [element.name, element.available, element.source]),
    [
      ["user_request", true, "compressedLoopContext.requestSummary"],
      ["task_breakdown", true, "meetingHistory[].summary"],
      ["role_routes", true, "meetingHistory[].role"],
      ["openclaw_outputs", true, "compressedLoopContext.latestOpenClawSummary"],
      ["hermes_reviews", true, "compressedLoopContext.latestHermesSummary"],
      ["meeting_history", true, "meetingHistory[]"],
      ["final_synthesis", true, "meetingHistory[kind=final_synthesis].summary"],
      ["escalation", true, "compressedLoopContext.escalationReasons"],
    ],
  );
  assert.equal(result.preservation.allRequiredElementsAvailable, true);

  const representative = buildRepresentativeLoopContextInput();
  for (const rawTurn of representative.turns.map((turn) => turn.content)) {
    assert.equal(result.preservation.requiredElements.some((element) => element.evidence.includes(rawTurn)), false);
  }
});

test("compression verification fails when a required preservation element is unavailable", () => {
  const representative = buildRepresentativeLoopContextInput();
  const withoutHermes = representative.turns.filter((turn) => turn.role !== "hermes-reviewer");
  const result = verifyRepresentativeLoopContextCompression(withoutHermes);

  assert.equal(result.status, "failed");
  assert.equal(result.preservation.allRequiredElementsAvailable, false);
  assert.deepEqual(
    result.preservation.requiredElements
      .filter((element) => !element.available)
      .map((element) => element.name),
    ["role_routes", "hermes_reviews"],
  );
});

test("compression verification command returns stable observable output", () => {
  const result = executeLoopContextCompressionVerificationCommand([]);
  const parsed = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(parsed.command, "ai-agent check-loop-context-compression-verification");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.comparison.meetsFortyPercentTarget, true);
  assert.equal(parsed.rawExposure.rawFullTextHiddenFromCompressedContext, true);
  assert.equal(parsed.preservation.allRequiredElementsAvailable, true);
});

test("compression verification command returns stable invalid-input errors", () => {
  const result = executeLoopContextCompressionVerificationCommand(["--unexpected"]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "unexpected argument: --unexpected",
  });
});
