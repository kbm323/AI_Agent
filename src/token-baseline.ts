import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { AgentRole, TurnKind, TurnRecord } from "./types.ts";
import {
  buildCompressedLoopContextArtifact,
  type CompressedLoopContextArtifact,
} from "./summarization.ts";

export interface RepresentativeLoopTurn {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  content: string;
  visibleSummary: string;
}

export interface TokenBaselineInput {
  turns: RepresentativeLoopTurn[];
  compressedContext?: string;
}

export interface TurnTokenBaseline {
  round: number;
  role: AgentRole;
  kind: TurnKind;
  rawFullTextTokens: number;
  exposedSummaryTokens: number;
}

export interface TokenReductionThreshold {
  reductionPercent: number;
  maxAllowedTokens: number;
  minimumSavedTokens: number;
}

export interface TokenBaselineMeasurement {
  method: "deterministic-local-estimate-v1";
  turnCount: number;
  rawFullTextTokens: number;
  exposedLoopContextTokens: number;
  compressedLoopContextTokens: number;
  exposedReductionPercent: number;
  compressedReductionPercent: number;
  targetReductionThresholds: TokenReductionThreshold[];
  perTurn: TurnTokenBaseline[];
}

export interface RepresentativeWorkflowTokenUsageMeasurementInput {
  runResult: WorkflowTokenBaselineInput["runResult"];
  storedTurns: TurnRecord[];
  compressedContext?: string;
}

export interface RepresentativeWorkflowTokenUsageMeasurement {
  method: "deterministic-local-estimate-v1";
  taskId: string;
  turnCount: number;
  beforePruningTokens: number;
  afterPruningTokens: number;
  afterCompactionTokens: number;
  pruningSavedTokens: number;
  compactionSavedTokens: number;
  pruningSavingsPercent: number;
  compactionSavingsPercent: number;
  comparableTokenCounts: boolean;
  meetsFortyPercentCompactionTarget: boolean;
  sourceArtifacts: {
    runResultTaskId: string;
    storedTurnIds: string[];
    compressedContextSource: "provided" | "generated-from-stored-turn-summaries";
  };
  baseline: TokenBaselineMeasurement;
}

export interface WorkflowTokenBaselineInput {
  runResult: {
    task: { id: string };
    meetingHistory: Array<{
      round: number;
      role: AgentRole;
      kind: TurnKind;
      summary: string;
    }>;
  };
  storedTurns: TurnRecord[];
  compressedContext?: string;
}

export interface TokenSavingsEstimatorInput {
  baselineContext: string | string[];
  proposedCompressedContext: string;
}

export interface TokenSavingsEstimate {
  method: "deterministic-local-estimate-v1";
  baselineTokens: number;
  proposedCompressedTokens: number;
  savedTokens: number;
  savingsPercent: number;
  meetsFortyPercentTarget: boolean;
}

export type TokenAccountingValue = number | string | string[];

export interface TokenAccountingInput {
  baseline: TokenAccountingValue;
  optimized: TokenAccountingValue;
}

export interface TokenAccountingResult {
  method: "deterministic-local-estimate-v1";
  baselineTokens: number;
  optimizedTokens: number;
  absoluteReductionTokens: number;
  percentSavings: number;
  meetsFortyPercentTarget: boolean;
  meetsFiftyPercentTarget: boolean;
}

export interface TokenReductionTargetRange {
  minimumPercentSavings: number;
  maximumPercentSavings: number;
}

export interface TokenReductionSavingsMeasurementInput {
  baseline: TokenAccountingValue;
  reduced: TokenAccountingValue;
  targetRange?: TokenReductionTargetRange;
}

export interface TokenReductionSavingsMeasurement {
  method: "deterministic-local-estimate-v1";
  baselineTokens: number;
  reducedTokens: number;
  savedTokens: number;
  savingsPercent: number;
  targetRange: TokenReductionTargetRange;
  meetsMinimumTarget: boolean;
  withinTargetRange: boolean;
  exceedsTargetRange: boolean;
}

export interface TokenCostControlTargetThreshold {
  percentSavings: 40;
  maxOptimizedTokenCount: number;
  minimumSavedTokenCount: number;
}

