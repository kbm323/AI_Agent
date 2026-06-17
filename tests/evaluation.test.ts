import test from "node:test";
import assert from "node:assert/strict";
import * as evaluationModule from "../src/evaluation.ts";
import {
  compareFindingsByEvaluationPriority,
  decideImplementationDirectionFromStructuredEvaluation,
  decideImplementationDirection,
  evaluateProjectFindings,
  evaluationCategoryPriority,
  evaluationDecisionPolicy,
  formatCitedEvaluationEvidence,
  assertImplementationDecisionLabel,
  implementationDecisionLabels,
  isImplementationDecisionLabel,
  justifyImplementationDecision,
  rankEvaluationDecisionCriteria,
  rankReviewEvidence,
  type ReviewFinding,
} from "../src/index.ts";

const evaluationRuntimeExports = [
  "assertImplementationDecisionLabel",
  "compareFindingsByEvaluationPriority",
  "decideImplementationDirectionFromStructuredEvaluation",
  "decideImplementationDirection",
  "evaluateProjectFindings",
  "evaluationCategoryPriority",
  "evaluationDecisionPolicy",
  "formatCitedEvaluationEvidence",
  "implementationDecisionLabels",
  "isImplementationDecisionLabel",
  "justifyImplementationDecision",
  "rankEvaluationDecisionCriteria",
  "rankReviewEvidence",
] as const;

test("evaluation workflow module public exports have primary success-path coverage", () => {
  assert.deepEqual(Object.keys(evaluationModule).sort(), [...evaluationRuntimeExports].sort());

  const findings = [
    finding("error-low", "error_frequency", "low"),
    finding("token-high", "token_cost", "high"),
  ];
  const rankedEvidence = evaluationModule.rankReviewEvidence(findings);
  const decision = evaluationModule.decideImplementationDirection(rankedEvidence);
  const evaluation = evaluationModule.evaluateProjectFindings(findings);
  const justification = evaluationModule.justifyImplementationDecision(rankedEvidence, decision.outcome);

  const coveredExports = new Set<string>();
  assert.deepEqual(evaluationModule.evaluationCategoryPriority.slice(0, 3), [
    "error_frequency",
    "maintainability",
    "token_cost",
  ]);
  coveredExports.add("evaluationCategoryPriority");
  assert.deepEqual(evaluationModule.evaluationDecisionPolicy[0], {
    category: "error_frequency",
    priority: 1,
    label: "error frequency",
  });
  coveredExports.add("evaluationDecisionPolicy");
  assert.deepEqual(evaluationModule.rankEvaluationDecisionCriteria(["token_cost", "error_frequency"]), [
    { category: "error_frequency", priority: 1, label: "error frequency" },
    { category: "token_cost", priority: 3, label: "token cost" },
  ]);
  coveredExports.add("rankEvaluationDecisionCriteria");
  assert.equal(evaluationModule.formatCitedEvaluationEvidence(rankedEvidence), "#1 error-low, #2 token-high");
  coveredExports.add("formatCitedEvaluationEvidence");
  assert.deepEqual([...evaluationModule.implementationDecisionLabels], ["Keep", "partial redesign", "full replan"]);
  coveredExports.add("implementationDecisionLabels");
  assert.equal(evaluationModule.compareFindingsByEvaluationPriority(findings[0], findings[1]) < 0, true);
  coveredExports.add("compareFindingsByEvaluationPriority");
  assert.deepEqual(rankedEvidence.map((item) => item.finding.id), ["error-low", "token-high"]);
  coveredExports.add("rankReviewEvidence");
  assert.deepEqual(decision, { outcome: "partial_redesign", label: "partial redesign" });
  coveredExports.add("decideImplementationDirection");
  assert.deepEqual(
    evaluationModule.decideImplementationDirectionFromStructuredEvaluation({
      criteria: [
        { category: "architecture_fit", risk: "low", evidenceCount: 1 },
        { category: "feature_completeness", risk: "none" },
      ],
    }),
    { outcome: "keep", label: "Keep" },
  );
  coveredExports.add("decideImplementationDirectionFromStructuredEvaluation");
  assert.equal(evaluation.dominantCategory, "error_frequency");
  assert.equal(evaluation.recommendation, "partial_redesign");
  coveredExports.add("evaluateProjectFindings");
  assert.equal(justification.rule, "high_or_token_cost_evidence");
  coveredExports.add("justifyImplementationDecision");
  assert.equal(evaluationModule.isImplementationDecisionLabel(decision.label), true);
  coveredExports.add("isImplementationDecisionLabel");
  assert.doesNotThrow(() => evaluationModule.assertImplementationDecisionLabel(decision.label));
  coveredExports.add("assertImplementationDecisionLabel");

  assert.deepEqual([...coveredExports].sort(), [...evaluationRuntimeExports].sort());
});

