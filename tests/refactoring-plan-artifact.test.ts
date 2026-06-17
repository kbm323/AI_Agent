import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const refactoringPlanPath = join(projectRoot, "docs", "refactoring-plan.md");

const requiredSections = [
  "Evaluation Priority",
  "MVP Coverage",
  "Phase 1: Stabilize MVP Surface",
  "Phase 2: Separate Planning From Orchestration",
  "Phase 3: Preserve Full Text, Expose Summaries",
  "Token and Context Strategy",
  "Phase 4: Convergence and Escalation Rules",
  "Phase 5: Verification Hardening",
  "Phase 6: Later Non-MVP Work",
] as const;

test("refactoring plan artifact exists and contains all required plan sections", () => {
  assert.equal(existsSync(refactoringPlanPath), true);

  const plan = readFileSync(refactoringPlanPath, "utf8");

  for (const section of requiredSections) {
    assert.match(plan, new RegExp(`^## ${escapeRegExp(section)}$`, "m"), `missing section: ${section}`);
  }
});

test("refactoring plan preserves Seed priorities and verification hardening requirements", () => {
  const plan = readFileSync(refactoringPlanPath, "utf8");

  assert.match(
    plan,
    /Error frequency[\s\S]*Maintenance difficulty[\s\S]*Token cost[\s\S]*Architecture fit[\s\S]*Feature completeness/,
  );
  assert.match(plan, /40-50%/);
  assert.match(plan, /raw full-text storage/i);
  assert.match(plan, /bounded summaries/i);
  assert.match(plan, /acceptanceEvidence[\s\S]*generated artifacts/i);
  assert.match(plan, /configured `npm run typecheck` command/i);
});

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
