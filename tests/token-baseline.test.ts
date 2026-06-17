import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import {
  AiAgentDatabase,
  CompanyOrchestrator,
  accountTokenReduction,
  buildRepresentativeLoopContextInput,
  compactPromptContext,
  estimateCompressedContextSavings,
  estimateRepresentativeCompressedContextSavings,
  estimateTokenCount,
  generateRepresentativeCompressedLoopContextArtifact,
  measureCurrentTokenBaseline,
  measureRepresentativeWorkflowTokenUsage,
  measureTokenReductionSavings,
  measureTurnTokenBaseline,
  measureWorkflowTokenBaseline,
  verifyRepresentativeTokenCostControl,
  writeTokenCostControlVerificationArtifact,
} from "../src/index.ts";
import type {
  DiscordDelivery,
  FinalizerExecutor,
  OwnerExecutor,
  ReviewerExecutor,
  TokenBaselineInput,
  TokenBaselineMeasurement,
  TurnRecord,
} from "../src/index.ts";
import {
  checkTokenCostControl,
  executeTokenCostControlCheckCommand,
} from "../scripts/check-token-cost-control.ts";

test("token accounting reports exactly 40 percent savings", () => {
  assert.deepEqual(accountTokenReduction({ baseline: 100, optimized: 60 }), {
    method: "deterministic-local-estimate-v1",
    baselineTokens: 100,
    optimizedTokens: 60,
    absoluteReductionTokens: 40,
    percentSavings: 40,
    meetsFortyPercentTarget: true,
    meetsFiftyPercentTarget: false,
  });
});

test("token accounting reports exactly 50 percent savings", () => {
  assert.deepEqual(accountTokenReduction({ baseline: 100, optimized: 50 }), {
    method: "deterministic-local-estimate-v1",
    baselineTokens: 100,
    optimizedTokens: 50,
    absoluteReductionTokens: 50,
    percentSavings: 50,
    meetsFortyPercentTarget: true,
    meetsFiftyPercentTarget: true,
  });
});

test("token accounting reports below-target savings without passing thresholds", () => {
  assert.deepEqual(accountTokenReduction({ baseline: 100, optimized: 75 }), {
    method: "deterministic-local-estimate-v1",
    baselineTokens: 100,
    optimizedTokens: 75,
    absoluteReductionTokens: 25,
    percentSavings: 25,
    meetsFortyPercentTarget: false,
    meetsFiftyPercentTarget: false,
  });
});

test("token reduction savings measurement computes the 40 to 50 percent target range", () => {
  assert.deepEqual(
    [
      measureTokenReductionSavings({ baseline: 100, reduced: 61 }),
      measureTokenReductionSavings({ baseline: 100, reduced: 60 }),
      measureTokenReductionSavings({ baseline: 100, reduced: 55 }),
      measureTokenReductionSavings({ baseline: 100, reduced: 50 }),
      measureTokenReductionSavings({ baseline: 100, reduced: 49 }),
    ],
    [
      {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 100,
        reducedTokens: 61,
        savedTokens: 39,
        savingsPercent: 39,
        targetRange: {
          minimumPercentSavings: 40,
          maximumPercentSavings: 50,
        },
        meetsMinimumTarget: false,
        withinTargetRange: false,
        exceedsTargetRange: false,
      },
      {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 100,
        reducedTokens: 60,
        savedTokens: 40,
        savingsPercent: 40,
        targetRange: {
          minimumPercentSavings: 40,
          maximumPercentSavings: 50,
        },
        meetsMinimumTarget: true,
        withinTargetRange: true,
        exceedsTargetRange: false,
      },
      {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 100,
        reducedTokens: 55,
        savedTokens: 45,
        savingsPercent: 45,
        targetRange: {
          minimumPercentSavings: 40,
          maximumPercentSavings: 50,
        },
        meetsMinimumTarget: true,
        withinTargetRange: true,
        exceedsTargetRange: false,
      },
      {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 100,
        reducedTokens: 50,
        savedTokens: 50,
        savingsPercent: 50,
        targetRange: {
          minimumPercentSavings: 40,
          maximumPercentSavings: 50,
        },
        meetsMinimumTarget: true,
        withinTargetRange: true,
        exceedsTargetRange: false,
      },
      {
        method: "deterministic-local-estimate-v1",
        baselineTokens: 100,
        reducedTokens: 49,
        savedTokens: 51,
        savingsPercent: 51,
        targetRange: {
          minimumPercentSavings: 40,
          maximumPercentSavings: 50,
        },
        meetsMinimumTarget: true,
        withinTargetRange: false,
        exceedsTargetRange: true,
      },
    ],
  );

  assert.throws(
    () =>
      measureTokenReductionSavings({
        baseline: 100,
        reduced: 50,
        targetRange: { minimumPercentSavings: 50, maximumPercentSavings: 40 },
      }),
    /minimum cannot exceed maximum/,
  );
});

