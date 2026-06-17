import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkDryRunContract } from "../scripts/check-dry-run-contract.ts";
import { checkDecisionDeterminism } from "../scripts/check-decision-determinism.ts";
import { checkEscalationSerialization } from "../scripts/check-escalation-serialization.ts";
import { checkContextStorageBoundary } from "../scripts/check-context-storage-boundary.ts";
import { checkFinalOutputSchema } from "../scripts/check-final-output-schema.ts";
import { checkFinalSynthesisArtifact } from "../scripts/check-final-synthesis-artifact.ts";
import { checkFinalSynthesisStability } from "../scripts/check-final-synthesis-stability.ts";
import { checkFixtureHarness } from "../scripts/check-fixture-harness.ts";
import { checkMeetingLoopArtifacts } from "../scripts/check-meeting-loop-artifacts.ts";
import { checkMeetingLoopRouting } from "../scripts/check-meeting-loop-routing.ts";
import { checkOpenClawHermesLoop } from "../scripts/check-openclaw-hermes-loop.ts";
import { checkPublicApi, PUBLIC_API_ARTIFACT_PATH } from "../scripts/check-public-api.ts";
import { checkRequestAnalysis } from "../scripts/check-request-analysis.ts";
import { checkRequirementGapMapping } from "../scripts/check-requirement-gap.ts";
import { checkRoutingAssignment } from "../scripts/check-routing-assignment.ts";
import { checkTokenStrategyArtifact } from "../scripts/check-token-strategy.ts";
import { runGenerateDiagnosisReportCommand } from "../scripts/generate-diagnosis-report.ts";
import { generateTokenStrategyArtifact } from "../scripts/generate-token-strategy.ts";
import { generateReviewEvidence } from "../scripts/review-evidence.ts";
import { runHealthCheck } from "../scripts/health-check.ts";
import {
  writeContextStorageBoundaryArtifact,
  writeReviewEvidenceArtifact,
  writeTokenReductionStrategyArtifact,
} from "../src/index.ts";

