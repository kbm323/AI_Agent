import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeDryRunCommand } from "../scripts/dry-run.ts";
import {
  formatStableJsonForComparison,
  normalizeCapturedResponse,
  normalizeCapturedStream,
  normalizeJsonArrayOrder,
} from "../src/output-normalization.ts";

test("captured dry-run stdout normalization canonicalizes volatile run metadata", async () => {
  const first = await executeDryRunCommand([
    "--request",
    "뮤직비디오 오프닝 아이디어를 회의해줘.",
    "--input-id",
    "capture-a",
    "--run-id",
    "run-a",
  ]);
  const second = await executeDryRunCommand([
    "--request",
    "뮤직비디오 오프닝 아이디어를 회의해줘.",
    "--input-id",
    "capture-b",
    "--run-id",
    "run-b",
  ]);

  assert.equal(first.exitCode, 0);
  assert.equal(second.exitCode, 0);
  assert.notEqual(first.stdout, second.stdout);

  const normalizedFirst = normalizeCapturedResponse(first);
  const normalizedSecond = normalizeCapturedResponse(second);

  assert.deepEqual(normalizedFirst, normalizedSecond);
  assert.deepEqual(readJsonPath(normalizedFirst.stdout, "metadata.executionId"), "<execution-id>");
  assert.deepEqual(readJsonPath(normalizedFirst.stdout, "metadata.inputIdentifier"), "<input-identifier>");
  assert.deepEqual(readJsonPath(normalizedFirst.stdout, "metadata.version.runtime.version"), "<runtime-version>");
  assert.deepEqual(readJsonPath(normalizedFirst.stdout, "threadId"), "<thread-id>");
  assert.deepEqual(readJsonPath(normalizedFirst.stdout, "status"), "finalized");
});

test("captured dry-run prior-review normalization canonicalizes temporary artifact paths", async () => {
  const firstRoot = mkdtempSync(join(tmpdir(), "ai-agent-normalize-a-"));
  const secondRoot = mkdtempSync(join(tmpdir(), "ai-agent-normalize-b-"));
  try {
    const firstArtifact = writePriorReviewFixture(firstRoot);
    const secondArtifact = writePriorReviewFixture(secondRoot);

    const first = await executeDryRunCommand([
      "--request",
      "뮤직비디오 오프닝 아이디어를 회의해줘.",
      "--prior-review-artifact",
      firstArtifact,
    ]);
    const second = await executeDryRunCommand([
      "--request",
      "뮤직비디오 오프닝 아이디어를 회의해줘.",
      "--prior-review-artifact",
      secondArtifact,
    ]);

    assert.equal(first.exitCode, 0);
    assert.equal(second.exitCode, 0);
    assert.notEqual(first.stdout, second.stdout);

    const normalizedFirst = normalizeCapturedResponse(first);
    const normalizedSecond = normalizeCapturedResponse(second);

    assert.deepEqual(normalizedFirst, normalizedSecond);
    assert.deepEqual(readJsonPath(normalizedFirst.stdout, "priorReview.artifact.path"), "<path>");
    assert.deepEqual(readJsonPath(normalizedFirst.stdout, "priorReview.decisionBasis.priorReviewArtifactPath"), "<prior-review-artifact-path>");
    assert.deepEqual(readJsonPath(normalizedFirst.stdout, "priorReview.runnable.dryRunCommand"), "<dry-run-command>");
  } finally {
    rmSync(firstRoot, { recursive: true, force: true });
    rmSync(secondRoot, { recursive: true, force: true });
  }
});