test("evaluation module exposes the fixed project finding priority order", () => {
  assert.deepEqual(evaluationCategoryPriority, [
    "error_frequency",
    "maintainability",
    "token_cost",
    "architecture_fit",
    "feature_completeness",
  ]);
});

test("evaluation decision policy ranks Seed criteria in the required order", () => {
  assert.deepEqual(evaluationDecisionPolicy, [
    { category: "error_frequency", priority: 1, label: "error frequency" },
    { category: "maintainability", priority: 2, label: "maintainability difficulty" },
    { category: "token_cost", priority: 3, label: "token cost" },
    { category: "architecture_fit", priority: 4, label: "architecture fit" },
    { category: "feature_completeness", priority: 5, label: "feature completeness" },
  ]);

  assert.deepEqual(
    rankEvaluationDecisionCriteria([
      "feature_completeness",
      "architecture_fit",
      "token_cost",
      "maintainability",
      "error_frequency",
    ]).map((criterion) => criterion.category),
    [
      "error_frequency",
      "maintainability",
      "token_cost",
      "architecture_fit",
      "feature_completeness",
    ],
  );
});

test("evaluation orders collected findings by category precedence before severity", () => {
  const findings = [
    finding("feature-critical", "feature_completeness", "critical"),
    finding("architecture-high", "architecture_fit", "high"),
    finding("token-low", "token_cost", "low"),
    finding("maintainability-low", "maintainability", "low"),
    finding("error-low", "error_frequency", "low"),
  ];

  const evaluation = evaluateProjectFindings(findings);

  assert.deepEqual(
    evaluation.orderedFindings.map((item) => item.id),
    ["error-low", "maintainability-low", "token-low", "architecture-high", "feature-critical"],
  );
  assert.equal(evaluation.dominantCategory, "error_frequency");
  assert.equal(evaluation.recommendation, "full_replan");
  assert.equal(evaluation.justification.outcome, "full_replan");
  assert.equal(evaluation.justification.rule, "critical_evidence");
});

test("evaluation maps mixed findings into deterministic ranked evidence by Seed priority", () => {
  const findings = [
    finding("feature-critical", "feature_completeness", "critical"),
    finding("maintainability-low", "maintainability", "low"),
    finding("error-medium", "error_frequency", "medium"),
    finding("token-high", "token_cost", "high"),
    finding("architecture-critical", "architecture_fit", "critical"),
  ];

  const rankedEvidence = rankReviewEvidence(findings);

  assert.deepEqual(
    rankedEvidence.map((item) => ({
      rank: item.rank,
      priority: item.priority,
      id: item.finding.id,
      category: item.category,
      severity: item.severity,
    })),
    [
      { rank: 1, priority: 1, id: "error-medium", category: "error_frequency", severity: "medium" },
      { rank: 2, priority: 2, id: "maintainability-low", category: "maintainability", severity: "low" },
      { rank: 3, priority: 3, id: "token-high", category: "token_cost", severity: "high" },
      { rank: 4, priority: 4, id: "architecture-critical", category: "architecture_fit", severity: "critical" },
      { rank: 5, priority: 5, id: "feature-critical", category: "feature_completeness", severity: "critical" },
    ],
  );
});

test("decision function maps ranked evidence to exactly Keep when no redesign evidence is present", () => {
  assert.deepEqual(decideImplementationDirection([]), {
    outcome: "keep",
    label: "Keep",
  });
  assert.deepEqual(decideImplementationDirection(rankReviewEvidence([finding("architecture-low", "architecture_fit", "low")])), {
    outcome: "keep",
    label: "Keep",
  });
});

