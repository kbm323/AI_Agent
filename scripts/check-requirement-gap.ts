import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { loadDiagnosisReportArtifact, parseReadmeMvpRequirements } from "../src/inspection.ts";
import type {
  MvpCapabilityStatus,
  ReadmeMvpRequirementEntry,
  ReadmeRequirementImplementationStatus,
  ReadmeRequirementImplementationMapping,
  RequirementCapabilityMatchStatus,
  ReviewFindingCategory,
} from "../src/inspection.ts";

const requiredPriorityOrder: ReviewFindingCategory[] = [
  "error_frequency",
  "maintainability",
  "token_cost",
  "architecture_fit",
  "feature_completeness",
];

export interface RequirementGapCheckResult {
  command: "ai-agent check-requirement-gap";
  artifact: {
    name: "requirementToGapMappingArtifact";
    present: boolean;
    schemaVersion?: string;
    capabilityIds: string[];
    implementedCount?: number;
    missingCount?: number;
    readmeRequirementCount?: number;
    readmeRequirementStatusCounts?: Record<ReadmeRequirementImplementationStatus, number>;
    artifactPath: string;
    artifactFilePresent: boolean;
    artifactFileMatchesDiagnosis: boolean;
    capabilityMappings: Array<{
      id: string;
      readmeRequirement: string;
      readmeRequirementIds: string[];
      status: MvpCapabilityStatus;
      gapDetected: boolean;
      evidenceSourceIds: string[];
    }>;
    readmeRequirementMappings: Array<{
      id: string;
      category: string;
      text: string;
      status: ReadmeRequirementImplementationStatus;
      capabilityIds: string[];
      evidenceSourceIds: string[];
    }>;
    requirementCapabilityMatches: Array<{
      id: string;
      requirementId: string;
      requirementText: string;
      capabilityIds: string[];
      status: RequirementCapabilityMatchStatus;
      evidenceSourceIds: string[];
    }>;
    requirementCapabilityMatchStatusCounts?: Record<RequirementCapabilityMatchStatus, number>;
    readmeRequirementCoverage: RequirementCoverageValidation;
    priorityOrderVerified: boolean;
    priorityOrderedEvidence: Array<{
      rank: number;
      category: ReviewFindingCategory;
      findingCount: number;
      present: boolean;
    }>;
    gapReviewArtifact: {
      path: string;
      present: boolean;
      schemaVersion?: string;
      artifactFilePresent: boolean;
      artifactFileMatchesComputed: boolean;
      observedGapCount: number;
      capabilityGapCount: number;
      readmeRequirementGapCount: number;
      nonOverlappingRequirementCoverage: boolean;
      duplicateCoverageKeys: string[];
      overlappingRequirementIds: string[];
    };
  };
  source: {
    diagnosisReportPath: string;
    readmePath: string;
    reviewEvidencePath: string;
  };
}

export interface RequirementCoverageValidation {
  valid: boolean;
  expectedCount: number;
  mappedCount: number;
  coveredExactlyOnce: boolean;
  missingRequirementIds: string[];
  duplicateRequirementIds: string[];
  unexpectedRequirementIds: string[];
}

export interface RequirementGapReviewArtifact {
  schemaVersion: "requirement-gap-review.v1";
  source: {
    mappingArtifactPath: string;
    diagnosisReportPath: string;
    readmePath: string;
  };
  observedGaps: ClassifiedRequirementGapRecord[];
  summary: {
    observedGapCount: number;
    capabilityGapCount: number;
    readmeRequirementGapCount: number;
    nonOverlappingRequirementCoverage: boolean;
    duplicateCoverageKeys: string[];
    overlappingRequirementIds: string[];
    sourceCapabilityCount: number;
    sourceReadmeRequirementCount: number;
  };
}

export interface ClassifiedRequirementGapRecord {
  kind: "classified_requirement_gap";
  id: string;
  requirementId: string;
  requirementText: string;
  status: Exclude<RequirementCapabilityMatchStatus, "matched">;
  missingCapabilityIds: string[];
  matchedCapabilityIds: string[];
  coverageKeys: string[];
  evidenceSourceIds: string[];
}

