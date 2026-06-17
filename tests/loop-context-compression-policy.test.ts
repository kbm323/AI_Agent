import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildLoopContextCompressionPolicyArtifact,
  renderLoopContextCompressionPolicyMarkdown,
  validateLoopContextCompressionPolicyArtifact,
  writeLoopContextCompressionPolicyArtifact,
} from "../src/index.ts";
import { executeLoopContextCompressionPolicyCheckCommand } from "../scripts/check-loop-context-compression-policy.ts";
import { executeGenerateLoopContextCompressionPolicyCommand } from "../scripts/generate-loop-context-compression-policy.ts";

test("loop context compression policy defines required schema groups", () => {
  const artifact = buildLoopContextCompressionPolicyArtifact();
  const markdown = renderLoopContextCompressionPolicyMarkdown(artifact);
  const validation = validateLoopContextCompressionPolicyArtifact(artifact, markdown);

  assert.equal(artifact.schemaVersion, "loop-context-compression-policy.v1");
  assert.equal(validation.passed, true);
  assert.deepEqual(artifact.validationSections, [
    "Retained Fields",
    "Summarized Fields",
    "Dropped Fields",
    "Iteration Boundaries",
    "Deterministic Ordering",
  ]);
  assert.deepEqual(
    artifact.retainedFields.map((field) => field.mode),
    ["retained", "retained", "retained"],
  );
  assert.equal(artifact.summarizedFields.every((field) => field.mode === "summarized"), true);
  assert.equal(artifact.droppedFields.every((field) => field.mode === "dropped"), true);
  assert.equal(artifact.iterationBoundaries.length >= 3, true);
  assert.deepEqual(validation.missingSections, []);
  assert.deepEqual(validation.missingPolicyGroups, []);
  assert.equal(validation.deterministicOrderingValid, true);
  assert.equal(validation.iterationBoundariesValid, true);

  for (const section of artifact.validationSections) {
    assert.match(markdown, new RegExp(`## ${escapeRegExp(section)}`));
  }
  assert.match(markdown, /tasks\.user_request/);
  assert.match(markdown, /turns\.visibleSummary/);
  assert.match(markdown, /turns\.content\.intermediateScratchpad/);
  assert.match(markdown, /hermes_to_next_openclaw_or_final/);
});

test("loop context compression policy generator writes artifact metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-loop-compression-generate-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    const result = executeGenerateLoopContextCompressionPolicyCommand([
      "--output",
      "artifact/loop-context-compression-policy.md",
    ]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(parsed.command, "ai-agent generate-loop-context-compression-policy");
    assert.equal(parsed.artifact.schemaVersion, "loop-context-compression-policy.v1");
    assert.equal(existsSync(parsed.artifact.path), true);
    assert.deepEqual(parsed.artifact.sections, [
      "Retained Fields",
      "Summarized Fields",
      "Dropped Fields",
      "Iteration Boundaries",
      "Deterministic Ordering",
    ]);
    assert.equal(parsed.artifact.retainedFieldCount, 3);
    assert.equal(parsed.artifact.summarizedFieldCount, 5);
    assert.equal(parsed.artifact.droppedFieldCount, 3);
    assert.equal(parsed.artifact.iterationBoundaryCount, 3);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("loop context compression policy checker verifies artifact sections and schema", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-loop-compression-check-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    writeLoopContextCompressionPolicyArtifact({ projectRoot: root });

    const result = executeLoopContextCompressionPolicyCheckCommand([]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(parsed.command, "ai-agent check-loop-context-compression-policy");
    assert.equal(parsed.status, "passed");
    assert.equal(parsed.artifact.present, true);
    assert.deepEqual(parsed.artifact.missingSections, []);
    assert.equal(parsed.schema.deterministicOrderingValid, true);
    assert.equal(parsed.schema.iterationBoundariesValid, true);
    assert.deepEqual(parsed.schema.missingPolicyGroups, []);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("loop context compression policy checker exits non-zero when required section is missing", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-loop-compression-missing-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    const written = writeLoopContextCompressionPolicyArtifact({ projectRoot: root });
    const markdown = readFileSync(written.path, "utf8").replace("## Dropped Fields", "## Removed Fields");
    writeFileSync(written.path, markdown);

    const result = executeLoopContextCompressionPolicyCheckCommand([]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.equal(parsed.status, "failed");
    assert.deepEqual(parsed.artifact.missingSections, ["Dropped Fields"]);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("loop context compression policy commands return stable invalid-input errors", () => {
  const generateResult = executeGenerateLoopContextCompressionPolicyCommand(["--output", ""]);
  const checkResult = executeLoopContextCompressionPolicyCheckCommand(["--artifact", ""]);

  assert.equal(generateResult.exitCode, 2);
  assert.equal(generateResult.stdout, "");
  assert.deepEqual(JSON.parse(generateResult.stderr), {
    error: "invalid_input",
    message: "output path must be a non-empty string",
  });

  assert.equal(checkResult.exitCode, 2);
  assert.equal(checkResult.stdout, "");
  assert.deepEqual(JSON.parse(checkResult.stderr), {
    error: "invalid_input",
    message: "--artifact must be followed by a non-empty value",
  });
});

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
