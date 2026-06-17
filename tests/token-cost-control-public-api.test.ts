import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import {
  buildCompressedLoopContextArtifact,
  buildDefaultTokenStrategy,
  buildRepresentativeLoopContextInput,
  buildTokenReductionStrategyArtifact,
  estimateCompressedContextSavings,
  estimateRepresentativeCompressedContextSavings,
  estimateTokenCount,
  measureCurrentTokenBaseline,
  measureTurnTokenBaseline,
  renderTokenReductionStrategyMarkdown,
  summarizeMeetingHistory,
  summarizeMeetingTurn,
  writeTokenReductionStrategyArtifact,
} from "../src/index.ts";
import type { TurnRecord } from "../src/index.ts";

test("token-cost control public functions produce stable success-path output", () => {
  const representative = buildRepresentativeLoopContextInput();
  const baseline = measureCurrentTokenBaseline(representative);
  const turns: TurnRecord[] = representative.turns.map((turn, index) => ({
    id: `turn-${index + 1}`,
    taskId: "task-token-cost-control-public-api",
    round: turn.round,
    role: turn.role,
    kind: turn.kind,
    content: turn.content,
    visibleSummary: turn.visibleSummary,
    createdAt: "2026-06-05T00:00:00.000Z",
  }));
  const turnBaseline = measureTurnTokenBaseline(turns);
  const directSavings = estimateCompressedContextSavings({
    baselineContext: representative.turns.map((turn) => turn.content),
    proposedCompressedContext: representative.compressedContext ?? "",
  });
  const representativeSavings = estimateRepresentativeCompressedContextSavings(representative);
  const tokenStrategy = buildDefaultTokenStrategy();
  const strategyArtifact = buildTokenReductionStrategyArtifact();
  const markdown = renderTokenReductionStrategyMarkdown(strategyArtifact);

  assert.deepEqual(
    {
      baseline: {
        method: baseline.method,
        turnCount: baseline.turnCount,
        rawFullTextTokens: baseline.rawFullTextTokens,
        exposedLoopContextTokens: baseline.exposedLoopContextTokens,
        compressedLoopContextTokens: baseline.compressedLoopContextTokens,
        exposedReductionPercent: baseline.exposedReductionPercent,
        compressedReductionPercent: baseline.compressedReductionPercent,
        targetReductionThresholds: baseline.targetReductionThresholds,
      },
      turnBaseline: {
        rawFullTextTokens: turnBaseline.rawFullTextTokens,
        exposedLoopContextTokens: turnBaseline.exposedLoopContextTokens,
        compressedLoopContextTokens: turnBaseline.compressedLoopContextTokens,
        compressedReductionPercent: turnBaseline.compressedReductionPercent,
        targetReductionThresholds: turnBaseline.targetReductionThresholds,
      },
      directSavings,
      representativeSavings,
      tokenCountSamples: {
        empty: estimateTokenCount(""),
        english: estimateTokenCount("OpenClaw final synthesis"),
        koreanMixed: estimateTokenCount("Hermes 리뷰 완료."),
      },
      strategy: tokenStrategy,
      artifact: {
        schemaVersion: strategyArtifact.schemaVersion,
        targetSavings: strategyArtifact.targetSavings,
        validationSections: strategyArtifact.validationSections,
      },
      markdownHead: markdown.split("\n").slice(0, 8),
    },
    {
      baseline: {
        method: "deterministic-local-estimate-v1",
        turnCount: 7,
        rawFullTextTokens: 734,
        exposedLoopContextTokens: 260,
        compressedLoopContextTokens: 67,
        exposedReductionPercent: 64.6,
        compressedReductionPercent: 90.9,
        targetReductionThresholds: [
          {
            reductionPercent: 40,
            maxAllowedTokens: 440,
            minimumSavedTokens: 294,
          },
          {
            reductionPercent: 50,
            maxAllowedTokens: 367,
            minimumSavedTokens: 367,
          },
        ],
      },
      turnBaseline: {
        rawFullTextTokens: 734,
        exposedLoopContextTokens: 260,
        compressedLoopContextTokens: 119,
        compressedReductionPercent: 83.8,
        targetReductionThresholds: [
          {
            reductionPercent: 40,
            maxAllowedTokens: 440,
            minimumSavedTokens: 294,
          },
          {
            reductionPercent: 50,
            maxAllowedTokens: 367,
            minimumSavedTokens: 367,
          },
        ],
      },
      directSavings: {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 734,
        proposedCompressedTokens: 67,
        savedTokens: 667,
        savingsPercent: 90.9,
        meetsFortyPercentTarget: true,
      },
      representativeSavings: {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 734,
        proposedCompressedTokens: 67,
        savedTokens: 667,
        savingsPercent: 90.9,
        meetsFortyPercentTarget: true,
      },
      tokenCountSamples: {
        empty: 0,
        english: 7,
        koreanMixed: 5,
      },
      strategy: {
        rawStorage: "SQLite turns.content stores full model outputs and reviewer requests for audit/history.",
        exposedLoopContext: "Discord thread messages and loop prompts use bounded summaries from turns.visibleSummary.",
        compressionPolicy:
          "Each round carries request summary, latest draft summary, latest Hermes verdict, accepted feedback, rejected feedback, and escalation reasons instead of replaying full raw text.",
        targetReduction: "Reduce exposed loop tokens by at least 40-50% compared with replaying every full turn.",
      },
      artifact: {
        schemaVersion: "token-reduction-strategy.v1",
        targetSavings: {
          minimumPercent: 40,
          maximumPercent: 50,
          statement: "Reduce exposed loop tokens by at least 40-50% compared with replaying every full turn.",
        },
        validationSections: [
          "40-50% Savings Target",
          "Original Text Retention Policy",
          "Exposed Context Summary Separation",
          "Compressed Context Approach",
          "Baseline Measurement",
        ],
      },
      markdownHead: [
        "# Token Reduction Strategy",
        "",
        "Schema: `token-reduction-strategy.v1`",
        "",
        "## 40-50% Savings Target",
        "",
        "Reduce exposed loop tokens by at least 40-50% compared with replaying every full turn.",
        "",
      ],
    },
  );
});

