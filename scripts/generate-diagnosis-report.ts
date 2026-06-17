import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildGovernedRecommendationDecision,
  loadDiagnosisReportArtifact,
  renderDiagnosisReportWithRequirementGapSection,
  type DiagnosisReportArtifact,
  type GovernedRecommendationDecision,
  type ReviewEvidenceArtifact,
} from "../src/inspection.ts";
import {
  verifyInventoryOrchestrationReportGenerated,
  writeInventoryOrchestrationReport,
  type InventoryOrchestrationReport,
  type InventoryOrchestrationVerificationResult,
} from "../src/inventory-orchestration.ts";
import {
  validateCompletedReviewArtifacts,
  type ReviewArtifactCompletenessResult,
} from "../src/review-artifact-completeness.ts";
import { generateReviewEvidence } from "./review-evidence.ts";

export interface DiagnosisReportGenerationArtifact {
  schemaVersion: "diagnosis-report-generation.v1";
  diagnosisReport: DiagnosisReportArtifact;
  requirementGapMapping: {
    artifactPath: string;
    schemaVersion: DiagnosisReportArtifact["requirementToGapMappingArtifact"]["schemaVersion"];
    implementedCount: number;
    missingCount: number;
    readmeRequirementCount: number;
  };
  reviewEvidence: {
    artifactPath: string;
    schemaVersion: ReviewEvidenceArtifact["schemaVersion"];
    recommendation: ReviewEvidenceArtifact["summary"]["recommendation"];
    inspectedModules: number;
    findingCount: number;
  };
  inventoryOrchestration: {
    artifactPath: string;
    schemaVersion: InventoryOrchestrationReport["schemaVersion"];
    sourceFileCount: number;
    runnableEntryPointCount: number;
    testFileCount: number;
    configFileCount: number;
    verification: InventoryOrchestrationVerificationResult;
  };
  decisionGate: GovernedRecommendationDecision["decisionGate"];
  reviewArtifactCompleteness: ReviewArtifactCompletenessResult;
}

export interface GenerateDiagnosisReportCommandResult {
  command: "ai-agent generate-diagnosis-report";
  decisionResult: {
    recommendation?: ReviewEvidenceArtifact["summary"]["recommendation"];
    decisionGateAccepted: boolean;
    evidenceArtifactPath: string;
  };
  artifact: {
    path: string;
    schemaVersion: DiagnosisReportGenerationArtifact["schemaVersion"];
    diagnosisReportPath: string;
    requirementGapMappingPath: string;
    reviewEvidencePath: string;
    inventoryOrchestrationReportPath: string;
    recommendation?: ReviewEvidenceArtifact["summary"]["recommendation"];
    decisionGateAccepted: boolean;
  };
}

export function generateDiagnosisReport(input: {
  projectRoot?: string;
  outputPath?: string;
  reviewEvidenceOutputPath?: string;
} = {}): { artifact: DiagnosisReportGenerationArtifact; artifactPath: string } {
  const projectRoot = resolve(input.projectRoot ?? process.cwd());
  const artifactPath = resolve(projectRoot, input.outputPath ?? "docs/generated/diagnosis-report.json");
  const requirementGapMappingPath = resolve(projectRoot, "docs/generated/requirement-gap-mapping.json");
  const inventoryOrchestration = writeInventoryOrchestrationReport({ projectRoot });
  const inventoryOrchestrationVerification = verifyInventoryOrchestrationReportGenerated({
    projectRoot,
    reportPath: inventoryOrchestration.reportPath,
  });
  if (!inventoryOrchestrationVerification.valid) {
    throw new Error(
      `inventory orchestration report is invalid: ${[
        ...inventoryOrchestrationVerification.missingFields,
        ...inventoryOrchestrationVerification.inconsistentFields,
      ].join(", ")}`,
    );
  }
  const reviewEvidence = generateReviewEvidence({
    projectRoot,
    outputPath: input.reviewEvidenceOutputPath ?? "docs/review-evidence.json",
  });
  const diagnosisReport = loadDiagnosisReportArtifact({ projectRoot });
  const priorEvidenceDecision = buildGovernedRecommendationDecision({
    artifact: reviewEvidence.artifact,
    evidenceArtifactCreated: true,
    evidenceArtifactPath: reviewEvidence.artifactPath,
  });
  const artifactWithoutCompleteness: Omit<DiagnosisReportGenerationArtifact, "reviewArtifactCompleteness"> = {
    schemaVersion: "diagnosis-report-generation.v1",
    diagnosisReport,
    requirementGapMapping: {
      artifactPath: requirementGapMappingPath,
      schemaVersion: diagnosisReport.requirementToGapMappingArtifact.schemaVersion,
      implementedCount: diagnosisReport.requirementToGapMappingArtifact.summary.implementedCount,
      missingCount: diagnosisReport.requirementToGapMappingArtifact.summary.missingCount,
      readmeRequirementCount: diagnosisReport.requirementToGapMappingArtifact.summary.readmeRequirementCount,
    },
    reviewEvidence: {
      artifactPath: reviewEvidence.artifactPath,
      schemaVersion: reviewEvidence.artifact.schemaVersion,
      recommendation: reviewEvidence.artifact.summary.recommendation,
      inspectedModules: reviewEvidence.artifact.summary.inspectedModules,
      findingCount: reviewEvidence.artifact.summary.findingCount,
    },
    inventoryOrchestration: {
      artifactPath: inventoryOrchestration.reportPath,
      schemaVersion: inventoryOrchestration.report.schemaVersion,
      sourceFileCount: inventoryOrchestration.report.summary.sourceFileCount,
      runnableEntryPointCount: inventoryOrchestration.report.summary.runnableEntryPointCount,
      testFileCount: inventoryOrchestration.report.summary.testFileCount,
      configFileCount: inventoryOrchestration.report.summary.configFileCount,
      verification: inventoryOrchestrationVerification,
    },
    decisionGate: priorEvidenceDecision.decisionGate,
  };

  mkdirSync(dirname(artifactPath), { recursive: true });
  mkdirSync(dirname(requirementGapMappingPath), { recursive: true });
  mkdirSync(dirname(diagnosisReport.source.diagnosisReportPath), { recursive: true });
  writeFileSync(
    requirementGapMappingPath,
    `${JSON.stringify(diagnosisReport.requirementToGapMappingArtifact, null, 2)}\n`,
    "utf8",
  );
  writeFileSync(
    diagnosisReport.source.diagnosisReportPath,
    renderDiagnosisReportWithRequirementGapSection({
      markdown: readFileSync(diagnosisReport.source.diagnosisReportPath, "utf8"),
      mapping: diagnosisReport.requirementToGapMappingArtifact,
    }),
    "utf8",
  );
  writeFileSync(artifactPath, `${JSON.stringify(artifactWithoutCompleteness, null, 2)}\n`, "utf8");

  const reviewArtifactCompleteness = validateCompletedReviewArtifacts(projectRoot, {
    generated_diagnosis_report: artifactPath,
    review_evidence: reviewEvidence.artifactPath,
  });
  const decision = buildGovernedRecommendationDecision({
    artifact: reviewEvidence.artifact,
    evidenceArtifactCreated: true,
    evidenceArtifactPath: reviewEvidence.artifactPath,
    completedReviewArtifacts: reviewArtifactCompleteness,
  });
  const artifact: DiagnosisReportGenerationArtifact = {
    ...artifactWithoutCompleteness,
    decisionGate: decision.decisionGate,
    reviewArtifactCompleteness,
  };
  writeFileSync(artifactPath, `${JSON.stringify(artifact, null, 2)}\n`, "utf8");
  if (reviewArtifactCompleteness.status !== "passed") {
    throw new Error(buildReviewArtifactCompletenessGateError(reviewArtifactCompleteness));
  }

  return { artifact, artifactPath };
}

