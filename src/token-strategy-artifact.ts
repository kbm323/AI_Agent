import { dirname, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import { buildDefaultTokenStrategy } from "./planning.ts";
import { measureCurrentTokenBaseline } from "./token-baseline.ts";
import type { TokenBaselineMeasurement } from "./token-baseline.ts";

export interface TokenReductionStrategyArtifact {
  schemaVersion: "token-reduction-strategy.v1";
  targetSavings: {
    minimumPercent: 40;
    maximumPercent: 50;
    statement: string;
  };
  baseline: TokenBaselineMeasurement;
  originalTextRetentionPolicy: string;
  exposedContextSummarySeparation: string;
  compressedContextApproach: string;
  validationSections: string[];
}

export interface WrittenTokenReductionStrategyArtifact {
  path: string;
  artifact: TokenReductionStrategyArtifact;
  markdown: string;
}

export function buildTokenReductionStrategyArtifact(): TokenReductionStrategyArtifact {
  const tokenStrategy = buildDefaultTokenStrategy();
  const baseline = measureCurrentTokenBaseline();

  return {
    schemaVersion: "token-reduction-strategy.v1",
    targetSavings: {
      minimumPercent: 40,
      maximumPercent: 50,
      statement: tokenStrategy.targetReduction,
    },
    baseline,
    originalTextRetentionPolicy:
      "Persist complete user requests, OpenClaw drafts, Hermes review requests, Hermes reviews, final synthesis, and escalation messages in raw turn storage for audit and replay.",
    exposedContextSummarySeparation:
      "Expose only bounded visible summaries to loop prompts, meeting history, and user-facing thread output; raw full text is not replayed unless an explicit audit/debug path requests it.",
    compressedContextApproach: tokenStrategy.compressionPolicy,
    validationSections: [
      "40-50% Savings Target",
      "Original Text Retention Policy",
      "Exposed Context Summary Separation",
      "Compressed Context Approach",
      "Baseline Measurement",
    ],
  };
}

export function renderTokenReductionStrategyMarkdown(artifact = buildTokenReductionStrategyArtifact()): string {
  return [
    "# Token Reduction Strategy",
    "",
    `Schema: \`${artifact.schemaVersion}\``,
    "",
    "## 40-50% Savings Target",
    "",
    artifact.targetSavings.statement,
    "",
    `Target band: ${artifact.targetSavings.minimumPercent}-${artifact.targetSavings.maximumPercent}% reduction from raw full-history replay.`,
    "",
    "## Original Text Retention Policy",
    "",
    artifact.originalTextRetentionPolicy,
    "",
    "Raw storage remains the source of truth. Summaries are derived context, not replacements for stored meeting turns.",
    "",
    "## Exposed Context Summary Separation",
    "",
    artifact.exposedContextSummarySeparation,
    "",
    "The loop context boundary is `turns.visibleSummary`; raw `turns.content` stays behind the persistence/audit boundary.",
    "",
    "## Compressed Context Approach",
    "",
    artifact.compressedContextApproach,
    "",
    "The compressed loop context should carry request summary, latest OpenClaw draft summary, latest Hermes verdict, accepted feedback, rejected feedback, and escalation reasons.",
    "",
    "## Baseline Measurement",
    "",
    `Method: \`${artifact.baseline.method}\``,
    "",
    `Representative turns: ${artifact.baseline.turnCount}`,
    "",
    `Raw full-text tokens: ${artifact.baseline.rawFullTextTokens}`,
    "",
    `Exposed summary tokens: ${artifact.baseline.exposedLoopContextTokens} (${artifact.baseline.exposedReductionPercent}% reduction)`,
    "",
    `Compressed context tokens: ${artifact.baseline.compressedLoopContextTokens} (${artifact.baseline.compressedReductionPercent}% reduction)`,
    "",
    "Target thresholds:",
    "",
    ...artifact.baseline.targetReductionThresholds.map(
      (threshold) =>
        `- ${threshold.reductionPercent}% reduction: expose at most ${threshold.maxAllowedTokens} tokens, saving at least ${threshold.minimumSavedTokens} tokens`,
    ),
    "",
    "## Validation Sections",
    "",
    ...artifact.validationSections.map((section) => `- ${section}`),
    "",
  ].join("\n");
}

export function writeTokenReductionStrategyArtifact(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): WrittenTokenReductionStrategyArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? "docs/token-reduction-strategy.md";
  const resolvedPath = resolve(projectRoot, outputPath);
  const artifact = buildTokenReductionStrategyArtifact();
  const markdown = renderTokenReductionStrategyMarkdown(artifact);

  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, markdown);

  return {
    path: resolvedPath,
    artifact,
    markdown,
  };
}
