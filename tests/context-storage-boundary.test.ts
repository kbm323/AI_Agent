import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildContextStorageBoundaryArtifact,
  demonstrateContextStorageAccessPaths,
  renderContextStorageBoundaryMarkdown,
  retrieveLoopVisibleContext,
  verifyContextStorageBoundary,
  writeContextStorageBoundaryArtifact,
} from "../src/index.ts";
import {
  checkContextStorageBoundary,
  executeContextStorageBoundaryCheckCommand,
} from "../scripts/check-context-storage-boundary.ts";

test("context storage artifact defines raw retention and loop-visible summary boundary", () => {
  const artifact = buildContextStorageBoundaryArtifact();
  const markdown = renderContextStorageBoundaryMarkdown(artifact);

  assert.equal(artifact.schemaVersion, "context-storage-boundary.v1");
  assert.equal(artifact.sourceOfTruth.rawTurnField, "turns.content");
  assert.equal(artifact.loopVisibleFields.includes("turns.visibleSummary"), true);
  assert.equal(artifact.auditOnlyFields.includes("turns.content"), true);
  assert.match(markdown, /## Source Of Truth/);
  assert.match(markdown, /## Loop Visible Fields/);
  assert.match(markdown, /## Audit Only Fields/);
  assert.match(markdown, /complete original text/);
  assert.match(markdown, /LoopVisibleContextRetrievalResult\.compressedLoopContext\.content/);
  assert.match(markdown, /separate observable paths/);
});

test("context storage verification passes when raw content is retained but not loop-visible", () => {
  const result = verifyContextStorageBoundary({
    turns: [
      {
        id: "turn-raw-retained-1",
        taskId: "task-1",
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: "RAW_FULL_TEXT::all implementation details and audit notes",
        visibleSummary: "Owner draft summary.",
        createdAt: "2026-06-05T01:02:03.000Z",
      },
    ],
    loopVisibleContext: "Meeting history\n- Owner draft summary.",
  });

  assert.equal(result.passed, true);
  assert.equal(result.fullOriginalTextRetained, true);
  assert.equal(result.rawTextHiddenFromLoopContext, true);
  assert.equal(result.summariesVisibleInLoopContext, true);
  assert.deepEqual(result.violations, []);
});

test("context storage verification fails when raw content leaks into loop context", () => {
  const result = verifyContextStorageBoundary({
    turns: [
      {
        id: "turn-leaked-1",
        taskId: "task-1",
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        content: "RAW_FULL_TEXT::detailed Hermes critique",
        visibleSummary: "Hermes review summary.",
        createdAt: "2026-06-05T01:02:03.000Z",
      },
    ],
    loopVisibleContext: "Hermes review summary.\nRAW_FULL_TEXT::detailed Hermes critique",
  });

  assert.equal(result.passed, false);
  assert.equal(result.rawTextHiddenFromLoopContext, false);
  assert.deepEqual(result.violations, ["turn-leaked-1: raw full content leaked into loop-visible context"]);
});

test("loop-visible context retrieval returns summaries and compressed context separate from raw text", () => {
  const rawOpenClawText =
    "RAW_FULL_TEXT::OpenClaw draft has long implementation transcript, rejected options, and private audit details.";
  const rawHermesText =
    "RAW_FULL_TEXT::Hermes review has detailed critique, convergence notes, and internal risk scoring.";
  const retrieval = retrieveLoopVisibleContext({
    userRequestSummary: "Build the virtual-company multi-agent meeting MVP.",
    turns: [
      {
        taskId: "task-1",
        id: "turn-1",
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: rawOpenClawText,
        visibleSummary: "OpenClaw summary: draft covers request analysis and routing.",
        createdAt: "2026-06-05T01:02:03.000Z",
      },
      {
        taskId: "task-1",
        id: "turn-2",
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        content: rawHermesText,
        visibleSummary: "Hermes review: agree_with_changes. Add escalation evidence.",
        createdAt: "2026-06-05T01:02:04.000Z",
      },
    ],
    acceptedFeedback: ["Add escalation evidence before final synthesis."],
    rejectedFeedback: ["Expose raw private audit details in every loop prompt."],
    escalationReasons: [],
  });

  assert.equal(retrieval.schemaVersion, "loop-visible-context-retrieval.v1");
  assert.equal(retrieval.rawTurnCount, 2);
  assert.equal(retrieval.rawOriginalTextRetained, true);
  assert.deepEqual(retrieval.meetingHistory, [
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      summary: "OpenClaw summary: draft covers request analysis and routing.",
    },
    {
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      summary: "Hermes review: agree_with_changes. Add escalation evidence.",
    },
  ]);
  assert.equal(retrieval.meetingHistory.every((turn) => !Object.hasOwn(turn, "content")), true);
  assert.equal(retrieval.compressedLoopContext.schemaVersion, "compressed-loop-context.v1");
  assert.equal(retrieval.compressedLoopContext.latestHermesVerdict, "agree_with_changes");
  assert.equal(retrieval.compressedLoopContext.content.includes(rawOpenClawText), false);
  assert.equal(retrieval.compressedLoopContext.content.includes(rawHermesText), false);
});

test("context storage access path demo proves raw and summary paths are separate", () => {
  const rawOriginalText =
    "RAW_ORIGINAL_TEXT::complete OpenClaw transcript with implementation details, audit evidence, and private notes";
  const visibleSummary = "OpenClaw summary: implementation details are compressed for the next loop turn.";
  const demo = demonstrateContextStorageAccessPaths({
    userRequestSummary: "Build the virtual-company multi-agent meeting MVP.",
    turn: {
      id: "turn-access-path-1",
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: rawOriginalText,
      visibleSummary,
    },
  });

  assert.equal(demo.schemaVersion, "context-storage-access-path-demo.v1");
  assert.equal(demo.rawOriginalTextPath, "turns.content");
  assert.equal(demo.loopVisibleSummaryPath, "LoopVisibleContextRetrievalResult.meetingHistory[].summary");
  assert.equal(demo.rawOriginalTextRetainedExactly, true);
  assert.equal(demo.loopVisibleSummaryRetrievedExactly, true);
  assert.equal(demo.observablyDifferentValues, true);
  assert.equal(demo.separateAccessPaths, true);
  assert.equal(demo.rawHiddenFromLoopVisiblePath, true);
  assert.equal(demo.rawOriginalTextFingerprint.length, rawOriginalText.length);
  assert.equal(demo.loopVisibleSummaryFingerprint.length, visibleSummary.length);
  assert.notEqual(demo.rawOriginalTextFingerprint.prefix, demo.loopVisibleSummaryFingerprint.prefix);
});

test("context storage artifact writer and check command produce stable observable output", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-context-storage-"));
  try {
    const written = writeContextStorageBoundaryArtifact({ projectRoot: root });
    const content = readFileSync(written.path, "utf8");
    const check = checkContextStorageBoundary(root);
    const command = executeContextStorageBoundaryCheckCommand(["--project-root", root]);
    const parsed = JSON.parse(command.stdout);

    assert.equal(existsSync(written.path), true);
    assert.match(content, /# Context Storage Boundary/);
    assert.equal(check.command, "ai-agent check-context-storage-boundary");
    assert.equal(check.status, "passed");
    assert.equal(check.verification.passed, true);
    assert.deepEqual(check.retrieval, {
      schemaVersion: "loop-visible-context-retrieval.v1",
      compressedContextSchemaVersion: "compressed-loop-context.v1",
      rawTurnCount: 2,
      rawOriginalTextRetained: true,
      meetingHistorySummaryOnly: true,
      compressedContextHiddenFromRawText: true,
    });
    assert.equal(command.exitCode, 0);
    assert.equal(command.stderr, "");
    assert.equal(parsed.status, "passed");
    assert.deepEqual(parsed.artifact.missingSections, []);
    assert.equal(parsed.retrieval.compressedContextHiddenFromRawText, true);
    assert.deepEqual(parsed.accessPaths, {
      schemaVersion: "context-storage-access-path-demo.v1",
      turnId: "context-boundary-turn-1",
      rawOriginalTextPath: "turns.content",
      loopVisibleSummaryPath: "LoopVisibleContextRetrievalResult.meetingHistory[].summary",
      rawOriginalTextRetainedExactly: true,
      loopVisibleSummaryRetrievedExactly: true,
      observablyDifferentValues: true,
      separateAccessPaths: true,
      rawHiddenFromLoopVisiblePath: true,
      rawOriginalTextFingerprint: {
        length: 77,
        prefix: "RAW_ORIGINAL_TEXT::compl",
      },
      loopVisibleSummaryFingerprint: {
        length: 33,
        prefix: "OpenClaw execution draft",
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("context storage check exits non-zero when the artifact is incomplete", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-context-storage-missing-"));
  try {
    const written = writeContextStorageBoundaryArtifact({ projectRoot: root });
    const markdown = readFileSync(written.path, "utf8").replace("## Audit Only Fields", "## Audit Fields");
    writeFileSync(written.path, markdown);

    const result = executeContextStorageBoundaryCheckCommand(["--project-root", root]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.equal(parsed.status, "failed");
    assert.deepEqual(parsed.artifact.missingSections, ["Audit Only Fields"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("context storage check invalid input exits non-zero with stable JSON error", () => {
  const result = executeContextStorageBoundaryCheckCommand(["--project-root", ""]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "--project-root must be followed by a non-empty value",
  });
});
