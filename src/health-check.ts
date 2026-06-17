import { resolve } from "node:path";
import { measureCurrentTokenBaseline } from "./token-baseline.ts";

export interface HealthCheckOutput {
  schemaVersion: "health-check.v1";
  status: "ok";
  baseline: {
    method: "deterministic-local-estimate-v1";
    turnCount: number;
    rawFullTextTokens: number;
    exposedLoopContextTokens: number;
    compressedLoopContextTokens: number;
  };
  projectedSavingsPercentage: number;
  strategyArtifactPath: string;
}

export function buildHealthCheckOutput(input: {
  projectRoot?: string;
  strategyArtifactPath?: string;
} = {}): HealthCheckOutput {
  const baseline = measureCurrentTokenBaseline();
  const projectRoot = input.projectRoot ?? process.cwd();
  const strategyArtifactPath = input.strategyArtifactPath ?? "docs/token-reduction-strategy.md";

  return {
    schemaVersion: "health-check.v1",
    status: "ok",
    baseline: {
      method: baseline.method,
      turnCount: baseline.turnCount,
      rawFullTextTokens: baseline.rawFullTextTokens,
      exposedLoopContextTokens: baseline.exposedLoopContextTokens,
      compressedLoopContextTokens: baseline.compressedLoopContextTokens,
    },
    projectedSavingsPercentage: baseline.compressedReductionPercent,
    strategyArtifactPath: resolve(projectRoot, strategyArtifactPath),
  };
}
