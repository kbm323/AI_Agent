import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import {
  buildCapabilityInventoryArtifact,
  validateCapabilityInventoryArtifact,
  type CapabilityInventoryArtifact,
} from "./capability-inventory.ts";
import {
  discoverRunnableEntryPoints,
  discoverSourceFiles,
  discoverTestAndConfigFiles,
  type ClassifiedSourceFileEntry,
  type ConfigFileInventoryEntry,
  type RedesignRecommendation,
  type RunnableEntryPoint,
  type TestFileInventoryEntry,
} from "./inspection.ts";

export interface InventoryOrchestrationReport {
  schemaVersion: "inventory-orchestration.v1";
  sourceFiles: ClassifiedSourceFileEntry[];
  runnableEntryPoints: RunnableEntryPoint[];
  testFiles: TestFileInventoryEntry[];
  configFiles: ConfigFileInventoryEntry[];
  moduleFeatureSummary: ImplementedModuleFeatureSummary;
  capabilityInventory: CapabilityInventoryArtifact;
  summary: {
    sourceFileCount: number;
    runnableEntryPointCount: number;
    testFileCount: number;
    configFileCount: number;
    normalizedPathSeparator: "/";
  };
}

export interface ImplementedModuleFeatureEntry {
  id: string;
  relativePath: string;
  moduleName: string;
  kind: ClassifiedSourceFileEntry["kind"];
  exportedSymbols: string[];
  localDependencies: string[];
  coveredByTests: string[];
  runnableEntryPointIds: string[];
  featureTags: string[];
}

export interface ImplementedModuleFeatureSummary {
  schemaVersion: "implemented-module-features.v1";
  modules: ImplementedModuleFeatureEntry[];
  summary: {
    moduleCount: number;
    modulesWithExports: number;
    modulesWithTestCoverage: number;
    runnableModuleCount: number;
    featureTags: string[];
    normalizedPathSeparator: "/";
  };
}

export interface InventoryOrchestrationVerificationResult {
  generated: boolean;
  valid: boolean;
  reportPath?: string;
  schemaVersion?: string;
  missingFields: string[];
  inconsistentFields: string[];
  summary?: InventoryOrchestrationReport["summary"];
}

export interface RedesignPlanStepInventoryGateResult {
  accepted: boolean;
  reasons: string[];
  inventoryVerification: InventoryOrchestrationVerificationResult;
}

export interface GovernedRedesignPlanStep {
  id: string;
  title: string;
  action: string;
  recommendation: RedesignRecommendation;
  inventoryReportPath?: string;
}

export function buildInventoryOrchestrationReport(projectRoot: string): InventoryOrchestrationReport {
  const sourceFiles = discoverSourceFiles(projectRoot);
  const runnableEntryPoints = discoverRunnableEntryPoints(projectRoot);
  const testAndConfig = discoverTestAndConfigFiles(projectRoot);
  const moduleFeatureSummary = buildImplementedModuleFeatureSummary({
    projectRoot,
    sourceFiles,
    runnableEntryPoints,
    testFiles: testAndConfig.testFiles,
  });
  const capabilityInventory = buildCapabilityInventoryArtifact(moduleFeatureSummary);

  return {
    schemaVersion: "inventory-orchestration.v1",
    sourceFiles,
    runnableEntryPoints,
    testFiles: testAndConfig.testFiles,
    configFiles: testAndConfig.configFiles,
    moduleFeatureSummary,
    capabilityInventory,
    summary: {
      sourceFileCount: sourceFiles.length,
      runnableEntryPointCount: runnableEntryPoints.length,
      testFileCount: testAndConfig.testFiles.length,
      configFileCount: testAndConfig.configFiles.length,
      normalizedPathSeparator: "/",
    },
  };
}

