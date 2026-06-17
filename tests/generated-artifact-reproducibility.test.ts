import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeGenerateLoopContextCompressionPolicyCommand } from "../scripts/generate-loop-context-compression-policy.ts";
import { executeGenerateTokenStrategyCommand } from "../scripts/generate-token-strategy.ts";

test("generated artifacts are reproducible across repeated runs with fixed inputs and configuration", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-generated-artifact-reproducibility-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);

    const tokenStrategy = assertRepeatedGenerationIsStable({
      run: () => executeGenerateTokenStrategyCommand(["--output", "artifacts/token-strategy.md"]),
      artifactPath: join(root, "artifacts", "token-strategy.md"),
    });
    const compressionPolicy = assertRepeatedGenerationIsStable({
      run: () =>
        executeGenerateLoopContextCompressionPolicyCommand(["--output", "artifacts/loop-context-compression-policy.md"]),
      artifactPath: join(root, "artifacts", "loop-context-compression-policy.md"),
    });

    assert.equal(tokenStrategy.stdout.artifact.schemaVersion, "token-reduction-strategy.v1");
    assert.equal(compressionPolicy.stdout.artifact.schemaVersion, "loop-context-compression-policy.v1");
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

function assertRepeatedGenerationIsStable(input: {
  run: () => { exitCode: number; stdout: string; stderr: string };
  artifactPath: string;
}): { stdout: { artifact: { schemaVersion: string } }; artifact: string } {
  const first = input.run();
  const firstArtifact = readFileSync(input.artifactPath, "utf8");
  const second = input.run();
  const secondArtifact = readFileSync(input.artifactPath, "utf8");

  assert.equal(first.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(second.exitCode, 0);
  assert.equal(second.stderr, "");
  assert.deepEqual(JSON.parse(second.stdout), JSON.parse(first.stdout));
  assert.equal(secondArtifact, firstArtifact);

  return {
    stdout: JSON.parse(first.stdout),
    artifact: firstArtifact,
  };
}