export interface TokenCostControlVerificationResult {
  schemaVersion: "token-cost-control-check.v1";
  status: "passed" | "failed";
  method: "deterministic-local-estimate-v1";
  baselineTokenCount: number;
  optimizedTokenCount: number;
  savedTokenCount: number;
  percentSavings: number;
  targetThreshold: TokenCostControlTargetThreshold;
  pass: boolean;
}

export interface WrittenTokenCostControlVerificationArtifact {
  path: string;
  artifact: TokenCostControlVerificationResult;
  json: string;
}

export interface RepresentativeCompressedLoopContextGeneration {
  schemaVersion: "representative-compressed-loop-context-generation.v1";
  artifact: CompressedLoopContextArtifact;
  savingsEstimate: TokenSavingsEstimate;
}

export function buildRepresentativeLoopContextInput(): TokenBaselineInput {
  const turns: RepresentativeLoopTurn[] = [
    {
      round: 0,
      role: "openclaw-owner",
      kind: "request_analysis",
      content: [
        "Request analysis",
        "",
        "Summary: 브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
        "",
        "Task breakdown:",
        "- task-001: 요청 의도와 성공 기준 정리",
        "- task-002: OpenClaw 실행 초안 작성",
        "- task-003: Hermes 리뷰와 수렴 판단",
        "- task-004: 최종 합성 또는 escalation",
        "",
        "Role routing:",
        "- task-001 -> openclaw-owner: 요청을 작업 단위로 분해하고 회의 목표를 고정한다.",
        "- task-002 -> openclaw-owner: 실행 가능한 초안을 작성하고 Hermes에게 리뷰를 요청한다.",
        "- task-003 -> hermes-reviewer: 초안 검토, 리스크 식별, 수렴 여부 또는 사용자 결정 필요성을 판단한다.",
        "- task-004 -> openclaw-finalizer: 합의된 내용을 최종 산출물로 합성하거나 escalation artifact를 남긴다.",
        "",
        "Loop context:",
        "- request_summary: 브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
        "- required_flow: analysis -> routing -> OpenClaw draft -> Hermes review -> final synthesis/escalation",
        "- storage_boundary: full text stays in SQLite; exposed context uses summaries",
      ].join("\n"),
      visibleSummary: [
        "Request analysis",
        "",
        "Summary: 브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
        "",
        "Task breakdown:",
        "- task-001: 요청 의도와 성공 기준 정리",
        "- task-002: OpenClaw 실행 초안 작성",
        "- task-003: Hermes 리뷰와 수렴 판단",
        "- task-004: 최종 합성 또는 escalation",
      ].join("\n"),
    },
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content:
        "OpenClaw draft: 캠페인 목적은 첫 주 문의 전환을 늘리는 것이다. 핵심 메시지는 고객의 현재 문제, 해결 장면, 실행 요청 순서로 구성한다. 산출물은 30초 영상 구조, 랜딩 페이지 문안, 배포 체크리스트, Hermes 검토 기준을 포함한다.",
      visibleSummary:
        "OpenClaw draft: 첫 주 문의 전환을 목표로 30초 영상 구조, 랜딩 페이지 문안, 배포 체크리스트, Hermes 검토 기준을 제안한다.",
    },
    {
      round: 1,
      role: "openclaw-owner",
      kind: "review_request",
      content: [
        "Hermes reviewer request (round 1)",
        "",
        "User request:",
        "브랜드 캠페인 제작 회의를 진행하고 최종 실행안을 만들어줘.",
        "",
        "Captured OpenClaw draft:",
        "캠페인 목적은 첫 주 문의 전환을 늘리는 것이다. 핵심 메시지는 고객의 현재 문제, 해결 장면, 실행 요청 순서로 구성한다. 산출물은 30초 영상 구조, 랜딩 페이지 문안, 배포 체크리스트, Hermes 검토 기준을 포함한다.",
        "",
        "Review task:",
        "OpenClaw draft를 기준으로 비판/보완/동의 여부를 판단하라.",
        "독립 제안을 새로 만들지 말고, draft의 장점/문제/리스크/수정안을 분리하라.",
        "",
        "Verdict must be one of: agree, agree_with_changes, disagree, needs_user_decision.",
      ].join("\n"),
      visibleSummary: "Hermes reviewer request (round 1): OpenClaw draft의 장점, 문제, 리스크, 수정안을 검토한다.",
    },
    {
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      content:
        "Hermes review: disagree. 성공 기준이 문의 전환이라고 되어 있지만 측정 기준과 책임자가 없다. 영상 구조는 가능하지만 승인 조건, 리스크 대응, 수정 기한이 없으므로 OpenClaw는 다음 라운드에서 측정 지표와 게이트를 추가해야 한다.",
      visibleSummary:
        "Hermes review: disagree. 측정 기준, 책임자, 승인 조건, 리스크 대응, 수정 기한을 다음 라운드에 추가해야 한다.",
    },
    {
      round: 2,
      role: "openclaw-owner",
      kind: "owner_draft",
      content:
        "OpenClaw draft round 2: 문의 전환율, 상담 예약 수, 영상 클릭률을 성공 지표로 둔다. 마케팅 담당자가 지표를 수집하고 Hermes가 공개 전 승인 게이트를 검토한다. 리스크 대응은 저작권 확인, 브랜드 문구 승인, 일정 지연시 축소 배포안으로 정리한다.",
      visibleSummary:
        "OpenClaw draft round 2: 성공 지표, 담당자, Hermes 승인 게이트, 저작권과 일정 리스크 대응을 추가한다.",
    },
    {
      round: 2,
      role: "hermes-reviewer",
      kind: "review",
      content:
        "Hermes review round 2: agree. 수정안은 성공 지표, 책임자, 승인 게이트, 리스크 대응을 포함한다. 남은 작업은 final synthesis에서 실행 순서와 산출물 목록을 압축해 전달하는 것이다.",
      visibleSummary:
        "Hermes review round 2: agree. 성공 지표, 책임자, 승인 게이트, 리스크 대응이 포함되어 final synthesis 가능.",
    },
    {
      round: 5,
      role: "openclaw-finalizer",
      kind: "final_synthesis",
      content:
        "Final synthesis: 브랜드 캠페인은 30초 영상, 랜딩 문안, 배포 체크리스트로 진행한다. 성공 지표는 문의 전환율, 상담 예약 수, 영상 클릭률이다. 공개 전 Hermes 승인 게이트에서 저작권, 브랜드 문구, 일정 리스크를 확인한다.",
      visibleSummary:
        "Final synthesis: 30초 영상, 랜딩 문안, 배포 체크리스트를 실행하고 Hermes 승인 게이트에서 주요 리스크를 확인한다.",
    },
  ];

  return {
    turns,
    compressedContext: [
      "Compressed loop context",
      "- request: 브랜드 캠페인 제작 회의와 최종 실행안",
      "- latest_openclaw: 성공 지표, 담당자, Hermes 승인 게이트, 저작권과 일정 리스크 대응 포함",
      "- latest_hermes: agree",
      "- finalizer_focus: 실행 순서와 산출물 목록만 합성",
    ].join("\n"),
  };
}

