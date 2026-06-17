import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export const VALIDATION_COMMAND_DOCUMENT_PATH = "docs/automated-validation-commands.md";

export type ValidationCommandId =
  | "automated_tests"
  | "mvp_tests"
  | "verification_workflow_smoke"
  | "typecheck"
  | "verification_output"
  | "environment_dependencies";

export interface ValidationCommandSpec {
  id: ValidationCommandId;
  command: string;
  expectedResult: string;
  resultArtifact: string;
  kind: "automated_test" | "static_validation";
  packageScript?: string;
  artifactValidator?: (projectRoot: string, artifactPath: string) => boolean;
}

export interface ValidationCommandDocumentationCheckResult {
  schemaVersion: "validation-command-documentation-check.v1";
  command: "ai-agent check:validation-command-documentation";
  status: "passed" | "failed";
  document: {
    path: string;
    rowsMatchedSpecification: boolean;
    documentedCommandCount: number;
  };
  checks: ValidationCommandDocumentationEntryCheck[];
  summary: {
    requiredCommandCount: number;
    mappedCommandCount: number;
    missingArtifactCount: number;
    failedCheckIds: ValidationCommandId[];
  };
}

export interface ValidationCommandDocumentationEntryCheck {
  id: ValidationCommandId;
  command: string;
  expectedResult: string;
  resultArtifact: string;
  kind: ValidationCommandSpec["kind"];
  packageScript?: string;
  mappedToRecordedExpectedResult: boolean;
  resultArtifactPresent: boolean;
  resultArtifactValid: boolean;
  failureReason?: string;
}

interface ParsedValidationCommandRow {
  id: string;
  command: string;
  expectedResult: string;
  resultArtifact: string;
  kind: string;
}

export const validationCommandSpecs: ValidationCommandSpec[] = [
  {
    id: "automated_tests",
    command: "npm test",
    expectedResult: "TAP output exits 0 with all test files passing",
    resultArtifact: "tests/*.test.ts",
    kind: "automated_test",
    packageScript: "test",
  },
  {
    id: "mvp_tests",
    command: "npm run test:mvp --silent",
    expectedResult: 'MVP test suite exits 0 after discovering MVP-related `*.test.ts` artifacts',
    resultArtifact: "scripts/run-mvp-tests.ts",
    kind: "automated_test",
    packageScript: "test:mvp",
  },
  {
    id: "verification_workflow_smoke",
    command: "npm run run-verification-workflow --silent",
    expectedResult:
      'JSON reports `status: "passed"` and writes independent reproduced workflow evidence to `docs/generated/verification-workflow-result.json`',
    resultArtifact: "docs/generated/verification-workflow-result.json",
    kind: "automated_test",
    packageScript: "run-verification-workflow",
    artifactValidator: verificationWorkflowArtifactIsValid,
  },
  {
    id: "typecheck",
    command: "npm run check:typecheck --silent",
    expectedResult: 'JSON with `schemaVersion: "typecheck-command-check.v1"` and `status: "passed"`',
    resultArtifact: "docs/generated/typecheck-check-result.json",
    kind: "static_validation",
    packageScript: "check:typecheck",
  },
  {
    id: "verification_output",
    command: "npm run check:verification-output --silent",
    expectedResult: 'JSON with `schemaVersion: "verification-output-check-result.v1"` and `status: "passed"`',
    resultArtifact: "docs/generated/verification-output.json",
    kind: "static_validation",
    packageScript: "check:verification-output",
  },
  {
    id: "environment_dependencies",
    command: "npm run check:environment-dependencies --silent",
    expectedResult: 'JSON with `schemaVersion: "environment-dependency-check.v1"` and `status: "passed"`',
    resultArtifact: "docs/environment-dependency-verification.md",
    kind: "static_validation",
    packageScript: "check:environment-dependencies",
  },
];

export function checkValidationCommandDocumentation(input: {
  projectRoot?: string;
  documentPath?: string;
} = {}): ValidationCommandDocumentationCheckResult {
  const projectRoot = input.projectRoot ?? process.cwd();
  const documentPath = input.documentPath ?? VALIDATION_COMMAND_DOCUMENT_PATH;
  const rows = readValidationCommandRows(resolve(projectRoot, documentPath));
  const rowsMatchedSpecification = validationRowsMatchSpecs(rows, validationCommandSpecs);
  const packageScripts = readPackageScripts(projectRoot);

  const checks = validationCommandSpecs.map((spec, index): ValidationCommandDocumentationEntryCheck => {
    const row = rows[index];
    const packageScriptPresent = spec.packageScript === undefined || Object.hasOwn(packageScripts, spec.packageScript);
    const resultArtifactPresent = resultArtifactExists(projectRoot, spec.resultArtifact);
    const resultArtifactValid =
      resultArtifactPresent && (spec.artifactValidator?.(projectRoot, spec.resultArtifact) ?? true);
    const mappedToRecordedExpectedResult =
      rowsMatchedSpecification &&
      packageScriptPresent &&
      resultArtifactPresent &&
      resultArtifactValid &&
      row?.expectedResult === spec.expectedResult &&
      row?.resultArtifact === spec.resultArtifact;

    return {
      id: spec.id,
      command: spec.command,
      expectedResult: spec.expectedResult,
      resultArtifact: spec.resultArtifact,
      kind: spec.kind,
      packageScript: spec.packageScript,
      mappedToRecordedExpectedResult,
      resultArtifactPresent,
      resultArtifactValid,
      failureReason: mappedToRecordedExpectedResult
        ? undefined
        : buildEntryFailureReason({
            rowsMatchedSpecification,
            packageScriptPresent,
            resultArtifactPresent,
            resultArtifactValid,
            packageScript: spec.packageScript,
          }),
    };
  });

  const failedCheckIds = checks.filter((check) => !check.mappedToRecordedExpectedResult).map((check) => check.id);

  return {
    schemaVersion: "validation-command-documentation-check.v1",
    command: "ai-agent check:validation-command-documentation",
    status: rowsMatchedSpecification && failedCheckIds.length === 0 ? "passed" : "failed",
    document: {
      path: resolve(projectRoot, documentPath),
      rowsMatchedSpecification,
      documentedCommandCount: rows.length,
    },
    checks,
    summary: {
      requiredCommandCount: validationCommandSpecs.length,
      mappedCommandCount: checks.filter((check) => check.mappedToRecordedExpectedResult).length,
      missingArtifactCount: checks.filter((check) => !check.resultArtifactPresent).length,
      failedCheckIds,
    },
  };
}