test("token accounting handles edge-case empty contexts and invalid counts predictably", () => {
  assert.deepEqual(accountTokenReduction({ baseline: "", optimized: [] }), {
    method: "deterministic-local-estimate-v1",
    baselineTokens: 0,
    optimizedTokens: 0,
    absoluteReductionTokens: 0,
    percentSavings: 0,
    meetsFortyPercentTarget: false,
    meetsFiftyPercentTarget: false,
  });
  assert.throws(
    () => accountTokenReduction({ baseline: -1, optimized: 0 }),
    /baseline token count must be a finite non-negative number/,
  );
});

test("representative loop token baseline returns stable current usage", () => {
  const measurement = measureCurrentTokenBaseline();

  assert.deepEqual(
    {
      method: measurement.method,
      turnCount: measurement.turnCount,
      rawFullTextTokens: measurement.rawFullTextTokens,
      exposedLoopContextTokens: measurement.exposedLoopContextTokens,
      compressedLoopContextTokens: measurement.compressedLoopContextTokens,
      exposedReductionPercent: measurement.exposedReductionPercent,
      compressedReductionPercent: measurement.compressedReductionPercent,
      targetReductionThresholds: measurement.targetReductionThresholds,
    },
    {
      method: "deterministic-local-estimate-v1",
      turnCount: 7,
      rawFullTextTokens: 734,
      exposedLoopContextTokens: 260,
      compressedLoopContextTokens: 67,
      exposedReductionPercent: 64.6,
      compressedReductionPercent: 90.9,
      targetReductionThresholds: [
        {
          reductionPercent: 40,
          maxAllowedTokens: 440,
          minimumSavedTokens: 294,
        },
        {
          reductionPercent: 50,
          maxAllowedTokens: 367,
          minimumSavedTokens: 367,
        },
      ],
    },
  );
  assert.deepEqual(
    measurement.perTurn.map((turn) => [turn.round, turn.role, turn.kind, turn.rawFullTextTokens, turn.exposedSummaryTokens]),
    [
      [0, "openclaw-owner", "request_analysis", 270, 70],
      [1, "openclaw-owner", "owner_draft", 62, 35],
      [1, "openclaw-owner", "review_request", 163, 29],
      [1, "hermes-reviewer", "review", 59, 31],
      [2, "openclaw-owner", "owner_draft", 66, 30],
      [2, "hermes-reviewer", "review", 53, 34],
      [5, "openclaw-finalizer", "final_synthesis", 61, 31],
    ],
  );
  assert.equal(measurement.exposedReductionPercent >= 40, true);
});

test("token baseline measurement matches the expected baseline fixture", () => {
  const fixture = loadTokenBaselineFixture("tests/fixtures/token-baseline-fixture.json");
  const measurement = measureCurrentTokenBaseline(fixture.input);

  assert.equal(fixture.name, "minimal-representative-token-baseline");
  assert.deepEqual(measurement, fixture.expected);
});

test("turn-record baseline uses stored raw content and visible summaries", () => {
  const representative = buildRepresentativeLoopContextInput();
  const turns: TurnRecord[] = representative.turns.map((turn, index) => ({
    id: `turn-${index + 1}`,
    taskId: "task-token-baseline-1",
    round: turn.round,
    role: turn.role,
    kind: turn.kind,
    content: turn.content,
    visibleSummary: turn.visibleSummary,
    createdAt: "2026-06-05T00:00:00.000Z",
  }));

  const measurement = measureTurnTokenBaseline(turns);

  assert.equal(measurement.rawFullTextTokens, 734);
  assert.equal(measurement.exposedLoopContextTokens, 260);
  assert.equal(measurement.compressedLoopContextTokens, 119);
  assert.equal(measurement.compressedReductionPercent, 83.8);
  assert.deepEqual(measurement.targetReductionThresholds, [
    {
      reductionPercent: 40,
      maxAllowedTokens: 440,
      minimumSavedTokens: 294,
    },
    {
      reductionPercent: 50,
      maxAllowedTokens: 367,
      minimumSavedTokens: 367,
    },
  ]);
});

