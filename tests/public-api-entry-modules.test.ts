import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AiAgentDatabase as implementedAiAgentDatabase } from "../src/db.ts";
import * as publicApi from "../src/index.ts";
import {
  CompanyOrchestrator as implementedCompanyOrchestrator,
  buildReviewerRequest as implementedBuildReviewerRequest,
  serializeEscalationResult as implementedSerializeEscalationResult,
} from "../src/orchestrator.ts";
import { summarizeForThread as implementedSummarizeForThread } from "../src/policies.ts";
import {
  analyzeUserRequest as implementedAnalyzeUserRequest,
  buildDefaultTokenStrategy as implementedBuildDefaultTokenStrategy,
  buildRoleRoutes as implementedBuildRoleRoutes,
  buildTaskGraph as implementedBuildTaskGraph,
  decomposeUserRequest as implementedDecomposeUserRequest,
} from "../src/planning.ts";
import { buildCompressedLoopContextArtifact as implementedBuildCompressedLoopContextArtifact } from "../src/summarization.ts";
import { ExecPersona as implementedExecPersona, run_execution as implementedRunExecution } from "../src/execution-persona.ts";
import { ReviewPersona as implementedReviewPersona, run_review as implementedRunReview } from "../src/review-persona.ts";
import type { DiscordDelivery, FinalizerExecutor, OwnerExecutor, ReviewerExecutor, ReviewFinding } from "../src/index.ts";
import { connect as implementedConnect, disconnect as implementedDisconnect, get_client as implementedGetClient } from "../src/discord-client.ts";
import { create_thread as implementedCreateThread, archive_thread as implementedArchiveThread, get_thread as implementedGetThread } from "../src/threads.ts";
import { send_message as implementedSendMessage, on_message as implementedOnMessage, register_handler as implementedRegisterHandler } from "../src/messages.ts";
import { parseDiscordInteraction as implementedParseDiscordInteraction } from "../src/discord-interaction-parser.ts";

type PublicPackageEntryModule = {
  AiAgentDatabase: typeof implementedAiAgentDatabase;
  CompanyOrchestrator: typeof implementedCompanyOrchestrator;
  analyzeUserRequest: typeof implementedAnalyzeUserRequest;
  buildCompressedLoopContextArtifact: typeof implementedBuildCompressedLoopContextArtifact;
  buildDefaultTokenStrategy: typeof implementedBuildDefaultTokenStrategy;
  buildReviewerRequest: typeof implementedBuildReviewerRequest;
  buildRoleRoutes: typeof implementedBuildRoleRoutes;
  buildTaskGraph: typeof implementedBuildTaskGraph;
  decomposeUserRequest: typeof implementedDecomposeUserRequest;
  serializeEscalationResult: typeof implementedSerializeEscalationResult;
  summarizeForThread: typeof implementedSummarizeForThread;
};

const documentedPackageRuntimeExports = [
  "AiAgentDatabase",
  "CompanyOrchestrator",
  "analyzeUserRequest",
  "buildCompressedLoopContextArtifact",
  "buildDefaultTokenStrategy",
  "buildReviewerRequest",
  "buildRoleRoutes",
  "buildTaskGraph",
  "decomposeUserRequest",
  "format_message",
  "load_config",
  "serializeEscalationResult",
  "summarizeForThread",
  "validate_token",
].sort();