export function measureCurrentTokenBaseline(input = buildRepresentativeLoopContextInput()): TokenBaselineMeasurement {
  const perTurn = input.turns.map((turn) => ({
    round: turn.round,
    role: turn.role,
    kind: turn.kind,
    rawFullTextTokens: estimateTokenCount(turn.content),
    exposedSummaryTokens: estimateTokenCount(turn.visibleSummary),
  }));
  const rawFullTextTokens = sum(perTurn.map((turn) => turn.rawFullTextTokens));
  const exposedLoopContextTokens = sum(perTurn.map((turn) => turn.exposedSummaryTokens));
  const compressedLoopContextTokens = estimateTokenCount(input.compressedContext ?? buildCompressedLoopContext(input.turns));

  return {
    method: "deterministic-local-estimate-v1",
    turnCount: input.turns.length,
    rawFullTextTokens,
    exposedLoopContextTokens,
    compressedLoopContextTokens,
    exposedReductionPercent: reductionPercent(rawFullTextTokens, exposedLoopContextTokens),
    compressedReductionPercent: reductionPercent(rawFullTextTokens, compressedLoopContextTokens),
    targetReductionThresholds: buildTokenReductionThresholds(rawFullTextTokens),
    perTurn,
  };
}

export function measureTurnTokenBaseline(turns: TurnRecord[]): TokenBaselineMeasurement {
  return measureCurrentTokenBaseline({
    turns: turns.map((turn) => ({
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      content: turn.content,
      visibleSummary: turn.visibleSummary,
    })),
  });
}