test("workflow baseline is computed from persisted multi-agent meeting artifacts", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const ownerDrafts = [
    "OpenClaw draft round 1: define campaign goals, assets, owner, and review gates before launch.",
    "OpenClaw draft round 2: add measurable conversion targets, asset checklist, owner, approval gate, and fallback plan.",
  ];
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      return ownerDrafts[round - 1] ?? ownerDrafts.at(-1) ?? "";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      if (round === 1) {
        return {
          verdict: "disagree",
          content: "Hermes review round 1: disagree. Add measurable targets and explicit approval gates.",
        };
      }
      return {
        verdict: "agree",
        content: "Hermes review round 2: agree. Targets, owner, approval gate, and fallback plan are present.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      return `Final synthesis: execute the campaign plan.\n\n${input.draft}\n\n${input.review}`;
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-token-workflow-baseline-1",
    config: { maxRounds: 3 },
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-token-baseline", name: "campaign" },
      userRequest: "캠페인 제작 회의를 열고 OpenClaw와 Hermes 검토 후 최종 실행안을 만들어줘.",
    });
    const storedTurns = db.getTurns(result.task.id);
    const measurement = measureWorkflowTokenBaseline({ runResult: result, storedTurns });

    assert.equal(result.status, "finalized");
    assert.equal(measurement.turnCount, storedTurns.length);
    assert.equal(
      measurement.rawFullTextTokens,
      storedTurns.reduce((total, turn) => total + estimateTokenCount(turn.content), 0),
    );
    assert.equal(
      measurement.exposedLoopContextTokens,
      storedTurns.reduce((total, turn) => total + estimateTokenCount(turn.visibleSummary), 0),
    );
    assert.equal(measurement.rawFullTextTokens > measurement.exposedLoopContextTokens, true);
    assert.deepEqual(measurement.perTurn.map((turn) => turn.kind), storedTurns.map((turn) => turn.kind));
    assert.equal(measurement.targetReductionThresholds[0].reductionPercent, 40);
  } finally {
    db.close();
  }
});

test("representative workflow token usage compares before pruning and after compaction counts", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      if (round === 1) {
        return [
          "OpenClaw draft round 1: define the launch request, campaign assets, approval owner, and review gate.",
          "Include audience, conversion goal, creative deliverables, rollout checklist, and Hermes validation criteria.",
        ].join(" ");
      }

      return [
        "OpenClaw draft round 2: add measurable conversion targets, accountable owner, approval gate, fallback plan, and risk response.",
        "Keep only the current accepted plan in the next prompt context while raw drafts remain stored for audit.",
      ].join(" ");
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      if (round === 1) {
        return {
          verdict: "agree_with_changes",
          content:
            "Hermes review round 1: agree_with_changes. Add explicit metrics, approval gate, owner, and fallback plan before final synthesis.",
        };
      }

      return {
        verdict: "agree",
        content:
          "Hermes review round 2: agree. Metrics, owner, approval gate, and fallback plan are present.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      return [
        "Final synthesis: execute the approved campaign plan with measurable conversion targets.",
        input.draft,
        input.review,
      ].join("\n\n");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-representative-token-usage-1",
    config: { maxRounds: 3 },
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-token-usage", name: "campaign" },
      userRequest: "캠페인 제작 회의를 열고 OpenClaw 실행안과 Hermes 리뷰 후 최종안을 합성해줘.",
    });
    const storedTurns = db.getTurns(result.task.id);
    const measurement = measureRepresentativeWorkflowTokenUsage({
      runResult: result,
      storedTurns,
    });

    assert.equal(result.status, "finalized");
    assert.equal(measurement.method, "deterministic-local-estimate-v1");
    assert.equal(measurement.taskId, result.task.id);
    assert.equal(measurement.turnCount, storedTurns.length);
    assert.equal(
      measurement.beforePruningTokens,
      storedTurns.reduce((total, turn) => total + estimateTokenCount(turn.content), 0),
    );
    assert.equal(
      measurement.afterPruningTokens,
      storedTurns.reduce((total, turn) => total + estimateTokenCount(turn.visibleSummary), 0),
    );
    assert.equal(measurement.beforePruningTokens > measurement.afterPruningTokens, true);
    assert.equal(measurement.afterPruningTokens >= measurement.afterCompactionTokens, true);
    assert.equal(measurement.comparableTokenCounts, true);
    assert.equal(measurement.meetsFortyPercentCompactionTarget, true);
    assert.equal(measurement.compactionSavingsPercent >= measurement.pruningSavingsPercent, true);
    assert.deepEqual(measurement.sourceArtifacts, {
      runResultTaskId: result.task.id,
      storedTurnIds: storedTurns.map((turn) => turn.id),
      compressedContextSource: "generated-from-stored-turn-summaries",
    });
  } finally {
    db.close();
  }
});