export function checkRequirementGapMapping(projectRoot = process.cwd()): RequirementGapCheckResult {
  const diagnosis = loadDiagnosisReportArtifact({ projectRoot });
  const mapping = diagnosis.requirementToGapMappingArtifact;
  const artifactPath = resolve(projectRoot, "docs", "generated", "requirement-gap-mapping.json");
  const artifactFilePresent = existsSync(artifactPath);
  const artifactFileMatchesDiagnosis = artifactFilePresent && artifactsMatch(readJsonArtifact(artifactPath), mapping);
  const reviewEvidencePath = resolve(projectRoot, "docs", "review-evidence.json");
  const priorityOrderedEvidence = loadPriorityOrderedEvidence(reviewEvidencePath);
  const readmeRequirements = parseReadmeMvpRequirements(readFileSync(diagnosis.source.readmePath, "utf8")).requirements;
  const readmeRequirementMappingsValid = validateReadmeRequirementMappings(mapping);
  const requirementCapabilityMatchesValid = validateRequirementCapabilityMatches(mapping);
  const readmeRequirementCoverage = validateReadmeRequirementCoverage({
    readmeRequirements,
    mappings: mapping.readmeRequirementMappings,
  });
  const priorityOrderVerified =
    priorityOrderedEvidence.length === requiredPriorityOrder.length &&
    priorityOrderedEvidence.every(
      (evidence, index) =>
        evidence.rank === index + 1 && evidence.category === requiredPriorityOrder[index] && evidence.present,
    );
  const gapReviewArtifactPath = resolve(projectRoot, "docs", "generated", "requirement-gap-review.json");
  const computedGapReviewArtifact = buildRequirementGapReviewArtifact({
    mapping,
    mappingArtifactPath: artifactPath,
    diagnosisReportPath: diagnosis.source.diagnosisReportPath,
    readmePath: diagnosis.source.readmePath,
  });
  writeRequirementGapReviewArtifact(gapReviewArtifactPath, computedGapReviewArtifact);
  const gapReviewFilePresent = existsSync(gapReviewArtifactPath);
  const gapReviewFileMatchesComputed =
    gapReviewFilePresent && artifactsMatch(readJsonArtifact(gapReviewArtifactPath), computedGapReviewArtifact);
  const gapReviewCoverageValid = computedGapReviewArtifact.summary.nonOverlappingRequirementCoverage;

  return {
    command: "ai-agent check-requirement-gap",
    artifact: {
      name: "requirementToGapMappingArtifact",
      present:
        mapping.schemaVersion === "implementation-capabilities.v1" &&
        mapping.capabilities.length > 0 &&
        artifactFilePresent &&
        artifactFileMatchesDiagnosis &&
        readmeRequirementMappingsValid &&
        requirementCapabilityMatchesValid &&
        readmeRequirementCoverage.valid &&
        priorityOrderVerified &&
        gapReviewFilePresent &&
        gapReviewFileMatchesComputed &&
        gapReviewCoverageValid,
      schemaVersion: mapping.schemaVersion,
      capabilityIds: mapping.capabilities.map((capability) => capability.id),
      implementedCount: mapping.summary.implementedCount,
      missingCount: mapping.summary.missingCount,
      readmeRequirementCount: mapping.summary.readmeRequirementCount,
      readmeRequirementStatusCounts: mapping.summary.readmeRequirementStatusCounts,
      artifactPath,
      artifactFilePresent,
      artifactFileMatchesDiagnosis,
      capabilityMappings: mapping.capabilities.map((capability) => ({
        id: capability.id,
        readmeRequirement: capability.requirement,
        readmeRequirementIds: capability.readmeRequirementIds,
        status: capability.status,
        gapDetected: capability.gapDetected,
        evidenceSourceIds: capability.evidenceSourceIds,
      })),
      readmeRequirementMappings: mapping.readmeRequirementMappings.map((requirement) => ({
        id: requirement.id,
        category: requirement.category,
        text: requirement.text,
        status: requirement.status,
        capabilityIds: requirement.capabilityIds,
        evidenceSourceIds: requirement.evidenceSourceIds,
      })),
      requirementCapabilityMatches: mapping.requirementCapabilityMatches.map((match) => ({
        id: match.id,
        requirementId: match.requirementId,
        requirementText: match.requirementText,
        capabilityIds: match.capabilityIds,
        status: match.status,
        evidenceSourceIds: match.evidenceSourceIds,
      })),
      requirementCapabilityMatchStatusCounts: mapping.summary.requirementCapabilityMatchStatusCounts,
      readmeRequirementCoverage,
      priorityOrderVerified,
      priorityOrderedEvidence,
      gapReviewArtifact: {
        path: gapReviewArtifactPath,
        present:
          computedGapReviewArtifact.schemaVersion === "requirement-gap-review.v1" &&
          gapReviewFilePresent &&
          gapReviewFileMatchesComputed &&
          gapReviewCoverageValid,
        schemaVersion: computedGapReviewArtifact.schemaVersion,
        artifactFilePresent: gapReviewFilePresent,
        artifactFileMatchesComputed: gapReviewFileMatchesComputed,
        observedGapCount: computedGapReviewArtifact.summary.observedGapCount,
        capabilityGapCount: computedGapReviewArtifact.summary.capabilityGapCount,
        readmeRequirementGapCount: computedGapReviewArtifact.summary.readmeRequirementGapCount,
        nonOverlappingRequirementCoverage: computedGapReviewArtifact.summary.nonOverlappingRequirementCoverage,
        duplicateCoverageKeys: computedGapReviewArtifact.summary.duplicateCoverageKeys,
        overlappingRequirementIds: computedGapReviewArtifact.summary.overlappingRequirementIds,
      },
    },
    source: {
      diagnosisReportPath: diagnosis.source.diagnosisReportPath,
      readmePath: diagnosis.source.readmePath,
      reviewEvidencePath,
    },
  };
}

