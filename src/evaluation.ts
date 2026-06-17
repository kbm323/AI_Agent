import type { ReviewEvidenceArtifact, ReviewFinding, ReviewFindingCategory, ReviewFindingSeverity } from "./inspection.ts";

export const evaluationCategoryPriority: ReviewFindingCategory[] = [
  "error_frequency",
  "maintainability",
  "token_cost",
  "architecture_fit",
  "feature_completeness",
];

export interface EvaluationDecisionCriterion {
  category: ReviewFindingCategory;
  priority: number;
  label: string;
}

const evaluationDecisionCriterionLabels: Record<ReviewFindingCategory, string> = {
  error_frequency: "error frequency",
  maintainability: "maintainability difficulty",
  token_cost: "token cost",
  architecture_fit: "architecture fit",
  feature_completeness: "feature completeness",
};

export const evaluationDecisionPolicy: EvaluationDecisionCriterion[] = evaluationCategoryPriority.map((category, index) => ({
  category,
  priority: index + 1,
  label: evaluationDecisionCriterionLabels[category],
}));

export interface EvaluationCategorySummary {
  category: ReviewFindingCategory;
  priority: number;
  findingCount: number;
  highestSeverity?: ReviewFindingSeverity;
}

export interface ProjectFindingEvaluation {
  priorityOrder: ReviewFindingCategory[];
  categorySummaries: EvaluationCategorySummary[];
  orderedFindings: ReviewFinding[];
  rankedEvidence: RankedReviewEvidence[];
  dominantCategory?: ReviewFindingCategory;
  recommendation: ImplementationDecisionOutcome;
  justification: ImplementationDecisionJustification;
}

export interface RankedReviewEvidence {
  rank: number;
  priority: number;
  category: ReviewFindingCategory;
  severity: ReviewFindingSeverity;
  finding: ReviewFinding;
}

export type ImplementationDecisionOutcome = ReviewEvidenceArtifact["summary"]["recommendation"];

export const implementationDecisionLabels = ["Keep", "partial redesign", "full replan"] as const;

export type ImplementationDecisionLabel = "Keep" | "partial redesign" | "full replan";

export interface ImplementationDecision {
  outcome: ImplementationDecisionOutcome;
  label: ImplementationDecisionLabel;
}

export type StructuredEvaluationRiskLevel = "none" | ReviewFindingSeverity;

export interface StructuredEvaluationCriterionInput {
  category: ReviewFindingCategory;
  risk: StructuredEvaluationRiskLevel;
  evidenceCount?: number;
  targetMet?: boolean;
}

export interface StructuredImplementationEvaluationInput {
  criteria: StructuredEvaluationCriterionInput[];
}

export interface ImplementationDecisionJustification {
  outcome: ImplementationDecisionOutcome;
  label: ImplementationDecisionLabel;
  rule: "critical_evidence" | "high_or_token_cost_evidence" | "no_redesign_evidence";
  summary: string;
  priorityOrder: ReviewFindingCategory[];
  supportingEvidence: ImplementationDecisionJustificationEvidence[];
}

export interface ImplementationDecisionJustificationEvidence {
  rank: number;
  priority: number;
  category: ReviewFindingCategory;
  severity: ReviewFindingSeverity;
  findingId: string;
  title: string;
}

export interface CitedEvaluationEvidence {
  rank: number;
  priority: number;
  category: ReviewFindingCategory;
  findingId?: string;
  finding?: Pick<ReviewFinding, "id">;
}

const severityPriority: ReviewFindingSeverity[] = ["critical", "high", "medium", "low"];

export function evaluateProjectFindings(findings: ReviewFinding[]): ProjectFindingEvaluation {
  const orderedFindings = [...findings].sort(compareFindingsByEvaluationPriority);
  const rankedEvidence = rankReviewEvidence(findings);
  const decision = decideImplementationDirection(rankedEvidence);
  const categorySummaries = evaluationCategoryPriority.map((category, index) => {
    const categoryFindings = findings.filter((finding) => finding.category === category);
    return {
      category,
      priority: index + 1,
      findingCount: categoryFindings.length,
      highestSeverity: highestSeverity(categoryFindings),
    };
  });

  return {
    priorityOrder: [...evaluationCategoryPriority],
    categorySummaries,
    orderedFindings,
    rankedEvidence,
    dominantCategory: categorySummaries.find((summary) => summary.findingCount > 0)?.category,
    recommendation: decision.outcome,
    justification: justifyImplementationDecision(rankedEvidence, decision.outcome),
  };
}