test("command entry helpers expose stable primary success-path artifacts", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-command-entry-"));
  try {
    writeFixtureProject(root);

    const health = runHealthCheck(root);
    assert.equal(health.schemaVersion, "health-check.v1");
    assert.equal(health.status, "ok");
    assert.equal(health.strategyArtifactPath, join(root, "docs/token-reduction-strategy.md"));

    const tokenStrategy = generateTokenStrategyArtifact(root, "artifacts/token-strategy.md");
    assert.equal(tokenStrategy.command, "ai-agent generate-token-strategy");
    assert.equal(tokenStrategy.artifact.path, join(root, "artifacts/token-strategy.md"));
    assert.equal(existsSync(tokenStrategy.artifact.path), true);
    assert.equal(tokenStrategy.artifact.targetSavingsPercent, "40-50");

    const tokenCheck = checkTokenStrategyArtifact(root, "artifacts/token-strategy.md");
    assert.equal(tokenCheck.command, "ai-agent check-token-strategy");
    assert.equal(tokenCheck.artifact.present, true);
    assert.deepEqual(tokenCheck.artifact.missingSections, []);

    const contextStorage = checkContextStorageBoundary(root);
    assert.equal(contextStorage.command, "ai-agent check-context-storage-boundary");
    assert.equal(contextStorage.status, "passed");
    assert.equal(contextStorage.verification.rawTextHiddenFromLoopContext, true);

    const reviewEvidence = generateReviewEvidence({
      projectRoot: root,
      outputPath: "artifacts/review-evidence.json",
    });
    assert.equal(reviewEvidence.artifact.schemaVersion, "review-evidence.v1");
    assert.equal(reviewEvidence.artifactPath, join(root, "artifacts/review-evidence.json"));
    assert.equal(existsSync(join(root, "artifacts/review-evidence.json")), true);
    assert.equal(reviewEvidence.artifact.summary.recommendation, "partial_redesign");

    const diagnosisReport = runGenerateDiagnosisReportCommand({
      projectRoot: root,
      outputPath: "artifacts/diagnosis-report.json",
      reviewEvidenceOutputPath: "artifacts/review-evidence.json",
    });
    assert.equal(diagnosisReport.command, "ai-agent generate-diagnosis-report");
    assert.equal(diagnosisReport.artifact.path, join(root, "artifacts/diagnosis-report.json"));
    assert.equal(diagnosisReport.artifact.schemaVersion, "diagnosis-report-generation.v1");
    assert.equal(diagnosisReport.artifact.requirementGapMappingPath, join(root, "docs", "generated", "requirement-gap-mapping.json"));
    assert.equal(
      diagnosisReport.artifact.inventoryOrchestrationReportPath,
      join(root, "docs", "generated", "inventory-orchestration-report.json"),
    );
    assert.equal(diagnosisReport.artifact.decisionGateAccepted, true);
    assert.equal(existsSync(join(root, "artifacts/diagnosis-report.json")), true);
    assert.equal(existsSync(join(root, "docs", "generated", "requirement-gap-mapping.json")), true);
    assert.equal(existsSync(join(root, "docs", "generated", "inventory-orchestration-report.json")), true);

    const requirementGap = checkRequirementGapMapping(root);
    assert.equal(requirementGap.command, "ai-agent check-requirement-gap");
    assert.equal(requirementGap.artifact.present, true);
    assert.equal(requirementGap.artifact.capabilityMappings.every((capability) => capability.status === "implemented"), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("command entry scenario checks return runnable stable output", async () => {
  const requestAnalysis = checkRequestAnalysis();
  assert.equal(requestAnalysis.command, "ai-agent check-request-analysis");
  assert.equal(requestAnalysis.status, "passed");
  assert.equal(requestAnalysis.scenario, "minimum");
  assert.equal(requestAnalysis.deterministic, true);
  assert.deepEqual(
    requestAnalysis.artifact.taskBreakdown.map((task) => `${task.id}:${task.title}`),
    [
      "task-001:요청 의도와 성공 기준 정리",
      "task-002:OpenClaw 실행 초안 작성",
      "task-003:Hermes 리뷰와 수렴 판단",
      "task-004:최종 합성 또는 escalation",
    ],
  );
  assert.deepEqual(
    requestAnalysis.artifact.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
    [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ],
  );
  assert.match(requestAnalysis.artifact.loopContextSummary, /final synthesis\/escalation/);
  assert.match(requestAnalysis.artifact.tokenStrategy.targetReduction, /40-50%/);

  const routingAssignment = checkRoutingAssignment();
  assert.equal(routingAssignment.command, "ai-agent check-routing-assignment");
  assert.equal(routingAssignment.status, "passed");
  assert.equal(routingAssignment.scenario, "representative_decomposed_task_set");
  assert.equal(routingAssignment.deterministic, true);
  assert.deepEqual(routingAssignment.executionResponsibilityProof, {
    executionTaskIds: ["task-002"],
    openclawExecutionRole: "openclaw-owner",
    allExecutionTasksAssignedToOpenClaw: true,
    responsibilities: ["실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다."],
  });
  assert.deepEqual(routingAssignment.reviewResponsibilityProof, {
    reviewTaskIds: ["task-003"],
    hermesReviewRole: "hermes-reviewer",
    allReviewTasksAssignedToHermes: true,
    reviewSignals: ["리뷰", "검토", "리스크", "수렴", "판정"],
    responsibilities: ["초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다."],
  });
  assert.deepEqual(routingAssignment.deterministicInputRuns[0].assignments, routingAssignment.deterministicInputRuns[1].assignments);
  assert.deepEqual(routingAssignment.deterministicInputRuns[0].assignments, routingAssignment.artifact.assignments);

  const openclawHermesLoop = await checkOpenClawHermesLoop();
  assert.equal(openclawHermesLoop.command, "ai-agent check-openclaw-hermes-loop");
  assert.equal(openclawHermesLoop.status, "passed");
  assert.equal(openclawHermesLoop.proof.hermesReviewedOpenClawDraft, true);
  assert.equal(openclawHermesLoop.proof.executionStep.order < openclawHermesLoop.proof.reviewStep.order, true);

  const routing = await checkMeetingLoopRouting();
  assert.equal(routing.command, "ai-agent check-meeting-loop-routing");
  assert.equal(routing.status, "passed");
  assert.equal(routing.artifact.meetingTurns.length, 5);

  const artifacts = await checkMeetingLoopArtifacts();
  assert.equal(artifacts.command, "ai-agent check-meeting-loop-artifacts");
  assert.equal(artifacts.status, "passed");
  assert.equal(artifacts.contract.deterministic, true);

  const synthesis = await checkFinalSynthesisStability();
  assert.equal(synthesis.command, "ai-agent check-final-synthesis-stability");
  assert.equal(synthesis.status, "passed");
  assert.deepEqual(synthesis.runs[0].synthesis, synthesis.runs[1].synthesis);

  const synthesisArtifact = await checkFinalSynthesisArtifact();
  assert.equal(synthesisArtifact.command, "ai-agent check-final-synthesis-artifact");
  assert.equal(synthesisArtifact.status, "passed");
  assert.equal(synthesisArtifact.scenario, "minimum");
  assert.equal(synthesisArtifact.artifact.schemaVersion, "final-synthesis-artifact.v1");
  assert.deepEqual(synthesisArtifact.artifact.structure, {
    hasFinalSynthesisContent: true,
    includesAcceptedMeetingLoop: true,
    includesContextPolicy: true,
    summaryOnlyMeetingTurns: true,
  });

  const schema = await checkFinalOutputSchema();
  assert.equal(schema.command, "ai-agent check-final-output-schema");
  assert.equal(schema.status, "passed");
  assert.deepEqual(schema.validation, {
    clearRequestValid: true,
    ambiguousRequestValid: true,
    missingRequiredFieldRejected: true,
  });

  const contract = await checkDryRunContract();
  assert.equal(contract.command, "ai-agent check-dry-run-contract");
  assert.equal(contract.status, "passed");
  assert.equal(contract.contract.deterministic, true);

  const decisionDeterminism = await checkDecisionDeterminism();
  assert.equal(decisionDeterminism.command, "ai-agent check-decision-determinism");
  assert.equal(decisionDeterminism.status, "passed");
  assert.equal(decisionDeterminism.determinism.deterministic, true);
  assert.deepEqual(decisionDeterminism.runs[0].decisionResult, decisionDeterminism.runs[1].decisionResult);

  const escalationSerialization = checkEscalationSerialization();
  assert.equal(escalationSerialization.command, "ai-agent check-escalation-serialization");
  assert.equal(escalationSerialization.status, "passed");
  assert.equal(escalationSerialization.serializationPath, "serializeEscalationResult");
  assert.equal(escalationSerialization.deterministic, true);
  assert.deepEqual(escalationSerialization.artifact, JSON.parse(escalationSerialization.serializedArtifact));
  assert.deepEqual(escalationSerialization.artifact, {
    schemaVersion: "escalation-result.v1",
    escalation: {
      required: true,
      reasons: ["reviewer_requested_user_decision", "max_rounds_without_agreement"],
      triggerType: "meeting_loop",
      nextRequiredAction: "Ask the user to choose a direction or provide stronger success criteria before continuing.",
    },
  });

  const harness = await checkFixtureHarness();
  assert.equal(harness.command, "ai-agent check-fixture-harness");
  assert.equal(harness.status, "passed");
  assert.equal(harness.harness.cases.length, 3);
  assert.equal(harness.harness.cases.every((fixtureCase) => fixtureCase.deterministic === true), true);
});

test("public API command helper writes the documented symbol artifact", async () => {
  const result = await checkPublicApi();
  const artifact = JSON.parse(readFileSync(PUBLIC_API_ARTIFACT_PATH, "utf8"));

  assert.deepEqual(result, {
    modulePath: "ai-agent",
    verifiedSymbols: [
      "CompanyOrchestrator",
      "AiAgentDatabase",
      "analyzeUserRequest",
      "decomposeUserRequest",
      "buildRoleRoutes",
      "buildReviewerRequest",
      "serializeEscalationResult",
      "summarizeForThread",
      "buildDefaultTokenStrategy",
      "buildCompressedLoopContextArtifact",
    ],
    exportedSymbols: [
      "AiAgentDatabase",
      "CompanyOrchestrator",
      "analyzeUserRequest",
      "buildCompressedLoopContextArtifact",
      "buildDefaultTokenStrategy",
      "buildReviewerRequest",
      "buildRoleRoutes",
      "decomposeUserRequest",
      "serializeEscalationResult",
      "summarizeForThread",
    ],
    undocumentedRuntimeSymbols: [],
    verifiedClassSymbols: ["CompanyOrchestrator", "AiAgentDatabase"],
    verifiedFunctionSymbols: [
      "analyzeUserRequest",
      "decomposeUserRequest",
      "buildRoleRoutes",
      "buildReviewerRequest",
      "serializeEscalationResult",
      "summarizeForThread",
      "buildDefaultTokenStrategy",
      "buildCompressedLoopContextArtifact",
    ],
    importSideEffects: {
      stdoutBytes: 0,
      stderrBytes: 0,
      createdFiles: [],
    },
  });
  assert.deepEqual(artifact, result);
});

function writeFixtureProject(root: string): void {
  mkdirSync(join(root, "src"), { recursive: true });
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "tests"), { recursive: true });
  mkdirSync(join(root, "docs"), { recursive: true });
  writeFileSync(join(root, "package.json"), "{}\n");
  writeFileSync(
    join(root, "README.md"),
    [
      "# AI_Agent",
      "",
      "## MVP 목표",
      "OpenClaw와 Hermes가 회의하고 escalation을 보존한다.",
      "",
      "## Public API",
      "",
      "```ts",
      'import { CompanyOrchestrator, AiAgentDatabase } from "ai-agent";',
      "```",
      "",
    ].join("\n"),
  );
  writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
  writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
  writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
  writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
  writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
  writeFileSync(join(root, "tests", "planning.test.ts"), "import test from 'node:test';\n");
  writeReviewEvidenceArtifact({
    outputPath: join(root, "docs", "review-evidence.json"),
    inventory: [],
    findings: [
      {
        id: "finding:fixture:token-cost",
        sourceId: "fixture:src/orchestrator.ts",
        relativePath: "src/orchestrator.ts",
        moduleName: "src.orchestrator",
        severity: "high",
        category: "token_cost",
        title: "Loop context repeats raw full text",
        evidence: "The fixture keeps enough evidence text for redesign gating.",
        recommendation: "Separate raw storage from exposed summaries.",
      },
    ],
  });
  writeTokenReductionStrategyArtifact({ projectRoot: root });
  writeFileSync(
    join(root, "docs", "diagnosis-report.md"),
    [
      "# AI_Agent MVP Diagnosis",
      "## Prior Review Artifact",
      "Decision evidence artifact: `docs/review-evidence.json`.",
      "## Decision",
      "Recommendation: **partial redesign**.",
      "## Priority Assessment",
      "1. Error frequency",
      "2. Maintenance difficulty",
      "3. Token cost",
      "4. Architecture fit",
      "5. Feature completeness",
      "## Requirement-to-Gap Mapping",
      "The mapping is generated from README and project inventory.",
      "## Token Strategy",
      "Compressed context separates raw full text from exposed loop summaries.",
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(root, "docs", "refactoring-plan.md"),
    [
      "# AI_Agent Refactoring Plan",
      "Decision basis: `docs/review-evidence.json` (`review-evidence.v1`, recommendation `partial_redesign`).",
      "## Phase 1: Stabilize MVP Surface",
      "Status: implemented.",
      "## Phase 2: Separate Planning From Orchestration",
      "Status: implemented.",
      "## Phase 3: Preserve Full Text, Expose Summaries",
      "Status: implemented.",
      "Use compressed context for loop prompts.",
      "## Phase 4: Convergence and Escalation Rules",
      "Status: implemented.",
      "## Phase 5: Verification Hardening",
      "Status: implemented for current verification artifacts.",
      "Compute acceptanceEvidence from generated artifacts.",
      "## Phase 6: Later Non-MVP Work",
      "Out of scope.",
      "",
    ].join("\n"),
  );
  writeContextStorageBoundaryArtifact({ projectRoot: root });
}