export function validateReadmeRequirementCoverage(input: {
  readmeRequirements: ReadmeMvpRequirementEntry[];
  mappings: ReadmeRequirementImplementationMapping[];
}): RequirementCoverageValidation {
  const expectedById = new Map(input.readmeRequirements.map((requirement) => [requirement.id, requirement]));
  const mappedById = new Map<string, ReadmeRequirementImplementationMapping[]>();

  for (const mapping of input.mappings) {
    const bucket = mappedById.get(mapping.id) ?? [];
    bucket.push(mapping);
    mappedById.set(mapping.id, bucket);
  }

  const missingRequirementIds = input.readmeRequirements
    .filter((requirement) => !mappedById.has(requirement.id))
    .map((requirement) => requirement.id)
    .sort(compareStable);
  const duplicateRequirementIds = [...mappedById.entries()]
    .filter(([, mappings]) => mappings.length !== 1)
    .map(([id]) => id)
    .sort(compareStable);
  const unexpectedRequirementIds = [...mappedById.keys()]
    .filter((id) => !expectedById.has(id))
    .sort(compareStable);
  const mismatchedRequirementIds = input.mappings
    .filter((mapping) => {
      const expected = expectedById.get(mapping.id);
      return (
        expected !== undefined &&
        (mapping.category !== expected.category ||
          mapping.sourceSection !== expected.sourceSection ||
          mapping.order !== expected.order ||
          mapping.text !== expected.text)
      );
    })
    .map((mapping) => mapping.id);

  for (const id of mismatchedRequirementIds) {
    if (!unexpectedRequirementIds.includes(id)) {
      unexpectedRequirementIds.push(id);
    }
  }
  unexpectedRequirementIds.sort(compareStable);

  const coveredExactlyOnce =
    missingRequirementIds.length === 0 &&
    duplicateRequirementIds.length === 0 &&
    unexpectedRequirementIds.length === 0;

  return {
    valid: coveredExactlyOnce && input.mappings.length === input.readmeRequirements.length,
    expectedCount: input.readmeRequirements.length,
    mappedCount: input.mappings.length,
    coveredExactlyOnce,
    missingRequirementIds,
    duplicateRequirementIds,
    unexpectedRequirementIds,
  };
}

