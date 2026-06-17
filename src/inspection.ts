import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { evaluateProjectFindings } from "./evaluation.ts";

export interface InspectionInventoryEntry {
  id: string;
  relativePath: string;
  kind: "source" | "test" | "script" | "doc" | "config";
  moduleName: string;
}

export type SourceFileKind = Extract<InspectionInventoryEntry["kind"], "source" | "test" | "script">;

export interface ClassifiedSourceFileEntry extends InspectionInventoryEntry {
  kind: SourceFileKind;
  extension: ".ts" | ".js";
}

export interface TestFileInventoryEntry {
  id: string;
  relativePath: string;
  kind: "test_file";
  extension: string;
  metadata: {
    directory: string;
    fileName: string;
    framework: "node:test" | "unknown";
    containsTestDeclaration: boolean;
  };
}

export interface ConfigFileInventoryEntry {
  id: string;
  relativePath: string;
  kind: "config_file";
  extension: string;
  metadata: {
    directory: string;
    fileName: string;
    configType: string;
    packageScriptNames?: string[];
    hasTypecheckScript?: boolean;
    hasTestScript?: boolean;
  };
}

export interface TestAndConfigDiscoveryInventory {
  schemaVersion: "test-config-discovery.v1";
  testFiles: TestFileInventoryEntry[];
  configFiles: ConfigFileInventoryEntry[];
  summary: {
    testFileCount: number;
    configFileCount: number;
    normalizedPathSeparator: "/";
  };
}

export type RunnableEntryPointSource =
  | "package_exports"
  | "package_main"
  | "package_bin"
  | "package_script"
  | "common_main_file"
  | "scripts_directory"
  | "guarded_main_block";

export interface RunnableEntryPoint {
  id: string;
  name: string;
  source: RunnableEntryPointSource;
  relativePath?: string;
  command?: string;
  evidence: string;
}

export type ReviewFindingSeverity = "critical" | "high" | "medium" | "low";

export type ReviewFindingCategory =
  | "error_frequency"
  | "maintainability"
  | "token_cost"
  | "architecture_fit"
  | "feature_completeness";

export interface InspectedModuleInput extends InspectionInventoryEntry {
  content?: string;
}

export interface ReviewFinding {
  id: string;
  sourceId: string;
  relativePath: string;
  moduleName: string;
  severity: ReviewFindingSeverity;
  category: ReviewFindingCategory;
  title: string;
  evidence: string;
  recommendation: string;
}

export interface ReviewEvidenceArtifact {
  schemaVersion: "review-evidence.v1";
  inventory: InspectionInventoryEntry[];
  findings: ReviewFinding[];
  summary: {
    inspectedModules: number;
    findingCount: number;
    findingsBySeverity: Record<ReviewFindingSeverity, number>;
    findingsByCategory: Record<ReviewFindingCategory, number>;
    recommendation: "keep" | "partial_redesign" | "full_replan";
  };
}

export interface ReviewEvidencePathEntry {
  relativePath: string;
  contentHash: {
    algorithm: "sha256";
    value: string;
  };
  byteLength: number;
}

export interface ReviewEvidencePathArtifact {
  schemaVersion: "review-evidence-paths.v1";
  inspectedPaths: ReviewEvidencePathEntry[];
  summary: {
    inspectedPathCount: number;
    hashAlgorithm: "sha256";
  };
}

export type RedesignRecommendation = "partial_redesign" | "full_replan";

export interface PriorReviewEvidenceValidationResult {
  valid: boolean;
  missingFields: string[];
}

export interface PriorReviewEvidenceCompletenessResult {
  complete: boolean;
  missingFields: string[];
  insufficientContent: string[];
}

export interface GovernedRecommendation {
  recommendation: ReviewEvidenceArtifact["summary"]["recommendation"];
  evidenceArtifactPath?: string;
  evidenceArtifactCreated: boolean;
  findingCount: number;
}

export type GovernedRecommendationStatus = "complete" | "incomplete";

export interface GovernedRecommendationDecision {
  status: GovernedRecommendationStatus;
  evidenceArtifactPath?: string;
  evidenceArtifactCreated: boolean;
  findingCount: number;
  decisionGate: RedesignDecisionGateResult;
  recommendation?: ReviewEvidenceArtifact["summary"]["recommendation"];
  blockedRecommendation?: RedesignRecommendation;
  incompleteReasons: string[];
}

export interface ReadmeDerivedMvpRequirements {
  mvpGoalFlow: string[];
  operatingPrinciples: string[];
  executionCommands: string[];
  publicApiSymbols: string[];
}

export type ReadmeMvpRequirementCategory =
  | "mvp_goal_flow"
  | "operating_principle"
  | "execution_command"
  | "public_api_symbol";

export interface ReadmeMvpRequirementEntry {
  id: string;
  category: ReadmeMvpRequirementCategory;
  sourceSection: string;
  order: number;
  text: string;
}

export interface ReadmeMvpRequirementExtraction {
  schemaVersion: "readme-mvp-requirements.v1";
  source: {
    document: "README.md";
    sections: string[];
  };
  requirements: ReadmeMvpRequirementEntry[];
  summary: {
    totalCount: number;
    countByCategory: Record<ReadmeMvpRequirementCategory, number>;
  };
}

export interface ReadmeMvpRequirementValidationResult {
  valid: boolean;
  errors: string[];
  computed: {
    totalCount: number;
    countByCategory: Record<ReadmeMvpRequirementCategory, number>;
    sections: string[];
  };
}

export interface DiagnosisReportArtifact {
  schemaVersion: "diagnosis-report.v1";
  source: {
    readmePath: string;
    diagnosisReportPath: string;
    externalReadmePath: string;
    externalReadmeAccessible: boolean;
  };
  readmeDerivedMvpRequirements: ReadmeDerivedMvpRequirements;
  diagnosis: {
    decision?: ReviewEvidenceArtifact["summary"]["recommendation"];
    decisionEvidenceArtifact?: string;
  };
  requirementToGapMappingArtifact: ImplementationCapabilityArtifact;
}

export type MvpCapabilityStatus = "implemented" | "missing";
export type ReadmeRequirementImplementationStatus = "covered" | "partial" | "missing" | "unknown";
export type RequirementCapabilityMatchStatus = "matched" | "partial" | "missing";

export interface ReadmeRequirementImplementationMapping {
  id: string;
  category: ReadmeMvpRequirementCategory;
  sourceSection: string;
  order: number;
  text: string;
  status: ReadmeRequirementImplementationStatus;
  capabilityIds: string[];
  evidenceSourceIds: string[];
}

export interface RequirementCapabilityMatchRecord {
  id: string;
  requirementId: string;
  requirementText: string;
  capabilityIds: string[];
  status: RequirementCapabilityMatchStatus;
  evidenceSourceIds: string[];
}

export interface MvpCapabilityScanEntry {
  id: string;
  requirement: string;
  readmeRequirementIds: string[];
  gapDescription: string;
  gapDetected: boolean;
  status: MvpCapabilityStatus;
  evidenceSourceIds: string[];
}

export interface ImplementationCapabilityArtifact {
  schemaVersion: "implementation-capabilities.v1";
  capabilities: MvpCapabilityScanEntry[];
  readmeRequirementMappings: ReadmeRequirementImplementationMapping[];
  requirementCapabilityMatches: RequirementCapabilityMatchRecord[];
  summary: {
    implementedCount: number;
    missingCount: number;
    readmeRequirementCount: number;
    readmeRequirementStatusCounts: Record<ReadmeRequirementImplementationStatus, number>;
    requirementCapabilityMatchStatusCounts: Record<RequirementCapabilityMatchStatus, number>;
  };
}

export interface RequirementGapMappingValidationResult {
  valid: boolean;
  errors: string[];
  computed: {
    readmeRequirementCount: number;
    readmeRequirementStatusCounts: Record<ReadmeRequirementImplementationStatus, number>;
  };
}

export interface RedesignDecisionGateResult {
  accepted: boolean;
  reasons: string[];
  reviewRequired: boolean;
  reviewReasons: string[];
  missingFields: string[];
  insufficientContent: string[];
}

export interface CompletedReviewArtifactGateInput {
  status: "passed" | "failed";
  summary: {
    missingArtifactIds: string[];
    incompleteArtifactIds: string[];
  };
}

export interface PriorReviewArtifactHandlerResponse {
  command: "ai-agent prior-review";
  artifact: {
    identifier: string;
    path: string;
    schemaVersion?: string;
    recommendation?: string;
    inspectedModules?: number;
    findingCount?: number;
  };
  decisionBasis: {
    priorReviewArtifactPath: string;
    recommendation: ReviewEvidenceArtifact["summary"]["recommendation"];
  };
  validation: PriorReviewEvidenceValidationResult;
  completeness: PriorReviewEvidenceCompletenessResult;
  decisionGate: RedesignDecisionGateResult;
  runnable: {
    dryRunCommand: string;
  };
  escalation?: {
    required: true;
    reasons: string[];
  };
}

