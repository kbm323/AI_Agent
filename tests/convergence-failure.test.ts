import test from "node:test";
import assert from "node:assert/strict";
import { detectConvergenceFailure } from "../src/convergence-failure.ts";

test("detectConvergenceFailure identifies failed convergence at the iteration threshold", () => {
  assert.deepEqual(
    detectConvergenceFailure({
      iteration: 3,
      maxIterations: 3,
      converged: false,
      reviewerVerdict: "disagree",
    }),
    {
      failed: true,
      reason: "max_rounds_without_agreement",
      threshold: {
        kind: "iteration",
        observed: 3,
        limit: 3,
      },
      iterationExhausted: true,
      retryExhausted: false,
    },
  );
});

test("detectConvergenceFailure identifies failed convergence at the retry threshold", () => {
  assert.deepEqual(
    detectConvergenceFailure({
      iteration: 2,
      maxIterations: 5,
      retryCount: 2,
      maxRetries: 2,
      converged: false,
      reviewerVerdict: "disagree",
    }),
    {
      failed: true,
      reason: "max_retries_without_convergence",
      threshold: {
        kind: "retry",
        observed: 2,
        limit: 2,
      },
      iterationExhausted: false,
      retryExhausted: true,
    },
  );
});

test("detectConvergenceFailure does not fail before thresholds or after convergence", () => {
  assert.deepEqual(
    detectConvergenceFailure({
      iteration: 2,
      maxIterations: 3,
      retryCount: 1,
      maxRetries: 2,
      converged: false,
      reviewerVerdict: "disagree",
    }),
    {
      failed: false,
      iterationExhausted: false,
      retryExhausted: false,
    },
  );

  assert.deepEqual(
    detectConvergenceFailure({
      iteration: 3,
      maxIterations: 3,
      retryCount: 2,
      maxRetries: 2,
      converged: true,
      reviewerVerdict: "agree",
    }),
    {
      failed: false,
      iterationExhausted: false,
      retryExhausted: false,
    },
  );
});

test("detectConvergenceFailure rejects malformed threshold state", () => {
  assert.throws(
    () =>
      detectConvergenceFailure({
        iteration: 0,
        maxIterations: 0,
        converged: false,
      }),
    /maxIterations must be a positive integer/,
  );

  assert.throws(
    () =>
      detectConvergenceFailure({
        iteration: -1,
        maxIterations: 2,
        converged: false,
      }),
    /iteration must be a non-negative integer/,
  );
});