export function buildRequirementGapReviewArtifact(input: {
  mapping: ReturnType<typeof loadDiagnosisReportArtifact>["requirementToGapMappingArtifact"];
  mappingArtifactPath: string;
  diagnosisReportPath: string;
  readmePath: string;
}): RequirementGapReviewArtifact {
  const capabilitiesById = new Map(input.mapping.capabilities.map((capability) => [capability.id, capability]));
  const observedGaps = input.mapping.requirementCapabilityMatches
    .filter((match): match is typeof match & { status: Exclude<RequirementCapabilityMatchStatus, "matched"> } =>
      match.status === "missing" || match.status === "partial",
    )
    .map((match) => {
      const missingCapabilityIds = match.capabilityIds
        .filter((capabilityId) => capabilitiesById.get(capabilityId)?.status === "missing")
        .sort(compareStable);
      const matchedCapabilityIds = match.capabilityIds
        .filter((capabilityId) => capabilitiesById.get(capabilityId)?.status === "implemented")
        .sort(compareStable);
      const coveredCapabilityIds = missingCapabilityIds.length > 0 ? missingCapabilityIds : match.capabilityIds;
      return {
        kind: "classified_requirement_gap" as const,
        id: `classified:${match.requirementId}`,
        requirementId: match.requirementId,
        requirementText: match.requirementText,
        status: match.status,
        missingCapabilityIds,
        matchedCapabilityIds,
        coverageKeys: coveredCapabilityIds.map((capabilityId) => `${match.requirementId}::${capabilityId}`).sort(compareStable),
        evidenceSourceIds: [...match.evidenceSourceIds].sort(compareStable),
      };
    })
    .sort((left, right) => compareStable(left.id, right.id));
  const coverageAudit = auditRequirementGapCoverage(observedGaps);
  const capabilityGapCount = new Set(observedGaps.flatMap((gap) => gap.missingCapabilityIds)).size;

  return {
    schemaVersion: "requirement-gap-review.v1",
    source: {
      mappingArtifactPath: input.mappingArtifactPath,
      diagnosisReportPath: input.diagnosisReportPath,
      readmePath: input.readmePath,
    },
    observedGaps,
    summary: {
      observedGapCount: observedGaps.length,
      capabilityGapCount,
      readmeRequirementGapCount: observedGaps.length,
      nonOverlappingRequirementCoverage: coverageAudit.nonOverlapping,
      duplicateCoverageKeys: coverageAudit.duplicateCoverageKeys,
      overlappingRequirementIds: coverageAudit.overlappingRequirementIds,
      sourceCapabilityCount: input.mapping.capabilities.length,
      sourceReadmeRequirementCount: input.mapping.readmeRequirementMappings.length,
    },
  };
}

function auditRequirementGapCoverage(gaps: ClassifiedRequirementGapRecord[]): {
  nonOverlapping: boolean;
  duplicateCoverageKeys: string[];
  overlappingRequirementIds: string[];
} {
  const seenCoverageKeys = new Set<string>();
  const duplicateCoverageKeys = new Set<string>();
  const seenRequirementIds = new Set<string>();
  const overlappingRequirementIds = new Set<string>();

  for (const gap of gaps) {
    if (seenRequirementIds.has(gap.requirementId)) {
      overlappingRequirementIds.add(gap.requirementId);
    }
    seenRequirementIds.add(gap.requirementId);

    for (const key of gap.coverageKeys) {
      if (seenCoverageKeys.has(key)) {
        duplicateCoverageKeys.add(key);
      }
      seenCoverageKeys.add(key);
    }
  }

  return {
    nonOverlapping: duplicateCoverageKeys.size === 0 && overlappingRequirementIds.size === 0,
    duplicateCoverageKeys: [...duplicateCoverageKeys].sort(compareStable),
    overlappingRequirementIds: [...overlappingRequirementIds].sort(compareStable),
  };
}