export function buildImplementedModuleFeatureSummary(input: {
  projectRoot: string;
  sourceFiles: ClassifiedSourceFileEntry[];
  runnableEntryPoints: RunnableEntryPoint[];
  testFiles: TestFileInventoryEntry[];
}): ImplementedModuleFeatureSummary {
  const projectRoot = resolve(input.projectRoot);
  const sourcePathSet = new Set(input.sourceFiles.map((entry) => entry.relativePath));
  const modules = input.sourceFiles
    .map((entry) => {
      const content = readFileSync(resolve(projectRoot, entry.relativePath), "utf8");
      const coveredByTests = findTestCoverage(entry, input.testFiles);
      const runnableEntryPointIds = input.runnableEntryPoints
        .filter((entryPoint) => entryPoint.relativePath === entry.relativePath)
        .map((entryPoint) => entryPoint.id)
        .sort(compareStable);
      return {
        id: entry.id,
        relativePath: entry.relativePath,
        moduleName: entry.moduleName,
        kind: entry.kind,
        exportedSymbols: extractExportedSymbols(content),
        localDependencies: extractLocalDependencies({
          projectRoot,
          relativePath: entry.relativePath,
          content,
          sourcePathSet,
        }),
        coveredByTests,
        runnableEntryPointIds,
        featureTags: inferFeatureTags(entry.moduleName, content),
      };
    })
    .sort((left, right) => compareStable(left.relativePath, right.relativePath));
  const featureTags = modules.flatMap((entry) => entry.featureTags).filter(unique).sort(compareStable);

  return {
    schemaVersion: "implemented-module-features.v1",
    modules,
    summary: {
      moduleCount: modules.length,
      modulesWithExports: modules.filter((entry) => entry.exportedSymbols.length > 0).length,
      modulesWithTestCoverage: modules.filter((entry) => entry.coveredByTests.length > 0).length,
      runnableModuleCount: modules.filter((entry) => entry.runnableEntryPointIds.length > 0).length,
      featureTags,
      normalizedPathSeparator: "/",
    },
  };
}

export function writeInventoryOrchestrationReport(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): { report: InventoryOrchestrationReport; reportPath: string } {
  const projectRoot = resolve(input.projectRoot ?? process.cwd());
  const reportPath = resolve(projectRoot, input.outputPath ?? "docs/generated/inventory-orchestration-report.json");
  const report = buildInventoryOrchestrationReport(projectRoot);

  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return { report, reportPath };
}

export function verifyInventoryOrchestrationReportGenerated(input: {
  projectRoot?: string;
  reportPath?: string;
  report?: unknown;
} = {}): InventoryOrchestrationVerificationResult {
  const projectRoot = resolve(input.projectRoot ?? process.cwd());
  const reportPath = input.reportPath ? resolve(projectRoot, input.reportPath) : undefined;
  let report = input.report;

  if (report === undefined) {
    if (!reportPath || !existsSync(reportPath)) {
      return {
        generated: false,
        valid: false,
        reportPath,
        missingFields: ["inventory_orchestration_report"],
        inconsistentFields: [],
      };
    }
    report = JSON.parse(readFileSync(reportPath, "utf8"));
  }

  const validation = validateInventoryOrchestrationReport(report);
  validateInventoryCompletenessForCurrentRepository(projectRoot, report, validation.missingFields, validation.inconsistentFields);
  return {
    generated: true,
    valid: validation.missingFields.length === 0 && validation.inconsistentFields.length === 0,
    reportPath,
    schemaVersion: isRecord(report) && typeof report.schemaVersion === "string" ? report.schemaVersion : undefined,
    missingFields: validation.missingFields,
    inconsistentFields: validation.inconsistentFields,
    summary: isInventoryOrchestrationReport(report) ? report.summary : undefined,
  };
}

export function gateRedesignPlanStepWithInventory(input: {
  recommendation: RedesignRecommendation | "keep";
  inventoryVerification: InventoryOrchestrationVerificationResult;
}): RedesignPlanStepInventoryGateResult {
  if (input.recommendation === "keep") {
    return {
      accepted: true,
      reasons: [],
      inventoryVerification: input.inventoryVerification,
    };
  }

  const reasons: string[] = [];
  if (!input.inventoryVerification.generated) {
    reasons.push("inventory_orchestration_report_not_generated");
  }
  if (input.inventoryVerification.generated && !input.inventoryVerification.valid) {
    reasons.push("inventory_orchestration_report_invalid");
  }

  return {
    accepted: reasons.length === 0,
    reasons,
    inventoryVerification: input.inventoryVerification,
  };
}

export function buildGovernedRedesignPlanStep(input: {
  id: string;
  title: string;
  action: string;
  recommendation: RedesignRecommendation;
  inventoryVerification: InventoryOrchestrationVerificationResult;
}): GovernedRedesignPlanStep {
  const gate = gateRedesignPlanStepWithInventory({
    recommendation: input.recommendation,
    inventoryVerification: input.inventoryVerification,
  });
  if (!gate.accepted) {
    throw new Error(`Inventory orchestration report must be generated before redesign plan steps run: ${gate.reasons.join(", ")}`);
  }

  return {
    id: input.id,
    title: input.title,
    action: input.action,
    recommendation: input.recommendation,
    inventoryReportPath: input.inventoryVerification.reportPath,
  };
}