export function runGenerateDiagnosisReportCommand(input: {
  projectRoot?: string;
  outputPath?: string;
  reviewEvidenceOutputPath?: string;
} = {}): GenerateDiagnosisReportCommandResult {
  const { artifact, artifactPath } = generateDiagnosisReport(input);

  return {
    command: "ai-agent generate-diagnosis-report",
    decisionResult: {
      recommendation: artifact.diagnosisReport.diagnosis.decision,
      decisionGateAccepted: artifact.decisionGate.accepted,
      evidenceArtifactPath: artifact.reviewEvidence.artifactPath,
    },
    artifact: {
      path: artifactPath,
      schemaVersion: artifact.schemaVersion,
      diagnosisReportPath: artifact.diagnosisReport.source.diagnosisReportPath,
      requirementGapMappingPath: artifact.requirementGapMapping.artifactPath,
      reviewEvidencePath: artifact.reviewEvidence.artifactPath,
      inventoryOrchestrationReportPath: artifact.inventoryOrchestration.artifactPath,
      recommendation: artifact.diagnosisReport.diagnosis.decision,
      decisionGateAccepted: artifact.decisionGate.accepted,
    },
  };
}

export function executeGenerateDiagnosisReportCommand(args: string[]): { exitCode: number; stdout: string; stderr: string } {
  try {
    const options = parseArgs(args);
    const result = runGenerateDiagnosisReportCommand(options);
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown diagnosis-report generation failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function parseArgs(args: string[]): { projectRoot?: string; outputPath?: string; reviewEvidenceOutputPath?: string } {
  const options: { projectRoot?: string; outputPath?: string; reviewEvidenceOutputPath?: string } = {};

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--project-root") {
      options.projectRoot = readRequiredFlagValue(args, index, arg);
      index += 1;
      continue;
    }
    if (arg === "--output") {
      options.outputPath = readRequiredFlagValue(args, index, arg);
      index += 1;
      continue;
    }
    if (arg === "--review-evidence-output") {
      options.reviewEvidenceOutputPath = readRequiredFlagValue(args, index, arg);
      index += 1;
      continue;
    }
    throw new TypeError(`unknown diagnosis-report argument: ${arg}`);
  }

  return options;
}

function readRequiredFlagValue(args: string[], index: number, flag: string): string {
  const value = args[index + 1] ?? "";
  if (value.trim() === "" || value.startsWith("--")) {
    throw new TypeError(`${flag} requires a non-empty value`);
  }
  return value;
}

function buildReviewArtifactCompletenessGateError(result: ReviewArtifactCompletenessResult): string {
  const missing = result.summary.missingArtifactIds.length > 0
    ? `missing artifacts: ${result.summary.missingArtifactIds.join(", ")}`
    : "";
  const incomplete = result.summary.incompleteArtifactIds.length > 0
    ? `incomplete artifacts: ${result.summary.incompleteArtifactIds.join(", ")}`
    : "";
  const details = [missing, incomplete].filter(Boolean).join("; ");
  return `Review artifact completeness gate blocked redesign recommendation generation (${details}).`;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeGenerateDiagnosisReportCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