export function measureWorkflowTokenBaseline(input: WorkflowTokenBaselineInput): TokenBaselineMeasurement {
  validateWorkflowTokenBaselineInput(input);

  return measureCurrentTokenBaseline({
    turns: input.storedTurns.map((turn) => ({
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      content: turn.content,
      visibleSummary: turn.visibleSummary,
    })),
    compressedContext: input.compressedContext,
  });
}

export function measureRepresentativeWorkflowTokenUsage(
  input: RepresentativeWorkflowTokenUsageMeasurementInput,
): RepresentativeWorkflowTokenUsageMeasurement {
  const baseline = measureWorkflowTokenBaseline(input);
  const afterCompactionTokens = baseline.compressedLoopContextTokens;
  const compressedContextSource = input.compressedContext ? "provided" : "generated-from-stored-turn-summaries";
  const compactionSavingsPercent = reductionPercent(baseline.rawFullTextTokens, afterCompactionTokens);

  return {
    method: "deterministic-local-estimate-v1",
    taskId: input.runResult.task.id,
    turnCount: baseline.turnCount,
    beforePruningTokens: baseline.rawFullTextTokens,
    afterPruningTokens: baseline.exposedLoopContextTokens,
    afterCompactionTokens,
    pruningSavedTokens: baseline.rawFullTextTokens - baseline.exposedLoopContextTokens,
    compactionSavedTokens: baseline.rawFullTextTokens - afterCompactionTokens,
    pruningSavingsPercent: baseline.exposedReductionPercent,
    compactionSavingsPercent,
    comparableTokenCounts:
      baseline.rawFullTextTokens >= baseline.exposedLoopContextTokens &&
      baseline.exposedLoopContextTokens >= afterCompactionTokens,
    meetsFortyPercentCompactionTarget: compactionSavingsPercent >= 40,
    sourceArtifacts: {
      runResultTaskId: input.runResult.task.id,
      storedTurnIds: input.storedTurns.map((turn) => turn.id),
      compressedContextSource,
    },
    baseline,
  };
}

export function estimateCompressedContextSavings(input: TokenSavingsEstimatorInput): TokenSavingsEstimate {
  const baselineContext =
    typeof input.baselineContext === "string" ? input.baselineContext : input.baselineContext.join("\n");
  const baselineTokens = estimateTokenCount(baselineContext);
  const proposedCompressedTokens = estimateTokenCount(input.proposedCompressedContext);
  const savedTokens = Math.max(0, baselineTokens - proposedCompressedTokens);
  const savingsPercent = reductionPercent(baselineTokens, proposedCompressedTokens);

  return {
    method: "deterministic-local-estimate-v1",
    baselineTokens,
    proposedCompressedTokens,
    savedTokens,
    savingsPercent,
    meetsFortyPercentTarget: savingsPercent >= 40,
  };
}

export function estimateRepresentativeCompressedContextSavings(
  input = buildRepresentativeLoopContextInput(),
): TokenSavingsEstimate {
  return estimateCompressedContextSavings({
    baselineContext: input.turns.map((turn) => turn.content),
    proposedCompressedContext: input.compressedContext ?? buildCompressedLoopContext(input.turns),
  });
}

export function accountTokenReduction(input: TokenAccountingInput): TokenAccountingResult {
  const baselineTokens = resolveTokenAccountingValue(input.baseline, "baseline");
  const optimizedTokens = resolveTokenAccountingValue(input.optimized, "optimized");
  const absoluteReductionTokens = baselineTokens - optimizedTokens;
  const percentSavings = reductionPercent(baselineTokens, optimizedTokens);

  return {
    method: "deterministic-local-estimate-v1",
    baselineTokens,
    optimizedTokens,
    absoluteReductionTokens,
    percentSavings,
    meetsFortyPercentTarget: percentSavings >= 40,
    meetsFiftyPercentTarget: percentSavings >= 50,
  };
}