const inspectedExtensions = new Set([".ts", ".js", ".json", ".md"]);
const sourceFileExtensions = new Set([".ts", ".js"]);
const testFileExtensions = new Set([".ts", ".js", ".mjs", ".cjs"]);
const readmeMvpRequirementCategories = [
  "mvp_goal_flow",
  "operating_principle",
  "execution_command",
  "public_api_symbol",
] as const satisfies readonly ReadmeMvpRequirementCategory[];
const readmeRequirementImplementationStatuses = [
  "covered",
  "partial",
  "missing",
  "unknown",
] as const satisfies readonly ReadmeRequirementImplementationStatus[];
const readmeMvpRequirementSourceSections: Record<ReadmeMvpRequirementCategory, string> = {
  mvp_goal_flow: "## MVP 목표",
  operating_principle: "## 운영 원칙",
  execution_command: "## 실행",
  public_api_symbol: "## Public API",
};
const configFileNames = new Set([
  ".eslintrc",
  ".eslintrc.cjs",
  ".eslintrc.js",
  ".eslintrc.json",
  ".prettierrc",
  ".prettierrc.cjs",
  ".prettierrc.js",
  ".prettierrc.json",
  "eslint.config.cjs",
  "eslint.config.js",
  "eslint.config.mjs",
  "jsconfig.json",
  "package.json",
  "prettier.config.cjs",
  "prettier.config.js",
  "pyproject.toml",
  "pytest.ini",
  "ruff.toml",
  "tsconfig.json",
  "tsconfig.build.json",
  "vite.config.js",
  "vite.config.mjs",
  "vite.config.ts",
  "vitest.config.js",
  "vitest.config.mjs",
  "vitest.config.ts",
]);
const ignoredDirectories = new Set([
  ".cache",
  ".git",
  ".mypy_cache",
  ".next",
  ".nuxt",
  ".pytest_cache",
  ".ruff_cache",
  ".turbo",
  ".venv",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "env",
  "generated",
  "node_modules",
  "out",
  "tmp",
  "venv",
]);
const inspectedRoots = ["README.md", "package.json", "src", "scripts", "tests", "docs"];

export function buildInspectionInventory(projectRoot: string): InspectionInventoryEntry[] {
  const files = inspectedRoots.flatMap((rootName) => collectFiles(join(projectRoot, rootName), projectRoot));
  return files
    .filter((file) => inspectedExtensions.has(extname(file)))
    .sort(compareStable)
    .map((relativePath) => ({
      id: `existing:${relativePath}`,
      relativePath,
      kind: classifyPath(relativePath),
      moduleName: buildModuleName(relativePath),
    }));
}

export function discoverSourceFiles(projectRoot: string): ClassifiedSourceFileEntry[] {
  return buildInspectionInventory(projectRoot)
    .filter(isClassifiedSourceFileEntry)
    .map((entry) => ({
      ...entry,
      extension: extname(entry.relativePath) as ClassifiedSourceFileEntry["extension"],
    }))
    .sort((left, right) => compareStable(left.relativePath, right.relativePath));
}

export function discoverTestAndConfigFiles(projectRoot: string): TestAndConfigDiscoveryInventory {
  const root = resolve(projectRoot);
  const allFiles = collectFiles(root, root).sort(compareStable);
  const testFiles = allFiles.filter(isTestFilePath).map((relativePath) => buildTestFileInventoryEntry(root, relativePath));
  const configFiles = allFiles
    .filter(isProjectConfigFilePath)
    .map((relativePath) => buildConfigFileInventoryEntry(root, relativePath));

  return {
    schemaVersion: "test-config-discovery.v1",
    testFiles,
    configFiles,
    summary: {
      testFileCount: testFiles.length,
      configFileCount: configFiles.length,
      normalizedPathSeparator: "/",
    },
  };
}

export function discoverRunnableEntryPoints(projectRoot: string): RunnableEntryPoint[] {
  const root = resolve(projectRoot);
  const packageMetadata = readPackageMetadata(root);
  const entryPoints: RunnableEntryPoint[] = [];

  if (packageMetadata) {
    entryPoints.push(...discoverPackageEntryPoints(root, packageMetadata));
  }

  entryPoints.push(...discoverCommonMainFileEntryPoints(root));
  entryPoints.push(...discoverScriptDirectoryEntryPoints(root));
  entryPoints.push(...discoverGuardedMainBlockEntryPoints(root));

  return entryPoints
    .filter((entry, index, entries) => entries.findIndex((candidate) => candidate.id === entry.id) === index)
    .sort(compareRunnableEntryPoints);
}

export function buildReviewEvidencePathArtifact(input: {
  projectRoot: string;
  paths: string[];
}): ReviewEvidencePathArtifact {
  const projectRoot = resolve(input.projectRoot);
  const inspectedPaths = input.paths
    .map((path) => normalizeEvidenceRelativePath(projectRoot, path))
    .filter(unique)
    .sort(compareStable)
    .map((relativePath) => {
      const absolutePath = resolve(projectRoot, relativePath);
      const stats = statSync(absolutePath);
      if (!stats.isFile()) {
        throw new TypeError(`review evidence path must be a file: ${relativePath}`);
      }
      const content = readFileSync(absolutePath);
      return {
        relativePath,
        contentHash: {
          algorithm: "sha256" as const,
          value: createHash("sha256").update(content).digest("hex"),
        },
        byteLength: content.byteLength,
      };
    });

  return {
    schemaVersion: "review-evidence-paths.v1",
    inspectedPaths,
    summary: {
      inspectedPathCount: inspectedPaths.length,
      hashAlgorithm: "sha256",
    },
  };
}

export function extractReviewFindings(inputs: InspectedModuleInput[]): ReviewFinding[] {
  return inputs.flatMap((input) => buildFindingsForModule(input)).sort(compareFindings);
}

export function buildReviewEvidenceArtifact(input: {
  inventory: InspectionInventoryEntry[];
  findings: ReviewFinding[];
}): ReviewEvidenceArtifact {
  const inventory = [...input.inventory].sort((left, right) => compareStable(left.id, right.id));
  const findings = [...input.findings].sort(compareFindings);

  return {
    schemaVersion: "review-evidence.v1",
    inventory,
    findings,
    summary: {
      inspectedModules: inventory.length,
      findingCount: findings.length,
      findingsBySeverity: countBy(reviewFindingSeverities, findings, (finding) => finding.severity),
      findingsByCategory: countBy(reviewFindingCategories, findings, (finding) => finding.category),
      recommendation: evaluateProjectFindings(findings).recommendation,
    },
  };
}

export function buildImplementationCapabilityArtifact(input: {
  inventory: InspectionInventoryEntry[];
  readmeRequirements: ReadmeDerivedMvpRequirements;
}): ImplementationCapabilityArtifact {
  const inventory = [...input.inventory].sort((left, right) => compareStable(left.id, right.id));
  const capabilityRules = buildMvpCapabilityRules();
  const readmeRequirementEntries = flattenReadmeRequirements(input.readmeRequirements);
  const ruleEvidence = capabilityRules.map((rule) => ({
    rule,
    evidenceSourceIds: inventory
      .filter((entry) => rule.matches(entry))
      .map((entry) => entry.id)
      .sort(compareStable),
  }));
  const capabilities = ruleEvidence.map(({ rule, evidenceSourceIds }) => {
    const status: MvpCapabilityStatus = evidenceSourceIds.length > 0 ? "implemented" : "missing";
    return {
      id: rule.id,
      requirement: rule.requirement,
      readmeRequirementIds: readmeRequirementEntries
        .filter((entry) => rule.matchesReadmeRequirement(entry))
        .map((entry) => entry.id)
        .sort(compareStable),
      gapDescription: rule.gapDescription,
      gapDetected: status === "missing",
      status,
      evidenceSourceIds,
    };
  });
  const readmeRequirementMappings = readmeRequirementEntries.map((requirement) =>
    buildReadmeRequirementImplementationMapping(requirement, ruleEvidence),
  );
  const requirementCapabilityMatches = readmeRequirementEntries.map((requirement) =>
    buildRequirementCapabilityMatchRecord(requirement, ruleEvidence),
  );

  const implementedCount = capabilities.filter((capability) => capability.status === "implemented").length;

  return {
    schemaVersion: "implementation-capabilities.v1",
    capabilities,
    readmeRequirementMappings,
    requirementCapabilityMatches,
    summary: {
      implementedCount,
      missingCount: capabilities.length - implementedCount,
      readmeRequirementCount: readmeRequirementEntries.length,
      readmeRequirementStatusCounts: countReadmeRequirementMappingStatuses(readmeRequirementMappings),
      requirementCapabilityMatchStatusCounts: countRequirementCapabilityMatchStatuses(requirementCapabilityMatches),
    },
  };
}

export function writeReviewEvidenceArtifact(input: {
  outputPath: string;
  inventory: InspectionInventoryEntry[];
  findings: ReviewFinding[];
}): ReviewEvidenceArtifact {
  const artifact = buildReviewEvidenceArtifact({ inventory: input.inventory, findings: input.findings });
  mkdirSync(dirname(input.outputPath), { recursive: true });
  writeFileSync(input.outputPath, `${JSON.stringify(artifact, null, 2)}\n`, "utf8");
  return artifact;
}

export function emitGovernedRecommendation(input: {
  artifact: ReviewEvidenceArtifact;
  evidenceArtifactCreated: boolean;
  evidenceArtifactPath?: string;
  completedReviewArtifacts?: CompletedReviewArtifactGateInput;
}): GovernedRecommendation {
  const decision = buildGovernedRecommendationDecision(input);
  if (decision.status === "incomplete") {
    throw new Error(buildRedesignDecisionGateError(decision.decisionGate));
  }

  return {
    recommendation: decision.recommendation,
    evidenceArtifactPath: input.evidenceArtifactPath,
    evidenceArtifactCreated: input.evidenceArtifactCreated,
    findingCount: input.artifact.summary.findingCount,
  };
}

