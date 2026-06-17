import test from "node:test";
import assert from "node:assert/strict";
import {
  formatDecisionJustificationReport,
  type ReviewFinding,
  type StructuredEvaluationCriterionInput,
} from "../src/index.ts";

test("decision justification report formats a selected recommendation from structured evidence", () => {
  const criteria: StructuredEvaluationCriterionInput[] = [
    { category: "maintainability", risk: "medium", evidenceCount: 2, targetMet: true },
    { category: "token_cost", risk: "low", evidenceCount: 1, targetMet: false },
  ];
  const evidenceItems: ReviewFinding[] = [
    finding("finding:token-context", "token_cost", "low", "Loop prompt exposes raw transcript text"),
    finding("finding:maintainability-router", "maintainability", "medium", "Routing and execution share mutable state"),
  ];

  const report = formatDecisionJustificationReport({
    selectedRecommendation: {
      outcome: "partial_redesign",
      rationale: "Reduce exposed meeting-loop context while preserving the approved MVP flow.",
    },
    evidence: {
      artifactPath: "docs/review-evidence.json",
      criteria,
      evidenceItems,
    },
  });

  assert.deepEqual(
    {
      schemaVersion: report.schemaVersion,
      selectedRecommendation: report.selectedRecommendation,
      selectedLabel: report.selectedLabel,
      expectedRecommendation: report.expectedRecommendation,
      expectedLabel: report.expectedLabel,
      recommendationMatchesEvidence: report.recommendationMatchesEvidence,
      evidenceArtifactPath: report.evidenceArtifactPath,
      supportingEvidence: report.supportingEvidence,
    },
    {
      schemaVersion: "decision-justification-report.v1",
      selectedRecommendation: "partial_redesign",
      selectedLabel: "partial redesign",
      expectedRecommendation: "partial_redesign",
      expectedLabel: "partial redesign",
      recommendationMatchesEvidence: true,
      evidenceArtifactPath: "docs/review-evidence.json",
      supportingEvidence: [
        {
          rank: 2,
          priority: 3,
          category: "token_cost",
          severity: "low",
          findingId: "finding:token-context",
          title: "Loop prompt exposes raw transcript text",
        },
      ],
    },
  );
  assert.deepEqual(
    report.criteria.map((criterion) => ({
      category: criterion.category,
      priority: criterion.priority,
      risk: criterion.risk,
      evidenceCount: criterion.evidenceCount,
      targetMet: criterion.targetMet,
      decisionRelevant: criterion.decisionRelevant,
    })),
    [
      {
        category: "error_frequency",
        priority: 1,
        risk: "none",
        evidenceCount: 0,
        targetMet: undefined,
        decisionRelevant: false,
      },
      {
        category: "maintainability",
        priority: 2,
        risk: "medium",
        evidenceCount: 2,
        targetMet: true,
        decisionRelevant: true,
      },
      {
        category: "token_cost",
        priority: 3,
        risk: "low",
        evidenceCount: 1,
        targetMet: false,
        decisionRelevant: true,
      },
      {
        category: "architecture_fit",
        priority: 4,
        risk: "none",
        evidenceCount: 0,
        targetMet: undefined,
        decisionRelevant: false,
      },
      {
        category: "feature_completeness",
        priority: 5,
        risk: "none",
        evidenceCount: 0,
        targetMet: undefined,
        decisionRelevant: false,
      },
    ],
  );
  assert.match(report.summary, /partial redesign: selected recommendation matches structured evidence/);
  assert.match(report.summary, /2\. maintainability difficulty=medium; 3\. token cost=low/);
  assert.match(
    report.summary,
    /supporting evidence items: #2 finding:token-context \(Loop prompt exposes raw transcript text\)/,
  );
  assert.match(report.summary, /Reduce exposed meeting-loop context/);
});

test("decision justification report exposes selected recommendation mismatches", () => {
  const report = formatDecisionJustificationReport({
    selectedRecommendation: "keep",
    evidence: {
      criteria: [{ category: "error_frequency", risk: "critical", evidenceCount: 1 }],
    },
  });

  assert.equal(report.selectedRecommendation, "keep");
  assert.equal(report.expectedRecommendation, "full_replan");
  assert.equal(report.recommendationMatchesEvidence, false);
  assert.match(report.summary, /Keep: selected recommendation differs from structured evidence; expected full replan/);
  assert.match(report.summary, /supporting evidence items: none/);
  assert.deepEqual(
    report.criteria.filter((criterion) => criterion.decisionRelevant).map((criterion) => ({
      category: criterion.category,
      priority: criterion.priority,
      risk: criterion.risk,
      evidenceCount: criterion.evidenceCount,
    })),
    [{ category: "error_frequency", priority: 1, risk: "critical", evidenceCount: 1 }],
  );
});

function finding(
  id: string,
  category: ReviewFinding["category"],
  severity: ReviewFinding["severity"],
  title: string,
): ReviewFinding {
  return {
    id,
    sourceId: `source:${id}`,
    relativePath: `src/${id}.ts`,
    moduleName: id,
    category,
    severity,
    title,
    evidence: `${title} evidence with concrete artifact detail.`,
    recommendation: `${title} recommendation with concrete next action.`,
  };
}