function validateInventoryOrchestrationReport(report: unknown): {
  missingFields: string[];
  inconsistentFields: string[];
} {
  const missingFields: string[] = [];
  const inconsistentFields: string[] = [];

  if (!isRecord(report)) {
    return {
      missingFields: ["schemaVersion", "sourceFiles", "runnableEntryPoints", "testFiles", "configFiles", "summary"],
      inconsistentFields,
    };
  }

  requireLiteral(report, "schemaVersion", "inventory-orchestration.v1", missingFields);
  requireArray(report, "sourceFiles", missingFields);
  requireArray(report, "runnableEntryPoints", missingFields);
  requireArray(report, "testFiles", missingFields);
  requireArray(report, "configFiles", missingFields);
  requireRecord(report, "moduleFeatureSummary", missingFields);
  requireRecord(report, "capabilityInventory", missingFields);
  requireRecord(report, "summary", missingFields);

  validateImplementedModuleFeatureSummary(report.moduleFeatureSummary, missingFields, inconsistentFields);
  validateCapabilityInventory(report.capabilityInventory, missingFields, inconsistentFields);

  if (isRecord(report.summary)) {
    requireCount(report, "sourceFiles", "summary.sourceFileCount", missingFields, inconsistentFields);
    requireCount(report, "runnableEntryPoints", "summary.runnableEntryPointCount", missingFields, inconsistentFields);
    requireCount(report, "testFiles", "summary.testFileCount", missingFields, inconsistentFields);
    requireCount(report, "configFiles", "summary.configFileCount", missingFields, inconsistentFields);
    if (report.summary.normalizedPathSeparator !== "/") {
      missingFields.push("summary.normalizedPathSeparator");
    }
  }

  return { missingFields, inconsistentFields };
}

function validateInventoryCompletenessForCurrentRepository(
  projectRoot: string,
  report: unknown,
  missingFields: string[],
  inconsistentFields: string[],
): void {
  if (!isInventoryOrchestrationReportShape(report)) {
    return;
  }

  const expectedSourceFiles = discoverSourceFiles(projectRoot).map((entry) => entry.relativePath).sort(compareStable);
  const expectedRunnableEntryPoints = discoverRunnableEntryPoints(projectRoot).map((entry) => entry.id).sort(compareStable);
  const expectedTestAndConfig = discoverTestAndConfigFiles(projectRoot);
  const expectedTestFiles = expectedTestAndConfig.testFiles.map((entry) => entry.relativePath).sort(compareStable);
  const expectedConfigFiles = expectedTestAndConfig.configFiles.map((entry) => entry.relativePath).sort(compareStable);

  const actualSourceFiles = report.sourceFiles.map((entry) => entry.relativePath).sort(compareStable);
  const actualRunnableEntryPoints = report.runnableEntryPoints.map((entry) => entry.id).sort(compareStable);
  const actualTestFiles = report.testFiles.map((entry) => entry.relativePath).sort(compareStable);
  const actualConfigFiles = report.configFiles.map((entry) => entry.relativePath).sort(compareStable);

  requireSameSet(actualSourceFiles, expectedSourceFiles, "currentRepository.sourceFiles", inconsistentFields);
  requireSameSet(actualRunnableEntryPoints, expectedRunnableEntryPoints, "currentRepository.runnableEntryPoints", inconsistentFields);
  requireSameSet(actualTestFiles, expectedTestFiles, "currentRepository.testFiles", inconsistentFields);
  requireSameSet(actualConfigFiles, expectedConfigFiles, "currentRepository.configFiles", inconsistentFields);

  const modulePaths = report.moduleFeatureSummary.modules.map((entry) => entry.relativePath).sort(compareStable);
  requireSameSet(modulePaths, expectedSourceFiles, "moduleFeatureSummary.modules", inconsistentFields);

  for (const relativePath of [...actualSourceFiles, ...actualTestFiles, ...actualConfigFiles]) {
    if (relativePath.startsWith("/") || relativePath.includes("\\") || relativePath.includes("..")) {
      inconsistentFields.push(`paths.normalized:${relativePath}`);
      continue;
    }
    if (!existsSync(resolve(projectRoot, relativePath))) {
      inconsistentFields.push(`paths.exists:${relativePath}`);
    }
  }

  const hasRunnableModule = report.runnableEntryPoints.length > 0;
  const hasRunnableUnitTest = report.testFiles.some((entry) => entry.metadata.containsTestDeclaration);
  if (!hasRunnableModule && !hasRunnableUnitTest) {
    missingFields.push("runnable_module_or_unit_test");
  }
}