export function buildGovernedRecommendationDecision(input: {
  artifact: ReviewEvidenceArtifact;
  evidenceArtifactCreated: boolean;
  evidenceArtifactPath?: string;
  completedReviewArtifacts?: CompletedReviewArtifactGateInput;
}): GovernedRecommendationDecision {
  const recommendation = input.artifact.summary.recommendation;
  const gate = gateRedesignDecision({
    recommendation,
    priorReviewEvidence: input.artifact,
    evidenceArtifactCreated: input.evidenceArtifactCreated,
    completedReviewArtifacts: input.completedReviewArtifacts,
  });
  const incompleteReasons = [...gate.reasons, ...gate.reviewReasons];

  if (!gate.accepted && isRedesignRecommendation(recommendation)) {
    return {
      status: "incomplete",
      evidenceArtifactPath: input.evidenceArtifactPath,
      evidenceArtifactCreated: input.evidenceArtifactCreated,
      findingCount: input.artifact.summary.findingCount,
      decisionGate: gate,
      blockedRecommendation: recommendation,
      incompleteReasons,
    };
  }

  return {
    status: "complete",
    evidenceArtifactPath: input.evidenceArtifactPath,
    evidenceArtifactCreated: input.evidenceArtifactCreated,
    findingCount: input.artifact.summary.findingCount,
    decisionGate: gate,
    recommendation,
    incompleteReasons,
  };
}

export function handlePriorReviewArtifact(input: { identifier: string; projectRoot?: string }): PriorReviewArtifactHandlerResponse {
  const artifactPath = resolvePriorReviewArtifactIdentifier(input);
  const artifact = readJsonArtifact(artifactPath);
  const validation = validatePriorReviewEvidenceForRedesignDecision(artifact);
  const completeness = checkPriorReviewEvidenceCompletenessForRedesignDecision(artifact);
  const recommendation = readSummaryRecommendation(artifact);
  const decisionGate = gateRedesignDecision({
    recommendation,
    priorReviewEvidence: artifact,
    evidenceArtifactCreated: true,
  });
  const reasons = [...decisionGate.reasons, ...decisionGate.reviewReasons];

  return {
    command: "ai-agent prior-review",
    artifact: {
      identifier: input.identifier,
      path: artifactPath,
      schemaVersion: readStringAtPath(artifact, "schemaVersion"),
      recommendation,
      inspectedModules: readNumberAtPath(artifact, "summary.inspectedModules"),
      findingCount: readNumberAtPath(artifact, "summary.findingCount"),
    },
    decisionBasis: {
      priorReviewArtifactPath: artifactPath,
      recommendation,
    },
    validation,
    completeness,
    decisionGate,
    runnable: {
      dryRunCommand: `npm run dry-run -- --prior-review-artifact ${JSON.stringify(input.identifier)}`,
    },
    escalation: decisionGate.accepted
      ? undefined
      : {
          required: true,
          reasons,
        },
  };
}

export function loadDiagnosisReportArtifact(input: {
  projectRoot?: string;
  readmePath?: string;
  diagnosisReportPath?: string;
  externalReadmePath?: string;
} = {}): DiagnosisReportArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const readmePath = resolve(projectRoot, input.readmePath ?? "README.md");
  const diagnosisReportPath = resolve(projectRoot, input.diagnosisReportPath ?? join("docs", "diagnosis-report.md"));
  const externalReadmePath = input.externalReadmePath ?? "C:\\Users\\KBM\\Downloads\\260526_README.md";
  const readme = readFileSync(readmePath, "utf8");
  const diagnosisReport = readFileIfPresent(diagnosisReportPath);
  const readmeDerivedMvpRequirements = parseReadmeDerivedMvpRequirements(readme);

  return {
    schemaVersion: "diagnosis-report.v1",
    source: {
      readmePath,
      diagnosisReportPath,
      externalReadmePath,
      externalReadmeAccessible: existsSync(externalReadmePath),
    },
    readmeDerivedMvpRequirements,
    diagnosis: {
      decision: parseDiagnosisDecision(diagnosisReport),
      decisionEvidenceArtifact: parseDecisionEvidenceArtifact(diagnosisReport),
    },
    requirementToGapMappingArtifact: buildImplementationCapabilityArtifact({
      inventory: buildInspectionInventory(projectRoot),
      readmeRequirements: readmeDerivedMvpRequirements,
    }),
  };
}

export function validateRequirementGapMappingArtifact(
  artifact: unknown,
): RequirementGapMappingValidationResult {
  const errors: string[] = [];
  const mappings = isRecord(artifact) && Array.isArray(artifact.readmeRequirementMappings)
    ? artifact.readmeRequirementMappings
    : [];
  const mappingRecords = mappings.filter(isRecord);
  const computed = {
    readmeRequirementCount: mappings.length,
    readmeRequirementStatusCounts: countReadmeRequirementMappingStatuses(
      mappingRecords.filter((mapping): mapping is ReadmeRequirementImplementationMapping =>
        isReadmeRequirementImplementationMapping(mapping),
      ),
    ),
  };

  if (!isRecord(artifact)) {
    return {
      valid: false,
      errors: ["mapping artifact must be an object"],
      computed,
    };
  }
  if (artifact.schemaVersion !== "implementation-capabilities.v1") {
    errors.push("schemaVersion must be implementation-capabilities.v1");
  }
  if (!Array.isArray(artifact.readmeRequirementMappings)) {
    errors.push("readmeRequirementMappings must be an array");
  }
  if (!isRecord(artifact.summary)) {
    errors.push("summary must be an object");
  } else {
    if (artifact.summary.readmeRequirementCount !== computed.readmeRequirementCount) {
      errors.push("summary.readmeRequirementCount must match readmeRequirementMappings length");
    }
    if (!isRecord(artifact.summary.readmeRequirementStatusCounts)) {
      errors.push("summary.readmeRequirementStatusCounts must be an object");
    } else {
      for (const status of readmeRequirementImplementationStatuses) {
        if (artifact.summary.readmeRequirementStatusCounts[status] !== computed.readmeRequirementStatusCounts[status]) {
          errors.push(`summary.readmeRequirementStatusCounts.${status} must match mapped requirement count`);
        }
      }
    }
  }

  const ids = new Set<string>();
  mappings.forEach((mapping, index) => {
    const path = `readmeRequirementMappings[${index}]`;
    if (!isRecord(mapping)) {
      errors.push(`${path} must be an object`);
      return;
    }
    const id = typeof mapping.id === "string" && mapping.id.trim().length > 0 ? mapping.id : path;
    if (ids.has(id)) {
      errors.push(`${id} must be unique`);
    }
    ids.add(id);
    if (!isReadmeRequirementImplementationMapping(mapping)) {
      errors.push(`${id} must include id, category, sourceSection, order, text, status, capabilityIds, and evidenceSourceIds`);
      return;
    }
    if (mapping.status !== "unknown" && mapping.capabilityIds.length === 0) {
      errors.push(`${id} must include capabilityIds unless status is unknown`);
    }
    if ((mapping.status === "covered" || mapping.status === "partial") && mapping.evidenceSourceIds.length === 0) {
      errors.push(`${id} must include evidenceSourceIds when covered or partial`);
    }
  });

  return {
    valid: errors.length === 0,
    errors,
    computed,
  };
}

export function renderRequirementGapMappingSection(mapping: ImplementationCapabilityArtifact): string {
  const validation = validateRequirementGapMappingArtifact(mapping);
  if (!validation.valid) {
    throw new TypeError(`requirement-gap mapping is invalid: ${validation.errors.join(", ")}`);
  }

  const lines = [
    "## Requirement-to-Gap Mapping",
    "",
    "Generated from the validated README requirement-to-capability mapping artifact.",
    "",
    "| Requirement | Status | Capabilities | Evidence | Gap |",
    "| --- | --- | --- | --- | --- |",
    ...mapping.readmeRequirementMappings.map((requirement) => {
      const capabilities = requirement.capabilityIds.length > 0
        ? requirement.capabilityIds.map((id) => `\`${id}\``).join(", ")
        : "none";
      const evidence = requirement.evidenceSourceIds.length > 0
        ? requirement.evidenceSourceIds.map((id) => `\`${id.replace(/^existing:/, "")}\``).join(", ")
        : "none";
      const gap = requirement.status === "covered" ? "no" : requirement.status;
      return `| \`${requirement.id}\` ${escapeMarkdownTableText(requirement.text)} | ${requirement.status} | ${capabilities} | ${evidence} | ${gap} |`;
    }),
    "",
    `Mapped requirements: ${validation.computed.readmeRequirementCount}.`,
  ];

  return lines.join("\n");
}