test("workflow-level context reduction uses fewer tokens while preserving required workflow inputs", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft({ round }) {
      if (round === 1) {
        return [
          "OpenClaw draft round 1: define campaign audience, launch message, channel plan, owner, approval gate, and measurement baseline.",
          "Include full creative notes, rollout dependencies, review risks, data collection steps, fallback criteria, and detailed Hermes questions.",
          "Raw detail should be available for audit, but the next loop prompt should not need this full draft repeated verbatim.",
        ].join(" ");
      }

      return [
        "OpenClaw draft round 2: keep the campaign audience, owner, channel plan, approval gate, metrics, fallback criteria, and delivery checklist.",
        "Compress prior rejected details into accepted actions so Hermes can confirm final synthesis readiness without replaying every raw note.",
      ].join(" ");
    },
  };
  const reviewer: ReviewerExecutor = {
    async review({ round }) {
      if (round === 1) {
        return {
          verdict: "disagree",
          content:
            "Hermes review round 1: disagree. The draft has useful raw detail, but final readiness requires explicit success metrics, owner, approval gate, fallback criteria, and risk response.",
        };
      }

      return {
        verdict: "agree_with_changes",
        content:
          "Hermes review round 2: agree_with_changes. Success metrics, owner, approval gate, fallback criteria, and risk response are present for final synthesis.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize(input) {
      return [
        "Final synthesis: execute the campaign with the approved owner, metrics, approval gate, fallback criteria, and risk response.",
        input.draft,
        input.review,
      ].join("\n\n");
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-workflow-level-token-reduction-1",
    config: { maxRounds: 3 },
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-workflow-token-reduction", name: "campaign" },
      userRequest: "캠페인 제작 회의를 열고 OpenClaw 실행안과 Hermes 리뷰 후 최종안을 합성해줘.",
    });
    const storedTurns = db.getTurns(result.task.id);
    const requiredInputs = {
      userRequest: result.task.userRequest,
      taskBreakdown: result.requestAnalysis.taskBreakdown.map((task) => `${task.id}:${task.title}`),
      roleRoutes: result.requestAnalysis.roleRoutes.map((route) => `${route.taskId}:${route.role}`),
      meetingTurnIds: storedTurns.map((turn) => `${turn.round}:${turn.role}:${turn.kind}`),
      finalSynthesis: result.finalSynthesis,
      escalationReasons: result.escalationReasons,
    };
    const unreducedContext = [
      {
        id: "user-request",
        kind: "user_request" as const,
        content: requiredInputs.userRequest,
      },
      {
        id: "task-breakdown",
        kind: "task_breakdown" as const,
        content: requiredInputs.taskBreakdown.join("\n"),
      },
      {
        id: "role-routes",
        kind: "role_route" as const,
        content: requiredInputs.roleRoutes.join("\n"),
      },
      ...storedTurns.map((turn) => ({
        id: turn.id,
        kind: "meeting_turn" as const,
        content: turn.content,
        round: turn.round,
        role: turn.role,
      })),
      {
        id: "final-synthesis",
        kind: "final_synthesis" as const,
        content: result.finalSynthesis ?? "",
      },
      {
        id: "escalation",
        kind: "escalation" as const,
        content: result.escalationReasons.join("\n"),
      },
    ];
    const reducedContext = compactPromptContext(unreducedContext, {
      maxSummaryChars: 160,
      compressKinds: ["meeting_turn"],
    });
    const unreducedTokens = estimateTokenCount(unreducedContext.map((message) => message.content).join("\n"));
    const reducedTokens = estimateTokenCount(reducedContext.messages.map((message) => message.content).join("\n"));

    assert.equal(result.status, "finalized");
    assert.equal(storedTurns.length > 0, true);
    assert.equal(reducedContext.originalMessageCount, unreducedContext.length);
    assert.equal(reducedContext.compactedMessageCount, unreducedContext.length);
    assert.equal(reducedContext.removedMessageIds.length, 0);
    assert.equal(reducedContext.compressedMessageIds.length > 0, true);
    assert.equal(reducedTokens < unreducedTokens, true);

    assert.deepEqual(
      reducedContext.messages.filter((message) => message.kind !== "meeting_turn").map((message) => [
        message.id,
        message.kind,
        message.content,
      ]),
      unreducedContext.filter((message) => message.kind !== "meeting_turn").map((message) => [
        message.id,
        message.kind,
        message.content,
      ]),
    );
    assert.deepEqual(
      reducedContext.messages.filter((message) => message.kind === "meeting_turn").map((message) => [
        message.round,
        message.role,
      ]),
      storedTurns.map((turn) => [turn.round, turn.role]),
    );
    assert.deepEqual(
      {
        userRequest: reducedContext.messages.find((message) => message.id === "user-request")?.content,
        taskBreakdown: reducedContext.messages.find((message) => message.id === "task-breakdown")?.content.split("\n"),
        roleRoutes: reducedContext.messages.find((message) => message.id === "role-routes")?.content.split("\n"),
        meetingTurnIds: reducedContext.messages
          .filter((message) => message.kind === "meeting_turn")
          .map((message) => `${message.round}:${message.role}:${storedTurns.find((turn) => turn.id === message.id)?.kind}`),
        finalSynthesis: reducedContext.messages.find((message) => message.id === "final-synthesis")?.content,
        escalationReasons: reducedContext.messages.find((message) => message.id === "escalation")?.content.split("\n").filter(Boolean),
      },
      requiredInputs,
    );
    assert.equal(
      reducedContext.compressedMessageIds.every((messageId) => {
        const storedTurn = storedTurns.find((turn) => turn.id === messageId);
        const reducedMessage = reducedContext.messages.find((message) => message.id === messageId);
        return storedTurn !== undefined && reducedMessage !== undefined && reducedMessage.content !== storedTurn.content;
      }),
      true,
    );
  } finally {
    db.close();
  }
});