function validateImplementedModuleFeatureSummary(
  value: unknown,
  missingFields: string[],
  inconsistentFields: string[],
): void {
  if (!isRecord(value)) {
    return;
  }
  requireLiteral(value, "schemaVersion", "implemented-module-features.v1", missingFields);
  requireArray(value, "modules", missingFields);
  requireRecord(value, "summary", missingFields);
  if (!Array.isArray(value.modules) || !isRecord(value.summary)) {
    return;
  }
  if (value.modules.length !== value.summary.moduleCount) {
    inconsistentFields.push("moduleFeatureSummary.summary.moduleCount");
  }
  for (const moduleEntry of value.modules) {
    if (!isRecord(moduleEntry)) {
      missingFields.push("moduleFeatureSummary.modules[]");
      continue;
    }
    for (const field of [
      "id",
      "relativePath",
      "moduleName",
      "kind",
      "exportedSymbols",
      "localDependencies",
      "coveredByTests",
      "runnableEntryPointIds",
      "featureTags",
    ]) {
      if (!(field in moduleEntry)) {
        missingFields.push(`moduleFeatureSummary.modules[].${field}`);
      }
    }
    for (const arrayField of ["exportedSymbols", "localDependencies", "coveredByTests", "runnableEntryPointIds", "featureTags"]) {
      if (!Array.isArray(moduleEntry[arrayField])) {
        missingFields.push(`moduleFeatureSummary.modules[].${arrayField}`);
      }
    }
  }
  if (value.summary.normalizedPathSeparator !== "/") {
    missingFields.push("moduleFeatureSummary.summary.normalizedPathSeparator");
  }
}

function validateCapabilityInventory(value: unknown, missingFields: string[], inconsistentFields: string[]): void {
  const validation = validateCapabilityInventoryArtifact(value);
  missingFields.push(...validation.missingFields.map((field) => `capabilityInventory.${field}`));
  inconsistentFields.push(...validation.inconsistentFields.map((field) => `capabilityInventory.${field}`));
}

function extractExportedSymbols(content: string): string[] {
  const symbols = [
    ...content.matchAll(/\bexport\s+(?:async\s+)?(?:function|class|const|let|var|interface|type)\s+([A-Za-z_$][\w$]*)/g),
  ].map((match) => match[1]);
  for (const exportBlock of content.matchAll(/\bexport\s*\{([^}]+)\}/g)) {
    for (const segment of exportBlock[1].split(",")) {
      const symbol = segment.trim().split(/\s+as\s+/)[0]?.trim();
      if (symbol) {
        symbols.push(symbol);
      }
    }
  }
  return symbols.filter(unique).sort(compareStable);
}

