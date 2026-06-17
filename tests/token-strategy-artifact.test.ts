import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildTokenReductionStrategyArtifact,
  renderTokenReductionStrategyMarkdown,
  writeTokenReductionStrategyArtifact,
} from "../src/index.ts";
import { executeTokenReductionSavingsBandCheckCommand } from "../scripts/check-token-reduction-savings-band.ts";
import { executeTokenStrategyCheckCommand } from "../scripts/check-token-strategy.ts";
import { executeGenerateTokenStrategyCommand } from "../scripts/generate-token-strategy.ts";

test("token reduction strategy artifact contains required policy sections", () => {
  const artifact = buildTokenReductionStrategyArtifact();
  const markdown = renderTokenReductionStrategyMarkdown(artifact);

  assert.equal(artifact.schemaVersion, "token-reduction-strategy.v1");
  assert.equal(artifact.targetSavings.minimumPercent, 40);
  assert.equal(artifact.targetSavings.maximumPercent, 50);
  assert.equal(artifact.baseline.exposedReductionPercent >= 40, true);
  assert.equal(artifact.baseline.compressedReductionPercent >= 50, true);

  for (const section of artifact.validationSections) {
    assert.match(markdown, new RegExp(`## ${escapeRegExp(section)}`));
  }

  assert.match(markdown, /40-50%/);
  assert.match(markdown, /Original Text Retention Policy/);
  assert.match(markdown, /Exposed Context Summary Separation/);
  assert.match(markdown, /Compressed Context Approach/);
});

test("token reduction strategy generator writes a runnable artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-strategy-"));
  try {
    const written = writeTokenReductionStrategyArtifact({ projectRoot: root });
    const content = readFileSync(written.path, "utf8");

    assert.equal(existsSync(written.path), true);
    assert.match(content, /# Token Reduction Strategy/);
    assert.match(content, /Target band: 40-50% reduction/);
    assert.match(content, /Raw storage remains the source of truth/);
    assert.match(content, /turns\.visibleSummary/);
    assert.match(content, /Compressed context tokens:/);
    assert.match(content, /40% reduction: expose at most 440 tokens, saving at least 294 tokens/);
    assert.match(content, /50% reduction: expose at most 367 tokens, saving at least 367 tokens/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("generate-token-strategy command reports generated artifact metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-strategy-command-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    const result = executeGenerateTokenStrategyCommand(["--output", "artifact/token-strategy.md"]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(parsed.command, "ai-agent generate-token-strategy");
    assert.equal(parsed.artifact.schemaVersion, "token-reduction-strategy.v1");
    assert.equal(parsed.artifact.targetSavingsPercent, "40-50");
    assert.equal(existsSync(parsed.artifact.path), true);
    assert.deepEqual(parsed.artifact.sections, [
      "40-50% Savings Target",
      "Original Text Retention Policy",
      "Exposed Context Summary Separation",
      "Compressed Context Approach",
      "Baseline Measurement",
    ]);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("check-token-strategy command verifies artifact existence and required sections", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-strategy-check-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    writeTokenReductionStrategyArtifact({ projectRoot: root });

    const result = executeTokenStrategyCheckCommand([]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(parsed.command, "ai-agent check-token-strategy");
    assert.equal(parsed.artifact.present, true);
    assert.equal(parsed.artifact.schemaVersion, "token-reduction-strategy.v1");
    assert.deepEqual(parsed.artifact.missingSections, []);
    assert.deepEqual(parsed.artifact.requiredSections, [
      "40-50% Savings Target",
      "Original Text Retention Policy",
      "Exposed Context Summary Separation",
      "Compressed Context Approach",
      "Baseline Measurement",
    ]);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("check-token-strategy command exits non-zero when a required section is missing", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-strategy-missing-section-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    const written = writeTokenReductionStrategyArtifact({ projectRoot: root });
    const markdown = readFileSync(written.path, "utf8").replace(
      "## Exposed Context Summary Separation",
      "## Exposed Context Boundary",
    );
    writeFileSync(written.path, markdown);

    const result = executeTokenStrategyCheckCommand([]);
    const parsed = JSON.parse(result.stdout);

    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    assert.equal(parsed.artifact.present, true);
    assert.deepEqual(parsed.artifact.missingSections, ["Exposed Context Summary Separation"]);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("check-token-reduction-savings-band passes when fixture savings is within 40 to 50 percent", () => {
  const result = executeTokenReductionSavingsBandCheckCommand([]);
  const parsed = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(parsed.command, "ai-agent check-token-reduction-savings-band");
  assert.equal(parsed.fixture.present, true);
  assert.equal(parsed.fixture.name, "minimal-representative-token-baseline");
  assert.equal(parsed.savings.savingsPercent, 47.1);
  assert.equal(parsed.savings.withinTargetRange, true);
  assert.equal(parsed.pass, true);
});

test("check-token-reduction-savings-band exits non-zero below or above the target band", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-savings-band-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    writeFixture("low.json", repeatToken("a", 100), repeatToken("a", 61));
    writeFixture("high.json", repeatToken("a", 100), repeatToken("a", 49));

    const lowResult = executeTokenReductionSavingsBandCheckCommand(["--fixture", "low.json"]);
    const highResult = executeTokenReductionSavingsBandCheckCommand(["--fixture", "high.json"]);
    const lowParsed = JSON.parse(lowResult.stdout);
    const highParsed = JSON.parse(highResult.stdout);

    assert.equal(lowResult.exitCode, 1);
    assert.equal(lowResult.stderr, "");
    assert.equal(lowParsed.savings.savingsPercent, 39);
    assert.equal(lowParsed.savings.withinTargetRange, false);
    assert.equal(lowParsed.savings.exceedsTargetRange, false);
    assert.equal(lowParsed.pass, false);

    assert.equal(highResult.exitCode, 1);
    assert.equal(highResult.stderr, "");
    assert.equal(highParsed.savings.savingsPercent, 51);
    assert.equal(highParsed.savings.withinTargetRange, false);
    assert.equal(highParsed.savings.exceedsTargetRange, true);
    assert.equal(highParsed.pass, false);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("check-token-reduction-savings-band exits non-zero for malformed fixture input", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-savings-band-malformed-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    writeFileSync("malformed.json", JSON.stringify({ name: "malformed", input: { turns: [] } }));

    const result = executeTokenReductionSavingsBandCheckCommand(["--fixture", "malformed.json"]);
    const parsed = JSON.parse(result.stderr);

    assert.equal(result.exitCode, 2);
    assert.equal(result.stdout, "");
    assert.equal(parsed.error, "invalid_input");
    assert.match(parsed.message, /input\.turns must be a non-empty array/);
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("generate-token-strategy invalid output path exits non-zero with stable JSON error", () => {
  const result = executeGenerateTokenStrategyCommand(["--output", ""]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "output path must be a non-empty string",
  });
});

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function writeFixture(path: string, content: string, visibleSummary: string): void {
  writeFileSync(
    path,
    JSON.stringify(
      {
        name: path,
        input: {
          turns: [
            {
              round: 1,
              role: "openclaw-owner",
              kind: "owner_draft",
              content,
              visibleSummary,
            },
          ],
        },
      },
      null,
      2,
    ),
  );
}

function repeatToken(token: string, count: number): string {
  return Array.from({ length: count }, () => token).join(" ");
}
