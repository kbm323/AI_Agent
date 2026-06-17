/**
 * Configuration management for the AI_Agent meeting system.
 *
 * This module mirrors the Python ``shared.config`` module and provides
 * TypeScript-equivalent configuration types and the ``load_config``
 * loader that is importable from the documented ``ai-agent`` package path.
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

// ---------------------------------------------------------------------------
// Configuration types
// ---------------------------------------------------------------------------

export interface CompressionConfig {
  maxVisibleSummaryChars: number;
  maxCompressedSummaryChars: number;
  maxFeedbackSummaryChars: number;
}

export interface TokenBudgetConfig {
  targetMinimumSavingsPercent: number;
  targetMaximumSavingsPercent: number;
  tokenEstimationMethod: string;
  charactersPerToken: number;
}

export interface SharedConfig {
  compression: CompressionConfig;
  tokenBudget: TokenBudgetConfig;
  schemaVersion: string;
}

// ---------------------------------------------------------------------------
// Defaults — kept in sync with src/shared/config.py
// ---------------------------------------------------------------------------

const defaultCompression: CompressionConfig = {
  maxVisibleSummaryChars: 1200,
  maxCompressedSummaryChars: 240,
  maxFeedbackSummaryChars: 160,
};

const defaultTokenBudget: TokenBudgetConfig = {
  targetMinimumSavingsPercent: 40,
  targetMaximumSavingsPercent: 50,
  tokenEstimationMethod: "deterministic-local-estimate-v1",
  charactersPerToken: 3.85,
};

export const defaultConfig: SharedConfig = {
  compression: defaultCompression,
  tokenBudget: defaultTokenBudget,
  schemaVersion: "shared-infrastructure.v1",
};

// ---------------------------------------------------------------------------
// load_config
// ---------------------------------------------------------------------------

/**
 * Load configuration from a JSON file or return the default configuration.
 *
 * When ``path`` is provided and the file exists, reads the JSON file and
 * merges its values on top of the default configuration.  Missing keys
 * in the file fall back to the defaults.
 *
 * When ``path`` is omitted or the file does not exist, the default
 * configuration is returned unchanged.
 */
export function load_config(path?: string | null): SharedConfig {
  if (!path) {
    return { ...defaultConfig };
  }

  const resolved = resolve(path.replace(/^~/, process.env.HOME ?? "~"));
  if (!existsSync(resolved)) {
    return { ...defaultConfig };
  }

  const raw: Record<string, unknown> = JSON.parse(
    readFileSync(resolved, "utf8"),
  );

  const compressionRaw = (raw.compression ?? {}) as Record<string, unknown>;
  const tokenBudgetRaw = (raw.tokenBudget ?? {}) as Record<string, unknown>;

  return {
    compression: {
      maxVisibleSummaryChars:
        (compressionRaw.max_visible_summary_chars ??
          compressionRaw.maxVisibleSummaryChars ??
          defaultCompression.maxVisibleSummaryChars) as number,
      maxCompressedSummaryChars:
        (compressionRaw.max_compressed_summary_chars ??
          compressionRaw.maxCompressedSummaryChars ??
          defaultCompression.maxCompressedSummaryChars) as number,
      maxFeedbackSummaryChars:
        (compressionRaw.max_feedback_summary_chars ??
          compressionRaw.maxFeedbackSummaryChars ??
          defaultCompression.maxFeedbackSummaryChars) as number,
    },
    tokenBudget: {
      targetMinimumSavingsPercent:
        (tokenBudgetRaw.target_minimum_savings_percent ??
          tokenBudgetRaw.targetMinimumSavingsPercent ??
          defaultTokenBudget.targetMinimumSavingsPercent) as number,
      targetMaximumSavingsPercent:
        (tokenBudgetRaw.target_maximum_savings_percent ??
          tokenBudgetRaw.targetMaximumSavingsPercent ??
          defaultTokenBudget.targetMaximumSavingsPercent) as number,
      tokenEstimationMethod:
        (tokenBudgetRaw.token_estimation_method ??
          tokenBudgetRaw.tokenEstimationMethod ??
          defaultTokenBudget.tokenEstimationMethod) as string,
      charactersPerToken:
        (tokenBudgetRaw.characters_per_token ??
          tokenBudgetRaw.charactersPerToken ??
          defaultTokenBudget.charactersPerToken) as number,
    },
    schemaVersion:
      (raw.schema_version ??
        raw.schemaVersion ??
        defaultConfig.schemaVersion) as string,
  };
}