test("decision labels are validated against the exact allowed result set", () => {
  assert.deepEqual([...implementationDecisionLabels], ["Keep", "partial redesign", "full replan"]);
  for (const label of implementationDecisionLabels) {
    assert.equal(isImplementationDecisionLabel(label), true);
    assert.doesNotThrow(() => assertImplementationDecisionLabel(label));
  }
  for (const label of ["keep", "full redesign", "full replanning", "partial_redesign", "maintain"]) {
    assert.equal(isImplementationDecisionLabel(label), false);
    assert.throws(() => assertImplementationDecisionLabel(label), /must be exactly one of: Keep, partial redesign, full replan/);
  }
});

test("decision function maps ranked evidence to exactly partial redesign for high or token-cost evidence", () => {
  assert.deepEqual(decideImplementationDirection(rankReviewEvidence([finding("error-high", "error_frequency", "high")])), {
    outcome: "partial_redesign",
    label: "partial redesign",
  });
  assert.deepEqual(decideImplementationDirection(rankReviewEvidence([finding("token-low", "token_cost", "low")])), {
    outcome: "partial_redesign",
    label: "partial redesign",
  });
});

test("decision function applies severity thresholds at medium, high, and critical boundaries", () => {
  const thresholdCases = [
    {
      name: "medium non-token evidence stays below redesign threshold",
      findings: [finding("error-medium", "error_frequency", "medium")],
      expected: { outcome: "keep", label: "Keep" },
    },
    {
      name: "high evidence reaches scoped redesign threshold",
      findings: [finding("maintainability-high", "maintainability", "high")],
      expected: { outcome: "partial_redesign", label: "partial redesign" },
    },
    {
      name: "critical evidence reaches full replan threshold",
      findings: [finding("architecture-critical", "architecture_fit", "critical")],
      expected: { outcome: "full_replan", label: "full replan" },
    },
    {
      name: "token-cost evidence reaches scoped redesign threshold even when low severity",
      findings: [finding("token-low", "token_cost", "low")],
      expected: { outcome: "partial_redesign", label: "partial redesign" },
    },
  ] as const;

  for (const decisionCase of thresholdCases) {
    assert.deepEqual(
      decideImplementationDirection(rankReviewEvidence([...decisionCase.findings])),
      decisionCase.expected,
      decisionCase.name,
    );
  }
});

test("decision function gives full-replan evidence precedence over higher-ranked partial-redesign evidence", () => {
  const rankedEvidence = rankReviewEvidence([
    finding("error-high", "error_frequency", "high"),
    finding("maintainability-high", "maintainability", "high"),
    finding("token-low", "token_cost", "low"),
    finding("feature-critical", "feature_completeness", "critical"),
  ]);

  assert.deepEqual(
    rankedEvidence.map((item) => ({
      priority: item.priority,
      category: item.category,
      severity: item.severity,
      findingId: item.finding.id,
    })),
    [
      { priority: 1, category: "error_frequency", severity: "high", findingId: "error-high" },
      { priority: 2, category: "maintainability", severity: "high", findingId: "maintainability-high" },
      { priority: 3, category: "token_cost", severity: "low", findingId: "token-low" },
      { priority: 5, category: "feature_completeness", severity: "critical", findingId: "feature-critical" },
    ],
  );
  assert.deepEqual(decideImplementationDirection(rankedEvidence), {
    outcome: "full_replan",
    label: "full replan",
  });
});

test("structured decision function maps concrete evaluation criteria to keep", () => {
  assert.deepEqual(
    decideImplementationDirectionFromStructuredEvaluation({
      criteria: [
        { category: "architecture_fit", risk: "low", evidenceCount: 1, targetMet: true },
        { category: "feature_completeness", risk: "medium", evidenceCount: 1, targetMet: true },
      ],
    }),
    { outcome: "keep", label: "Keep" },
  );
});

test("structured decision function maps high risk or missed targets to partial redesign", () => {
  assert.deepEqual(
    decideImplementationDirectionFromStructuredEvaluation({
      criteria: [{ category: "maintainability", risk: "high", evidenceCount: 1 }],
    }),
    { outcome: "partial_redesign", label: "partial redesign" },
  );
  assert.deepEqual(
    decideImplementationDirectionFromStructuredEvaluation({
      criteria: [{ category: "token_cost", risk: "none", evidenceCount: 0, targetMet: false }],
    }),
    { outcome: "partial_redesign", label: "partial redesign" },
  );
});