export function measureTokenReductionSavings(
  input: TokenReductionSavingsMeasurementInput,
): TokenReductionSavingsMeasurement {
  const targetRange = input.targetRange ?? {
    minimumPercentSavings: 40,
    maximumPercentSavings: 50,
  };
  validateTokenReductionTargetRange(targetRange);

  const baselineTokens = resolveTokenAccountingValue(input.baseline, "baseline");
  const reducedTokens = resolveTokenAccountingValue(input.reduced, "optimized");
  const savingsPercent = reductionPercent(baselineTokens, reducedTokens);
  const savedTokens = baselineTokens - reducedTokens;

  return {
    method: "deterministic-local-estimate-v1",
    baselineTokens,
    reducedTokens,
    savedTokens,
    savingsPercent,
    targetRange,
    meetsMinimumTarget: savingsPercent >= targetRange.minimumPercentSavings,
    withinTargetRange:
      savingsPercent >= targetRange.minimumPercentSavings && savingsPercent <= targetRange.maximumPercentSavings,
    exceedsTargetRange: savingsPercent > targetRange.maximumPercentSavings,
  };
}

export function verifyRepresentativeTokenCostControl(
  input = buildRepresentativeLoopContextInput(),
): TokenCostControlVerificationResult {
  const generated = generateRepresentativeCompressedLoopContextArtifact(input);
  const baselineTokenCount = generated.savingsEstimate.baselineTokens;
  const optimizedTokenCount = generated.savingsEstimate.proposedCompressedTokens;
  const savedTokenCount = generated.savingsEstimate.savedTokens;
  const percentSavings = generated.savingsEstimate.savingsPercent;
  const maxOptimizedTokenCount = Math.floor(baselineTokenCount * 0.6);
  const minimumSavedTokenCount = baselineTokenCount - maxOptimizedTokenCount;
  const pass = percentSavings >= 40 && optimizedTokenCount <= maxOptimizedTokenCount;

  return {
    schemaVersion: "token-cost-control-check.v1",
    status: pass ? "passed" : "failed",
    method: "deterministic-local-estimate-v1",
    baselineTokenCount,
    optimizedTokenCount,
    savedTokenCount,
    percentSavings,
    targetThreshold: {
      percentSavings: 40,
      maxOptimizedTokenCount,
      minimumSavedTokenCount,
    },
    pass,
  };
}

export function writeTokenCostControlVerificationArtifact(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): WrittenTokenCostControlVerificationArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? "docs/generated/token-reduction-check-result.json";
  const resolvedPath = resolve(projectRoot, outputPath);
  const artifact = verifyRepresentativeTokenCostControl();
  const json = `${JSON.stringify(artifact, null, 2)}\n`;

  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, json, "utf8");

  return {
    path: resolvedPath,
    artifact,
    json,
  };
}

export function generateRepresentativeCompressedLoopContextArtifact(
  input = buildRepresentativeLoopContextInput(),
): RepresentativeCompressedLoopContextGeneration {
  const artifact = buildCompressedLoopContextArtifact({
    userRequestSummary: extractRepresentativeRequestSummary(input),
    meetingTurns: input.turns.map((turn) => ({
      round: turn.round,
      role: turn.role,
      kind: turn.kind,
      summary: turn.visibleSummary,
    })),
    acceptedFeedback: [
      "Hermes feedback accepted: add success metrics, accountable owner, approval gate, and risk response before final synthesis.",
    ],
    rejectedFeedback: [
      "Do not replay raw full-text meeting turns into the next loop context.",
    ],
    escalationReasons: [],
  });

  return {
    schemaVersion: "representative-compressed-loop-context-generation.v1",
    artifact,
    savingsEstimate: estimateCompressedContextSavings({
      baselineContext: input.turns.map((turn) => turn.content),
      proposedCompressedContext: artifact.content,
    }),
  };
}

export function estimateTokenCount(text: string): number {
  const normalized = text.trim().replace(/\s+/g, " ");
  if (normalized.length === 0) return 0;

  const segments = normalized.match(/[A-Za-z0-9_]+|[가-힣]+|[^\sA-Za-z0-9_가-힣]/gu) ?? [];
  return sum(
    segments.map((segment) => {
      if (/^[A-Za-z0-9_]+$/.test(segment)) return Math.ceil(segment.length / 4);
      if (/^[가-힣]+$/u.test(segment)) return Math.ceil(segment.length / 2);
      return 1;
    }),
  );
}

function extractRepresentativeRequestSummary(input: TokenBaselineInput): string {
  const requestAnalysis = input.turns.find((turn) => turn.kind === "request_analysis");
  const summaryLine = requestAnalysis?.visibleSummary
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.startsWith("Summary:"));

  return summaryLine?.replace(/^Summary:\s*/, "") || "Representative multi-agent meeting loop context";
}