function extractLocalDependencies(input: {
  projectRoot: string;
  relativePath: string;
  content: string;
  sourcePathSet: Set<string>;
}): string[] {
  return [...input.content.matchAll(/\b(?:import|export)\s+(?:[^"']*from\s+)?["']([^"']+)["']/g)]
    .map((match) => match[1])
    .filter((specifier) => specifier.startsWith("."))
    .map((specifier) => resolveLocalDependency(input.projectRoot, input.relativePath, specifier, input.sourcePathSet))
    .filter((relativePath): relativePath is string => relativePath !== undefined)
    .filter(unique)
    .sort(compareStable);
}

function resolveLocalDependency(
  projectRoot: string,
  fromRelativePath: string,
  specifier: string,
  sourcePathSet: Set<string>,
): string | undefined {
  const absoluteImportPath = resolve(projectRoot, dirname(fromRelativePath), specifier);
  const candidates = [absoluteImportPath, `${absoluteImportPath}.ts`, `${absoluteImportPath}.js`, resolve(absoluteImportPath, "index.ts")];
  for (const candidate of candidates) {
    const relativePath = normalizeRelativePath(projectRoot, candidate);
    if (sourcePathSet.has(relativePath)) {
      return relativePath;
    }
  }
  return undefined;
}

function findTestCoverage(sourceFile: ClassifiedSourceFileEntry, testFiles: TestFileInventoryEntry[]): string[] {
  if (sourceFile.kind === "test") {
    return [sourceFile.relativePath];
  }
  const sourceBaseName = sourceFile.relativePath
    .replace(/^src\//, "")
    .replace(/^scripts\//, "")
    .replace(/\.[^.]+$/, "");
  return testFiles
    .filter((testFile) => {
      const testBaseName = testFile.relativePath
        .replace(/^tests\//, "")
        .replace(/\.test\.[^.]+$/, "")
        .replace(/\.[^.]+$/, "");
      return testBaseName === sourceBaseName || testFile.relativePath.includes(sourceBaseName);
    })
    .map((entry) => entry.relativePath)
    .sort(compareStable);
}

function inferFeatureTags(moduleName: string, content: string): string[] {
  const haystack = `${moduleName}\n${content}`.toLowerCase();
  const rules: Array<[string, RegExp]> = [
    ["request_analysis", /analyzeuserrequest|decomposeuserrequest|user request|task breakdown|planning/],
    ["role_routing", /buildroleroutes|role route|routing|assignment/],
    ["meeting_loop", /openclaw|hermes|meeting loop|reviewer request|meeting history/],
    ["final_synthesis", /final synthesis|finalsynthesis/],
    ["escalation", /escalation|user decision/],
    ["context_storage", /sqlite|database|raw full-text|full text|context storage/],
    ["context_compression", /compressed loop context|compression|summarizeforthread|summary timeline/],
    ["token_cost", /token|cost|baseline|savings/],
    ["verification", /verify|verification|evidence|check/],
    ["public_api", /export \{|public api|index/],
  ];
  return rules
    .filter(([, pattern]) => pattern.test(haystack))
    .map(([tag]) => tag)
    .sort(compareStable);
}

function normalizeRelativePath(projectRoot: string, absolutePath: string): string {
  return relative(projectRoot, absolutePath).split(sep).join("/");
}

function compareStable(left: string, right: string): number {
  return left.localeCompare(right, "en");
}

function unique<T>(value: T, index: number, values: T[]): boolean {
  return values.indexOf(value) === index;
}

function isInventoryOrchestrationReport(report: unknown): report is InventoryOrchestrationReport {
  const validation = validateInventoryOrchestrationReport(report);
  return validation.missingFields.length === 0 && validation.inconsistentFields.length === 0;
}

function requireLiteral(record: Record<string, unknown>, field: string, expected: string, missingFields: string[]): void {
  if (record[field] !== expected) {
    missingFields.push(field);
  }
}

function requireArray(record: Record<string, unknown>, field: string, missingFields: string[]): void {
  if (!Array.isArray(record[field])) {
    missingFields.push(field);
  }
}

function requireRecord(record: Record<string, unknown>, field: string, missingFields: string[]): void {
  if (!isRecord(record[field])) {
    missingFields.push(field);
  }
}

function requireCount(
  report: Record<string, unknown>,
  arrayField: string,
  countPath: string,
  missingFields: string[],
  inconsistentFields: string[],
): void {
  const arrayValue = report[arrayField];
  const countValue = readNestedNumber(report, countPath);
  if (!Array.isArray(arrayValue) || countValue === undefined) {
    missingFields.push(countPath);
    return;
  }
  if (arrayValue.length !== countValue) {
    inconsistentFields.push(countPath);
  }
}

function requireSameSet(actual: string[], expected: string[], field: string, inconsistentFields: string[]): void {
  if (actual.length !== expected.length || actual.some((value, index) => value !== expected[index])) {
    inconsistentFields.push(field);
  }
}

function readNestedNumber(record: Record<string, unknown>, path: string): number | undefined {
  const value = path.split(".").reduce<unknown>((current, segment) => {
    if (!isRecord(current)) {
      return undefined;
    }
    return current[segment];
  }, record);
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isInventoryOrchestrationReportShape(report: unknown): report is InventoryOrchestrationReport {
  if (!isRecord(report)) {
    return false;
  }
  return (
    Array.isArray(report.sourceFiles) &&
    Array.isArray(report.runnableEntryPoints) &&
    Array.isArray(report.testFiles) &&
    Array.isArray(report.configFiles) &&
    isRecord(report.moduleFeatureSummary) &&
    Array.isArray(report.moduleFeatureSummary.modules)
  );
}