test("workflow baseline rejects missing or mismatched concrete artifacts", async () => {
  const db = new AiAgentDatabase();
  const discord = createFakeDiscord();
  const owner: OwnerExecutor = {
    async createDraft() {
      return "OpenClaw draft: concrete plan with owner and review gate.";
    },
  };
  const reviewer: ReviewerExecutor = {
    async review() {
      return {
        verdict: "agree",
        content: "Hermes review: agree. The plan is ready for final synthesis.",
      };
    },
  };
  const finalizer: FinalizerExecutor = {
    async synthesize() {
      return "Final synthesis: publish the approved execution plan.";
    },
  };
  const orchestrator = new CompanyOrchestrator({
    db,
    discord,
    owner,
    reviewer,
    finalizer,
    idFactory: () => "task-token-workflow-baseline-2",
  });

  try {
    const result = await orchestrator.runUserRequest({
      project: { channelId: "parent-token-baseline", name: "campaign" },
      userRequest: "캠페인 제작 회의를 열고 최종 실행안을 합성해줘.",
    });
    const storedTurns = db.getTurns(result.task.id);

    assert.throws(
      () =>
        measureWorkflowTokenBaseline({
          runResult: result,
          storedTurns: storedTurns.filter((turn) => turn.kind !== "review_request"),
        }),
      /missing stored artifact|missing required meeting artifacts/,
    );
    assert.throws(
      () =>
        measureWorkflowTokenBaseline({
          runResult: result,
          storedTurns: [{ ...storedTurns[0], taskId: "different-task" }, ...storedTurns.slice(1)],
        }),
      /does not belong to task/,
    );
  } finally {
    db.close();
  }
});

