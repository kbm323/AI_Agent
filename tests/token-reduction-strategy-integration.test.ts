import test from "node:test";
import assert from "node:assert/strict";
import {
  accountTokenReduction,
  buildRepresentativeLoopContextInput,
  buildTokenReductionStrategyArtifact,
  estimateCompressedContextSavings,
  generateRepresentativeCompressedLoopContextArtifact,
  retrieveLoopVisibleContext,
} from "../src/index.ts";

test("combined token reduction strategy exceeds 40-50 percent target for representative workflow input", () => {
  const representative = buildRepresentativeLoopContextInput();
  const strategy = buildTokenReductionStrategyArtifact();
  const generated = generateRepresentativeCompressedLoopContextArtifact(representative);
  const retrieval = retrieveLoopVisibleContext({
    userRequestSummary: generated.artifact.requestSummary,
    turns: representative.turns,
    acceptedFeedback: generated.artifact.acceptedFeedback,
    rejectedFeedback: generated.artifact.rejectedFeedback,
    escalationReasons: generated.artifact.escalationReasons,
  });
  const savings = estimateCompressedContextSavings({
    baselineContext: representative.turns.map((turn) => turn.content),
    proposedCompressedContext: retrieval.compressedLoopContext.content,
  });
  const accounting = accountTokenReduction({
    baseline: savings.baselineTokens,
    optimized: savings.proposedCompressedTokens,
  });

  assert.equal(strategy.targetSavings.minimumPercent, 40);
  assert.equal(strategy.targetSavings.maximumPercent, 50);
  assert.equal(strategy.baseline.rawFullTextTokens, savings.baselineTokens);
  assert.equal(retrieval.rawOriginalTextRetained, true);
  assert.equal(retrieval.rawTurnCount, representative.turns.length);
  assert.equal(
    retrieval.meetingHistory.every((turn) => !Object.hasOwn(turn, "content")),
    true,
  );
  assert.equal(
    representative.turns.every((turn) => !retrieval.compressedLoopContext.content.includes(turn.content)),
    true,
  );
  assert.deepEqual(
    {
      baselineTokens: savings.baselineTokens,
      optimizedTokens: savings.proposedCompressedTokens,
      savedTokens: savings.savedTokens,
      savingsPercent: savings.savingsPercent,
      meetsFortyPercentTarget: savings.meetsFortyPercentTarget,
      meetsFiftyPercentTarget: accounting.meetsFiftyPercentTarget,
    },
    {
      baselineTokens: 734,
      optimizedTokens: 189,
      savedTokens: 545,
      savingsPercent: 74.3,
      meetsFortyPercentTarget: true,
      meetsFiftyPercentTarget: true,
    },
  );
  assert.equal(savings.savingsPercent >= strategy.targetSavings.minimumPercent, true);
  assert.equal(savings.savingsPercent >= strategy.targetSavings.maximumPercent, true);
});
