import type { ReviewerVerdict } from "./types.ts";

export type ConvergenceFailureThresholdKind = "iteration" | "retry";

export interface ConvergenceFailureThreshold {
  kind: ConvergenceFailureThresholdKind;
  observed: number;
  limit: number;
}

export interface ConvergenceIterationState {
  iteration: number;
  maxIterations: number;
  retryCount?: number;
  maxRetries?: number;
  converged: boolean;
  reviewerVerdict?: ReviewerVerdict;
}

export interface ConvergenceFailureDetection {
  failed: boolean;
  reason?: "max_rounds_without_agreement" | "max_retries_without_convergence";
  threshold?: ConvergenceFailureThreshold;
  iterationExhausted: boolean;
  retryExhausted: boolean;
}

export function detectConvergenceFailure(state: ConvergenceIterationState): ConvergenceFailureDetection {
  const iteration = normalizeNonNegativeInteger(state.iteration, "iteration");
  const maxIterations = normalizePositiveInteger(state.maxIterations, "maxIterations");
  const retryCount = normalizeNonNegativeInteger(state.retryCount ?? 0, "retryCount");
  const maxRetries =
    state.maxRetries === undefined ? undefined : normalizePositiveInteger(state.maxRetries, "maxRetries");

  if (state.converged || state.reviewerVerdict === "agree" || state.reviewerVerdict === "agree_with_changes") {
    return {
      failed: false,
      iterationExhausted: false,
      retryExhausted: false,
    };
  }

  const retryExhausted = maxRetries !== undefined && retryCount >= maxRetries;
  if (retryExhausted) {
    return {
      failed: true,
      reason: "max_retries_without_convergence",
      threshold: {
        kind: "retry",
        observed: retryCount,
        limit: maxRetries,
      },
      iterationExhausted: iteration >= maxIterations,
      retryExhausted,
    };
  }

  const iterationExhausted = iteration >= maxIterations;
  if (iterationExhausted) {
    return {
      failed: true,
      reason: "max_rounds_without_agreement",
      threshold: {
        kind: "iteration",
        observed: iteration,
        limit: maxIterations,
      },
      iterationExhausted,
      retryExhausted,
    };
  }

  return {
    failed: false,
    iterationExhausted,
    retryExhausted,
  };
}

function normalizePositiveInteger(value: number, field: string): number {
  if (!Number.isInteger(value) || value < 1) {
    throw new TypeError(`${field} must be a positive integer`);
  }
  return value;
}

function normalizeNonNegativeInteger(value: number, field: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError(`${field} must be a non-negative integer`);
  }
  return value;
}
