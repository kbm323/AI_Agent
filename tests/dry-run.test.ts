import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeCheckDryRunContractCommand } from "../scripts/check-dry-run-contract.ts";
import { executeDryRunCommand, runDryRunEntrypoint } from "../scripts/dry-run.ts";

test("dry-run command entrypoint writes deterministic stable observable output for fixed input", async () => {
  const artifactPath = "docs/generated/dry-run-final-output.json";
  const args = [
    "--request",
    "고정 입력으로 회의 결과를 안정적으로 생성해줘.",
    "--input-id",
    "seed-sub-ac-1-fixed-input",
    "--run-id",
    "seed-sub-ac-1-fixed-run",
    "--write-artifact",
  ];
  rmSync(artifactPath, { force: true });

  const first = await runDryRun(args);
  const firstArtifact = readFileSync(artifactPath, "utf8");
  rmSync(artifactPath, { force: true });
  const second = await runDryRun(args);
  const secondArtifact = readFileSync(artifactPath, "utf8");

  assert.equal(first.exitCode, 0);
  assert.equal(second.exitCode, 0);
  assert.equal(first.stderr, "");
  assert.equal(second.stderr, "");
  assert.equal(firstArtifact, secondArtifact);

  const parsed = JSON.parse(firstArtifact);
  assert.deepEqual(
    {
      command: parsed.command,
      schemaVersion: parsed.schemaVersion,
      status: parsed.status,
      inputIdentifier: parsed.metadata.inputIdentifier,
      executionId: parsed.metadata.executionId,
      roleRoutes: parsed.requestAnalysis.roleRoutes,
      escalationRequired: parsed.escalation.required,
    },
    {
      command: "ai-agent dry-run",
      schemaVersion: "final-output-artifact.v1",
      status: "finalized",
      inputIdentifier: "seed-sub-ac-1-fixed-input",
      executionId: "seed-sub-ac-1-fixed-run",
      roleRoutes: [
        "task-001->openclaw-owner",
        "task-002->openclaw-owner",
        "task-003->hermes-reviewer",
        "task-004->openclaw-finalizer",
      ],
      escalationRequired: false,
    },
  );
});

test("dry-run command entrypoint exits non-zero when required request input is missing", async () => {
  const result = await runDryRun([]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "missing required request input: provide --request <text> or --request-file <path>",
  });
});