test("captured invalid-input stderr normalization preserves stable failure semantics", async () => {
  const result = await executeDryRunCommand(["--request", "   "]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");

  const normalized = normalizeCapturedResponse(result);

  assert.deepEqual(normalized, {
    exitCode: 2,
    stdout: {
      parseableJson: false,
      text: "",
    },
    stderr: {
      parseableJson: true,
      json: {
        error: "invalid_input",
        message: "userRequest must be a non-empty string",
      },
    },
  });
});

test("non-JSON stream normalization canonicalizes line endings and volatile text tokens", () => {
  assert.deepEqual(normalizeCapturedStream("run:0123456789abcdef\r\n"), {
    parseableJson: false,
    text: "<execution-id>",
  });
});

test("runtime timestamp normalization canonicalizes timestamp-dependent captured data", () => {
  const first = normalizeCapturedResponse({
    exitCode: 0,
    stdout: JSON.stringify({ createdAt: "2026-06-05T01:02:03.000Z", nested: { updatedAt: "2026-06-05T01:03:04.000Z" } }),
    stderr: "2026-06-05T01:04:05.000Z\n",
  });
  const second = normalizeCapturedResponse({
    exitCode: 0,
    stdout: JSON.stringify({ createdAt: "2026-06-05T09:08:07.000Z", nested: { updatedAt: "2026-06-05T09:09:09.000Z" } }),
    stderr: "2026-06-05T09:10:11.000Z\n",
  });

  assert.deepEqual(first, second);
  assert.deepEqual(readJsonPath(first.stdout, "createdAt"), "<runtime-timestamp>");
  assert.deepEqual(readJsonPath(first.stdout, "nested.updatedAt"), "<runtime-timestamp>");
  assert.deepEqual(first.stderr, { parseableJson: false, text: "<runtime-timestamp>" });
});

test("non-deterministic artifact array ordering is normalized in one helper", () => {
  const first = normalizeCapturedStream(
    JSON.stringify({
      artifactEvidence: [
        { id: "typecheck", valid: true, path: "/tmp/ai-agent-a/typecheck.json" },
        { id: "verification-output", valid: true, path: "/tmp/ai-agent-a/verification.json" },
      ],
      files: [
        { relativePath: "src/zeta.ts", kind: "source" },
        { relativePath: "src/alpha.ts", kind: "source" },
      ],
    }),
  );
  const second = normalizeCapturedStream(
    JSON.stringify({
      files: [
        { relativePath: "src/alpha.ts", kind: "source" },
        { relativePath: "src/zeta.ts", kind: "source" },
      ],
      artifactEvidence: [
        { id: "verification-output", path: "/tmp/ai-agent-b/verification.json", valid: true },
        { id: "typecheck", path: "/tmp/ai-agent-b/typecheck.json", valid: true },
      ],
    }),
  );

  assert.deepEqual(first, second);
  assert.deepEqual(readJsonPath(first, "artifactEvidence"), [
    { id: "typecheck", path: "<path>", valid: true },
    { id: "verification-output", path: "<path>", valid: true },
  ]);
  assert.deepEqual(readJsonPath(first, "files"), [
    { kind: "source", relativePath: "src/alpha.ts" },
    { kind: "source", relativePath: "src/zeta.ts" },
  ]);
});

test("stable JSON comparison formatter canonicalizes snapshot-friendly object output", () => {
  const first = formatStableJsonForComparison({
    meetingTurns: [
      { order: 2, id: "turn-002", summary: "Hermes review" },
      { order: 1, id: "turn-001", summary: "OpenClaw draft" },
    ],
    artifactEvidence: [
      { valid: true, path: "/tmp/ai-agent-a/verification.json", id: "verification-output" },
      { valid: true, path: "/tmp/ai-agent-a/typecheck.json", id: "typecheck" },
    ],
    metadata: {
      executionId: "run:0123456789abcdef",
      createdAt: "2026-06-05T01:02:03.000Z",
    },
  });
  const second = formatStableJsonForComparison({
    metadata: {
      createdAt: "2026-06-05T09:08:07.000Z",
      executionId: "run:fedcba9876543210",
    },
    artifactEvidence: [
      { id: "typecheck", path: "/tmp/ai-agent-b/typecheck.json", valid: true },
      { id: "verification-output", path: "/tmp/ai-agent-b/verification.json", valid: true },
    ],
    meetingTurns: [
      { summary: "Hermes review", id: "turn-002", order: 2 },
      { summary: "OpenClaw draft", id: "turn-001", order: 1 },
    ],
  });

  assert.equal(first, second);
  assert.equal(first.endsWith("\n"), true);
  assert.equal(first.includes('"path": "<path>"'), true);
  assert.equal(first.indexOf('"id": "turn-002"') < first.indexOf('"id": "turn-001"'), true);
});

test("stable output comparison detects semantic changes after normalizing volatile capture fields", () => {
  const firstStableOutput = formatStableJsonForComparison(
    normalizeCapturedResponse({
      exitCode: 0,
      stdout: JSON.stringify({
        status: "finalized",
        threadId: "thread-123",
        metadata: {
          executionId: "run:0123456789abcdef",
          createdAt: "2026-06-05T01:02:03.000Z",
        },
        artifactEvidence: [
          { id: "verification-output", path: "/tmp/ai-agent-a/verification.json", valid: true },
          { id: "typecheck", path: "/tmp/ai-agent-a/typecheck.json", valid: true },
        ],
      }),
      stderr: "",
    }),
  );
  const secondStableOutput = formatStableJsonForComparison(
    normalizeCapturedResponse({
      exitCode: 0,
      stdout: JSON.stringify({
        artifactEvidence: [
          { valid: true, path: "/tmp/ai-agent-b/typecheck.json", id: "typecheck" },
          { valid: true, path: "/tmp/ai-agent-b/verification.json", id: "verification-output" },
        ],
        metadata: {
          createdAt: "2026-06-05T09:08:07.000Z",
          executionId: "run:fedcba9876543210",
        },
        threadId: "thread-456",
        status: "finalized",
      }),
      stderr: "",
    }),
  );
  const changedStableOutput = formatStableJsonForComparison(
    normalizeCapturedResponse({
      exitCode: 0,
      stdout: JSON.stringify({
        status: "waiting_for_user",
        threadId: "thread-789",
        metadata: {
          executionId: "run:aaaaaaaaaaaaaaaa",
          createdAt: "2026-06-05T11:12:13.000Z",
        },
        artifactEvidence: [
          { id: "typecheck", path: "/tmp/ai-agent-c/typecheck.json", valid: true },
          { id: "verification-output", path: "/tmp/ai-agent-c/verification.json", valid: true },
        ],
      }),
      stderr: "",
    }),
  );

  assert.equal(secondStableOutput, firstStableOutput);
  assert.notEqual(changedStableOutput, firstStableOutput);
});

test("array ordering helper preserves ordered sequences without stable unordered keys", () => {
  const orderedTurns = [
    { order: 2, role: "hermes-reviewer" },
    { order: 1, role: "openclaw-owner" },
  ];

  assert.deepEqual(normalizeJsonArrayOrder(orderedTurns), orderedTurns);
  assert.deepEqual(normalizeJsonArrayOrder(["second", "first"]), ["second", "first"]);
});

function writePriorReviewFixture(root: string): string {
  const artifactPath = join(root, "prior-review.json");
  writeFileSync(
    artifactPath,
    `${JSON.stringify(
      {
        schemaVersion: "review-evidence.v1",
        inventory: [
          {
            id: "existing:src/orchestrator.ts",
            relativePath: "src/orchestrator.ts",
            kind: "source",
            moduleName: "src.orchestrator",
          },
        ],
        findings: [
          {
            id: "finding:existing:src/orchestrator.ts:missing-test",
            sourceId: "existing:src/orchestrator.ts",
            relativePath: "src/orchestrator.ts",
            moduleName: "src.orchestrator",
            severity: "high",
            category: "error_frequency",
            title: "Source module has no observable test coverage",
            evidence: "No test reference was detected for this source module.",
            recommendation: "Add a focused runnable test before using this module in redesign decisions.",
          },
        ],
        summary: {
          inspectedModules: 1,
          findingCount: 1,
          findingsBySeverity: {
            critical: 0,
            high: 1,
            medium: 0,
            low: 0,
          },
          findingsByCategory: {
            error_frequency: 1,
            maintainability: 0,
            token_cost: 0,
            architecture_fit: 0,
            feature_completeness: 0,
          },
          recommendation: "partial_redesign",
        },
      },
      null,
      2,
    )}\n`,
  );
  return artifactPath;
}

function readJsonPath(stream: ReturnType<typeof normalizeCapturedStream>, path: string): unknown {
  assert.equal(stream.parseableJson, true);
  return path.split(".").reduce<unknown>((current, key) => {
    if (current === undefined || current === null || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, stream.json);
}