export function renderDiagnosisReportWithRequirementGapSection(input: {
  markdown: string;
  mapping: ImplementationCapabilityArtifact;
}): string {
  const section = renderRequirementGapMappingSection(input.mapping);
  const lines = input.markdown.split(/\r?\n/);
  const sectionStart = lines.findIndex((line) => line.trim() === "## Requirement-to-Gap Mapping");
  if (sectionStart === -1) {
    return `${input.markdown.replace(/\s+$/, "")}\n\n${section}\n`;
  }
  const nextSectionStart = lines.findIndex((line, index) => index > sectionStart && /^## [^#]/.test(line));
  const before = lines.slice(0, sectionStart).join("\n").replace(/\s+$/, "");
  const after = nextSectionStart === -1 ? "" : lines.slice(nextSectionStart).join("\n").replace(/^\s+/, "");
  return [before, section, after].filter((part) => part.length > 0).join("\n\n") + "\n";
}

export function parseReadmeDerivedMvpRequirements(readme: string): ReadmeDerivedMvpRequirements {
  return {
    mvpGoalFlow: extractCodeBlockLines(readme, "## MVP 목표"),
    operatingPrinciples: extractBulletLines(readme, "## 운영 원칙"),
    executionCommands: extractCodeBlockLines(readme, "## 실행"),
    publicApiSymbols: extractPublicApiSymbols(readme),
  };
}

export function extractReadmeMvpRequirementList(readme: string): ReadmeMvpRequirementEntry[] {
  return flattenReadmeRequirements(parseReadmeDerivedMvpRequirements(readme));
}

export function parseReadmeMvpRequirements(readme: string): ReadmeMvpRequirementExtraction {
  const derivedRequirements = parseReadmeDerivedMvpRequirements(readme);
  const requirements = flattenReadmeRequirements(derivedRequirements);

  return {
    schemaVersion: "readme-mvp-requirements.v1",
    source: {
      document: "README.md",
      sections: requirements.map((requirement) => requirement.sourceSection).filter(unique),
    },
    requirements,
    summary: {
      totalCount: requirements.length,
      countByCategory: countReadmeRequirementsByCategory(requirements),
    },
  };
}

export function validateReadmeMvpRequirementExtraction(extraction: unknown): ReadmeMvpRequirementValidationResult {
  const errors: string[] = [];
  const requirements = isRecord(extraction) && Array.isArray(extraction.requirements) ? extraction.requirements : [];
  const requirementRecords = requirements.filter(isRecord);
  const computed = {
    totalCount: requirements.length,
    countByCategory: countReadmeRequirementsByCategory(requirementRecords),
    sections: requirementRecords
      .map((requirement) => requirement.sourceSection)
      .filter((section): section is string => typeof section === "string" && section.trim().length > 0)
      .filter(unique),
  };

  if (!isRecord(extraction)) {
    return {
      valid: false,
      errors: ["extraction must be an object"],
      computed,
    };
  }

  if (extraction.schemaVersion !== "readme-mvp-requirements.v1") {
    errors.push("schemaVersion must be readme-mvp-requirements.v1");
  }
  if (!isRecord(extraction.source)) {
    errors.push("source must be an object");
  } else if (extraction.source.document !== "README.md") {
    errors.push("source.document must be README.md");
  }
  const sourceSections = isRecord(extraction.source) && Array.isArray(extraction.source.sections) ? extraction.source.sections : [];
  if (!sameStringList(sourceSections.filter(isString), computed.sections)) {
    errors.push("source.sections must match sections derived from requirements");
  }
  if (!Array.isArray(extraction.requirements)) {
    errors.push("requirements must be an array");
  }
  if (!isRecord(extraction.summary)) {
    errors.push("summary must be an object");
  } else if (extraction.summary.totalCount !== computed.totalCount) {
    errors.push("summary.totalCount must match requirement count");
  }

  for (const category of readmeMvpRequirementCategories) {
    const countByCategory = isRecord(extraction.summary) && isRecord(extraction.summary.countByCategory)
      ? extraction.summary.countByCategory
      : {};
    if (countByCategory[category] !== computed.countByCategory[category]) {
      errors.push(`summary.countByCategory.${category} must match derived count`);
    }
  }

  requirements.forEach((requirement, index) => {
    const path = `requirements[${index}]`;
    if (!isRecord(requirement)) {
      errors.push(`${path} must be an object`);
      return;
    }
    const id = typeof requirement.id === "string" && requirement.id.trim().length > 0 ? requirement.id : path;
    if (typeof requirement.id !== "string" || requirement.id.trim().length === 0) {
      errors.push(`${path}.id must be a non-empty string`);
    }
    if (!isReadmeMvpRequirementCategory(requirement.category)) {
      errors.push(`${id}.category must be one of ${readmeMvpRequirementCategories.join(", ")}`);
    }
    if (typeof requirement.sourceSection !== "string" || requirement.sourceSection.trim().length === 0) {
      errors.push(`${id}.sourceSection must be a non-empty string`);
    }
    if (typeof requirement.order !== "number" || !Number.isInteger(requirement.order) || requirement.order < 1) {
      errors.push(`${id}.order must be a positive integer`);
    }
    if (!isReadmeMvpRequirementCategory(requirement.category) && (typeof requirement.text !== "string" || requirement.text.trim().length === 0)) {
      errors.push(`${id} must include non-empty text`);
    }
  });

  for (const category of readmeMvpRequirementCategories) {
    const categoryRequirements = requirementRecords.filter((requirement) => requirement.category === category);
    categoryRequirements.forEach((requirement, index) => {
      const expectedOrder = index + 1;
      const expectedId = `${category}:${expectedOrder.toString().padStart(3, "0")}`;
      if (requirement.id !== expectedId) {
        errors.push(`${requirement.id} must use stable id ${expectedId}`);
      }
      if (requirement.order !== expectedOrder) {
        errors.push(`${requirement.id} must use stable order ${expectedOrder}`);
      }
      if (requirement.sourceSection !== readmeMvpRequirementSourceSections[category]) {
        errors.push(`${requirement.id} must use source section ${readmeMvpRequirementSourceSections[category]}`);
      }
      if (requirement.text.trim().length === 0) {
        errors.push(`${requirement.id} must include non-empty text`);
      }
    });
  }

  return {
    valid: errors.length === 0,
    errors,
    computed,
  };
}

export function resolvePriorReviewArtifactIdentifier(input: { identifier: string; projectRoot?: string }): string {
  const identifier = input.identifier.trim();
  if (identifier.length === 0) {
    throw new TypeError("priorReviewArtifact must be a non-empty string");
  }

  const projectRoot = input.projectRoot ?? process.cwd();
  const candidates = buildArtifactPathCandidates(identifier, projectRoot);
  const artifactPath = candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile());
  if (!artifactPath) {
    throw new TypeError(`priorReviewArtifact could not be resolved: ${identifier}`);
  }
  return artifactPath;
}

export function gateRedesignDecision(input: {
  recommendation: ReviewEvidenceArtifact["summary"]["recommendation"];
  priorReviewEvidence?: unknown;
  evidenceArtifactCreated: boolean;
  completedReviewArtifacts?: CompletedReviewArtifactGateInput;
}): RedesignDecisionGateResult {
  if (!isRedesignRecommendation(input.recommendation)) {
    return {
      accepted: true,
      reasons: [],
      reviewRequired: false,
      reviewReasons: [],
      missingFields: [],
      insufficientContent: [],
    };
  }

  const reasons: string[] = [];
  if (!input.evidenceArtifactCreated) {
    reasons.push("review_evidence_artifact_not_created");
  }
  if (input.completedReviewArtifacts && input.completedReviewArtifacts.status !== "passed") {
    reasons.push("completed_review_artifacts_incomplete");
  }
  if (input.priorReviewEvidence === undefined) {
    reasons.push("prior_review_evidence_missing");
  }

  const completeness = checkPriorReviewEvidenceCompletenessForRedesignDecision(input.priorReviewEvidence);
  if (!completeness.complete) {
    reasons.push("prior_review_evidence_incomplete");
  }
  const incompleteButPresent = input.priorReviewEvidence !== undefined && !completeness.complete;
  const reviewReasons = incompleteButPresent ? ["prior_review_evidence_incomplete_but_present"] : [];

  return {
    accepted: reasons.length === 0,
    reasons,
    reviewRequired: reviewReasons.length > 0,
    reviewReasons,
    missingFields: completeness.missingFields,
    insufficientContent: completeness.insufficientContent,
  };
}

export function validatePriorReviewEvidenceForRedesignDecision(artifact: unknown): PriorReviewEvidenceValidationResult {
  const missingFields: string[] = [];

  if (!isRecord(artifact)) {
    return {
      valid: false,
      missingFields: ["schemaVersion", "inventory", "findings", "summary"],
    };
  }

  requireNonEmptyString(artifact, "schemaVersion", missingFields);
  requireArray(artifact, "inventory", missingFields);
  requireArray(artifact, "findings", missingFields);
  requireRecord(artifact, "summary", missingFields);

  if (Array.isArray(artifact.inventory)) {
    artifact.inventory.forEach((entry, index) => {
      const path = `inventory[${index}]`;
      if (!isRecord(entry)) {
        missingFields.push(path);
        return;
      }
      requireNonEmptyStringField(entry, "id", `${path}.id`, missingFields);
      requireNonEmptyStringField(entry, "relativePath", `${path}.relativePath`, missingFields);
      requireNonEmptyStringField(entry, "kind", `${path}.kind`, missingFields);
      requireNonEmptyStringField(entry, "moduleName", `${path}.moduleName`, missingFields);
    });
  }

  if (Array.isArray(artifact.findings)) {
    if (artifact.findings.length === 0) {
      missingFields.push("findings[0]");
    }
    artifact.findings.forEach((finding, index) => {
      const path = `findings[${index}]`;
      if (!isRecord(finding)) {
        missingFields.push(path);
        return;
      }
      requireNonEmptyStringField(finding, "id", `${path}.id`, missingFields);
      requireNonEmptyStringField(finding, "sourceId", `${path}.sourceId`, missingFields);
      requireNonEmptyStringField(finding, "relativePath", `${path}.relativePath`, missingFields);
      requireNonEmptyStringField(finding, "moduleName", `${path}.moduleName`, missingFields);
      requireNonEmptyStringField(finding, "severity", `${path}.severity`, missingFields);
      requireNonEmptyStringField(finding, "category", `${path}.category`, missingFields);
      requireNonEmptyStringField(finding, "title", `${path}.title`, missingFields);
      requireNonEmptyStringField(finding, "evidence", `${path}.evidence`, missingFields);
      requireNonEmptyStringField(finding, "recommendation", `${path}.recommendation`, missingFields);
    });
  }

  if (isRecord(artifact.summary)) {
    requireNumber(artifact, "summary.inspectedModules", missingFields);
    requireNumber(artifact, "summary.findingCount", missingFields);
    requireRecord(artifact, "summary.findingsBySeverity", missingFields);
    requireRecord(artifact, "summary.findingsByCategory", missingFields);
    requireNonEmptyString(artifact, "summary.recommendation", missingFields);
  }

  return {
    valid: missingFields.length === 0,
    missingFields,
  };
}

