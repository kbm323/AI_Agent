import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  executeCheckValidationCommandDocumentationCommand,
  checkValidationCommandDocumentationCommand,
} from "../scripts/check-validation-command-documentation.ts";
import {
  checkValidationCommandDocumentation,
  validationCommandSpecs,
  VALIDATION_COMMAND_DOCUMENT_PATH,
  type ValidationCommandDocumentationCheckResult,
  type ValidationCommandDocumentationEntryCheck,
  type ValidationCommandId,
  type ValidationCommandSpec,
} from "../src/validation-command-documentation.ts";

test("validation command documentation public exports expose the documented success-path contract", () => {
  const documentedIds = [
    "automated_tests",
    "mvp_tests",
    "verification_workflow_smoke",
    "typecheck",
    "verification_output",
    "environment_dependencies",
  ] satisfies ValidationCommandId[];
  const typedSpecs: ValidationCommandSpec[] = validationCommandSpecs;

  assert.equal(VALIDATION_COMMAND_DOCUMENT_PATH, "docs/automated-validation-commands.md");
  assert.equal(typedSpecs.length, documentedIds.length);
  assert.deepEqual(
    typedSpecs.map((spec) => spec.id),
    documentedIds,
  );
  assert.equal(typedSpecs.every((spec) => spec.command.length > 0), true);
  assert.equal(typedSpecs.every((spec) => spec.expectedResult.length > 0), true);
  assert.equal(typedSpecs.every((spec) => spec.resultArtifact.length > 0), true);
  assert.equal(typedSpecs.every((spec) => ["automated_test", "static_validation"].includes(spec.kind)), true);
});