test("compression public helpers produce summary-only loop context without raw content", () => {
  const longRawDraft =
    "OpenClaw draft: raw implementation transcript contains private notes, detailed rejected options, and debug traces that must stay outside the exposed loop context.";
  const oneTurnSummary = summarizeMeetingTurn(
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: longRawDraft,
    },
    72,
  );
  const meetingHistory = summarizeMeetingHistory(
    [
      {
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: longRawDraft,
      },
      {
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        content: "Hermes review: agree_with_changes. Keep raw storage separate and pass compressed context onward.",
      },
    ],
    72,
  );
  const compressed = buildCompressedLoopContextArtifact({
    userRequestSummary: "Build the meeting loop while reducing exposed tokens.",
    meetingTurns: meetingHistory,
    acceptedFeedback: ["Keep raw storage separate from visible summaries."],
    rejectedFeedback: ["Replay private notes in each review request."],
    escalationReasons: ["User decision required when Hermes returns needs_user_decision."],
  });

  assert.deepEqual(oneTurnSummary, {
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    summary: "OpenClaw draft: raw implementation transcript contains private notes, d…",
  });
  assert.equal(meetingHistory.every((turn) => !Object.hasOwn(turn, "content")), true);
  assert.deepEqual(compressed, {
    schemaVersion: "compressed-loop-context.v1",
    requestSummary: "Build the meeting loop while reducing exposed tokens.",
    latestOpenClawSummary: "OpenClaw draft: raw implementation transcript contains private notes, d…",
    latestHermesSummary: "Hermes review: agree_with_changes. Keep raw storage separate and pass c…",
    latestHermesVerdict: "agree_with_changes",
    acceptedFeedback: ["Keep raw storage separate from visible summaries."],
    rejectedFeedback: ["Replay private notes in each review request."],
    escalationReasons: ["User decision required when Hermes returns needs_user_decision."],
    content: [
      "Compressed loop context",
      "- request_summary: Build the meeting loop while reducing exposed tokens.",
      "- latest_openclaw: OpenClaw draft: raw implementation transcript contains private notes, d…",
      "- latest_hermes_verdict: agree_with_changes",
      "- latest_hermes: Hermes review: agree_with_changes. Keep raw storage separate and pass c…",
      "- accepted_feedback: Keep raw storage separate from visible summaries.",
      "- rejected_feedback: Replay private notes in each review request.",
      "- escalation_reasons: User decision required when Hermes returns needs_user_decision.",
    ].join("\n"),
  });
  assert.equal(compressed.content.includes("debug traces"), false);
});

test("token reduction strategy writer public function emits stable artifact file", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-cost-public-api-"));
  try {
    const written = writeTokenReductionStrategyArtifact({
      projectRoot: root,
      outputPath: "artifacts/token-reduction-strategy.md",
    });
    const content = readFileSync(written.path, "utf8");

    assert.deepEqual(
      {
        basename: basename(written.path),
        exists: existsSync(written.path),
        schemaVersion: written.artifact.schemaVersion,
        target: `${written.artifact.targetSavings.minimumPercent}-${written.artifact.targetSavings.maximumPercent}`,
        markdownMatchesFile: written.markdown === content,
        requiredHeadingsPresent: written.artifact.validationSections.every((section) =>
          content.includes(`## ${section}`),
        ),
      },
      {
        basename: "token-reduction-strategy.md",
        exists: true,
        schemaVersion: "token-reduction-strategy.v1",
        target: "40-50",
        markdownMatchesFile: true,
        requiredHeadingsPresent: true,
      },
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