const publicPackageEntryModuleCases = [
  {
    exportKey: ".",
    modulePath: "ai-agent",
    run: async () => {
      const importedModule = asPublicPackageEntryModule(await import("ai-agent"));
      await assertPrimaryPackageEntrySuccessPath(importedModule);
    },
  },
  {
    exportKey: "./role-routing",
    modulePath: "ai-agent/role-routing",
    run: async () => {
      const importedModule = await import("ai-agent/role-routing");
      assertRoleRoutingSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./execution-persona",
    modulePath: "ai-agent/execution-persona",
    run: async () => {
      const importedModule = await import("ai-agent/execution-persona");
      await assertExecutionPersonaSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./review-persona",
    modulePath: "ai-agent/review-persona",
    run: async () => {
      const importedModule = await import("ai-agent/review-persona");
      await assertReviewPersonaSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./meeting-loop",
    modulePath: "ai-agent/meeting-loop",
    run: async () => {
      const importedModule = await import("ai-agent/meeting-loop");
      assertMeetingLoopSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./discord",
    modulePath: "ai-agent/discord",
    run: async () => {
      const importedModule = await import("ai-agent/discord");
      assertDiscordSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./discord-interaction-parser",
    modulePath: "ai-agent/discord-interaction-parser",
    run: async () => {
      const importedModule = await import("ai-agent/discord-interaction-parser");
      assertDiscordInteractionParserSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./threads",
    modulePath: "ai-agent/threads",
    run: async () => {
      const importedModule = await import("ai-agent/threads");
      assertThreadsSubPackageExports(importedModule);
    },
  },
  {
    exportKey: "./messages",
    modulePath: "ai-agent/messages",
    run: async () => {
      const importedModule = await import("ai-agent/messages");
      assertMessagesSubPackageExports(importedModule);
    },
  },
];

test("documented public module paths with named symbols are importable", async () => {
  const documentedModules = extractDocumentedPublicModuleImports(readFileSync(new URL("../README.md", import.meta.url), "utf8"));

  assert.deepEqual(documentedModules, [
    {
      modulePath: "ai-agent",
      symbols: ["CompanyOrchestrator", "AiAgentDatabase"],
    },
    {
      modulePath: "ai-agent",
      symbols: [
        "analyzeUserRequest",
        "buildCompressedLoopContextArtifact",
        "buildDefaultTokenStrategy",
        "buildReviewerRequest",
        "buildRoleRoutes",
        "buildTaskGraph",
        "decomposeUserRequest",
        "serializeEscalationResult",
        "summarizeForThread",
      ],
    },
    {
      modulePath: "ai-agent/execution-persona",
      symbols: ["ExecPersona", "run_execution"],
    },
    {
      modulePath: "ai-agent/review-persona",
      symbols: ["ReviewPersona", "run_review"],
    },
    {
      modulePath: "ai-agent/meeting-loop",
      symbols: ["CompanyOrchestrator", "buildReviewerRequest", "buildThreadName", "serializeEscalationResult"],
    },
    {
      modulePath: "ai-agent/discord",
      symbols: ["connect", "disconnect", "get_client"],
    },
    {
      modulePath: "ai-agent/threads",
      symbols: ["create_thread", "archive_thread", "get_thread"],
    },
    {
      modulePath: "ai-agent/messages",
      symbols: ["send_message", "on_message", "register_handler"],
    },
  ]);

  for (const documentedModule of documentedModules) {
    const importedModule = await import(documentedModule.modulePath);

    for (const symbol of documentedModule.symbols) {
      assert.notEqual(
        importedModule[symbol],
        undefined,
        `${symbol} should be importable from documented module path ${documentedModule.modulePath}`,
      );
    }
  }
});

test("primary success-path coverage exists for every package public entry module", () => {
  const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const exportedEntryModules = Object.entries(packageJson.exports as Record<string, string>)
    .filter(([, modulePath]) => modulePath.startsWith("./src/"))
    .map(([exportKey]) => exportKey)
    .sort();
  const coveredEntryModules = publicPackageEntryModuleCases.map((entryCase) => entryCase.exportKey).sort();

  assert.deepEqual(coveredEntryModules, exportedEntryModules);
});

for (const entryCase of publicPackageEntryModuleCases) {
  test(`public package entry module primary success path: ${entryCase.modulePath}`, async () => {
    await entryCase.run();
  });
}

test("package-level re-exports resolve to documented implementation objects", async () => {
  const packageModule = await import("ai-agent");
  const documentedImplementationExports = [
    { symbol: "CompanyOrchestrator", implementation: implementedCompanyOrchestrator },
    { symbol: "AiAgentDatabase", implementation: implementedAiAgentDatabase },
    { symbol: "analyzeUserRequest", implementation: implementedAnalyzeUserRequest },
    { symbol: "decomposeUserRequest", implementation: implementedDecomposeUserRequest },
    { symbol: "buildRoleRoutes", implementation: implementedBuildRoleRoutes },
    { symbol: "buildTaskGraph", implementation: implementedBuildTaskGraph },
    { symbol: "buildReviewerRequest", implementation: implementedBuildReviewerRequest },
    { symbol: "serializeEscalationResult", implementation: implementedSerializeEscalationResult },
    { symbol: "summarizeForThread", implementation: implementedSummarizeForThread },
    { symbol: "buildDefaultTokenStrategy", implementation: implementedBuildDefaultTokenStrategy },
    { symbol: "buildCompressedLoopContextArtifact", implementation: implementedBuildCompressedLoopContextArtifact },
  ];

  for (const { symbol, implementation } of documentedImplementationExports) {
    assert.equal(
      packageModule[symbol],
      implementation,
      `ai-agent:${symbol} should re-export the documented implementation object by identity`,
    );
  }
});

test("documented public class symbols are exported from their documented module path", async () => {
  const documentedModules = extractDocumentedPublicModuleImports(readFileSync(new URL("../README.md", import.meta.url), "utf8"));

  for (const documentedModule of documentedModules) {
    const importedModule = await import(documentedModule.modulePath);

    for (const symbol of documentedModule.symbols) {
      if (!Function.prototype.toString.call(importedModule[symbol]).startsWith("class ")) continue;

      assert.equal(
        Function.prototype.toString.call(importedModule[symbol]).startsWith("class "),
        true,
        `${symbol} should be a public class export from documented module path ${documentedModule.modulePath}`,
      );
    }
  }
});

test("documented public function symbols are exported from their documented module path", async () => {
  const documentedModules = extractDocumentedPublicModuleImports(readFileSync(new URL("../README.md", import.meta.url), "utf8"));
  const verifiedFunctions: string[] = [];

  for (const documentedModule of documentedModules) {
    const importedModule = await import(documentedModule.modulePath);

    for (const symbol of documentedModule.symbols) {
      const exportText = Function.prototype.toString.call(importedModule[symbol]);
      if (exportText.startsWith("class ")) continue;

      assert.equal(
        typeof importedModule[symbol],
        "function",
        `${symbol} should be a public function export from documented module path ${documentedModule.modulePath}`,
      );
      verifiedFunctions.push(`${documentedModule.modulePath}:${symbol}`);
    }
  }

  assert.deepEqual(verifiedFunctions, [
    "ai-agent:analyzeUserRequest",
    "ai-agent:buildCompressedLoopContextArtifact",
    "ai-agent:buildDefaultTokenStrategy",
    "ai-agent:buildReviewerRequest",
    "ai-agent:buildRoleRoutes",
    "ai-agent:buildTaskGraph",
    "ai-agent:decomposeUserRequest",
    "ai-agent:serializeEscalationResult",
    "ai-agent:summarizeForThread",
    "ai-agent/execution-persona:run_execution",
    "ai-agent/review-persona:run_review",
    "ai-agent/meeting-loop:buildReviewerRequest",
    "ai-agent/meeting-loop:buildThreadName",
    "ai-agent/meeting-loop:serializeEscalationResult",
    "ai-agent/discord:connect",
    "ai-agent/discord:disconnect",
    "ai-agent/discord:get_client",
    "ai-agent/threads:create_thread",
    "ai-agent/threads:archive_thread",
    "ai-agent/threads:get_thread",
    "ai-agent/messages:send_message",
    "ai-agent/messages:on_message",
    "ai-agent/messages:register_handler",
  ]);
});

test("package-level re-exports expose only README-documented runtime symbols", async () => {
  const packageModule = await import("ai-agent");

  assert.deepEqual(Object.keys(packageModule).sort(), documentedPackageRuntimeExports);
  assert.equal(
    "buildHealthCheckOutput" in packageModule,
    false,
    "ai-agent should not expose internal helpers that are absent from the README Public API block",
  );
});

test("public API entry module functions and class methods have primary success-path coverage", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-public-entry-"));
  try {
    writeFixtureProject(root);

    const userRequest = "브랜드 캠페인 제작 회의를 진행하고 최종안을 만들어줘.";
    const tasks = publicApi.decomposeUserRequest(userRequest);
    const analysis = publicApi.analyzeUserRequest(userRequest);
    const artifact = publicApi.buildStructuredAnalysisArtifact(userRequest);
    const parsedTasks = publicApi.parseTaskBreakdownFromAnalysisArtifact(artifact);
    const routes = publicApi.buildRoleRoutes(parsedTasks);
    const route = publicApi.matchRoleForTask("task-003");
    const routeText = publicApi.formatRoleRoute(route);
    const routingMetadata = publicApi.buildRoleRoutingMetadata(routes);
    const overlap = publicApi.assessTaskDecompositionOverlap(tasks);
    const tokenStrategy = publicApi.buildDefaultTokenStrategy();
    const taskDecompositionValidation = publicApi.validateTaskDecompositionOutput(tasks);

    assert.deepEqual(analysis.taskBreakdown, tasks);
    assert.deepEqual(parsedTasks, tasks);
    assert.deepEqual(publicApi.taskDecompositionRequiredFields, ["id", "title", "rationale"]);
    assert.deepEqual(taskDecompositionValidation, {
      valid: true,
      errors: [],
      taskCount: 4,
      requiredFields: ["id", "title", "rationale"],
    });
    assert.equal(route.role, "hermes-reviewer");
    assert.match(routeText, /task-003 -> hermes-reviewer/);
    assert.deepEqual(routingMetadata.workflowOrder, [
      "task-001:openclaw-owner",
      "task-002:openclaw-owner",
      "task-003:hermes-reviewer",
      "task-004:openclaw-finalizer",
    ]);
    assert.equal(overlap.nonOverlapping, true);
    assert.match(tokenStrategy.targetReduction, /40-50%/);

    const policy = publicApi.createDefaultEscalationPolicy();
    assert.deepEqual(
      policy.requiresUserDecision({
        userRequest: "외부 공개 전 브랜드 승인 필요",
        draft: "OpenClaw draft",
        review: "Hermes review",
        reviewerVerdict: "agree",
      }),
      ["brand_or_public_release"],
    );
    assert.equal(publicApi.summarizeForThread("a\n\n\n\nb", 20), "a\n\nb");
    assert.match(
      publicApi.serializeEscalationResult({
        reasons: ["reviewer_requested_user_decision"],
        triggerType: "meeting_loop",
        nextRequiredAction: "Ask the user to clarify the blocked decision.",
      }),
      /"triggerType": "meeting_loop"/,
    );

    const db = new publicApi.AiAgentDatabase();
    const task = db.createTask({
      id: "public-entry-task-1",
      projectChannelId: "channel-1",
      threadId: "thread-1",
      userRequest,
      now: "2026-06-05T00:00:00.000Z",
    });
    db.updateTaskStatus(task.id, "owner_drafted", "2026-06-05T00:01:00.000Z");
    assert.equal(db.getTask(task.id)?.status, "owner_drafted");
    const storedTurn = db.insertTurn({
      id: "public-entry-turn-1",
      taskId: task.id,
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: "raw OpenClaw draft",
      visibleSummary: "draft summary",
      createdAt: "2026-06-05T00:02:00.000Z",
    });
    assert.deepEqual(db.getTurns(task.id), [storedTurn]);
    assert.deepEqual(
      db.insertDecision({
        id: "public-entry-decision-1",
        taskId: task.id,
        requiresUserDecision: false,
        reasons: [],
        createdAt: "2026-06-05T00:03:00.000Z",
      }).reasons,
      [],
    );

    const orchestratorDb = new publicApi.AiAgentDatabase();
    const discord = createFakeDiscord();
    const owner: OwnerExecutor = {
      async createDraft() {
        return "Owner draft: agenda, asset list, and delivery checklist.";
      },
    };
    const reviewer: ReviewerExecutor = {
      async review() {
        return { verdict: "agree", content: "Hermes review: agree. Draft is ready." };
      },
    };
    const finalizer: FinalizerExecutor = {
      async synthesize({ draft, review }) {
        return `Final synthesis: ${draft} ${review}`;
      },
    };
    const orchestrator = new publicApi.CompanyOrchestrator({
      db: orchestratorDb,
      discord,
      owner,
      reviewer,
      finalizer,
      idFactory: () => "public-entry-orchestrator-task-1",
    });
    const runResult = await orchestrator.runUserRequest({
      project: { channelId: "channel-1" },
      userRequest,
    });
    assert.equal(runResult.status, "finalized");
    assert.equal(runResult.meetingHistory.at(-1)?.kind, "final_synthesis");
    assert.match(publicApi.buildReviewerRequest({ userRequest, draft: "draft", round: 1 }), /Hermes reviewer request/);
    assert.equal(publicApi.buildThreadName(` ${userRequest} `), `Task: ${userRequest}`);
    assert.deepEqual(publicApi.verifyRepresentativeTokenCostControl(), {
      schemaVersion: "token-cost-control-check.v1",
      status: "passed",
      method: "deterministic-local-estimate-v1",
      baselineTokenCount: 734,
      optimizedTokenCount: 189,
      savedTokenCount: 545,
      percentSavings: 74.3,
      targetThreshold: {
        percentSavings: 40,
        maxOptimizedTokenCount: 440,
        minimumSavedTokenCount: 294,
      },
      pass: true,
    });

    const findingA = finding("a", "error_frequency", "low");
    const findingB = finding("b", "token_cost", "medium");
    assert.equal(publicApi.compareFindingsByEvaluationPriority(findingA, findingB) < 0, true);
    const ranked = publicApi.rankReviewEvidence([findingB, findingA]);
    const decision = publicApi.decideImplementationDirection(ranked);
    assert.deepEqual(decision, { outcome: "partial_redesign", label: "partial redesign" });
    assert.deepEqual([...publicApi.implementationDecisionLabels], ["Keep", "partial redesign", "full replan"]);
    assert.equal(publicApi.isImplementationDecisionLabel(decision.label), true);
    assert.equal(publicApi.isImplementationDecisionLabel("full redesign"), false);
    assert.doesNotThrow(() => publicApi.assertImplementationDecisionLabel(decision.label));
    assert.equal(publicApi.justifyImplementationDecision(ranked, decision.outcome).outcome, "partial_redesign");
    assert.equal(publicApi.evaluateProjectFindings([findingA, findingB]).dominantCategory, "error_frequency");
    assert.deepEqual(publicApi.evaluationCategoryPriority.slice(0, 3), ["error_frequency", "maintainability", "token_cost"]);
    assert.deepEqual(publicApi.evaluationDecisionPolicy.map((criterion) => criterion.category), [
      "error_frequency",
      "maintainability",
      "token_cost",
      "architecture_fit",
      "feature_completeness",
    ]);
    assert.deepEqual(
      publicApi.rankEvaluationDecisionCriteria(["feature_completeness", "error_frequency"]).map((criterion) => criterion.category),
      ["error_frequency", "feature_completeness"],
    );

    const inventory = publicApi.buildInspectionInventory(root);
    const reviewFindings = publicApi.extractReviewFindings([
      { ...inventory.find((entry) => entry.relativePath === "src/uncovered.ts")!, content: "export const uncovered = true;\n" },
    ]);
    const reviewArtifact = publicApi.buildReviewEvidenceArtifact({ inventory, findings: [findingB] });
    const evidencePathArtifact = publicApi.buildReviewEvidencePathArtifact({
      projectRoot: root,
      paths: ["README.md", "src/planning.ts"],
    });
    const fixtureReadme = readFileSync(join(root, "README.md"), "utf8");
    const readmeRequirements = publicApi.parseReadmeDerivedMvpRequirements(fixtureReadme);
    const readmeRequirementList = publicApi.extractReadmeMvpRequirementList(fixtureReadme);
    const structuredReadmeRequirements = publicApi.parseReadmeMvpRequirements(fixtureReadme);
    const readmeRequirementValidation = publicApi.validateReadmeMvpRequirementExtraction(structuredReadmeRequirements);
    const capabilityArtifact = publicApi.buildImplementationCapabilityArtifact({
      inventory,
      readmeRequirements,
    });
    const writtenEvidence = publicApi.writeReviewEvidenceArtifact({
      outputPath: join(root, "docs", "review-evidence-written.json"),
      inventory,
      findings: [findingB],
    });
    const governedDecision = publicApi.buildGovernedRecommendationDecision({
      artifact: reviewArtifact,
      evidenceArtifactCreated: true,
    });
    const governedRecommendation = publicApi.emitGovernedRecommendation({
      artifact: reviewArtifact,
      evidenceArtifactCreated: true,
    });
    const priorPath = publicApi.resolvePriorReviewArtifactIdentifier({
      identifier: "docs/review-evidence-written.json",
      projectRoot: root,
    });
    const handledPrior = publicApi.handlePriorReviewArtifact({
      identifier: "docs/review-evidence-written.json",
      projectRoot: root,
    });
    const diagnosis = publicApi.loadDiagnosisReportArtifact({ projectRoot: root });
    const validation = publicApi.validatePriorReviewEvidenceForRedesignDecision(reviewArtifact);
    const completeness = publicApi.checkPriorReviewEvidenceCompletenessForRedesignDecision(reviewArtifact);
    const gate = publicApi.gateRedesignDecision({
      recommendation: "partial_redesign",
      priorReviewEvidence: reviewArtifact,
      evidenceArtifactCreated: true,
    });

    assert.equal(reviewFindings.length, 1);
    assert.equal(readmeRequirements.publicApiSymbols.includes("CompanyOrchestrator"), true);
    assert.equal(readmeRequirementList.some((entry) => entry.category === "public_api_symbol"), true);
    assert.deepEqual(structuredReadmeRequirements.requirements, readmeRequirementList);
    assert.equal(structuredReadmeRequirements.summary.totalCount, readmeRequirementList.length);
    assert.equal(readmeRequirementValidation.valid, true);
    assert.equal(evidencePathArtifact.summary.inspectedPathCount, 2);
    assert.equal(capabilityArtifact.summary.implementedCount, 6);
    assert.equal(writtenEvidence.schemaVersion, "review-evidence.v1");
    assert.equal(governedDecision.status, "complete");
    assert.equal(governedRecommendation.recommendation, "partial_redesign");
    assert.equal(priorPath, join(root, "docs", "review-evidence-written.json"));
    assert.equal(handledPrior.artifact.recommendation, "partial_redesign");
    assert.equal(diagnosis.schemaVersion, "diagnosis-report.v1");
    assert.equal(validation.valid, true);
    assert.equal(completeness.complete, true);
    assert.equal(gate.accepted, true);

    const representative = publicApi.buildRepresentativeLoopContextInput();
    const baseline = publicApi.measureCurrentTokenBaseline(representative);
    const turnBaseline = publicApi.measureTurnTokenBaseline(
      representative.turns.map((turn, index) => ({
        id: `turn-${index + 1}`,
        taskId: "token-task-1",
        round: turn.round,
        role: turn.role,
        kind: turn.kind,
        content: turn.content,
        visibleSummary: turn.visibleSummary,
        createdAt: "2026-06-05T00:00:00.000Z",
      })),
    );
    const savings = publicApi.estimateCompressedContextSavings({
      baselineContext: representative.turns.map((turn) => turn.content),
      proposedCompressedContext: representative.compressedContext ?? "",
    });
    const representativeSavings = publicApi.estimateRepresentativeCompressedContextSavings(representative);
    const representativeCompressedArtifact = publicApi.generateRepresentativeCompressedLoopContextArtifact(representative);
    const loopVisibleContext = publicApi.retrieveLoopVisibleContext({
      userRequestSummary: "Build the meeting loop MVP.",
      turns: [
        {
          taskId: "public-entry-task-1",
          id: "public-entry-loop-turn-1",
          round: 1,
          role: "openclaw-owner",
          kind: "owner_draft",
          content: "RAW_PUBLIC_ENTRY::full OpenClaw execution transcript",
          visibleSummary: "OpenClaw summary for public entry test.",
          createdAt: "2026-06-05T00:04:00.000Z",
        },
      ],
      acceptedFeedback: ["Keep summary-only loop context."],
    });
    assert.equal(publicApi.estimateTokenCount("OpenClaw final synthesis"), 7);
    assert.equal(baseline.rawFullTextTokens, 734);
    assert.equal(turnBaseline.turnCount, 7);
    assert.equal(savings.meetsFortyPercentTarget, true);
    assert.equal(representativeSavings.meetsFortyPercentTarget, true);
    assert.equal(representativeCompressedArtifact.schemaVersion, "representative-compressed-loop-context-generation.v1");
    assert.equal(representativeCompressedArtifact.artifact.schemaVersion, "compressed-loop-context.v1");
    assert.equal(representativeCompressedArtifact.savingsEstimate.meetsFortyPercentTarget, true);
    assert.equal(loopVisibleContext.schemaVersion, "loop-visible-context-retrieval.v1");
    assert.equal(loopVisibleContext.meetingHistory[0]?.summary, "OpenClaw summary for public entry test.");
    assert.equal(loopVisibleContext.compressedLoopContext.content.includes("RAW_PUBLIC_ENTRY"), false);

    const strategyArtifact = publicApi.buildTokenReductionStrategyArtifact();
    const strategyMarkdown = publicApi.renderTokenReductionStrategyMarkdown(strategyArtifact);
    const writtenStrategy = publicApi.writeTokenReductionStrategyArtifact({
      projectRoot: root,
      outputPath: "docs/token-strategy-public-entry.md",
    });
    const writtenTokenCheck = publicApi.writeTokenCostControlVerificationArtifact({
      projectRoot: root,
      outputPath: "docs/generated/token-reduction-check-public-entry.json",
    });
    assert.match(strategyMarkdown, /Token Reduction Strategy/);
    assert.equal(existsSync(writtenStrategy.path), true);
    assert.equal(writtenTokenCheck.artifact.schemaVersion, "token-cost-control-check.v1");
    assert.equal(existsSync(writtenTokenCheck.path), true);

    const compressionPolicy = publicApi.buildLoopContextCompressionPolicyArtifact();
    const compressionPolicyMarkdown = publicApi.renderLoopContextCompressionPolicyMarkdown(compressionPolicy);
    const compressionPolicyValidation = publicApi.validateLoopContextCompressionPolicyArtifact(
      compressionPolicy,
      compressionPolicyMarkdown,
    );
    const writtenCompressionPolicy = publicApi.writeLoopContextCompressionPolicyArtifact({
      projectRoot: root,
      outputPath: "docs/loop-compression-public-entry.md",
    });
    assert.equal(compressionPolicy.schemaVersion, "loop-context-compression-policy.v1");
    assert.equal(compressionPolicyValidation.passed, true);
    assert.match(compressionPolicyMarkdown, /Iteration Boundaries/);
    assert.equal(existsSync(writtenCompressionPolicy.path), true);

    const normalizedJson = publicApi.normalizeJsonValue({
      threadId: "thread-123",
      z: 2,
      a: "run:0123456789abcdef",
    });
    assert.deepEqual(publicApi.normalizeCapturedStream("{\"threadId\":\"thread-123\"}\n"), {
      parseableJson: true,
      json: { threadId: "<thread-id>" },
    });
    assert.deepEqual(publicApi.normalizeCapturedResponse({ exitCode: 0, stdout: "run:0123456789abcdef\n", stderr: "" }), {
      exitCode: 0,
      stdout: { parseableJson: false, text: "<execution-id>" },
      stderr: { parseableJson: false, text: "" },
    });
    assert.deepEqual(normalizedJson, { a: "<execution-id>", threadId: "<thread-id>", z: 2 });

    const synthesisArtifact = buildMeetingLoopArtifact();
    const synthesisContract = publicApi.validateFinalSynthesisMeetingLoopArtifact(synthesisArtifact);
    const synthesis = publicApi.generateFinalSynthesisFromMeetingLoopArtifact(synthesisArtifact);
    const publicFinalSynthesisArtifact = publicApi.buildFinalSynthesisArtifactFromMeetingLoopArtifact(synthesisArtifact);
    assert.equal(synthesisContract.finalTurn.kind, "final_synthesis");
    assert.match(synthesis.content, /raw full text remained in storage/);
    assert.equal(publicFinalSynthesisArtifact.schemaVersion, "final-synthesis-artifact.v1");
    assert.deepEqual(publicFinalSynthesisArtifact.structure, {
      hasFinalSynthesisContent: true,
      includesAcceptedMeetingLoop: true,
      includesContextPolicy: true,
      summaryOnlyMeetingTurns: true,
    });

    const finalOutput = JSON.parse(readFileSync(join(root, "docs", "final-output.json"), "utf8"));
    assert.equal(publicApi.finalOutputArtifactSchema.$id, "ai-agent.final-output-artifact.v1");
    assert.equal(publicApi.finalOutputRequiredFields.includes("tokenStrategy.targetReduction"), true);
    assert.deepEqual(publicApi.validateFinalOutputArtifact(finalOutput), {
      valid: true,
      schemaVersion: "final-output-artifact.v1",
      checkedFields: [...publicApi.finalOutputRequiredFields],
      errors: [],
    });

    db.close();
    orchestratorDb.close();
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function createFakeDiscord(): DiscordDelivery & {
  parentPosts: string[];
  threadPosts: Array<{ threadId: string; content: string; fullContent?: string }>;
} {
  return {
    parentPosts: [],
    threadPosts: [],
    async createThread() {
      return { threadId: "thread-1", url: "https://discord.test/thread-1" };
    },
    async archiveThread(_threadId: string) {
      // no-op
    },
    async getThread(threadId: string) {
      return { threadId, name: `thread-${threadId}`, archived: false };
    },
    async postParent(input) {
      this.parentPosts.push(input.content);
    },
    async postThread(input) {
      this.threadPosts.push(input);
    },
  };
}

function asPublicPackageEntryModule(importedModule: Record<string, unknown>): PublicPackageEntryModule {
  for (const symbol of documentedPackageRuntimeExports) {
    assert.notEqual(importedModule[symbol], undefined, `${symbol} should exist on the public package entry module`);
  }

  return importedModule as unknown as PublicPackageEntryModule;
}

async function assertPrimaryPackageEntrySuccessPath(publicEntry: PublicPackageEntryModule): Promise<void> {
  const userRequest = "브랜드 캠페인 제작 회의를 진행하고 최종안을 만들어줘.";
  const taskBreakdown = publicEntry.decomposeUserRequest(userRequest);
  const analysis = publicEntry.analyzeUserRequest(userRequest);
  const roleRoutes = publicEntry.buildRoleRoutes(taskBreakdown);

  assert.deepEqual(analysis.taskBreakdown, taskBreakdown);
  assert.deepEqual(roleRoutes.map((route) => `${route.taskId}:${route.role}`), [
    "task-001:openclaw-owner",
    "task-002:openclaw-owner",
    "task-003:hermes-reviewer",
    "task-004:openclaw-finalizer",
  ]);
  assert.match(publicEntry.buildDefaultTokenStrategy().targetReduction, /40-50%/);
  assert.equal(publicEntry.summarizeForThread("OpenClaw raw draft\n\nHermes review", 80), "OpenClaw raw draft\n\nHermes review");
  assert.match(publicEntry.buildReviewerRequest({ userRequest, draft: "Owner draft", round: 1 }), /Hermes reviewer request/);

  const db = new publicEntry.AiAgentDatabase();
  try {
    const discord = createFakeDiscord();
    const owner: OwnerExecutor = {
      async createDraft() {
        return "Owner draft: channel plan, assets, timeline, and success criteria.";
      },
    };
    const reviewer: ReviewerExecutor = {
      async review() {
        return { verdict: "agree", content: "Hermes review: agree. The plan is ready for final synthesis." };
      },
    };
    const finalizer: FinalizerExecutor = {
      async synthesize({ draft, review }) {
        return `Final synthesis: ${draft} ${review}`;
      },
    };
    const orchestrator = new publicEntry.CompanyOrchestrator({
      db,
      discord,
      owner,
      reviewer,
      finalizer,
      idFactory: () => "public-package-entry-task-1",
    });

    const result = await orchestrator.runUserRequest({
      project: { channelId: "public-package-channel-1" },
      userRequest,
    });
    const compressedContext = publicEntry.buildCompressedLoopContextArtifact({
      userRequestSummary: result.requestAnalysis.userRequestSummary,
      meetingTurns: result.meetingHistory.map((turn) => ({
        round: turn.round,
        role: turn.role,
        kind: turn.kind,
        summary:
          turn.role === "hermes-reviewer"
            ? "Hermes review: agree. The plan is ready for final synthesis."
            : turn.visibleSummary,
      })),
      acceptedFeedback: ["Hermes agreed with the OpenClaw draft."],
      escalationReasons: result.escalationReasons,
    });

    assert.equal(result.status, "finalized");
    assert.equal(result.meetingHistory.at(-1)?.kind, "final_synthesis");
    assert.deepEqual(result.escalationReasons, []);
    assert.equal(db.getTurns(result.task.id).length, 5);
    assert.equal(discord.parentPosts.length, 1);
    assert.equal(discord.threadPosts.length, 5);
    assert.equal(compressedContext.schemaVersion, "compressed-loop-context.v1");
    assert.equal(compressedContext.latestHermesVerdict, "agree");
    assert.match(
      publicEntry.serializeEscalationResult({ reasons: [], triggerType: "none", nextRequiredAction: "Continue." }),
      /"reasons": \[\]/,
    );
  } finally {
    db.close();
  }
}

function finding(id: string, category: ReviewFinding["category"], severity: ReviewFinding["severity"]): ReviewFinding {
  return {
    id: `finding-${id}`,
    sourceId: "existing:src/planning.ts",
    relativePath: "src/planning.ts",
    moduleName: "src.planning",
    severity,
    category,
    title: `Finding ${id} has enough stable evidence`,
    evidence: `Finding ${id} evidence describes a real implementation condition.`,
    recommendation: `Address finding ${id} with a concrete implementation change.`,
  };
}

function extractDocumentedPublicModuleImports(readme: string): Array<{ modulePath: string; symbols: string[] }> {
  const publicApiSection = readme.match(/## Public API\n\n```ts\n(?<code>[\s\S]*?)\n```/);
  assert.ok(publicApiSection?.groups?.code, "README.md must document public API TypeScript imports");

  const imports = [...publicApiSection.groups.code.matchAll(/import\s+\{\s*(?<symbols>[^}]+?)\s*\}\s+from\s+"(?<modulePath>[^"]+)";/g)];
  assert.ok(imports.length > 0, "README.md Public API section must include at least one named import");

  return imports.map((entry) => {
    assert.ok(entry.groups?.modulePath, "Documented public API import must include a module path");
    assert.ok(entry.groups.symbols, "Documented public API import must include named symbols");

    return {
      modulePath: entry.groups.modulePath,
      symbols: entry.groups.symbols
        .split(",")
        .map((symbol) => symbol.trim())
        .filter(Boolean),
    };
  });
}

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
      "",
      "```text",
      "parent channel user request",
      "  -> task 생성",
      "  -> OpenClaw owner draft",
      "  -> Hermes review",
      "  -> OpenClaw final synthesis",
      "  -> thread timeline 게시",
      "```",
      "",
      "## 운영 원칙",
      "",
      "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
      "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
      "",
      "## 실행",
      "",
      "```bash",
      "npm test",
      "npm run dry-run",
      "```",
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
  writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");
  writeFileSync(join(root, "src", "uncovered.ts"), "export const uncovered = true;\n");
  writeFileSync(join(root, "tests", "planning.test.ts"), "import test from 'node:test';\n");
  writeFileSync(join(root, "scripts", "dry-run.ts"), "console.log('dry-run');\n");
  writeFileSync(
    join(root, "docs", "diagnosis-report.md"),
    "Decision: partial redesign\n\nDecision evidence artifact: `docs/review-evidence-written.json`\n",
  );
  writeFileSync(join(root, "docs", "final-output.json"), `${JSON.stringify(buildValidFinalOutput(), null, 2)}\n`);
}

function buildMeetingLoopArtifact(): publicApi.MinimumMeetingLoopArtifact {
  return {
    schemaVersion: "meeting-process-artifact.v1",
    meetingProcessId: "meeting-1",
    taskId: "task-1",
    threadId: "thread-1",
    status: "finalized",
    meetingTurns: [
      turn(1, 0, "openclaw-owner", "request_analysis", "request analyzed"),
      turn(2, 1, "openclaw-owner", "owner_draft", "owner draft created"),
      turn(3, 1, "openclaw-owner", "review_request", "review requested"),
      turn(4, 1, "hermes-reviewer", "review", "Hermes agreed"),
      turn(5, 2, "openclaw-finalizer", "final_synthesis", "final answer"),
    ],
    retentionEvidence: {
      rawContextStoredAfterCompletion: true,
      summaryArtifactOnly: true,
      rawSentinelHiddenFromArtifact: true,
      ownerDraftSummaryCompressed: true,
    },
    personaLoopIteration: {
      openclawRole: "openclaw-owner",
      hermesRole: "hermes-reviewer",
      openclawCompletedDraft: true,
      hermesCompletedReview: true,
      hermesVerdict: "agree",
      hermesReviewedOpenClawDraft: true,
    },
  };
}

function turn(
  order: number,
  round: number,
  role: publicApi.AgentRole,
  kind: publicApi.TurnKind,
  summary: string,
): publicApi.MinimumMeetingLoopTurn {
  return {
    id: `turn-${order}`,
    order,
    round,
    role,
    kind,
    summary,
  };
}

function buildValidFinalOutput(): publicApi.FinalOutputArtifact {
  const sections = [
    {
      title: "Prior Review Evidence" as const,
      evidence: {
        artifactPath: "/tmp/review-evidence.json",
        validationValid: true,
        completenessComplete: true,
        decisionGateAccepted: true,
      },
    },
    { title: "Keep Decision" as const, evidence: {} },
    { title: "Partial Redesign Decision" as const, evidence: {} },
    { title: "Full Redesign Decision" as const, evidence: {} },
  ];
  return {
    schemaVersion: "final-output-artifact.v1",
    command: "ai-agent dry-run",
    metadata: {
      executionId: "run:public-entry",
      inputIdentifier: "request:public-entry",
      inputSource: "inline",
      version: {
        schemaVersion: "run-version-metadata.v1",
        artifactSchemaVersion: "final-output-artifact.v1",
        commandVersion: "ai-agent-dry-run.v1",
        implementationVersion: "multi-agent-meeting-mvp.v1",
        runtime: { name: "node", version: "v24.0.0" },
      },
      runSettings: {
        executionMode: "dry_run",
        orchestrator: { maxRounds: 2, escalationPolicy: "default" },
        models: {
          openclawOwner: { provider: "local", model: "openclaw", temperature: 0, maxOutputTokens: 1000 },
          hermesReviewer: { provider: "local", model: "hermes", temperature: 0, maxOutputTokens: 1000 },
          openclawFinalizer: { provider: "local", model: "openclaw", temperature: 0, maxOutputTokens: 1000 },
        },
      },
    },
    status: "finalized",
    threadId: "thread-1",
    userRequest: "브랜드 캠페인 제작 회의를 진행하고 최종안을 만들어줘.",
    diagnosis: {
      decision: "partial_redesign",
      decisionLabel: "partial redesign",
      basis: "review evidence",
      justification: {},
    },
    diagnosticOutput: { sections },
    requestAnalysis: {
      taskBreakdown: ["task-001", "task-002", "task-003", "task-004"],
      roleRoutes: ["task-001:openclaw-owner", "task-003:hermes-reviewer"],
      tokenStrategy: "summary-only loop context",
    },
    openclawOutputs: [{ round: 1, role: "openclaw-owner", kind: "owner_draft", summary: "draft" }],
    hermesReviews: [{ round: 1, role: "hermes-reviewer", kind: "review", summary: "agree" }],
    meetingHistory: [
      { round: 1, role: "openclaw-owner", kind: "owner_draft", summary: "draft" },
      { round: 1, role: "hermes-reviewer", kind: "review", summary: "agree" },
    ],
    finalSynthesis: "Final synthesis",
    escalation: {
      required: false,
      reasons: [],
      decisionContext: {
        status: "finalized",
        trigger: "none",
        preservedTurns: 2,
        latestMeetingSummary: "agree",
        diagnosisDecision: "partial_redesign",
      },
      nextAction: { type: "continue", prompt: "", requestedFields: [] },
      preservedContext: {
        rawStorage: "turns.content",
        exposedSummary: "turns.visibleSummary",
        compressedContext: "latest summaries",
      },
    },
    tokenStrategy: {
      rawStorage: "SQLite turns.content stores full model outputs.",
      exposedLoopContext: "bounded summaries",
      compressionPolicy: "latest summaries instead of replaying full raw text",
      targetReduction: "40-50%",
    },
  };
}

// ── meeting-loop sub-package verification ──────────────────────

function assertMeetingLoopSubPackageExports(module: Record<string, unknown>): void {
  // Must export CompanyOrchestrator class
  assert.equal(typeof module.CompanyOrchestrator, "function", "ai-agent/meeting-loop must export CompanyOrchestrator");
  assert.ok(
    Function.prototype.toString.call(module.CompanyOrchestrator).startsWith("class "),
    "CompanyOrchestrator must be a class export",
  );

  // Must export buildReviewerRequest function
  assert.equal(typeof module.buildReviewerRequest, "function", "ai-agent/meeting-loop must export buildReviewerRequest");

  // Must export buildThreadName function
  assert.equal(typeof module.buildThreadName, "function", "ai-agent/meeting-loop must export buildThreadName");
  assert.match(
    (module.buildThreadName as (s: string) => string)("Test request"),
    /Task: Test request/,
    "buildThreadName should prefix the user request",
  );

  // Must export serializeEscalationResult function
  assert.equal(typeof module.serializeEscalationResult, "function", "ai-agent/meeting-loop must export serializeEscalationResult");
}

// ── discord sub-package verification ───────────────────────────

function assertDiscordSubPackageExports(module: Record<string, unknown>): void {
  // Must export connect function
  assert.equal(typeof module.connect, "function", "ai-agent/discord must export connect");
  // connect should be an async function
  assert.ok(
    module.connect?.constructor?.name === "AsyncFunction",
    "connect must be an async function (connection bootstrap)",
  );

  // Must export disconnect function
  assert.equal(typeof module.disconnect, "function", "ai-agent/discord must export disconnect");
  assert.ok(
    module.disconnect?.constructor?.name === "AsyncFunction",
    "disconnect must be an async function (graceful teardown)",
  );

  // Must export get_client function
  assert.equal(typeof module.get_client, "function", "ai-agent/discord must export get_client");
  // get_client is synchronous — returns the singleton client or null
  assert.equal(
    module.get_client?.constructor?.name,
    "Function",
    "get_client must be a synchronous function (singleton accessor)",
  );

  // get_client returns null before connect
  assert.equal((module.get_client as () => unknown)(), null, "get_client must return null before connect");
}

// ── discord-interaction-parser sub-package verification ─────────

function assertDiscordInteractionParserSubPackageExports(module: Record<string, unknown>): void {
  // Must export parseDiscordInteraction function
  assert.equal(
    typeof module.parseDiscordInteraction,
    "function",
    "ai-agent/discord-interaction-parser must export parseDiscordInteraction",
  );

  // parseDiscordInteraction must be a synchronous function
  assert.equal(
    module.parseDiscordInteraction?.constructor?.name,
    "Function",
    "parseDiscordInteraction must be a synchronous function",
  );

  // Must export verifyDiscordSignature function
  assert.equal(
    typeof module.verifyDiscordSignature,
    "function",
    "ai-agent/discord-interaction-parser must export verifyDiscordSignature",
  );

  // Must export InteractionParseError class
  assert.equal(
    typeof module.InteractionParseError,
    "function",
    "ai-agent/discord-interaction-parser must export InteractionParseError",
  );
  assert.ok(
    Function.prototype.toString.call(module.InteractionParseError).startsWith("class "),
    "InteractionParseError must be a class export",
  );

  // Must export InteractionType constant
  assert.equal(
    typeof module.InteractionType,
    "object",
    "ai-agent/discord-interaction-parser must export InteractionType",
  );
  assert.equal(
    (module.InteractionType as Record<string, number>).PING,
    1,
    "InteractionType.PING must be 1",
  );
  assert.equal(
    (module.InteractionType as Record<string, number>).APPLICATION_COMMAND,
    2,
    "InteractionType.APPLICATION_COMMAND must be 2",
  );
}

// ── threads sub-package verification ───────────────────────────

async function assertThreadsSubPackageExports(module: Record<string, unknown>): Promise<void> {
  // Must export create_thread async function
  assert.equal(typeof module.create_thread, "function", "ai-agent/threads must export create_thread");
  assert.ok(
    module.create_thread?.constructor?.name === "AsyncFunction",
    "create_thread must be an async function",
  );

  // Must export archive_thread async function
  assert.equal(typeof module.archive_thread, "function", "ai-agent/threads must export archive_thread");
  assert.ok(
    module.archive_thread?.constructor?.name === "AsyncFunction",
    "archive_thread must be an async function",
  );

  // Must export get_thread async function
  assert.equal(typeof module.get_thread, "function", "ai-agent/threads must export get_thread");
  assert.ok(
    module.get_thread?.constructor?.name === "AsyncFunction",
    "get_thread must be an async function",
  );

  // create_thread throws when client is not connected
  await assert.rejects(
    (module.create_thread as (input: { parentChannelId: string; name: string; initialMessage: string }) => Promise<unknown>)({
      parentChannelId: "channel-1",
      name: "Test thread",
      initialMessage: "Hello",
    }),
    /Discord client not connected/,
    "create_thread must throw when Discord client is not connected",
  );

  // archive_thread throws when client is not connected
  await assert.rejects(
    (module.archive_thread as (threadId: string) => Promise<void>)("thread-1"),
    /Discord client not connected/,
    "archive_thread must throw when Discord client is not connected",
  );

  // get_thread throws when client is not connected
  await assert.rejects(
    (module.get_thread as (threadId: string) => Promise<unknown>)("thread-1"),
    /Discord client not connected/,
    "get_thread must throw when Discord client is not connected",
  );
}

// ── messages sub-package verification ──────────────────────────

async function assertMessagesSubPackageExports(module: Record<string, unknown>): Promise<void> {
  assert.equal(typeof module.send_message, "function", "ai-agent/messages must export send_message");
  assert.ok(
    module.send_message?.constructor?.name === "AsyncFunction",
    "send_message must be an async function",
  );

  assert.equal(typeof module.on_message, "function", "ai-agent/messages must export on_message");
  assert.equal(typeof module.register_handler, "function", "ai-agent/messages must export register_handler");

  // on_message returns an unsubscribe function
  const unsub1 = (module.on_message as (h: unknown) => () => void)(async () => {});
  assert.equal(typeof unsub1, "function", "on_message must return an unsubscribe function");
  unsub1();

  // register_handler returns an unsubscribe function
  const unsub2 = (module.register_handler as (p: string, h: unknown) => () => void)("/test", async () => {});
  assert.equal(typeof unsub2, "function", "register_handler must return an unsubscribe function");
  unsub2();

  // send_message throws when client is not connected
  await assert.rejects(
    (module.send_message as (channelId: string, content: string, threadId?: string) => Promise<unknown>)("channel-1", "Hello"),
    /Discord client not connected/,
    "send_message must throw when Discord client is not connected",
  );

  // send_message with threadId also throws when client not connected
  await assert.rejects(
    (module.send_message as (channelId: string, content: string, threadId?: string) => Promise<unknown>)("channel-1", "Hello", "thread-1"),
    /Discord client not connected/,
    "send_message must throw when Discord client is not connected (thread case)",
  );
}

// ── role-routing sub-package verification ──────────────────────

function assertRoleRoutingSubPackageExports(module: Record<string, unknown>): void {
  // Must export the new PersonaRouter class
  assert.equal(typeof module.PersonaRouter, "function", "ai-agent/role-routing must export PersonaRouter");
  assert.ok(
    Function.prototype.toString.call(module.PersonaRouter).startsWith("class "),
    "PersonaRouter must be a class export",
  );

  // Must export route_to_persona function
  assert.equal(typeof module.route_to_persona, "function", "ai-agent/role-routing must export route_to_persona");

  // Must also export existing routing symbols
  assert.equal(typeof module.assignDecomposedTasksToAgentRoles, "function", "ai-agent/role-routing must export assignDecomposedTasksToAgentRoles");
  assert.equal(typeof module.deriveTaskRoutingAttributes, "function", "ai-agent/role-routing must export deriveTaskRoutingAttributes");
  assert.equal(typeof module.validateTaskRoleAssignments, "function", "ai-agent/role-routing must export validateTaskRoleAssignments");
  assert.equal(typeof module.resolveTaskRoleEligibility, "function", "ai-agent/role-routing must export resolveTaskRoleEligibility");
}

// ── execution-persona sub-package verification ─────────────────

async function assertExecutionPersonaSubPackageExports(module: Record<string, unknown>): Promise<void> {
  // Must export ExecPersona class
  assert.equal(typeof module.ExecPersona, "function", "ai-agent/execution-persona must export ExecPersona");
  assert.ok(
    Function.prototype.toString.call(module.ExecPersona).startsWith("class "),
    "ExecPersona must be a class export",
  );

  // Must export run_execution function
  assert.equal(typeof module.run_execution, "function", "ai-agent/execution-persona must export run_execution");

  // run_execution must be callable and return the executor's result
  const result = await (module.run_execution as (executor: (p: string) => Promise<string>, prompt: string) => Promise<string>)(
    async (prompt: string) => `echo: ${prompt}`,
    "hello",
  );
  assert.equal(result, "echo: hello", "run_execution should forward the prompt through the executor");
}

// ── review-persona sub-package verification ─────────────────

async function assertReviewPersonaSubPackageExports(module: Record<string, unknown>): Promise<void> {
  // Must export ReviewPersona class
  assert.equal(typeof module.ReviewPersona, "function", "ai-agent/review-persona must export ReviewPersona");
  assert.ok(
    Function.prototype.toString.call(module.ReviewPersona).startsWith("class "),
    "ReviewPersona must be a class export",
  );

  // Must export run_review function
  assert.equal(typeof module.run_review, "function", "ai-agent/review-persona must export run_review");

  // run_review must be callable and return a structured review verdict
  const result = await (module.run_review as (
    executor: (p: string) => Promise<string>,
    input: { task: { id: string }; userRequest: string; draft: string; round: number },
  ) => Promise<{ content: string; verdict: string }>)(
    async (prompt: string) => `Review analysis complete.\n\nVERDICT: agree`,
    {
      task: { id: "review-test-task-1" },
      userRequest: "Test request",
      draft: "Test draft",
      round: 1,
    },
  );
  assert.equal(result.verdict, "agree", "run_review should parse the VERDICT line from Hermes output");
  assert.match(result.content, /Review analysis complete/, "run_review should strip the VERDICT line from content");
}