test("validation command documentation maps each command to a recorded expected result", () => {
  const root = buildValidationDocumentationFixture();
  try {
    const result = checkValidationCommandDocumentation({ projectRoot: root });
    const typedResult: ValidationCommandDocumentationCheckResult = result;
    const typedEntryChecks: ValidationCommandDocumentationEntryCheck[] = typedResult.checks;

    assert.equal(typedResult.schemaVersion, "validation-command-documentation-check.v1");
    assert.equal(typedResult.command, "ai-agent check:validation-command-documentation");
    assert.equal(typedResult.status, "passed");
    assert.equal(typedResult.document.rowsMatchedSpecification, true);
    assert.equal(typedResult.document.documentedCommandCount, validationCommandSpecs.length);
    assert.deepEqual(typedResult.summary, {
      requiredCommandCount: validationCommandSpecs.length,
      mappedCommandCount: validationCommandSpecs.length,
      missingArtifactCount: 0,
      failedCheckIds: [],
    });
    assert.equal(typedEntryChecks.every((check) => check.mappedToRecordedExpectedResult), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validation command documentation check fails when the command table drifts", () => {
  const root = buildValidationDocumentationFixture({ commandOverride: "npm run test:mvp" });
  try {
    const result = checkValidationCommandDocumentation({ projectRoot: root });

    assert.equal(result.status, "failed");
    assert.equal(result.document.rowsMatchedSpecification, false);
    assert.deepEqual(result.summary.failedCheckIds, validationCommandSpecs.map((spec) => spec.id));
    assert.equal(
      result.checks.every(
        (check) => check.failureReason === "documented validation command table does not match executable specification",
      ),
      true,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validation command documentation check fails when a package script is missing", () => {
  const root = buildValidationDocumentationFixture({ includeVerificationOutputScript: false });
  try {
    const result = checkValidationCommandDocumentation({ projectRoot: root });

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.failedCheckIds, ["verification_output"]);
    assert.equal(
      result.checks.find((check) => check.id === "verification_output")?.failureReason,
      "package.json scripts.check:verification-output is missing",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validation command documentation check fails when a recorded result artifact is missing", () => {
  const root = buildValidationDocumentationFixture({ includeTypecheckArtifact: false });
  try {
    const result = checkValidationCommandDocumentation({ projectRoot: root });

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.failedCheckIds, ["typecheck"]);
    assert.equal(result.summary.missingArtifactCount, 1);
    assert.equal(
      result.checks.find((check) => check.id === "typecheck")?.failureReason,
      "recorded result artifact is missing",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validation command documentation check fails when reproduced workflow evidence is malformed", () => {
  const root = buildValidationDocumentationFixture({ workflowArtifactOverride: { status: "passed" } });
  try {
    const result = checkValidationCommandDocumentation({ projectRoot: root });

    assert.equal(result.status, "failed");
    assert.deepEqual(result.summary.failedCheckIds, ["verification_workflow_smoke"]);
    assert.equal(
      result.checks.find((check) => check.id === "verification_workflow_smoke")?.failureReason,
      "recorded result artifact is invalid",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validation command documentation command wrapper produces stable observable JSON", () => {
  const result = executeCheckValidationCommandDocumentationCommand(process.cwd());
  const parsed = JSON.parse(result.stdout);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(parsed.schemaVersion, "validation-command-documentation-check.v1");
  assert.equal(parsed.command, "ai-agent check:validation-command-documentation");
  assert.equal(parsed.status, "passed");
  assert.equal(parsed.summary.failedCheckIds.length, 0);
});

test("validation command documentation command helper returns the successful check result", () => {
  const root = buildValidationDocumentationFixture();
  try {
    const result = checkValidationCommandDocumentationCommand(root);

    assert.equal(result.schemaVersion, "validation-command-documentation-check.v1");
    assert.equal(result.status, "passed");
    assert.equal(result.summary.mappedCommandCount, validationCommandSpecs.length);
    assert.deepEqual(result.summary.failedCheckIds, []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function buildValidationDocumentationFixture(input: {
  commandOverride?: string;
  includeVerificationOutputScript?: boolean;
  includeTypecheckArtifact?: boolean;
  workflowArtifactOverride?: unknown;
} = {}): string {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-validation-docs-"));
  mkdirSync(join(root, "docs", "generated"), { recursive: true });
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "tests"), { recursive: true });
  writeFileSync(
    join(root, "package.json"),
    `${JSON.stringify(
      {
        private: true,
        type: "module",
        scripts: {
          test: "node --test --test-concurrency=1 tests/*.test.ts",
          "test:mvp": "node scripts/run-mvp-tests.ts",
          "run-verification-workflow": "node scripts/run-verification-workflow.ts",
          "check:typecheck": "node scripts/check-typecheck.ts",
          ...(input.includeVerificationOutputScript === false
            ? {}
            : { "check:verification-output": "node scripts/check-verification-output.ts" }),
          "check:environment-dependencies": "node scripts/check-environment-dependencies.ts",
        },
      },
      null,
      2,
    )}\n`,
  );
  writeFileSync(join(root, "tests", "sample.test.ts"), "import test from 'node:test';\ntest('sample', () => {});\n");
  writeFileSync(join(root, "scripts", "run-mvp-tests.ts"), "export {};\n");
  writeFileSync(join(root, "scripts", "run-verification-workflow.ts"), "export {};\n");
  if (input.includeTypecheckArtifact !== false) {
    writeFileSync(join(root, "docs", "generated", "typecheck-check-result.json"), "{}\n");
  }
  writeFileSync(
    join(root, "docs", "generated", "verification-workflow-result.json"),
    `${JSON.stringify(input.workflowArtifactOverride ?? buildValidVerificationWorkflowArtifact(), null, 2)}\n`,
  );
  writeFileSync(join(root, "docs", "generated", "verification-output.json"), "{}\n");
  writeFileSync(join(root, "docs", "environment-dependency-verification.md"), "# env\n");
  mkdirSync(join(root, "docs"), { recursive: true });
  writeFileSync(join(root, VALIDATION_COMMAND_DOCUMENT_PATH), buildDocument(input.commandOverride));
  return root;
}

function buildValidVerificationWorkflowArtifact(): unknown {
  return {
    schemaVersion: "verification-workflow-runner.v1",
    command: "ai-agent run-verification-workflow",
    status: "passed",
    cases: [
      { name: "finalized_meeting_loop", status: "passed", observed: {}, failures: [] },
      { name: "ambiguous_request_escalation", status: "passed", observed: {}, failures: [] },
    ],
    summary: {
      caseCount: 2,
      passedCaseCount: 2,
      failedCaseCount: 0,
      mvpWorkflowExecuted: true,
      escalationWorkflowExecuted: true,
      rawStorageSeparatedFromLoopContext: true,
    },
  };
}

function buildDocument(commandOverride?: string): string {
  return [
    "# Automated Test And Static Validation Commands",
    "",
    "## Command Matrix",
    "",
    "| id | command | expected result | result artifact | kind |",
    "| --- | --- | --- | --- | --- |",
    ...validationCommandSpecs.map((spec) => {
      const command = spec.id === "mvp_tests" && commandOverride ? commandOverride : spec.command;
      return `| ${spec.id} | \`${command}\` | ${spec.expectedResult.replaceAll("`", "\\`")} | \`${spec.resultArtifact}\` | ${spec.kind} |`;
    }),
    "",
  ].join("\n");
}