export function rankReviewEvidence(findings: ReviewFinding[]): RankedReviewEvidence[] {
  return [...findings].sort(compareFindingsByEvaluationPriority).map((finding, index) => ({
    rank: index + 1,
    priority: categoryRank(finding.category) + 1,
    category: finding.category,
    severity: finding.severity,
    finding,
  }));
}

export function rankEvaluationDecisionCriteria(
  categories: ReviewFindingCategory[],
): EvaluationDecisionCriterion[] {
  const requestedCategories = new Set(categories);
  return evaluationDecisionPolicy.filter((criterion) => requestedCategories.has(criterion.category));
}

export function compareFindingsByEvaluationPriority(left: ReviewFinding, right: ReviewFinding): number {
  return (
    compareNumber(categoryRank(left.category), categoryRank(right.category)) ||
    compareNumber(severityRank(left.severity), severityRank(right.severity)) ||
    compareStable(left.id, right.id)
  );
}

export function decideImplementationDirection(rankedEvidence: RankedReviewEvidence[]): ImplementationDecision {
  if (rankedEvidence.some((evidence) => evidence.severity === "critical")) {
    return {
      outcome: "full_replan",
      label: "full replan",
    };
  }
  if (rankedEvidence.some((evidence) => evidence.severity === "high" || evidence.category === "token_cost")) {
    return {
      outcome: "partial_redesign",
      label: "partial redesign",
    };
  }
  return {
    outcome: "keep",
    label: "Keep",
  };
}

export function decideImplementationDirectionFromStructuredEvaluation(
  input: StructuredImplementationEvaluationInput,
): ImplementationDecision {
  const criteriaWithEvidence = input.criteria.filter(hasStructuredDecisionEvidence);

  if (criteriaWithEvidence.some((criterion) => criterion.risk === "critical")) {
    return decisionForOutcome("full_replan");
  }
  if (
    criteriaWithEvidence.some(
      (criterion) =>
        criterion.risk === "high" ||
        criterion.category === "token_cost" ||
        criterion.targetMet === false,
    )
  ) {
    return decisionForOutcome("partial_redesign");
  }
  return decisionForOutcome("keep");
}

export function isImplementationDecisionLabel(value: unknown): value is ImplementationDecisionLabel {
  return typeof value === "string" && implementationDecisionLabels.includes(value as ImplementationDecisionLabel);
}

export function assertImplementationDecisionLabel(value: unknown, fieldName = "decision result"): asserts value is ImplementationDecisionLabel {
  if (!isImplementationDecisionLabel(value)) {
    throw new TypeError(
      `${fieldName} must be exactly one of: ${implementationDecisionLabels.join(", ")}`,
    );
  }
}

export function justifyImplementationDecision(
  rankedEvidence: RankedReviewEvidence[],
  selectedOutcome: ImplementationDecisionOutcome,
): ImplementationDecisionJustification {
  const decision = decisionForOutcome(selectedOutcome);
  const rule = decisionRuleForOutcome(selectedOutcome);
  const supportingEvidence = selectSupportingEvidence(rankedEvidence, selectedOutcome);
  return {
    outcome: selectedOutcome,
    label: decision.label,
    rule,
    summary: buildJustificationSummary(decision.label, rule, supportingEvidence),
    priorityOrder: [...evaluationCategoryPriority],
    supportingEvidence,
  };
}

export function formatCitedEvaluationEvidence(evidence: Array<CitedEvaluationEvidence | RankedReviewEvidence>): string {
  if (evidence.length === 0) {
    return "none";
  }
  return [...evidence]
    .sort(compareCitedEvaluationEvidence)
    .map((item) => `#${item.rank} ${citedFindingId(item)}`)
    .join(", ");
}

