import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  executeReviewEvidenceCommand,
  generateReviewEvidence,
  runReviewEvidenceCommand,
} from "../scripts/review-evidence.ts";

test("generateReviewEvidence returns artifact data and writes the requested output artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-review-evidence-generate-"));
  try {
    writeFileSync(join(root, "README.md"), "# AI_Agent\n\nMVP OpenClaw Hermes escalation\n");
    writeFileSync(join(root, "package.json"), "{}\n");
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    writeFileSync(join(root, "src", "orchestrator.ts"), "export const fullContent = 'draft';\n");
    writeFileSync(join(root, "tests", "orchestrator.test.ts"), "import test from 'node:test';\n");

    const result = generateReviewEvidence({
      projectRoot: root,
      outputPath: "artifacts/generated-review-evidence.json",
    });

    assert.equal(result.artifactPath, join(root, "artifacts", "generated-review-evidence.json"));
    assert.equal(existsSync(result.artifactPath), true);
    assert.equal(result.artifact.schemaVersion, "review-evidence.v1");
    assert.deepEqual(
      result.artifact.inventory.map((entry) => entry.relativePath),
      ["README.md", "package.json", "src/orchestrator.ts", "tests/orchestrator.test.ts"],
    );
    assert.deepEqual(
      JSON.parse(readFileSync(result.artifactPath, "utf8")),
      result.artifact,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("review-evidence command writes deterministic artifact to stable default location", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-review-evidence-command-"));
  try {
    writeFileSync(join(root, "README.md"), "# AI_Agent\n\nMVP OpenClaw Hermes escalation\n");
    writeFileSync(join(root, "package.json"), "{}\n");
    mkdirSync(join(root, "src"), { recursive: true });
    mkdirSync(join(root, "tests"), { recursive: true });
    mkdirSync(join(root, "scripts"), { recursive: true });
    mkdirSync(join(root, "docs"), { recursive: true });
    writeFileSync(join(root, "src", "orchestrator.ts"), "export const fullContent = 'draft';\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "tests", "planning.test.ts"), "import test from 'node:test';\n");
    writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");

    const first = runReviewEvidenceCommand({ projectRoot: root });
    const firstArtifact = JSON.parse(readFileSync(join(root, "docs", "review-evidence.json"), "utf8"));
    const second = executeReviewEvidenceCommand(["--project-root", root]);
    const secondArtifact = JSON.parse(readFileSync(join(root, "docs", "review-evidence.json"), "utf8"));

    assert.equal(second.exitCode, 0);
    assert.equal(second.stderr, "");
    assert.deepEqual(JSON.parse(second.stdout), first);
    assert.deepEqual(secondArtifact, firstArtifact);
    assert.deepEqual(first, {
      command: "ai-agent review-evidence",
      artifact: {
        path: join(root, "docs", "review-evidence.json"),
        schemaVersion: "review-evidence.v1",
        inspectedModules: 7,
        findingCount: 3,
        recommendation: "partial_redesign",
      },
    });
    assert.deepEqual(
      firstArtifact.inventory.map((entry: { relativePath: string }) => entry.relativePath),
      [
        "README.md",
        "docs/diagnosis-report.md",
        "package.json",
        "scripts/dry-run.ts",
        "src/orchestrator.ts",
        "src/planning.ts",
        "tests/planning.test.ts",
      ],
    );
    assert.deepEqual(
      firstArtifact.findings.map((finding: { id: string }) => finding.id),
      [
        "finding:existing:src/orchestrator.ts:missing-test",
        "finding:existing:src/orchestrator.ts:raw-content-exposure",
        "finding:existing:src/planning.ts:missing-test",
      ],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("review-evidence command rejects invalid input with stable non-zero failure", () => {
  const result = executeReviewEvidenceCommand(["--project-root"]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "--project-root requires a non-empty value",
  });
});

test("review-evidence command supports explicit output path", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-review-evidence-output-"));
  try {
    writeFileSync(join(root, "README.md"), "# AI_Agent\n");
    writeFileSync(join(root, "package.json"), "{}\n");
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(join(root, "src", "db.ts"), "export const rawText = 'stored';\n");

    const result = executeReviewEvidenceCommand([
      "--project-root",
      root,
      "--output",
      "artifacts/review-evidence.json",
    ]);

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(existsSync(join(root, "artifacts", "review-evidence.json")), true);
    assert.equal(JSON.parse(result.stdout).artifact.path, join(root, "artifacts", "review-evidence.json"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