function validateWorkflowTokenBaselineInput(input: WorkflowTokenBaselineInput): void {
  if (input.storedTurns.length === 0) {
    throw new Error("workflow token baseline requires at least one stored turn artifact");
  }

  const taskId = input.runResult.task.id;
  const mismatchedTurn = input.storedTurns.find((turn) => turn.taskId !== taskId);
  if (mismatchedTurn) {
    throw new Error(`workflow token baseline turn ${mismatchedTurn.id} does not belong to task ${taskId}`);
  }

  const historyKey = (turn: { round: number; role: AgentRole; kind: TurnKind; summary: string }) =>
    `${turn.round}:${turn.role}:${turn.kind}:${turn.summary}`;
  const storedSummaryKeys = new Set(
    input.storedTurns.map((turn) =>
      historyKey({
        round: turn.round,
        role: turn.role,
        kind: turn.kind,
        summary: turn.visibleSummary,
      }),
    ),
  );
  const missingHistoryTurn = input.runResult.meetingHistory.find((turn) => !storedSummaryKeys.has(historyKey(turn)));
  if (missingHistoryTurn) {
    throw new Error(
      `workflow token baseline missing stored artifact for ${missingHistoryTurn.round}:${missingHistoryTurn.role}:${missingHistoryTurn.kind}`,
    );
  }

  const requiredKinds: TurnKind[] = ["request_analysis", "owner_draft", "review_request", "review"];
  const presentKinds = new Set(input.storedTurns.map((turn) => turn.kind));
  const missingRequiredKinds = requiredKinds.filter((kind) => !presentKinds.has(kind));
  if (missingRequiredKinds.length > 0) {
    throw new Error(`workflow token baseline missing required meeting artifacts: ${missingRequiredKinds.join(", ")}`);
  }

  if (!presentKinds.has("final_synthesis") && !presentKinds.has("escalation")) {
    throw new Error("workflow token baseline requires a final_synthesis or escalation artifact");
  }
}

function buildCompressedLoopContext(turns: RepresentativeLoopTurn[]): string {
  const latestOpenClaw = [...turns].reverse().find((turn) => turn.role === "openclaw-owner")?.visibleSummary ?? "";
  const latestHermes = [...turns].reverse().find((turn) => turn.role === "hermes-reviewer")?.visibleSummary ?? "";
  const latestFinalizer = [...turns].reverse().find((turn) => turn.role === "openclaw-finalizer")?.visibleSummary ?? "";

  return [
    "Compressed loop context",
    latestOpenClaw && `- latest_openclaw: ${latestOpenClaw}`,
    latestHermes && `- latest_hermes: ${latestHermes}`,
    latestFinalizer && `- latest_finalizer: ${latestFinalizer}`,
  ]
    .filter(Boolean)
    .join("\n");
}

function resolveTokenAccountingValue(value: TokenAccountingValue, fieldName: "baseline" | "optimized"): number {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < 0) {
      throw new Error(`${fieldName} token count must be a finite non-negative number`);
    }
    return Math.round(value);
  }

  return estimateTokenCount(typeof value === "string" ? value : value.join("\n"));
}

function validateTokenReductionTargetRange(targetRange: TokenReductionTargetRange): void {
  if (!Number.isFinite(targetRange.minimumPercentSavings) || !Number.isFinite(targetRange.maximumPercentSavings)) {
    throw new Error("token reduction target range percentages must be finite numbers");
  }
  if (targetRange.minimumPercentSavings < 0 || targetRange.maximumPercentSavings < 0) {
    throw new Error("token reduction target range percentages must be non-negative");
  }
  if (targetRange.minimumPercentSavings > targetRange.maximumPercentSavings) {
    throw new Error("token reduction target minimum cannot exceed maximum");
  }
}

function reductionPercent(before: number, after: number): number {
  if (before === 0) return 0;
  return Number((((before - after) / before) * 100).toFixed(1));
}

function buildTokenReductionThresholds(rawFullTextTokens: number): TokenReductionThreshold[] {
  return [40, 50].map((reductionPercent) => {
    const maxAllowedTokens = Math.floor(rawFullTextTokens * ((100 - reductionPercent) / 100));

    return {
      reductionPercent,
      maxAllowedTokens,
      minimumSavedTokens: rawFullTextTokens - maxAllowedTokens,
    };
  });
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