function highestSeverity(findings: ReviewFinding[]): ReviewFindingSeverity | undefined {
  return [...findings].sort((left, right) => compareNumber(severityRank(left.severity), severityRank(right.severity)))[0]?.severity;
}

function categoryRank(category: ReviewFindingCategory): number {
  const index = evaluationCategoryPriority.indexOf(category);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function severityRank(severity: ReviewFindingSeverity): number {
  const index = severityPriority.indexOf(severity);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function compareNumber(left: number, right: number): number {
  return left - right;
}

function compareStable(left: string, right: string): number {
  if (left < right) {
    return -1;
  }
  if (left > right) {
    return 1;
  }
  return 0;
}

function compareCitedEvaluationEvidence(
  left: CitedEvaluationEvidence | RankedReviewEvidence,
  right: CitedEvaluationEvidence | RankedReviewEvidence,
): number {
  return (
    compareNumber(categoryRank(left.category), categoryRank(right.category)) ||
    compareNumber(left.priority, right.priority) ||
    compareNumber(left.rank, right.rank) ||
    compareStable(citedFindingId(left), citedFindingId(right))
  );
}

function citedFindingId(evidence: CitedEvaluationEvidence | RankedReviewEvidence): string {
  return "findingId" in evidence ? evidence.findingId : evidence.finding.id;
}

function hasStructuredDecisionEvidence(criterion: StructuredEvaluationCriterionInput): boolean {
  return criterion.risk !== "none" || (criterion.evidenceCount ?? 0) > 0 || criterion.targetMet === false;
}

function decisionForOutcome(outcome: ImplementationDecisionOutcome): ImplementationDecision {
  if (outcome === "full_replan") {
    return { outcome, label: "full replan" };
  }
  if (outcome === "partial_redesign") {
    return { outcome, label: "partial redesign" };
  }
  return { outcome, label: "Keep" };
}

function decisionRuleForOutcome(
  outcome: ImplementationDecisionOutcome,
): ImplementationDecisionJustification["rule"] {
  if (outcome === "full_replan") {
    return "critical_evidence";
  }
  if (outcome === "partial_redesign") {
    return "high_or_token_cost_evidence";
  }
  return "no_redesign_evidence";
}

function selectSupportingEvidence(
  rankedEvidence: RankedReviewEvidence[],
  selectedOutcome: ImplementationDecisionOutcome,
): ImplementationDecisionJustificationEvidence[] {
  const matchingEvidence = rankedEvidence.filter((evidence) => evidenceSupportsOutcome(evidence, selectedOutcome));
  const selectedEvidence = matchingEvidence.length > 0 ? matchingEvidence : rankedEvidence.slice(0, 3);
  return selectedEvidence.map((evidence) => ({
    rank: evidence.rank,
    priority: evidence.priority,
    category: evidence.category,
    severity: evidence.severity,
    findingId: evidence.finding.id,
    title: evidence.finding.title,
  }));
}

function evidenceSupportsOutcome(
  evidence: RankedReviewEvidence,
  selectedOutcome: ImplementationDecisionOutcome,
): boolean {
  if (selectedOutcome === "full_replan") {
    return evidence.severity === "critical";
  }
  if (selectedOutcome === "partial_redesign") {
    return evidence.severity === "high" || evidence.category === "token_cost";
  }
  return false;
}

function buildJustificationSummary(
  label: ImplementationDecisionLabel,
  rule: ImplementationDecisionJustification["rule"],
  supportingEvidence: ImplementationDecisionJustificationEvidence[],
): string {
  if (rule === "critical_evidence") {
    return `${label}: critical evidence requires full replanning; supporting findings: ${formatSupportingFindingIds(supportingEvidence)}.`;
  }
  if (rule === "high_or_token_cost_evidence") {
    return `${label}: high-severity or token-cost evidence requires scoped redesign; supporting findings: ${formatSupportingFindingIds(supportingEvidence)}.`;
  }
  return `${label}: no critical, high-severity, or token-cost evidence was ranked for redesign.`;
}

function formatSupportingFindingIds(supportingEvidence: ImplementationDecisionJustificationEvidence[]): string {
  return formatCitedEvaluationEvidence(supportingEvidence);
}