export function checkPriorReviewEvidenceCompletenessForRedesignDecision(
  artifact: unknown,
): PriorReviewEvidenceCompletenessResult {
  const validation = validatePriorReviewEvidenceForRedesignDecision(artifact);
  const insufficientContent: string[] = [];

  if (!isRecord(artifact)) {
    return {
      complete: false,
      missingFields: validation.missingFields,
      insufficientContent,
    };
  }

  if (Array.isArray(artifact.inventory) && artifact.inventory.length === 0) {
    insufficientContent.push("inventory[0]");
  }

  if (Array.isArray(artifact.findings)) {
    artifact.findings.forEach((finding, index) => {
      if (!isRecord(finding)) {
        return;
      }

      requireMeaningfulEvidenceContent(finding.title, `findings[${index}].title`, insufficientContent);
      requireMeaningfulEvidenceContent(finding.evidence, `findings[${index}].evidence`, insufficientContent);
      requireActionableRecommendation(finding.recommendation, `findings[${index}].recommendation`, insufficientContent);

      if (typeof finding.category === "string" && !reviewFindingCategories.includes(finding.category as ReviewFindingCategory)) {
        insufficientContent.push(`findings[${index}].category`);
      }
      if (typeof finding.severity === "string" && !reviewFindingSeverities.includes(finding.severity as ReviewFindingSeverity)) {
        insufficientContent.push(`findings[${index}].severity`);
      }
    });
  }

  if (isRecord(artifact.summary)) {
    const recommendation = artifact.summary.recommendation;
    if (typeof recommendation === "string" && !["keep", "partial_redesign", "full_replan"].includes(recommendation)) {
      insufficientContent.push("summary.recommendation");
    }
  }

  return {
    complete: validation.valid && insufficientContent.length === 0,
    missingFields: validation.missingFields,
    insufficientContent,
  };
}

function buildArtifactPathCandidates(identifier: string, projectRoot: string): string[] {
  const directCandidates = isAbsolute(identifier) ? [identifier] : [resolve(projectRoot, identifier)];
  const extensionCandidates = extname(identifier) === "" ? directCandidates.map((candidate) => `${candidate}.json`) : [];
  const docsCandidates = isAbsolute(identifier)
    ? []
    : [resolve(projectRoot, "docs", identifier), ...(extname(identifier) === "" ? [resolve(projectRoot, "docs", `${identifier}.json`)] : [])];

  return [...directCandidates, ...extensionCandidates, ...docsCandidates].filter(unique);
}

function readJsonArtifact(artifactPath: string): unknown {
  try {
    return JSON.parse(readFileSync(artifactPath, "utf8"));
  } catch (error) {
    const reason = error instanceof Error ? error.message : "unknown JSON parse failure";
    throw new TypeError(`priorReviewArtifact is not valid JSON: ${reason}`);
  }
}

function readFileIfPresent(path: string): string {
  try {
    return readFileSync(path, "utf8");
  } catch (error) {
    return "";
  }
}

function parseDiagnosisDecision(report: string): ReviewEvidenceArtifact["summary"]["recommendation"] | undefined {
  if (/\bpartial redesign\b/i.test(report) || /\bpartial_redesign\b/i.test(report)) {
    return "partial_redesign";
  }
  if (/\bfull replan\b/i.test(report) || /\bfull_replan\b/i.test(report)) {
    return "full_replan";
  }
  if (/\bkeep\b/i.test(report)) {
    return "keep";
  }
  return undefined;
}