test("target thresholds compute the 40 to 50 percent reduction band from baseline tokens", () => {
  const measurement = measureCurrentTokenBaseline({
    turns: [
      {
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: "a".repeat(400),
        visibleSummary: "a".repeat(200),
      },
    ],
    compressedContext: "a".repeat(120),
  });

  assert.equal(measurement.rawFullTextTokens, 100);
  assert.deepEqual(measurement.targetReductionThresholds, [
    {
      reductionPercent: 40,
      maxAllowedTokens: 60,
      minimumSavedTokens: 40,
    },
    {
      reductionPercent: 50,
      maxAllowedTokens: 50,
      minimumSavedTokens: 50,
    },
  ]);
});

test("compressed context savings estimator compares raw baseline with proposed compressed context", () => {
  const representative = buildRepresentativeLoopContextInput();
  const estimate = estimateCompressedContextSavings({
    baselineContext: representative.turns.map((turn) => turn.content),
    proposedCompressedContext: representative.compressedContext ?? "",
  });

  assert.deepEqual(estimate, {
    method: "deterministic-local-estimate-v1",
    baselineTokens: 734,
    proposedCompressedTokens: 67,
    savedTokens: 667,
    savingsPercent: 90.9,
    meetsFortyPercentTarget: true,
  });
  assert.equal(estimate.savingsPercent >= 40, true);
});

test("representative compressed-context savings estimator proves the 40 percent target", () => {
  const estimate = estimateRepresentativeCompressedContextSavings();

  assert.equal(estimate.baselineTokens, 734);
  assert.equal(estimate.proposedCompressedTokens, 67);
  assert.equal(estimate.meetsFortyPercentTarget, true);
  assert.equal(estimate.savingsPercent >= 40, true);
});