function writeRequirementGapReviewArtifact(path: string, artifact: RequirementGapReviewArtifact): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(artifact, null, 2)}\n`, "utf8");
}

function readJsonArtifact(path: string): unknown {
  return JSON.parse(readFileSync(path, "utf8"));
}

function artifactsMatch(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function compareStable(left: string, right: string): number {
  return left.localeCompare(right, "en");
}

function validateReadmeRequirementMappings(mapping: ReturnType<typeof loadDiagnosisReportArtifact>["requirementToGapMappingArtifact"]): boolean {
  const allowedStatuses = new Set<ReadmeRequirementImplementationStatus>(["covered", "partial", "missing", "unknown"]);
  const mappings = mapping.readmeRequirementMappings;
  if (!Array.isArray(mappings) || mappings.length !== mapping.summary.readmeRequirementCount) return false;
  if (!mapping.summary.readmeRequirementStatusCounts) return false;

  const ids = new Set<string>();
  const computedCounts: Record<ReadmeRequirementImplementationStatus, number> = {
    covered: 0,
    partial: 0,
    missing: 0,
    unknown: 0,
  };

  for (const requirement of mappings) {
    if (ids.has(requirement.id)) return false;
    ids.add(requirement.id);
    if (!allowedStatuses.has(requirement.status)) return false;
    if (requirement.text.trim().length === 0) return false;
    if (!Array.isArray(requirement.capabilityIds) || !Array.isArray(requirement.evidenceSourceIds)) return false;
    if (requirement.status === "unknown" && requirement.capabilityIds.length > 0) return false;
    if (requirement.status !== "unknown" && requirement.capabilityIds.length === 0) return false;
    if ((requirement.status === "covered" || requirement.status === "partial") && requirement.evidenceSourceIds.length === 0) return false;
    if (requirement.status === "missing" && requirement.evidenceSourceIds.length !== 0) return false;
    computedCounts[requirement.status] += 1;
  }

  return JSON.stringify(computedCounts) === JSON.stringify(mapping.summary.readmeRequirementStatusCounts);
}

function validateRequirementCapabilityMatches(
  mapping: ReturnType<typeof loadDiagnosisReportArtifact>["requirementToGapMappingArtifact"],
): boolean {
  const allowedStatuses = new Set<RequirementCapabilityMatchStatus>(["matched", "partial", "missing"]);
  const matches = mapping.requirementCapabilityMatches;
  if (!Array.isArray(matches) || matches.length !== mapping.summary.readmeRequirementCount) return false;
  if (!mapping.summary.requirementCapabilityMatchStatusCounts) return false;

  const requirementIds = new Set(mapping.readmeRequirementMappings.map((requirement) => requirement.id));
  const ids = new Set<string>();
  const computedCounts: Record<RequirementCapabilityMatchStatus, number> = {
    matched: 0,
    partial: 0,
    missing: 0,
  };

  for (const match of matches) {
    if (ids.has(match.id)) return false;
    ids.add(match.id);
    if (!requirementIds.has(match.requirementId)) return false;
    if (!allowedStatuses.has(match.status)) return false;
    if (match.requirementText.trim().length === 0) return false;
    if (!Array.isArray(match.capabilityIds) || !Array.isArray(match.evidenceSourceIds)) return false;
    if (match.status === "matched" && (match.capabilityIds.length === 0 || match.evidenceSourceIds.length === 0)) return false;
    if (match.status === "partial" && (match.capabilityIds.length < 2 || match.evidenceSourceIds.length === 0)) return false;
    if (match.status === "missing" && match.evidenceSourceIds.length !== 0) return false;
    computedCounts[match.status] += 1;
  }

  return JSON.stringify(computedCounts) === JSON.stringify(mapping.summary.requirementCapabilityMatchStatusCounts);
}

export function executeRequirementGapCheckCommand(args: string[]): { exitCode: number; stdout: string; stderr: string } {
  try {
    const projectRoot = readProjectRootArg(args) ?? process.cwd();
    const result = checkRequirementGapMapping(projectRoot);
    if (!result.artifact.present) {
      return {
        exitCode: 1,
        stdout: `${JSON.stringify(result, null, 2)}\n`,
        stderr: "",
      };
    }
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown requirement-gap check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function readProjectRootArg(args: string[]): string | undefined {
  const rootFlagIndex = args.indexOf("--project-root");
  if (rootFlagIndex === -1) return undefined;
  return args[rootFlagIndex + 1] ?? "";
}

function loadPriorityOrderedEvidence(reviewEvidencePath: string): RequirementGapCheckResult["artifact"]["priorityOrderedEvidence"] {
  const artifact = JSON.parse(readFileSync(reviewEvidencePath, "utf8")) as {
    summary?: {
      findingsByCategory?: Partial<Record<ReviewFindingCategory, number>>;
    };
  };
  const findingsByCategory = artifact.summary?.findingsByCategory ?? {};

  return requiredPriorityOrder.map((category, index) => ({
    rank: index + 1,
    category,
    findingCount: findingsByCategory[category] ?? 0,
    present: Object.prototype.hasOwnProperty.call(findingsByCategory, category),
  }));
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeRequirementGapCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