test("dry-run command output is deterministic across repeated executions", () => {
  return (async () => {
    const first = await runDryRun(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);
    const second = await runDryRun(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘."]);

    assert.equal(first.exitCode, 0);
    assert.equal(second.exitCode, 0);
    assert.equal(first.stderr, "");
    assert.equal(second.stderr, "");
    assert.equal(first.stdout, second.stdout);

    const parsed = JSON.parse(first.stdout);
    assert.equal(parsed.status, "finalized");
    assert.deepEqual(omitJustification(parsed.selectedDecision), {
      outcome: "partial_redesign",
      label: "partial redesign",
      basis: "docs/diagnosis-report.md priority assessment: error frequency > maintenance difficulty > token cost > architecture fit > feature completeness",
    });
    assert.deepEqual(omitJustification(parsed.diagnosis), {
      decision: "partial_redesign",
      decisionLabel: "partial redesign",
      basis: "docs/diagnosis-report.md priority assessment: error frequency > maintenance difficulty > token cost > architecture fit > feature completeness",
    });
    assert.deepEqual(parsed.metadata, expectedMetadata("request:f42143edc0867a0d", "inline"));
    assert.equal(parsed.selectedDecision.justification.outcome, "partial_redesign");
    assert.equal(parsed.selectedDecision.justification.rule, "high_or_token_cost_evidence");
    assert.match(parsed.selectedDecision.justification.summary, /supporting findings: #1 finding:existing:src\/.+/);
    assert.deepEqual(parsed.selectedDecision.justification.priorityOrder, [
      "error_frequency",
      "maintainability",
      "token_cost",
      "architecture_fit",
      "feature_completeness",
    ]);
    assert.equal(parsed.selectedDecision.justification.supportingEvidence[0].rank, 1);
    assert.equal(parsed.selectedDecision.justification.supportingEvidence[0].priority, 1);
    assert.equal(parsed.selectedDecision.justification.supportingEvidence[0].category, "error_frequency");
    assert.equal(parsed.selectedDecision.justification.supportingEvidence[0].severity, "high");
    assert.match(parsed.selectedDecision.justification.supportingEvidence[0].findingId, /^finding:existing:src\/.+/);
    assert.equal(
      parsed.selectedDecision.justification.supportingEvidence[0].title,
      "Source module has no observable test coverage",
    );
    assert.deepEqual(parsed.diagnosis.justification, parsed.selectedDecision.justification);
    assert.equal(parsed.escalation.required, false);
    assert.deepEqual(parsed.requestAnalysis.roleRoutes, [
      "task-001->openclaw-owner",
      "task-002->openclaw-owner",
      "task-003->hermes-reviewer",
      "task-004->openclaw-finalizer",
    ]);
  })();
});

test("dry-run command records the provided input identifier in run metadata", async () => {
  const result = await runDryRun(["--request", "입력 식별자 메타데이터를 검증해줘.", "--input-id", "seed-sub-ac-2.1"]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(parsed.metadata, expectedMetadata("seed-sub-ac-2.1", "inline"));
});

test("dry-run command records a valid execution identifier in run metadata", async () => {
  const result = await runDryRun(["--request", "실행 식별자 기록을 검증해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.match(parsed.metadata.executionId, /^run:[a-f0-9]{16}$/);
});

test("dry-run command records the provided run identifier in run metadata", async () => {
  const result = await runDryRun(["--request", "사용자 지정 실행 식별자를 기록해줘.", "--run-id", "seed-sub-ac-2.3-run-1"]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.metadata.executionId, "seed-sub-ac-2.3-run-1");
});

test("dry-run command records required version information in run metadata", async () => {
  const result = await runDryRun(["--request", "버전 메타데이터를 검증해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(parsed.metadata.version, expectedVersionMetadata());
  assert.equal(typeof parsed.metadata.version.runtime.version, "string");
  assert.match(parsed.metadata.version.runtime.version, /^\d+\.\d+\.\d+/);
});

test("dry-run command records required configuration and model settings", async () => {
  const result = await runDryRun(["--request", "모델 설정 기록을 검증해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(parsed.metadata.runSettings, expectedRunSettings());
  assert.equal(parsed.metadata.runSettings.orchestrator.maxRounds, 4);
  assert.equal(parsed.metadata.runSettings.orchestrator.escalationPolicy, "default");
  for (const persona of ["openclawOwner", "hermesReviewer", "openclawFinalizer"]) {
    const settings = parsed.metadata.runSettings.models[persona];
    assert.equal(settings.provider, "local-deterministic");
    assert.equal(typeof settings.model, "string");
    assert.equal(settings.model.length > 0, true);
    assert.equal(settings.temperature, 0);
    assert.equal(Number.isInteger(settings.maxOutputTokens), true);
    assert.equal(settings.maxOutputTokens > 0, true);
  }
});

test("dry-run reporting output renders Prior Review Evidence diagnostic section", async () => {
  const result = await runDryRun(["--request", "진단 출력에 prior review evidence를 표시해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  const section = parsed.diagnosticOutput.sections.find((entry: any) => entry.title === "Prior Review Evidence");

  assert.deepEqual(section, {
    title: "Prior Review Evidence",
    evidence: {
      artifactPath: join(process.cwd(), "docs", "review-evidence.json"),
      schemaVersion: "review-evidence.v1",
      recommendation: "partial_redesign",
      ...readCurrentReviewEvidenceCounts(),
      validationValid: true,
      completenessComplete: true,
      decisionGateAccepted: true,
    },
  });
});

test("dry-run diagnostic output orders prior-review evidence before redesign decisions", async () => {
  const result = await runDryRun(["--request", "진단 출력 순서를 검증해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  const sectionTitles = parsed.diagnosticOutput.sections.map((section: any) => section.title);
  const priorReviewIndex = sectionTitles.indexOf("Prior Review Evidence");
  const decisionIndexes = ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"].map((title) =>
    sectionTitles.indexOf(title),
  );

  assert.equal(priorReviewIndex, 0);
  for (const decisionIndex of decisionIndexes) {
    assert.equal(decisionIndex > priorReviewIndex, true);
  }
});

test("dry-run reporting output renders only allowed decision result labels", async () => {
  const result = await runDryRun(["--request", "진단 출력에 재설계 판단 섹션 라벨을 표시해줘."]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  const decisionSections = parsed.diagnosticOutput.sections.filter((entry: any) =>
    ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"].includes(entry.title),
  );

  assert.deepEqual(
    decisionSections.map((section: any) => section.title),
    ["Keep Decision", "Partial Redesign Decision", "Full Redesign Decision"],
  );
  assert.deepEqual(
    decisionSections.map((section: any) => section.evidence.label),
    ["Keep", "partial redesign", "full replan"],
  );
  assert.deepEqual(
    decisionSections.map((section: any) => section.evidence.selected),
    [false, true, false],
  );
});

test("dry-run command writes the generated final output artifact to a deterministic path", async () => {
  const artifactPath = "docs/generated/dry-run-final-output.json";
  rmSync(artifactPath, { force: true });

  const result = await runDryRun(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘.", "--write-artifact"]);

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");
  assert.equal(existsSync(artifactPath), true);

  const parsed = JSON.parse(result.stdout);
  const persisted = JSON.parse(readFileSync(artifactPath, "utf8"));
  assert.deepEqual(parsed.generatedArtifact, {
    path: artifactPath,
    schemaVersion: "final-output-artifact.v1",
  });
  assert.equal(persisted.schemaVersion, "final-output-artifact.v1");
  assert.equal(persisted.command, "ai-agent dry-run");
  assert.equal(persisted.userRequest, "뮤직비디오 오프닝 아이디어를 회의해줘.");
  assert.equal(persisted.generatedArtifact, undefined);
});

test("dry-run contract check emits a stable parseable JSON contract", async () => {
  const result = await executeCheckDryRunContractCommand();

  assert.equal(result.exitCode, 0);
  assert.equal(result.stderr, "");

  const parsed = JSON.parse(result.stdout);
  assert.deepEqual(parsed, {
    command: "ai-agent check-dry-run-contract",
    status: "passed",
    contract: {
      schemaVersion: "dry-run-contract.v1",
      deterministic: true,
      dryRunCommand: "npm run dry-run -- --request <user_request>",
      cases: [
        {
          case: "clear_request",
          exitCode: 0,
          stream: "stdout",
          parseableJson: true,
          status: "finalized",
          requiredFields: [
            "command",
            "metadata",
            "metadata.executionId",
            "metadata.version",
            "metadata.runSettings",
            "metadata.runSettings.models",
            "schemaVersion",
            "status",
            "userRequest",
            "selectedDecision",
            "selectedDecision.justification",
            "diagnosis",
            "diagnosis.justification",
            "diagnosticOutput",
            "diagnosticOutput.sections",
            "requestAnalysis",
            "openclawOutputs",
            "hermesReviews",
            "meetingHistory",
            "finalSynthesis",
            "escalation",
            "escalation.decisionContext",
            "escalation.nextAction",
            "escalation.preservedContext",
            "tokenStrategy",
          ],
        },
        {
          case: "ambiguous_request",
          exitCode: 0,
          stream: "stdout",
          parseableJson: true,
          status: "waiting_for_user",
          requiredFields: [
            "command",
            "metadata",
            "metadata.executionId",
            "metadata.version",
            "metadata.runSettings",
            "metadata.runSettings.models",
            "schemaVersion",
            "status",
            "userRequest",
            "selectedDecision",
            "selectedDecision.justification",
            "diagnosis",
            "diagnosis.justification",
            "diagnosticOutput",
            "diagnosticOutput.sections",
            "requestAnalysis",
            "openclawOutputs",
            "hermesReviews",
            "meetingHistory",
            "escalation",
            "escalation.decisionContext",
            "escalation.nextAction",
            "escalation.preservedContext",
            "tokenStrategy",
          ],
        },
        {
          case: "invalid_input",
          exitCode: 2,
          stream: "stderr",
          parseableJson: true,
          error: "invalid_input",
          requiredFields: ["error", "message"],
        },
      ],
    },
  });
});

test("dry-run ambiguous request returns a normal escalation artifact", async () => {
  const result = await runDryRun(["--request", "대충 좋은 후보 여러 개 추천만 해줘."]);

  assert.equal(result.exitCode, 0);
  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.status, "waiting_for_user");
  assert.equal(parsed.diagnosis.decision, "partial_redesign");
  assert.equal(parsed.escalation.required, true);
  assert.deepEqual(parsed.escalation.reasons, ["underspecified_preference", "unclear_success_criteria"]);
  assert.deepEqual(parsed.escalation.decisionContext, {
    status: "waiting_for_user",
    trigger: "ambiguous_request",
    preservedTurns: 2,
    latestMeetingSummary: "User decision required\n\nReasons:\n- underspecified_preference\n- unclear_success_criteria",
    diagnosisDecision: "partial_redesign",
  });
  assert.deepEqual(parsed.escalation.nextAction, {
    type: "user_input_required",
    prompt: "Clarify the blocked decision before continuing the OpenClaw/Hermes loop.",
    requestedFields: ["success_criteria", "preferred_direction", "constraints_or_examples"],
  });
  assert.deepEqual(parsed.escalation.preservedContext, {
    rawStorage: "Full request, draft, review, and escalation text is retained in turns.content.",
    exposedSummary: "Normal dry-run output exposes bounded meetingHistory summaries instead of raw full text.",
    compressedContext: "Next loop turn should carry request summary, latest meeting summary, reasons, and requestedFields only.",
  });
});

test("dry-run invalid input exits non-zero with stable JSON error", async () => {
  const result = await runDryRun(["--request", "   "]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "userRequest must be a non-empty string",
  });
});

test("dry-run malformed request flag exits non-zero with stable JSON error", async () => {
  const result = await runDryRun(["--request", "--input-id", "missing-request-value"]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "--request requires a value",
  });
});

test("dry-run module entrypoint exits non-zero for malformed supplied argument values", async () => {
  let stdout = "";
  let stderr = "";
  const exitCode = await runDryRunEntrypoint(["--request", "--input-id", "missing-request-value"], {
    stdout: {
      write(chunk: string) {
        stdout += chunk;
        return true;
      },
    },
    stderr: {
      write(chunk: string) {
        stderr += chunk;
        return true;
      },
    },
  });

  assert.equal(exitCode, 2);
  assert.equal(stdout, "");
  assert.deepEqual(JSON.parse(stderr), {
    error: "invalid_input",
    message: "--request requires a value",
  });
});

test("dry-run module entrypoint exits non-zero for unsupported supplied argument values", async () => {
  let stdout = "";
  let stderr = "";
  const exitCode = await runDryRunEntrypoint(["--request", "검증 실행", "--input-id", ""], {
    stdout: {
      write(chunk: string) {
        stdout += chunk;
        return true;
      },
    },
    stderr: {
      write(chunk: string) {
        stderr += chunk;
        return true;
      },
    },
  });

  assert.equal(exitCode, 2);
  assert.equal(stdout, "");
  assert.deepEqual(JSON.parse(stderr), {
    error: "invalid_input",
    message: "inputIdentifier must be non-empty",
  });
});

test("dry-run accepts a prior-review artifact identifier and returns observable handler output", async () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-dry-prior-"));
  try {
    const artifactPath = join(root, "prior-review.json");
    writeFileSync(
      artifactPath,
      `${JSON.stringify(
        {
          schemaVersion: "review-evidence.v1",
          inventory: [
            {
              id: "existing:src/orchestrator.ts",
              relativePath: "src/orchestrator.ts",
              kind: "source",
              moduleName: "src.orchestrator",
            },
          ],
          findings: [
            {
              id: "finding:existing:src/orchestrator.ts:missing-test",
              sourceId: "existing:src/orchestrator.ts",
              relativePath: "src/orchestrator.ts",
              moduleName: "src.orchestrator",
              severity: "high",
              category: "error_frequency",
              title: "Source module has no observable test coverage",
              evidence: "No test reference was detected for this source module.",
              recommendation: "Add a focused runnable test before using this module in redesign decisions.",
            },
          ],
          summary: {
            inspectedModules: 1,
            findingCount: 1,
            findingsBySeverity: {
              critical: 0,
              high: 1,
              medium: 0,
              low: 0,
            },
            findingsByCategory: {
              error_frequency: 1,
              maintainability: 0,
              token_cost: 0,
              architecture_fit: 0,
              feature_completeness: 0,
            },
            recommendation: "partial_redesign",
          },
        },
        null,
        2,
      )}\n`,
    );

    const result = await runDryRun(["--request", "뮤직비디오 오프닝 아이디어를 회의해줘.", "--prior-review-artifact", artifactPath]);

    assert.equal(result.exitCode, 0);
    const parsed = JSON.parse(result.stdout);
    assert.equal(parsed.priorReview.command, "ai-agent prior-review");
    assert.equal(parsed.priorReview.artifact.path, artifactPath);
    assert.deepEqual(parsed.priorReview.decisionBasis, {
      priorReviewArtifactPath: artifactPath,
      recommendation: "partial_redesign",
    });
    assert.equal(parsed.priorReview.validation.valid, true);
    assert.equal(parsed.priorReview.completeness.complete, true);
    assert.equal(parsed.priorReview.decisionGate.accepted, true);
    assert.match(parsed.priorReview.runnable.dryRunCommand, /--prior-review-artifact/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("dry-run prior-review artifact resolution failure exits non-zero with stable JSON error", async () => {
  const result = await runDryRun(["--prior-review-artifact", ""]);

  assert.equal(result.exitCode, 2);
  assert.equal(result.stdout, "");
  assert.deepEqual(JSON.parse(result.stderr), {
    error: "invalid_input",
    message: "priorReviewArtifact must be a non-empty string",
  });
});

async function runDryRun(args: string[]) {
  return executeDryRunCommand(args);
}

function omitJustification<T extends { justification?: unknown }>(value: T): Omit<T, "justification"> {
  const { justification, ...rest } = value;
  void justification;
  return rest;
}

function readCurrentReviewEvidenceCounts(): { inspectedModules: number; findingCount: number } {
  const artifact = JSON.parse(readFileSync(join(process.cwd(), "docs", "review-evidence.json"), "utf8"));
  return {
    inspectedModules: artifact.summary.inspectedModules,
    findingCount: artifact.summary.findingCount,
  };
}

function expectedMetadata(inputIdentifier: string, inputSource: "default" | "inline" | "file") {
  return {
    executionId: expectedExecutionId(inputIdentifier),
    inputIdentifier,
    inputSource,
    version: expectedVersionMetadata(),
    runSettings: expectedRunSettings(),
  };
}

function expectedVersionMetadata() {
  return {
    schemaVersion: "run-version-metadata.v1",
    artifactSchemaVersion: "final-output-artifact.v1",
    commandVersion: "ai-agent-dry-run.v1",
    implementationVersion: "multi-agent-meeting-mvp.v1",
    runtime: {
      name: "node",
      version: process.versions.node,
    },
  };
}

function expectedExecutionId(inputIdentifier: string): string {
  const known: Record<string, string> = {
    "request:f42143edc0867a0d": "run:5f605735bb696dec",
    "request:4b663e07326e850a": "run:af5510a30d6bdc67",
    "seed-sub-ac-2.1": "run:26f1b8b1b18870c4",
  };
  return known[inputIdentifier] ?? assert.fail(`missing expected execution id for ${inputIdentifier}`);
}

function expectedRunSettings() {
  return {
    executionMode: "dry_run",
    orchestrator: {
      maxRounds: 4,
      escalationPolicy: "default",
    },
    models: {
      openclawOwner: {
        provider: "local-deterministic",
        model: "openclaw-dry-run-owner-v1",
        temperature: 0,
        maxOutputTokens: 512,
      },
      hermesReviewer: {
        provider: "local-deterministic",
        model: "hermes-dry-run-reviewer-v1",
        temperature: 0,
        maxOutputTokens: 512,
      },
      openclawFinalizer: {
        provider: "local-deterministic",
        model: "openclaw-dry-run-finalizer-v1",
        temperature: 0,
        maxOutputTokens: 768,
      },
    },
  };
}