test("token cost control verification exposes stable machine-readable pass/fail schema", () => {
  assert.deepEqual(verifyRepresentativeTokenCostControl(), {
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
});

test("token cost control command emits deterministic JSON output", () => {
  const result = executeTokenCostControlCheckCommand([]);
  const parsed = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.deepEqual(parsed, {
    command: "ai-agent check-token-cost-control",
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
  assert.deepEqual(checkTokenCostControl(), parsed);
});

test("token cost control artifact writer persists stable verification result", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-cost-control-artifact-"));
  try {
    const first = writeTokenCostControlVerificationArtifact({ projectRoot: root });
    const second = writeTokenCostControlVerificationArtifact({ projectRoot: root });
    const content = readFileSync(first.path, "utf8");
    const parsed = JSON.parse(content);

    assert.equal(first.path, join(root, "docs/generated/token-reduction-check-result.json"));
    assert.equal(basename(first.path), "token-reduction-check-result.json");
    assert.equal(existsSync(first.path), true);
    assert.equal(first.json, content);
    assert.equal(second.json, first.json);
    assert.deepEqual(parsed, verifyRepresentativeTokenCostControl());
    assert.deepEqual(Object.keys(parsed), [
      "schemaVersion",
      "status",
      "method",
      "baselineTokenCount",
      "optimizedTokenCount",
      "savedTokenCount",
      "percentSavings",
      "targetThreshold",
      "pass",
    ]);
    assert.equal(parsed.schemaVersion, "token-cost-control-check.v1");
    assert.equal(parsed.pass, true);
    assert.equal(parsed.percentSavings, 74.3);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("token cost control command can persist the check result artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-token-cost-control-command-artifact-"));
  const originalCwd = process.cwd();
  try {
    process.chdir(root);
    const result = executeTokenCostControlCheckCommand(["--write-artifact"]);
    const parsed = JSON.parse(result.stdout);
    const artifactContent = readFileSync(parsed.artifactPath, "utf8");

    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(parsed.command, "ai-agent check-token-cost-control");
    assert.equal(parsed.artifactPath, join(root, "docs/generated/token-reduction-check-result.json"));
    assert.deepEqual(JSON.parse(artifactContent), verifyRepresentativeTokenCostControl());
  } finally {
    process.chdir(originalCwd);
    rmSync(root, { recursive: true, force: true });
  }
});

test("token cost control command rejects invalid input predictably", () => {
  const result = executeTokenCostControlCheckCommand(["--unexpected"]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "unexpected argument: --unexpected",
  });
});

test("representative compressed-context artifact generation is deterministic across repeated runs", () => {
  const representative = buildRepresentativeLoopContextInput();
  const first = generateRepresentativeCompressedLoopContextArtifact(representative);
  const second = generateRepresentativeCompressedLoopContextArtifact(representative);

  assert.deepEqual(second, first);
  assert.equal(JSON.stringify(second), JSON.stringify(first));
  assert.deepEqual(first, {
    schemaVersion: "representative-compressed-loop-context-generation.v1",
    artifact: {
      schemaVersion: "compressed-loop-context.v1",
      requestSummary: "브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
      latestOpenClawSummary:
        "OpenClaw draft round 2: 성공 지표, 담당자, Hermes 승인 게이트, 저작권과 일정 리스크 대응을 추가한다.",
      latestHermesSummary:
        "Hermes review round 2: agree. 성공 지표, 책임자, 승인 게이트, 리스크 대응이 포함되어 final synthesis 가능.",
      latestHermesVerdict: "agree",
      acceptedFeedback: [
        "Hermes feedback accepted: add success metrics, accountable owner, approval gate, and risk response before final synthesis.",
      ],
      rejectedFeedback: ["Do not replay raw full-text meeting turns into the next loop context."],
      escalationReasons: [],
      content: [
        "Compressed loop context",
        "- request_summary: 브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
        "- latest_openclaw: OpenClaw draft round 2: 성공 지표, 담당자, Hermes 승인 게이트, 저작권과 일정 리스크 대응을 추가한다.",
        "- latest_hermes_verdict: agree",
        "- latest_hermes: Hermes review round 2: agree. 성공 지표, 책임자, 승인 게이트, 리스크 대응이 포함되어 final synthesis 가능.",
        "- accepted_feedback: Hermes feedback accepted: add success metrics, accountable owner, approval gate, and risk response before final synthesis.",
        "- rejected_feedback: Do not replay raw full-text meeting turns into the next loop context.",
        "- escalation_reasons: none",
      ].join("\n"),
    },
    savingsEstimate: {
      method: "deterministic-local-estimate-v1",
      baselineTokens: 734,
      proposedCompressedTokens: 189,
      savedTokens: 545,
      savingsPercent: 74.3,
      meetsFortyPercentTarget: true,
    },
  });
  assert.equal(first.savingsEstimate.meetsFortyPercentTarget, true);
  assert.equal(
    representative.turns.every((turn) => !first.artifact.content.includes(turn.content)),
    true,
  );
});

test("local token estimator is deterministic for empty, english, and korean text", () => {
  assert.equal(estimateTokenCount(""), 0);
  assert.equal(estimateTokenCount("OpenClaw final synthesis"), 7);
  assert.equal(estimateTokenCount("Hermes 리뷰 완료."), 5);
});

interface TokenBaselineFixture {
  name: string;
  input: TokenBaselineInput;
  expected: TokenBaselineMeasurement;
}

function loadTokenBaselineFixture(path: string): TokenBaselineFixture {
  const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));

  assertRecord(parsed, "token baseline fixture");
  assert.equal(typeof parsed.name, "string");
  assertRecord(parsed.input, "token baseline fixture input");
  assert.equal(Array.isArray(parsed.input.turns), true);
  assert.equal(parsed.input.turns.length > 0, true);
  assertRecord(parsed.expected, "token baseline fixture expected measurement");
  assert.equal(parsed.expected.method, "deterministic-local-estimate-v1");
  assert.equal(typeof parsed.expected.rawFullTextTokens, "number");
  assert.equal(typeof parsed.expected.exposedLoopContextTokens, "number");
  assert.equal(typeof parsed.expected.compressedLoopContextTokens, "number");
  assert.equal(Array.isArray(parsed.expected.perTurn), true);

  return parsed as TokenBaselineFixture;
}

function assertRecord(value: unknown, label: string): asserts value is Record<string, unknown> {
  assert.equal(typeof value, "object", `${label} must be an object`);
  assert.notEqual(value, null, `${label} must not be null`);
  assert.equal(Array.isArray(value), false, `${label} must not be an array`);
}

function createFakeDiscord(): DiscordDelivery & {
  parentPosts: string[];
  threadPosts: Array<{ threadId: string; content: string; fullContent?: string }>;
} {
  return {
    parentPosts: [],
    threadPosts: [],
    async createThread() {
      return { threadId: "thread-token-baseline-1", url: "https://discord.test/thread-token-baseline-1" };
    },
    async postParent(input) {
      this.parentPosts.push(input.content);
    },
    async postThread(input) {
      this.threadPosts.push(input);
    },
  };
}
