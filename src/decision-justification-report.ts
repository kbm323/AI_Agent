import {
  decideImplementationDirectionFromStructuredEvaluation,
  evaluationDecisionPolicy,
  justifyImplementationDecision,
  rankReviewEvidence,
  type ImplementationDecisionJustificationEvidence,
  type ImplementationDecisionOutcome,
  type ImplementationDecisionLabel,
  type StructuredEvaluationCriterionInput,
} from "./evaluation.ts";
import type { ReviewFinding, ReviewFindingCategory } from "./inspection.ts";

export interface SelectedRecommendationInput {
  outcome: ImplementationDecisionOutcome;
  rationale?: string;
}

export interface StructuredEvaluationEvidenceInput {
  artifactPath?: string;
  criteria: StructuredEvaluationCriterionInput[];
  evidenceItems?: ReviewFinding[];
}

export interface DecisionJustificationReportInput {
  selectedRecommendation: ImplementationDecisionOutcome | SelectedRecommendationInput;
  evidence: StructuredEvaluationEvidenceInput;
}

export interface DecisionJustificationCriterionReport {
  category: ReviewFindingCategory;
  priority: number;
  label: string;
  risk: StructuredEvaluationCriterionInput["risk"];
  evidenceCount: number;
  targetMet?: boolean;
  decisionRelevant: boolean;
}

export interface DecisionJustificationReport {
  schemaVersion: "decision-justification-report.v1";
  selectedRecommendation: ImplementationDecisionOutcome;
  selectedLabel: string;
  expectedRecommendation: ImplementationDecisionOutcome;
  expectedLabel: string;
  recommendationMatchesEvidence: boolean;
  evidenceArtifactPath?: string;
  summary: string;
  criteria: DecisionJustificationCriterionReport[];
  supportingEvidence: ImplementationDecisionJustificationEvidence[];
}

export function formatDecisionJustificationReport(
  input: DecisionJustificationReportInput,
): DecisionJustificationReport {
  const selectedRecommendation = normalizeSelectedRecommendation(input.selectedRecommendation);
  const expectedDecision = decideImplementationDirectionFromStructuredEvaluation({
    criteria: input.evidence.criteria,
  });
  const criteria = buildCriterionReports(input.evidence.criteria);
  const selectedLabel = decisionLabelForOutcome(selectedRecommendation);
  const recommendationMatchesEvidence = selectedRecommendation === expectedDecision.outcome;
  const supportingEvidence = buildSupportingEvidence(input.evidence.evidenceItems ?? [], selectedRecommendation);

  return {
    schemaVersion: "decision-justification-report.v1",
    selectedRecommendation,
    selectedLabel,
    expectedRecommendation: expectedDecision.outcome,
    expectedLabel: expectedDecision.label,
    recommendationMatchesEvidence,
    evidenceArtifactPath: input.evidence.artifactPath,
    summary: buildDecisionReportSummary({
      selectedLabel,
      expectedLabel: expectedDecision.label,
      recommendationMatchesEvidence,
      criteria,
      supportingEvidence,
      rationale: typeof input.selectedRecommendation === "string" ? undefined : input.selectedRecommendation.rationale,
    }),
    criteria,
    supportingEvidence,
  };
}

function normalizeSelectedRecommendation(
  selectedRecommendation: ImplementationDecisionOutcome | SelectedRecommendationInput,
): ImplementationDecisionOutcome {
  return typeof selectedRecommendation === "string" ? selectedRecommendation : selectedRecommendation.outcome;
}

function decisionLabelForOutcome(outcome: ImplementationDecisionOutcome): ImplementationDecisionLabel {
  if (outcome === "full_replan") {
    return "full replan";
  }
  if (outcome === "partial_redesign") {
    return "partial redesign";
  }
  return "Keep";
}

function buildCriterionReports(
  criteria: StructuredEvaluationCriterionInput[],
): DecisionJustificationCriterionReport[] {
  return evaluationDecisionPolicy.map((policy) => {
    const criterion = criteria.find((entry) => entry.category === policy.category);
    const risk = criterion?.risk ?? "none";
    const evidenceCount = criterion?.evidenceCount ?? 0;
    const targetMet = criterion?.targetMet;
    return {
      category: policy.category,
      priority: policy.priority,
      label: policy.label,
      risk,
      evidenceCount,
      targetMet,
      decisionRelevant: risk !== "none" || evidenceCount > 0 || targetMet === false,
    };
  });
}

function buildSupportingEvidence(
  evidenceItems: ReviewFinding[],
  selectedRecommendation: ImplementationDecisionOutcome,
): ImplementationDecisionJustificationEvidence[] {
  if (evidenceItems.length === 0) {
    return [];
  }
  return justifyImplementationDecision(rankReviewEvidence(evidenceItems), selectedRecommendation).supportingEvidence;
}

function buildDecisionReportSummary(input: {
  selectedLabel: string;
  expectedLabel: string;
  recommendationMatchesEvidence: boolean;
  criteria: DecisionJustificationCriterionReport[];
  supportingEvidence: ImplementationDecisionJustificationEvidence[];
  rationale?: string;
}): string {
  const relevantCriteria = input.criteria
    .filter((criterion) => criterion.decisionRelevant)
    .map((criterion) => `${criterion.priority}. ${criterion.label}=${criterion.risk}`)
    .join("; ");
  const evidenceSummary = relevantCriteria === "" ? "no decision-relevant evidence" : relevantCriteria;
  const matchSummary = input.recommendationMatchesEvidence
    ? "selected recommendation matches structured evidence"
    : "selected recommendation differs from structured evidence";
  const citationSummary =
    input.supportingEvidence.length === 0
      ? "supporting evidence items: none"
      : `supporting evidence items: ${input.supportingEvidence
          .map((item) => `#${item.rank} ${item.findingId} (${item.title})`)
          .join(", ")}`;
  const rationaleSuffix = input.rationale ? ` Rationale: ${input.rationale}` : "";

  return `${input.selectedLabel}: ${matchSummary}; expected ${input.expectedLabel}; evidence: ${evidenceSummary}; ${citationSummary}.${rationaleSuffix}`;
}