test("structured decision function maps any critical criterion to full replan", () => {
  assert.deepEqual(
    decideImplementationDirectionFromStructuredEvaluation({
      criteria: [
        { category: "token_cost", risk: "low", evidenceCount: 1 },
        { category: "error_frequency", risk: "critical", evidenceCount: 1 },
      ],
    }),
    { outcome: "full_replan", label: "full replan" },
  );
});

test("decision function maps ranked evidence to exactly full replan for any critical evidence", () => {
  const rankedEvidence = rankReviewEvidence([
    finding("token-low", "token_cost", "low"),
    finding("feature-critical", "feature_completeness", "critical"),
  ]);

  assert.deepEqual(decideImplementationDirection(rankedEvidence), {
    outcome: "full_replan",
    label: "full replan",
  });
});

test("justification function produces deterministic rationale from ranked evidence and selected partial redesign outcome", () => {
  const rankedEvidence = rankReviewEvidence([
    finding("feature-critical", "feature_completeness", "critical"),
    finding("maintainability-medium", "maintainability", "medium"),
    finding("token-low", "token_cost", "low"),
    finding("error-high", "error_frequency", "high"),
    finding("token-high", "token_cost", "high"),
  ]);

  assert.deepEqual(justifyImplementationDecision(rankedEvidence, "partial_redesign"), {
    outcome: "partial_redesign",
    label: "partial redesign",
    rule: "high_or_token_cost_evidence",
    summary:
      "partial redesign: high-severity or token-cost evidence requires scoped redesign; supporting findings: #1 error-high, #3 token-high, #4 token-low.",
    priorityOrder: [
      "error_frequency",
      "maintainability",
      "token_cost",
      "architecture_fit",
      "feature_completeness",
    ],
    supportingEvidence: [
      {
        rank: 1,
        priority: 1,
        category: "error_frequency",
        severity: "high",
        findingId: "error-high",
        title: "error-high finding title with useful detail",
      },
      {
        rank: 3,
        priority: 3,
        category: "token_cost",
        severity: "high",
        findingId: "token-high",
        title: "token-high finding title with useful detail",
      },
      {
        rank: 4,
        priority: 3,
        category: "token_cost",
        severity: "low",
        findingId: "token-low",
        title: "token-low finding title with useful detail",
      },
    ],
  });
});

test("decision justification applies the Seed evaluation priority order to supporting evidence", () => {
  const rankedEvidence = rankReviewEvidence([
    finding("feature-critical", "feature_completeness", "critical"),
    finding("architecture-critical", "architecture_fit", "critical"),
    finding("token-critical", "token_cost", "critical"),
    finding("maintainability-critical", "maintainability", "critical"),
    finding("error-critical", "error_frequency", "critical"),
  ]);

  const justification = justifyImplementationDecision(rankedEvidence, "full_replan");

  assert.deepEqual(justification.priorityOrder, [
    "error_frequency",
    "maintainability",
    "token_cost",
    "architecture_fit",
    "feature_completeness",
  ]);
  assert.deepEqual(
    justification.supportingEvidence.map((item) => ({
      rank: item.rank,
      priority: item.priority,
      category: item.category,
      findingId: item.findingId,
    })),
    [
      { rank: 1, priority: 1, category: "error_frequency", findingId: "error-critical" },
      { rank: 2, priority: 2, category: "maintainability", findingId: "maintainability-critical" },
      { rank: 3, priority: 3, category: "token_cost", findingId: "token-critical" },
      { rank: 4, priority: 4, category: "architecture_fit", findingId: "architecture-critical" },
      { rank: 5, priority: 5, category: "feature_completeness", findingId: "feature-critical" },
    ],
  );
  assert.equal(
    justification.summary,
    "full replan: critical evidence requires full replanning; supporting findings: #1 error-critical, #2 maintainability-critical, #3 token-critical, #4 architecture-critical, #5 feature-critical.",
  );
});