function parseDecisionEvidenceArtifact(report: string): string | undefined {
  const match = report.match(/Decision evidence artifact:\s*`([^`]+)`/i);
  return match?.[1];
}

function toReadmeRequirementEntries(input: {
  values: string[];
  category: ReadmeMvpRequirementCategory;
  sourceSection: string;
}): ReadmeMvpRequirementEntry[] {
  return input.values.map((text, index) => {
    const order = index + 1;
    return {
      id: `${input.category}:${order.toString().padStart(3, "0")}`,
      category: input.category,
      sourceSection: input.sourceSection,
      order,
      text,
    };
  });
}

function flattenReadmeRequirements(requirements: ReadmeDerivedMvpRequirements): ReadmeMvpRequirementEntry[] {
  return [
    ...toReadmeRequirementEntries({
      values: requirements.mvpGoalFlow,
      category: "mvp_goal_flow",
      sourceSection: readmeMvpRequirementSourceSections.mvp_goal_flow,
    }),
    ...toReadmeRequirementEntries({
      values: requirements.operatingPrinciples,
      category: "operating_principle",
      sourceSection: readmeMvpRequirementSourceSections.operating_principle,
    }),
    ...toReadmeRequirementEntries({
      values: requirements.executionCommands,
      category: "execution_command",
      sourceSection: readmeMvpRequirementSourceSections.execution_command,
    }),
    ...toReadmeRequirementEntries({
      values: requirements.publicApiSymbols,
      category: "public_api_symbol",
      sourceSection: readmeMvpRequirementSourceSections.public_api_symbol,
    }),
  ];
}

function countReadmeRequirementsByCategory(requirements: Array<{ category?: unknown }>): Record<ReadmeMvpRequirementCategory, number> {
  const counts: Record<ReadmeMvpRequirementCategory, number> = {
    mvp_goal_flow: 0,
    operating_principle: 0,
    execution_command: 0,
    public_api_symbol: 0,
  };

  for (const requirement of requirements) {
    if (isReadmeMvpRequirementCategory(requirement.category)) {
      counts[requirement.category] += 1;
    }
  }

  return counts;
}

function buildReadmeRequirementImplementationMapping(
  requirement: ReadmeMvpRequirementEntry,
  ruleEvidence: Array<{
    rule: ReturnType<typeof buildMvpCapabilityRules>[number];
    evidenceSourceIds: string[];
  }>,
): ReadmeRequirementImplementationMapping {
  const matchedRuleEvidence = ruleEvidence.filter(({ rule }) => rule.matchesReadmeRequirement(requirement));
  const evidenceSourceIds = matchedRuleEvidence.flatMap(({ evidenceSourceIds }) => evidenceSourceIds).filter(unique).sort(compareStable);
  const capabilityIds = matchedRuleEvidence.map(({ rule }) => rule.id).sort(compareStable);

  return {
    id: requirement.id,
    category: requirement.category,
    sourceSection: requirement.sourceSection,
    order: requirement.order,
    text: requirement.text,
    status: classifyReadmeRequirementImplementation(matchedRuleEvidence),
    capabilityIds,
    evidenceSourceIds,
  };
}

function buildRequirementCapabilityMatchRecord(
  requirement: ReadmeMvpRequirementEntry,
  ruleEvidence: Array<{
    rule: ReturnType<typeof buildMvpCapabilityRules>[number];
    evidenceSourceIds: string[];
  }>,
): RequirementCapabilityMatchRecord {
  const matchedRuleEvidence = ruleEvidence.filter(({ rule }) => rule.matchesReadmeRequirement(requirement));
  const capabilityIds = matchedRuleEvidence.map(({ rule }) => rule.id).sort(compareStable);
  const evidenceSourceIds = matchedRuleEvidence.flatMap(({ evidenceSourceIds }) => evidenceSourceIds).filter(unique).sort(compareStable);

  return {
    id: `requirement-capability-match:${requirement.id}`,
    requirementId: requirement.id,
    requirementText: requirement.text,
    capabilityIds,
    status: classifyRequirementCapabilityMatch(matchedRuleEvidence),
    evidenceSourceIds,
  };
}

function classifyReadmeRequirementImplementation(
  matchedRuleEvidence: Array<{
    evidenceSourceIds: string[];
  }>,
): ReadmeRequirementImplementationStatus {
  if (matchedRuleEvidence.length === 0) return "unknown";
  const implementedRuleCount = matchedRuleEvidence.filter(({ evidenceSourceIds }) => evidenceSourceIds.length > 0).length;
  if (implementedRuleCount === matchedRuleEvidence.length) return "covered";
  if (implementedRuleCount === 0) return "missing";
  return "partial";
}

function classifyRequirementCapabilityMatch(
  matchedRuleEvidence: Array<{
    evidenceSourceIds: string[];
  }>,
): RequirementCapabilityMatchStatus {
  if (matchedRuleEvidence.length === 0) return "missing";
  const matchedCapabilityCount = matchedRuleEvidence.filter(({ evidenceSourceIds }) => evidenceSourceIds.length > 0).length;
  if (matchedCapabilityCount === matchedRuleEvidence.length) return "matched";
  if (matchedCapabilityCount === 0) return "missing";
  return "partial";
}

function countReadmeRequirementMappingStatuses(
  mappings: ReadmeRequirementImplementationMapping[],
): Record<ReadmeRequirementImplementationStatus, number> {
  const counts: Record<ReadmeRequirementImplementationStatus, number> = {
    covered: 0,
    partial: 0,
    missing: 0,
    unknown: 0,
  };

  for (const mapping of mappings) {
    counts[mapping.status] += 1;
  }

  return counts;
}

function countRequirementCapabilityMatchStatuses(
  matches: RequirementCapabilityMatchRecord[],
): Record<RequirementCapabilityMatchStatus, number> {
  const counts: Record<RequirementCapabilityMatchStatus, number> = {
    matched: 0,
    partial: 0,
    missing: 0,
  };

  for (const match of matches) {
    counts[match.status] += 1;
  }

  return counts;
}

function extractCodeBlockLines(markdown: string, heading: string): string[] {
  const section = extractMarkdownSection(markdown, heading);
  const match = section.match(/```[^\n]*\n([\s\S]*?)```/);
  if (!match) {
    return [];
  }
  return match[1]
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function extractBulletLines(markdown: string, heading: string): string[] {
  return extractMarkdownSection(markdown, heading)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("- "))
    .map((line) => line.slice(2).trim())
    .filter((line) => line.length > 0);
}

function extractPublicApiSymbols(markdown: string): string[] {
  const section = extractMarkdownSection(markdown, "## Public API");
  const symbols = [...section.matchAll(/\bimport\s*\{\s*([^}]+)\s*\}/g)]
    .flatMap((match) => match[1].split(","))
    .map((symbol) => symbol.trim())
    .filter((symbol) => symbol.length > 0);
  return [...new Set(symbols)].sort(compareStable);
}

function extractMarkdownSection(markdown: string, heading: string): string {
  const start = markdown.indexOf(heading);
  if (start === -1) {
    return "";
  }
  const nextHeading = markdown.slice(start + heading.length).search(/\n##\s+/);
  if (nextHeading === -1) {
    return markdown.slice(start);
  }
  return markdown.slice(start, start + heading.length + nextHeading);
}

function readSummaryRecommendation(artifact: unknown): ReviewEvidenceArtifact["summary"]["recommendation"] {
  const recommendation = readStringAtPath(artifact, "summary.recommendation");
  if (recommendation === "keep" || recommendation === "partial_redesign" || recommendation === "full_replan") {
    return recommendation;
  }
  return "partial_redesign";
}

function readStringAtPath(artifact: unknown, path: string): string | undefined {
  const value = isRecord(artifact) ? valueAtPath(artifact, path) : undefined;
  return typeof value === "string" ? value : undefined;
}

function readNumberAtPath(artifact: unknown, path: string): number | undefined {
  const value = isRecord(artifact) ? valueAtPath(artifact, path) : undefined;
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isReadmeMvpRequirementCategory(value: unknown): value is ReadmeMvpRequirementCategory {
  return typeof value === "string" && readmeMvpRequirementCategories.includes(value as ReadmeMvpRequirementCategory);
}

function isReadmeRequirementImplementationStatus(value: unknown): value is ReadmeRequirementImplementationStatus {
  return typeof value === "string" &&
    readmeRequirementImplementationStatuses.includes(value as ReadmeRequirementImplementationStatus);
}

function isReadmeRequirementImplementationMapping(
  value: Record<string, unknown>,
): value is ReadmeRequirementImplementationMapping {
  return (
    typeof value.id === "string" &&
    value.id.trim().length > 0 &&
    isReadmeMvpRequirementCategory(value.category) &&
    typeof value.sourceSection === "string" &&
    value.sourceSection.trim().length > 0 &&
    typeof value.order === "number" &&
    Number.isInteger(value.order) &&
    value.order > 0 &&
    typeof value.text === "string" &&
    value.text.trim().length > 0 &&
    isReadmeRequirementImplementationStatus(value.status) &&
    Array.isArray(value.capabilityIds) &&
    value.capabilityIds.every(isString) &&
    Array.isArray(value.evidenceSourceIds) &&
    value.evidenceSourceIds.every(isString)
  );
}

function escapeMarkdownTableText(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\s+/g, " ").trim();
}

function unique(value: string, index: number, values: string[]): boolean {
  return values.indexOf(value) === index;
}

function sameStringList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function collectFiles(path: string, projectRoot: string): string[] {
  let stats;
  try {
    stats = statSync(path);
  } catch (error) {
    return [];
  }

  if (stats.isFile()) {
    return [toProjectRelative(projectRoot, path)];
  }

  if (!stats.isDirectory() || ignoredDirectories.has(basename(path))) {
    return [];
  }

  return readdirSync(path)
    .flatMap((entry) => collectFiles(join(path, entry), projectRoot))
    .sort(compareStable);
}

function toProjectRelative(projectRoot: string, path: string): string {
  return relative(projectRoot, path).split(sep).join("/");
}

function normalizeEvidenceRelativePath(projectRoot: string, path: string): string {
  const trimmedPath = path.trim();
  if (trimmedPath.length === 0) {
    throw new TypeError("review evidence path must be a non-empty string");
  }

  const absolutePath = isAbsolute(trimmedPath) ? resolve(trimmedPath) : resolve(projectRoot, trimmedPath);
  const relativePath = relative(projectRoot, absolutePath);
  if (relativePath === "" || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    throw new TypeError(`review evidence path must be inside project root: ${path}`);
  }

  return relativePath.split(sep).join("/");
}

function readPackageMetadata(projectRoot: string): Record<string, unknown> | undefined {
  const packageJsonPath = resolve(projectRoot, "package.json");
  if (!existsSync(packageJsonPath)) {
    return undefined;
  }

  const metadata = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  if (!isRecord(metadata)) {
    return undefined;
  }
  return metadata;
}

function discoverPackageEntryPoints(projectRoot: string, packageMetadata: Record<string, unknown>): RunnableEntryPoint[] {
  const entryPoints: RunnableEntryPoint[] = [];
  const main = packageMetadata.main;
  if (typeof main === "string" && main.trim() !== "") {
    entryPoints.push(packagePathEntryPoint(projectRoot, "package_main", "main", main, "package.json main"));
  }

  const exportsValue = packageMetadata.exports;
  const exportedPaths = collectPackageExportPaths(exportsValue);
  for (const exportedPath of exportedPaths) {
    entryPoints.push(packagePathEntryPoint(projectRoot, "package_exports", exportedPath.name, exportedPath.path, "package.json exports"));
  }

  const bin = packageMetadata.bin;
  if (typeof bin === "string" && bin.trim() !== "") {
    entryPoints.push(packagePathEntryPoint(projectRoot, "package_bin", "bin", bin, "package.json bin"));
  } else if (isRecord(bin)) {
    for (const [name, value] of Object.entries(bin).sort(([left], [right]) => compareStable(left, right))) {
      if (typeof value === "string" && value.trim() !== "") {
        entryPoints.push(packagePathEntryPoint(projectRoot, "package_bin", name, value, "package.json bin"));
      }
    }
  }

  if (isRecord(packageMetadata.scripts)) {
    for (const [name, value] of Object.entries(packageMetadata.scripts).sort(([left], [right]) => compareStable(left, right))) {
      if (typeof value === "string" && value.trim() !== "" && isRunnablePackageScript(name, value)) {
        entryPoints.push({
          id: `package_script:${name}`,
          name,
          source: "package_script",
          command: `npm run ${name}`,
          evidence: value,
        });
      }
    }
  }

  return entryPoints;
}

function packagePathEntryPoint(
  projectRoot: string,
  source: Extract<RunnableEntryPointSource, "package_exports" | "package_main" | "package_bin">,
  name: string,
  packagePath: string,
  evidence: string,
): RunnableEntryPoint {
  const normalizedPath = normalizePackageRelativePath(packagePath);
  return {
    id: `${source}:${name}:${normalizedPath}`,
    name,
    source,
    relativePath: normalizedPath,
    command: buildNodeCommandForPath(projectRoot, normalizedPath),
    evidence,
  };
}

function collectPackageExportPaths(value: unknown, name = "."): Array<{ name: string; path: string }> {
  if (typeof value === "string" && value.trim() !== "") {
    return [{ name, path: value }];
  }
  if (!isRecord(value)) {
    return [];
  }

  return Object.entries(value)
    .sort(([left], [right]) => compareStable(left, right))
    .flatMap(([key, nested]) => {
      const nestedName = name === "." ? key : `${name}.${key}`;
      return collectPackageExportPaths(nested, nestedName);
    });
}

function isRunnablePackageScript(name: string, command: string): boolean {
  return (
    /^(start|dev|serve|dry-run|health-check|check(?::|$)|generate(?::|$)|review-evidence|typecheck|test)$/i.test(name) ||
    /\b(node|tsx|ts-node)\b/.test(command)
  );
}

function discoverCommonMainFileEntryPoints(projectRoot: string): RunnableEntryPoint[] {
  const commonMainFiles = ["src/index.ts", "src/index.js", "src/main.ts", "src/main.js", "index.ts", "index.js", "main.ts", "main.js"];
  return commonMainFiles
    .filter((relativePath) => existsSync(resolve(projectRoot, relativePath)))
    .map((relativePath) => ({
      id: `common_main_file:${relativePath}`,
      name: basename(relativePath).replace(/\.[^.]+$/, ""),
      source: "common_main_file" as const,
      relativePath,
      command: buildNodeCommandForPath(projectRoot, relativePath),
      evidence: "common application main file name",
    }));
}

function discoverScriptDirectoryEntryPoints(projectRoot: string): RunnableEntryPoint[] {
  return discoverSourceFiles(projectRoot)
    .filter((entry) => entry.kind === "script")
    .map((entry) => ({
      id: `scripts_directory:${entry.relativePath}`,
      name: entry.moduleName.replace(/^scripts\./, ""),
      source: "scripts_directory" as const,
      relativePath: entry.relativePath,
      command: buildNodeCommandForPath(projectRoot, entry.relativePath),
      evidence: "script file under scripts/",
    }));
}

function discoverGuardedMainBlockEntryPoints(projectRoot: string): RunnableEntryPoint[] {
  return discoverSourceFiles(projectRoot)
    .filter((entry) => {
      const content = readFileSync(resolve(projectRoot, entry.relativePath), "utf8");
      return hasGuardedMainBlock(content);
    })
    .map((entry) => ({
      id: `guarded_main_block:${entry.relativePath}`,
      name: entry.moduleName,
      source: "guarded_main_block" as const,
      relativePath: entry.relativePath,
      command: buildNodeCommandForPath(projectRoot, entry.relativePath),
      evidence: "guarded main block compares invoked path with module URL or script path",
    }));
}

function hasGuardedMainBlock(content: string): boolean {
  return (
    /\bprocess\.argv\s*\[\s*1\s*\]/.test(content) &&
    (/fileURLToPath\s*\(\s*import\.meta\.url\s*\)/.test(content) || /\.endsWith\s*\(\s*["'][^"']+\.(?:ts|js)["']\s*\)/.test(content))
  );
}

function normalizePackageRelativePath(packagePath: string): string {
  return packagePath.trim().replace(/^\.\//, "");
}

function buildNodeCommandForPath(projectRoot: string, relativePath: string): string | undefined {
  if (!existsSync(resolve(projectRoot, relativePath))) {
    return undefined;
  }
  return `node ${relativePath}`;
}

function classifyPath(relativePath: string): InspectionInventoryEntry["kind"] {
  if (relativePath.startsWith("src/")) {
    return "source";
  }
  if (relativePath.startsWith("tests/")) {
    return "test";
  }
  if (relativePath.startsWith("scripts/")) {
    return "script";
  }
  if (relativePath.startsWith("docs/") || relativePath === "README.md") {
    return "doc";
  }
  return "config";
}

function isClassifiedSourceFileEntry(entry: InspectionInventoryEntry): entry is ClassifiedSourceFileEntry {
  return (
    (entry.kind === "source" || entry.kind === "test" || entry.kind === "script") &&
    sourceFileExtensions.has(extname(entry.relativePath))
  );
}

function buildTestFileInventoryEntry(projectRoot: string, relativePath: string): TestFileInventoryEntry {
  const content = readFileSync(resolve(projectRoot, relativePath), "utf8");
  return {
    id: `test:${relativePath}`,
    relativePath,
    kind: "test_file",
    extension: extname(relativePath),
    metadata: {
      directory: dirname(relativePath) === "." ? "" : dirname(relativePath).split(sep).join("/"),
      fileName: basename(relativePath),
      framework: detectTestFramework(content),
      containsTestDeclaration: /\btest\s*\(/.test(content),
    },
  };
}

function buildConfigFileInventoryEntry(projectRoot: string, relativePath: string): ConfigFileInventoryEntry {
  const content = readFileSync(resolve(projectRoot, relativePath), "utf8");
  const packageMetadata = relativePath === "package.json" ? parseJsonRecord(content) : undefined;
  const scripts = isRecord(packageMetadata?.scripts) ? packageMetadata.scripts : undefined;
  const packageScriptNames = scripts ? Object.keys(scripts).sort(compareStable) : undefined;

  return {
    id: `config:${relativePath}`,
    relativePath,
    kind: "config_file",
    extension: extname(relativePath),
    metadata: {
      directory: dirname(relativePath) === "." ? "" : dirname(relativePath).split(sep).join("/"),
      fileName: basename(relativePath),
      configType: classifyConfigType(relativePath),
      packageScriptNames,
      hasTypecheckScript: scripts ? typeof scripts.typecheck === "string" : undefined,
      hasTestScript: scripts ? typeof scripts.test === "string" : undefined,
    },
  };
}

function detectTestFramework(content: string): TestFileInventoryEntry["metadata"]["framework"] {
  return /(?:from\s+["']node:test["']|import\s+["']node:test["']|require\s*\(\s*["']node:test["']\s*\))/.test(content)
    ? "node:test"
    : "unknown";
}

function isTestFilePath(relativePath: string): boolean {
  return (
    relativePath.startsWith("tests/") &&
    testFileExtensions.has(extname(relativePath)) &&
    /(?:^|[./-])test\.(?:ts|js|mjs|cjs)$/.test(relativePath)
  );
}

function isProjectConfigFilePath(relativePath: string): boolean {
  const name = basename(relativePath);
  return (
    !relativePath.includes("/generated/") &&
    (configFileNames.has(name) || /^tsconfig\..+\.json$/.test(name) || /^(?:vite|vitest|eslint|prettier)\.config\./.test(name))
  );
}

function classifyConfigType(relativePath: string): string {
  const name = basename(relativePath);
  if (name === "package.json") return "package_manifest";
  if (/^tsconfig(?:\.|$)/.test(name)) return "typescript";
  if (/^(?:eslint|\.eslintrc)/.test(name)) return "eslint";
  if (/^(?:prettier|\.prettierrc)/.test(name)) return "prettier";
  if (/^vite\.config\./.test(name)) return "vite";
  if (/^vitest\.config\./.test(name)) return "vitest";
  if (name === "pyproject.toml") return "python_project";
  if (name === "ruff.toml") return "ruff";
  if (name === "pytest.ini") return "pytest";
  if (name === "jsconfig.json") return "javascript";
  return "project_config";
}

function parseJsonRecord(content: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(content);
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function buildModuleName(relativePath: string): string {
  return relativePath.replace(/\.[^.]+$/, "").replaceAll("/", ".");
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

function compareRunnableEntryPoints(left: RunnableEntryPoint, right: RunnableEntryPoint): number {
  const sourcePriority =
    runnableEntryPointSourcePriority(left.source) - runnableEntryPointSourcePriority(right.source);
  if (sourcePriority !== 0) return sourcePriority;

  const leftRootPriority = runnablePathRootPriority(left.relativePath);
  const rightRootPriority = runnablePathRootPriority(right.relativePath);
  if (leftRootPriority !== rightRootPriority) return leftRootPriority - rightRootPriority;

  const leftName = left.source === "package_exports" && left.name === "." ? "" : left.name;
  const rightName = right.source === "package_exports" && right.name === "." ? "" : right.name;
  const nameOrder = compareStable(leftName, rightName);
  if (nameOrder !== 0) return nameOrder;

  return compareStable(left.id, right.id);
}

function runnableEntryPointSourcePriority(source: RunnableEntryPointSource): number {
  return {
    common_main_file: 0,
    guarded_main_block: 1,
    package_bin: 2,
    package_exports: 3,
    package_main: 4,
    package_script: 5,
    scripts_directory: 6,
  }[source];
}

function runnablePathRootPriority(relativePath: string | undefined): number {
  if (relativePath?.startsWith("src/")) return 0;
  if (relativePath?.startsWith("scripts/")) return 1;
  return 2;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireNonEmptyString(record: Record<string, unknown>, path: string, missingFields: string[]): void {
  const value = valueAtPath(record, path);
  if (typeof value !== "string" || value.trim() === "") {
    missingFields.push(path);
  }
}

function requireNonEmptyStringField(record: Record<string, unknown>, key: string, reportedPath: string, missingFields: string[]): void {
  const value = record[key];
  if (typeof value !== "string" || value.trim() === "") {
    missingFields.push(reportedPath);
  }
}

function requireNumber(record: Record<string, unknown>, path: string, missingFields: string[]): void {
  const value = valueAtPath(record, path);
  if (typeof value !== "number" || !Number.isFinite(value)) {
    missingFields.push(path);
  }
}

function requireArray(record: Record<string, unknown>, path: string, missingFields: string[]): void {
  if (!Array.isArray(valueAtPath(record, path))) {
    missingFields.push(path);
  }
}

function requireRecord(record: Record<string, unknown>, path: string, missingFields: string[]): void {
  if (!isRecord(valueAtPath(record, path))) {
    missingFields.push(path);
  }
}

function valueAtPath(record: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => (isRecord(current) ? current[key] : undefined), record);
}

function requireMeaningfulEvidenceContent(value: unknown, path: string, insufficientContent: string[]): void {
  if (!isMeaningfulText(value, { minChars: 24, minWords: 4 })) {
    insufficientContent.push(path);
  }
}

function requireActionableRecommendation(value: unknown, path: string, insufficientContent: string[]): void {
  if (!isMeaningfulText(value, { minChars: 24, minWords: 4 })) {
    insufficientContent.push(path);
  }
}

function isMeaningfulText(value: unknown, minimum: { minChars: number; minWords: number }): boolean {
  if (typeof value !== "string") {
    return false;
  }

  const normalized = value.trim();
  if (normalized.length < minimum.minChars) {
    return false;
  }
  if (/\b(tbd|todo|n\/a|none|unknown|placeholder|fix later|insufficient evidence)\b/i.test(normalized)) {
    return false;
  }

  const wordCount = normalized.split(/\s+/).filter(Boolean).length;
  return wordCount >= minimum.minWords;
}

const reviewFindingSeverities: ReviewFindingSeverity[] = ["critical", "high", "medium", "low"];
const reviewFindingCategories: ReviewFindingCategory[] = [
  "error_frequency",
  "maintainability",
  "token_cost",
  "architecture_fit",
  "feature_completeness",
];

function buildMvpCapabilityRules(): Array<{
  id: string;
  requirement: string;
  gapDescription: string;
  matchesReadmeRequirement: (entry: ReadmeMvpRequirementEntry) => boolean;
  matches: (entry: InspectionInventoryEntry) => boolean;
}> {
  return [
    {
      id: "request-analysis-work-breakdown",
      requirement: "Analyze user request and decompose it into task_breakdown items.",
      gapDescription:
        "Gap exists when the implementation cannot turn a user request into stable, inspectable task_breakdown items.",
      matchesReadmeRequirement: (entry) =>
        entry.category === "mvp_goal_flow" && /user request|task/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/planning.ts",
    },
    {
      id: "role-based-routing",
      requirement: "Route work items to OpenClaw owner/finalizer and Hermes reviewer personas.",
      gapDescription:
        "Gap exists when work items are not assigned to explicit job roles for OpenClaw execution and Hermes review.",
      matchesReadmeRequirement: (entry) =>
        /OpenClaw|Hermes|owner|reviewer|finalizer/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/planning.ts" || entry.relativePath === "src/types.ts",
    },
    {
      id: "openclaw-hermes-meeting-loop",
      requirement: "Preserve OpenClaw execution and Hermes review turns in a meeting loop.",
      gapDescription:
        "Gap exists when meeting turns are not durably preserved across OpenClaw draft and Hermes review iterations.",
      matchesReadmeRequirement: (entry) =>
        entry.category === "mvp_goal_flow" && /draft|review|thread timeline/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/orchestrator.ts" || entry.relativePath === "src/db.ts",
    },
    {
      id: "final-synthesis",
      requirement: "Produce final synthesis after reviewer convergence.",
      gapDescription:
        "Gap exists when converged review feedback cannot be converted into one final synthesized output artifact.",
      matchesReadmeRequirement: (entry) => /final synthesis/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/orchestrator.ts",
    },
    {
      id: "escalation-artifact",
      requirement: "Surface convergence failure or user-decision needs as escalation artifacts.",
      gapDescription:
        "Gap exists when ambiguity, failed convergence, or required user decisions do not produce a structured escalation artifact.",
      matchesReadmeRequirement: (entry) => /사용자 결정|escalation/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/orchestrator.ts" || entry.relativePath === "src/policies.ts",
    },
    {
      id: "raw-storage-summary-context",
      requirement: "Separate raw full-text storage from exposed loop summaries and compressed context.",
      gapDescription:
        "Gap exists when raw full text is exposed directly to loop context instead of bounded summaries and compressed context.",
      matchesReadmeRequirement: (entry) => /요약 timeline|전문|SQLite|thread timeline/i.test(entry.text),
      matches: (entry) => entry.relativePath === "src/db.ts" || entry.relativePath === "src/planning.ts" || entry.relativePath === "src/policies.ts",
    },
  ];
}

function countBy<T extends string>(keys: T[], findings: ReviewFinding[], selectKey: (finding: ReviewFinding) => T): Record<T, number> {
  const counts = Object.fromEntries(keys.map((key) => [key, 0])) as Record<T, number>;
  for (const finding of findings) {
    counts[selectKey(finding)] += 1;
  }
  return counts;
}

function isRedesignRecommendation(recommendation: ReviewEvidenceArtifact["summary"]["recommendation"]): recommendation is RedesignRecommendation {
  return recommendation === "partial_redesign" || recommendation === "full_replan";
}

function buildRedesignDecisionGateError(gate: RedesignDecisionGateResult): string {
  if (gate.reasons.includes("review_evidence_artifact_not_created")) {
    return "Review evidence artifact must be created before emitting a redesign recommendation.";
  }

  const missing = gate.missingFields.length > 0 ? `missing fields: ${gate.missingFields.join(", ")}` : "";
  const insufficient = gate.insufficientContent.length > 0 ? `insufficient content: ${gate.insufficientContent.join(", ")}` : "";
  const details = [missing, insufficient].filter(Boolean).join("; ");
  return `Review evidence artifact is incomplete for a redesign decision (${details}).`;
}

function buildFindingsForModule(input: InspectedModuleInput): ReviewFinding[] {
  const content = input.content ?? "";
  const findings: ReviewFinding[] = [];

  if (input.kind === "source" && !hasNearbyTest(input, content)) {
    findings.push(
      buildFinding(input, {
        suffix: "missing-test",
        severity: "high",
        category: "error_frequency",
        title: "Source module has no observable test coverage",
        evidence: "No test reference was detected for this source module.",
        recommendation: "Add a focused runnable test before using this module as part of the MVP diagnosis or meeting loop.",
      }),
    );
  }

  if (/\bTODO\b|\bFIXME\b/i.test(content)) {
    const markerLine = sanitizeEvidenceSnippet(firstMatchingLine(content, /\bTODO\b|\bFIXME\b/i));
    findings.push(
      buildFinding(input, {
        suffix: "open-marker",
        severity: "medium",
        category: "maintainability",
        title: "Open implementation marker remains in inspected code",
        evidence: `The inspected module still contains an open implementation marker: ${markerLine}`,
        recommendation: "Resolve or convert the marker into a tracked finding with owner, risk, and next action.",
      }),
    );
  }

  if (input.kind === "source" && /full(Content|Text)|raw(Content|Text)|content:\s*fullContent/.test(content)) {
    const rawContentLine = sanitizeEvidenceSnippet(
      firstMatchingLine(content, /full(Content|Text)|raw(Content|Text)|content:\s*fullContent/),
    );
    findings.push(
      buildFinding(input, {
        suffix: "raw-content-exposure",
        severity: "medium",
        category: "token_cost",
        title: "Raw content handling needs explicit summary boundary",
        evidence: `The inspected source handles raw or full content and requires an explicit summary boundary: ${rawContentLine}`,
        recommendation:
          "Keep full text in durable storage and expose only bounded summaries to loop prompts, logs, and thread messages.",
      }),
    );
  }

  if (input.kind === "doc" && /MVP|OpenClaw|Hermes/i.test(content) && !/escalation|사용자 결정|user decision/i.test(content)) {
    findings.push(
      buildFinding(input, {
        suffix: "missing-escalation-doc",
        severity: "low",
        category: "feature_completeness",
        title: "MVP documentation omits escalation behavior",
        evidence: "Document mentions MVP meeting roles but no escalation or user-decision behavior was detected.",
        recommendation: "Document how ambiguous or insufficiently converged requests produce an escalation artifact.",
      }),
    );
  }

  return findings;
}

function buildFinding(
  input: InspectedModuleInput,
  finding: Omit<ReviewFinding, "id" | "sourceId" | "relativePath" | "moduleName"> & { suffix: string },
): ReviewFinding {
  return {
    id: `finding:${input.id}:${finding.suffix}`,
    sourceId: input.id,
    relativePath: input.relativePath,
    moduleName: input.moduleName,
    severity: finding.severity,
    category: finding.category,
    title: finding.title,
    evidence: finding.evidence,
    recommendation: finding.recommendation,
  };
}

function hasNearbyTest(input: InspectedModuleInput, content: string): boolean {
  const moduleStem = input.relativePath.replace(/^src\//, "").replace(/\.[^.]+$/, "");
  return (
    input.relativePath.endsWith(".test.ts") ||
    content.includes(".test") ||
    content.includes("node:test") ||
    input.moduleName.includes("test") ||
    moduleStem.includes("test")
  );
}

function firstMatchingLine(content: string, pattern: RegExp): string {
  return content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => pattern.test(line)) ?? "Matching evidence was detected in the inspected module.";
}

function sanitizeEvidenceSnippet(value: string): string {
  return value
    .replace(/\btbd\b/gi, "to-be-defined")
    .replace(/\btodo\b/gi, "to-do")
    .replace(/\bn\/a\b/gi, "not-applicable")
    .replace(/\bnone\b/gi, "no-value")
    .replace(/\bunknown\b/gi, "un-known")
    .replace(/\bplaceholder\b/gi, "place-holder")
    .replace(/\bfix later\b/gi, "fix-in-follow-up")
    .replace(/\binsufficient evidence\b/gi, "thin evidence");
}

function compareFindings(left: ReviewFinding, right: ReviewFinding): number {
  return compareStable(left.id, right.id);
}