function readValidationCommandRows(documentPath: string): ParsedValidationCommandRow[] {
  const markdown = readFileSync(documentPath, "utf8");
  return markdown
    .split("\n")
    .map((line) => line.match(/^\|\s*(?<id>[^|]+?)\s*\|\s*`(?<command>[^`]+)`\s*\|\s*(?<expectedResult>[^|]+?)\s*\|\s*`(?<resultArtifact>[^`]+)`\s*\|\s*(?<kind>[^|]+?)\s*\|$/)?.groups)
    .filter((row): row is Record<string, string> => row !== undefined && row.id !== "---")
    .map((row) => ({
      id: row.id.trim(),
      command: row.command.trim(),
      expectedResult: normalizeMarkdownCell(row.expectedResult),
      resultArtifact: row.resultArtifact.trim(),
      kind: row.kind.trim(),
    }));
}

function validationRowsMatchSpecs(rows: ParsedValidationCommandRow[], specs: ValidationCommandSpec[]): boolean {
  return (
    rows.length === specs.length &&
    rows.every((row, index) => {
      const spec = specs[index];
      return (
        row.id === spec?.id &&
        row.command === spec.command &&
        row.expectedResult === spec.expectedResult &&
        row.resultArtifact === spec.resultArtifact &&
        row.kind === spec.kind
      );
    })
  );
}

function readPackageScripts(projectRoot: string): Record<string, string> {
  const packageJson = JSON.parse(readFileSync(resolve(projectRoot, "package.json"), "utf8"));
  const scripts = packageJson.scripts;
  if (scripts === null || typeof scripts !== "object" || Array.isArray(scripts)) {
    return {};
  }
  return scripts;
}

function resultArtifactExists(projectRoot: string, artifactPath: string): boolean {
  if (artifactPath === "tests/*.test.ts") {
    const testsDirectory = resolve(projectRoot, "tests");
    try {
      return readDirNames(testsDirectory).some((entry) => entry.endsWith(".test.ts"));
    } catch {
      return false;
    }
  }
  try {
    readFileSync(resolve(projectRoot, artifactPath));
    return true;
  } catch {
    return false;
  }
}

function verificationWorkflowArtifactIsValid(projectRoot: string, artifactPath: string): boolean {
  try {
    const artifact = JSON.parse(readFileSync(resolve(projectRoot, artifactPath), "utf8"));
    const cases = Array.isArray(artifact.cases) ? artifact.cases : [];
    const caseNames = cases.map((entry) => entry?.name);
    return (
      artifact.schemaVersion === "verification-workflow-runner.v1" &&
      artifact.command === "ai-agent run-verification-workflow" &&
      artifact.status === "passed" &&
      artifact.summary?.caseCount === 2 &&
      artifact.summary?.passedCaseCount === 2 &&
      artifact.summary?.mvpWorkflowExecuted === true &&
      artifact.summary?.escalationWorkflowExecuted === true &&
      artifact.summary?.rawStorageSeparatedFromLoopContext === true &&
      caseNames.includes("finalized_meeting_loop") &&
      caseNames.includes("ambiguous_request_escalation")
    );
  } catch {
    return false;
  }
}

function readDirNames(directory: string): string[] {
  return readdirSync(directory);
}

function normalizeMarkdownCell(value: string): string {
  return value.trim().replaceAll("\\`", "`");
}

function buildEntryFailureReason(input: {
  rowsMatchedSpecification: boolean;
  packageScriptPresent: boolean;
  resultArtifactPresent: boolean;
  resultArtifactValid: boolean;
  packageScript?: string;
}): string {
  if (!input.rowsMatchedSpecification) {
    return "documented validation command table does not match executable specification";
  }
  if (!input.packageScriptPresent) {
    return `package.json scripts.${input.packageScript} is missing`;
  }
  if (!input.resultArtifactPresent) {
    return "recorded result artifact is missing";
  }
  if (!input.resultArtifactValid) {
    return "recorded result artifact is invalid";
  }
  return "command is not mapped to the recorded expected result";
}