test("formatter orders cited evaluation evidence by required priority sequence", () => {
  assert.equal(
    formatCitedEvaluationEvidence([
      { rank: 5, priority: 5, category: "feature_completeness", findingId: "feature-critical" },
      { rank: 3, priority: 3, category: "token_cost", findingId: "token-high" },
      { rank: 4, priority: 4, category: "architecture_fit", findingId: "architecture-critical" },
      { rank: 1, priority: 1, category: "error_frequency", findingId: "error-high" },
      { rank: 2, priority: 2, category: "maintainability", findingId: "maintainability-high" },
    ]),
    "#1 error-high, #2 maintainability-high, #3 token-high, #4 architecture-critical, #5 feature-critical",
  );
});

test("justification function is deterministic for keep outcome with no redesign evidence", () => {
  const rankedEvidence = rankReviewEvidence([finding("architecture-low", "architecture_fit", "low")]);

  assert.deepEqual(justifyImplementationDecision(rankedEvidence, "keep"), {
    outcome: "keep",
    label: "Keep",
    rule: "no_redesign_evidence",
    summary: "Keep: no critical, high-severity, or token-cost evidence was ranked for redesign.",
    priorityOrder: [
      "error_frequency",
      "maintainability",
      "token_cost",
      "architecture_fit",
      "feature_completeness",
    ],
    supportingEvidence: [
      {
        rank: 1,
        priority: 4,
        category: "architecture_fit",
        severity: "low",
        findingId: "architecture-low",
        title: "architecture-low finding title with useful detail",
      },
    ],
  });
});

test("evaluation ranked evidence uses severity then stable id for ties", () => {
  const findings = [
    finding("maintainability-medium-b", "maintainability", "medium"),
    finding("maintainability-high-b", "maintainability", "high"),
    finding("maintainability-medium-a", "maintainability", "medium"),
    finding("maintainability-high-a", "maintainability", "high"),
  ];

  assert.deepEqual(
    rankReviewEvidence(findings).map((item) => `${item.rank}:${item.finding.id}`),
    [
      "1:maintainability-high-a",
      "2:maintainability-high-b",
      "3:maintainability-medium-a",
      "4:maintainability-medium-b",
    ],
  );
});

test("evaluation sorts same-category findings by severity and stable id", () => {
  const findings = [
    finding("token-medium-b", "token_cost", "medium"),
    finding("token-high", "token_cost", "high"),
    finding("token-medium-a", "token_cost", "medium"),
  ];

  assert.deepEqual(
    [...findings].sort(compareFindingsByEvaluationPriority).map((item) => item.id),
    ["token-high", "token-medium-a", "token-medium-b"],
  );
});

test("evaluation category summaries preserve empty categories in priority order", () => {
  const evaluation = evaluateProjectFindings([
    finding("architecture-medium", "architecture_fit", "medium"),
    finding("token-low", "token_cost", "low"),
  ]);

  assert.deepEqual(
    evaluation.categorySummaries.map((summary) => ({
      category: summary.category,
      priority: summary.priority,
      findingCount: summary.findingCount,
      highestSeverity: summary.highestSeverity,
    })),
    [
      { category: "error_frequency", priority: 1, findingCount: 0, highestSeverity: undefined },
      { category: "maintainability", priority: 2, findingCount: 0, highestSeverity: undefined },
      { category: "token_cost", priority: 3, findingCount: 1, highestSeverity: "low" },
      { category: "architecture_fit", priority: 4, findingCount: 1, highestSeverity: "medium" },
      { category: "feature_completeness", priority: 5, findingCount: 0, highestSeverity: undefined },
    ],
  );
  assert.equal(evaluation.dominantCategory, "token_cost");
  assert.equal(evaluation.recommendation, "partial_redesign");
});

function finding(
  id: string,
  category: ReviewFinding["category"],
  severity: ReviewFinding["severity"],
): ReviewFinding {
  return {
    id,
    sourceId: `source:${id}`,
    relativePath: `src/${id}.ts`,
    moduleName: `src.${id}`,
    severity,
    category,
    title: `${id} finding title with useful detail`,
    evidence: `${id} evidence describes the observed implementation risk.`,
    recommendation: `${id} recommendation describes a concrete mitigation action.`,
  };
}
