import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  buildRequirementGapReviewArtifact,
  checkRequirementGapMapping,
  executeRequirementGapCheckCommand,
  validateReadmeRequirementCoverage,
} from "../scripts/check-requirement-gap.ts";
import { extractReadmeMvpRequirementList, loadDiagnosisReportArtifact } from "../src/inspection.ts";

test("requirement-gap check returns stable observable artifact presence output", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-requirement-gap-check-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(
      join(root, "README.md"),
      [
        "# AI_Agent",
        "",
        "## MVP 목표",
        "",
        "```text",
        "parent channel user request",
        "  -> task 생성",
        "  -> OpenClaw owner draft",
        "  -> Hermes review",
        "  -> OpenClaw final synthesis",
        "  -> thread timeline 게시",
        "```",
        "",
        "## 운영 원칙",
        "",
        "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
        "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
        "",
      ].join("\n"),
    );
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(
      join(root, "docs", "review-evidence.json"),
      `${JSON.stringify(
        {
          schemaVersion: "review-evidence.v1",
          summary: {
            findingsByCategory: {
              error_frequency: 4,
              maintainability: 2,
              token_cost: 3,
              architecture_fit: 0,
              feature_completeness: 0,
            },
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
    writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
    writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");
    writeRequirementGapMappingFixture(root);

    const apiResult = checkRequirementGapMapping(root);
    const commandResult = executeRequirementGapCheckCommand(["--project-root", root]);

    assert.equal(commandResult.exitCode, 0);
    assert.equal(commandResult.stderr, "");
    assert.deepEqual(JSON.parse(commandResult.stdout), apiResult);
    assert.deepEqual(apiResult, {
      command: "ai-agent check-requirement-gap",
      artifact: {
        name: "requirementToGapMappingArtifact",
        present: true,
        schemaVersion: "implementation-capabilities.v1",
        capabilityIds: [
          "request-analysis-work-breakdown",
          "role-based-routing",
          "openclaw-hermes-meeting-loop",
          "final-synthesis",
          "escalation-artifact",
          "raw-storage-summary-context",
        ],
        implementedCount: 6,
        missingCount: 0,
        readmeRequirementCount: 8,
        readmeRequirementStatusCounts: {
          covered: 8,
          partial: 0,
          missing: 0,
          unknown: 0,
        },
        artifactPath: join(root, "docs", "generated", "requirement-gap-mapping.json"),
        artifactFilePresent: true,
        artifactFileMatchesDiagnosis: true,
        capabilityMappings: [
          {
            id: "request-analysis-work-breakdown",
            readmeRequirement: "Analyze user request and decompose it into task_breakdown items.",
            readmeRequirementIds: ["mvp_goal_flow:001", "mvp_goal_flow:002"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/planning.ts"],
          },
          {
            id: "role-based-routing",
            readmeRequirement: "Route work items to OpenClaw owner/finalizer and Hermes reviewer personas.",
            readmeRequirementIds: ["mvp_goal_flow:003", "mvp_goal_flow:004", "mvp_goal_flow:005"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "openclaw-hermes-meeting-loop",
            readmeRequirement: "Preserve OpenClaw execution and Hermes review turns in a meeting loop.",
            readmeRequirementIds: ["mvp_goal_flow:003", "mvp_goal_flow:004", "mvp_goal_flow:006"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts"],
          },
          {
            id: "final-synthesis",
            readmeRequirement: "Produce final synthesis after reviewer convergence.",
            readmeRequirementIds: ["mvp_goal_flow:005"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/orchestrator.ts"],
          },
          {
            id: "escalation-artifact",
            readmeRequirement: "Surface convergence failure or user-decision needs as escalation artifacts.",
            readmeRequirementIds: ["operating_principle:002"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/policies.ts"],
          },
          {
            id: "raw-storage-summary-context",
            readmeRequirement: "Separate raw full-text storage from exposed loop summaries and compressed context.",
            readmeRequirementIds: ["mvp_goal_flow:006", "operating_principle:001"],
            status: "implemented",
            gapDetected: false,
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
          },
        ],
        readmeRequirementMappings: [
          {
            id: "mvp_goal_flow:001",
            category: "mvp_goal_flow",
            text: "parent channel user request",
            status: "covered",
            capabilityIds: ["request-analysis-work-breakdown"],
            evidenceSourceIds: ["existing:src/planning.ts"],
          },
          {
            id: "mvp_goal_flow:002",
            category: "mvp_goal_flow",
            text: "-> task 생성",
            status: "covered",
            capabilityIds: ["request-analysis-work-breakdown"],
            evidenceSourceIds: ["existing:src/planning.ts"],
          },
          {
            id: "mvp_goal_flow:003",
            category: "mvp_goal_flow",
            text: "-> OpenClaw owner draft",
            status: "covered",
            capabilityIds: ["openclaw-hermes-meeting-loop", "role-based-routing"],
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "mvp_goal_flow:004",
            category: "mvp_goal_flow",
            text: "-> Hermes review",
            status: "covered",
            capabilityIds: ["openclaw-hermes-meeting-loop", "role-based-routing"],
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "mvp_goal_flow:005",
            category: "mvp_goal_flow",
            text: "-> OpenClaw final synthesis",
            status: "covered",
            capabilityIds: ["final-synthesis", "role-based-routing"],
            evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "mvp_goal_flow:006",
            category: "mvp_goal_flow",
            text: "-> thread timeline 게시",
            status: "covered",
            capabilityIds: ["openclaw-hermes-meeting-loop", "raw-storage-summary-context"],
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
          },
          {
            id: "operating_principle:001",
            category: "operating_principle",
            text: "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
            status: "covered",
            capabilityIds: ["raw-storage-summary-context"],
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
          },
          {
            id: "operating_principle:002",
            category: "operating_principle",
            text: "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
            status: "covered",
            capabilityIds: ["escalation-artifact"],
            evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/policies.ts"],
          },
        ],
        requirementCapabilityMatches: [
          {
            id: "requirement-capability-match:mvp_goal_flow:001",
            requirementId: "mvp_goal_flow:001",
            requirementText: "parent channel user request",
            capabilityIds: ["request-analysis-work-breakdown"],
            status: "matched",
            evidenceSourceIds: ["existing:src/planning.ts"],
          },
          {
            id: "requirement-capability-match:mvp_goal_flow:002",
            requirementId: "mvp_goal_flow:002",
            requirementText: "-> task 생성",
            capabilityIds: ["request-analysis-work-breakdown"],
            status: "matched",
            evidenceSourceIds: ["existing:src/planning.ts"],
          },
          {
            id: "requirement-capability-match:mvp_goal_flow:003",
            requirementId: "mvp_goal_flow:003",
            requirementText: "-> OpenClaw owner draft",
            capabilityIds: ["openclaw-hermes-meeting-loop", "role-based-routing"],
            status: "matched",
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "requirement-capability-match:mvp_goal_flow:004",
            requirementId: "mvp_goal_flow:004",
            requirementText: "-> Hermes review",
            capabilityIds: ["openclaw-hermes-meeting-loop", "role-based-routing"],
            status: "matched",
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "requirement-capability-match:mvp_goal_flow:005",
            requirementId: "mvp_goal_flow:005",
            requirementText: "-> OpenClaw final synthesis",
            capabilityIds: ["final-synthesis", "role-based-routing"],
            status: "matched",
            evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/types.ts"],
          },
          {
            id: "requirement-capability-match:mvp_goal_flow:006",
            requirementId: "mvp_goal_flow:006",
            requirementText: "-> thread timeline 게시",
            capabilityIds: ["openclaw-hermes-meeting-loop", "raw-storage-summary-context"],
            status: "matched",
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/orchestrator.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
          },
          {
            id: "requirement-capability-match:operating_principle:001",
            requirementId: "operating_principle:001",
            requirementText: "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
            capabilityIds: ["raw-storage-summary-context"],
            status: "matched",
            evidenceSourceIds: ["existing:src/db.ts", "existing:src/planning.ts", "existing:src/policies.ts"],
          },
          {
            id: "requirement-capability-match:operating_principle:002",
            requirementId: "operating_principle:002",
            requirementText: "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
            capabilityIds: ["escalation-artifact"],
            status: "matched",
            evidenceSourceIds: ["existing:src/orchestrator.ts", "existing:src/policies.ts"],
          },
        ],
        requirementCapabilityMatchStatusCounts: {
          matched: 8,
          partial: 0,
          missing: 0,
        },
        readmeRequirementCoverage: {
          valid: true,
          expectedCount: 8,
          mappedCount: 8,
          coveredExactlyOnce: true,
          missingRequirementIds: [],
          duplicateRequirementIds: [],
          unexpectedRequirementIds: [],
        },
        priorityOrderVerified: true,
        priorityOrderedEvidence: [
          {
            rank: 1,
            category: "error_frequency",
            findingCount: 4,
            present: true,
          },
          {
            rank: 2,
            category: "maintainability",
            findingCount: 2,
            present: true,
          },
          {
            rank: 3,
            category: "token_cost",
            findingCount: 3,
            present: true,
          },
          {
            rank: 4,
            category: "architecture_fit",
            findingCount: 0,
            present: true,
          },
          {
            rank: 5,
            category: "feature_completeness",
            findingCount: 0,
            present: true,
          },
        ],
        gapReviewArtifact: {
          path: join(root, "docs", "generated", "requirement-gap-review.json"),
          present: true,
          schemaVersion: "requirement-gap-review.v1",
          artifactFilePresent: true,
          artifactFileMatchesComputed: true,
          observedGapCount: 0,
          capabilityGapCount: 0,
          readmeRequirementGapCount: 0,
          nonOverlappingRequirementCoverage: true,
          duplicateCoverageKeys: [],
          overlappingRequirementIds: [],
        },
      },
      source: {
        diagnosisReportPath: join(root, "docs", "diagnosis-report.md"),
        readmePath: join(root, "README.md"),
        reviewEvidencePath: join(root, "docs", "review-evidence.json"),
      },
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("requirement-gap check maps extracted README requirements without implementation evidence to missing gaps", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-requirement-gap-missing-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(
      join(root, "README.md"),
      [
        "# AI_Agent",
        "",
        "## MVP 목표",
        "",
        "```text",
        "parent channel user request",
        "  -> task 생성",
        "  -> OpenClaw owner draft",
        "  -> Hermes review",
        "  -> OpenClaw final synthesis",
        "  -> thread timeline 게시",
        "```",
        "",
        "## 운영 원칙",
        "",
        "- OpenClaw = orchestrator / owner / finalizer",
        "- Hermes = reviewer-only, mention/reply when requested",
        "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
        "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
        "",
      ].join("\n"),
    );
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(
      join(root, "docs", "review-evidence.json"),
      `${JSON.stringify(
        {
          schemaVersion: "review-evidence.v1",
          summary: {
            findingsByCategory: {
              error_frequency: 1,
              maintainability: 1,
              token_cost: 1,
              architecture_fit: 0,
              feature_completeness: 2,
            },
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "policies.ts"), "export function createDefaultEscalationPolicy() {}\n");
    writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");
    writeRequirementGapMappingFixture(root);

    const apiResult = checkRequirementGapMapping(root);
    const commandResult = executeRequirementGapCheckCommand(["--project-root", root]);
    const commandOutput = JSON.parse(commandResult.stdout) as typeof apiResult;
    const statusesByCapability = Object.fromEntries(
      apiResult.artifact.capabilityMappings.map((capability) => [capability.id, capability.status]),
    );
    const evidenceByCapability = Object.fromEntries(
      apiResult.artifact.capabilityMappings.map((capability) => [capability.id, capability.evidenceSourceIds]),
    );
    const missingReadmeRequirements = apiResult.artifact.capabilityMappings
      .filter((capability) => capability.status === "missing")
      .map((capability) => capability.readmeRequirement);
    const gapDetectedByCapability = Object.fromEntries(
      apiResult.artifact.capabilityMappings.map((capability) => [capability.id, capability.gapDetected]),
    );
    const readmeIdsByCapability = Object.fromEntries(
      apiResult.artifact.capabilityMappings.map((capability) => [capability.id, capability.readmeRequirementIds]),
    );
    const matchStatusesByRequirement = Object.fromEntries(
      apiResult.artifact.requirementCapabilityMatches.map((match) => [match.requirementId, match.status]),
    );

    assert.equal(commandResult.exitCode, 0);
    assert.equal(commandResult.stderr, "");
    assert.deepEqual(commandOutput, apiResult);
    assert.equal(apiResult.artifact.present, true);
    assert.equal(apiResult.artifact.gapReviewArtifact.present, true);
    assert.equal(apiResult.artifact.gapReviewArtifact.capabilityGapCount, 2);
    assert.equal(apiResult.artifact.gapReviewArtifact.readmeRequirementGapCount, 4);
    assert.equal(apiResult.artifact.gapReviewArtifact.nonOverlappingRequirementCoverage, true);
    assert.deepEqual(apiResult.artifact.gapReviewArtifact.duplicateCoverageKeys, []);
    assert.deepEqual(apiResult.artifact.gapReviewArtifact.overlappingRequirementIds, []);
    assert.equal(existsSync(apiResult.artifact.gapReviewArtifact.path), true);
    assert.equal(apiResult.artifact.readmeRequirementCount, 10);
    assert.deepEqual(apiResult.artifact.readmeRequirementCoverage, {
      valid: true,
      expectedCount: 10,
      mappedCount: 10,
      coveredExactlyOnce: true,
      missingRequirementIds: [],
      duplicateRequirementIds: [],
      unexpectedRequirementIds: [],
    });
    assert.equal(apiResult.artifact.implementedCount, 4);
    assert.equal(apiResult.artifact.missingCount, 2);
    assert.deepEqual(apiResult.artifact.requirementCapabilityMatchStatusCounts, {
      matched: 6,
      partial: 4,
      missing: 0,
    });
    assert.deepEqual(matchStatusesByRequirement, {
      "mvp_goal_flow:001": "matched",
      "mvp_goal_flow:002": "matched",
      "mvp_goal_flow:003": "partial",
      "mvp_goal_flow:004": "partial",
      "mvp_goal_flow:005": "partial",
      "mvp_goal_flow:006": "partial",
      "operating_principle:001": "matched",
      "operating_principle:002": "matched",
      "operating_principle:003": "matched",
      "operating_principle:004": "matched",
    });
    assert.deepEqual(statusesByCapability, {
      "request-analysis-work-breakdown": "implemented",
      "role-based-routing": "implemented",
      "openclaw-hermes-meeting-loop": "missing",
      "final-synthesis": "missing",
      "escalation-artifact": "implemented",
      "raw-storage-summary-context": "implemented",
    });
    assert.deepEqual(evidenceByCapability["openclaw-hermes-meeting-loop"], []);
    assert.deepEqual(evidenceByCapability["final-synthesis"], []);
    assert.equal(gapDetectedByCapability["openclaw-hermes-meeting-loop"], true);
    assert.equal(gapDetectedByCapability["final-synthesis"], true);
    assert.deepEqual(readmeIdsByCapability["openclaw-hermes-meeting-loop"], [
      "mvp_goal_flow:003",
      "mvp_goal_flow:004",
      "mvp_goal_flow:006",
    ]);
    assert.deepEqual(readmeIdsByCapability["final-synthesis"], ["mvp_goal_flow:005"]);
    assert.deepEqual(missingReadmeRequirements, [
      "Preserve OpenClaw execution and Hermes review turns in a meeting loop.",
      "Produce final synthesis after reviewer convergence.",
    ]);
    const gapReviewArtifact = JSON.parse(readFileSync(apiResult.artifact.gapReviewArtifact.path, "utf8"));
    assert.deepEqual(
      gapReviewArtifact.observedGaps.map((gap: any) => `${gap.kind}:${gap.requirementId}:${gap.status}`),
      [
        "classified_requirement_gap:mvp_goal_flow:003:partial",
        "classified_requirement_gap:mvp_goal_flow:004:partial",
        "classified_requirement_gap:mvp_goal_flow:005:partial",
        "classified_requirement_gap:mvp_goal_flow:006:partial",
      ],
    );
    assert.deepEqual(
      Object.fromEntries(gapReviewArtifact.observedGaps.map((gap: any) => [gap.requirementId, gap.missingCapabilityIds])),
      {
        "mvp_goal_flow:003": ["openclaw-hermes-meeting-loop"],
        "mvp_goal_flow:004": ["openclaw-hermes-meeting-loop"],
        "mvp_goal_flow:005": ["final-synthesis"],
        "mvp_goal_flow:006": ["openclaw-hermes-meeting-loop"],
      },
    );
    const coverageKeys = gapReviewArtifact.observedGaps.flatMap((gap: any) => gap.coverageKeys);
    const coveredRequirementIds = gapReviewArtifact.observedGaps.map((gap: any) => gap.requirementId);
    assert.equal(new Set(coverageKeys).size, coverageKeys.length);
    assert.equal(new Set(coveredRequirementIds).size, coveredRequirementIds.length);
    assert.deepEqual(gapReviewArtifact.summary, {
      observedGapCount: 4,
      capabilityGapCount: 2,
      readmeRequirementGapCount: 4,
      nonOverlappingRequirementCoverage: true,
      duplicateCoverageKeys: [],
      overlappingRequirementIds: [],
      sourceCapabilityCount: 6,
      sourceReadmeRequirementCount: 10,
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("requirement-gap check does not classify the same extracted README requirement as both implemented and missing", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-requirement-gap-disjoint-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(
      join(root, "README.md"),
      [
        "# AI_Agent",
        "",
        "## MVP 목표",
        "",
        "```text",
        "parent channel user request",
        "  -> task 생성",
        "  -> OpenClaw owner draft",
        "  -> Hermes review",
        "  -> OpenClaw final synthesis",
        "  -> thread timeline 게시",
        "```",
        "",
        "## 운영 원칙",
        "",
        "- OpenClaw = orchestrator / owner / finalizer",
        "- Hermes = reviewer-only, mention/reply when requested",
        "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
        "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
        "",
      ].join("\n"),
    );
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(
      join(root, "docs", "review-evidence.json"),
      `${JSON.stringify(
        {
          schemaVersion: "review-evidence.v1",
          summary: {
            findingsByCategory: {
              error_frequency: 1,
              maintainability: 1,
              token_cost: 1,
              architecture_fit: 0,
              feature_completeness: 1,
            },
          },
        },
        null,
        2,
      )}\n`,
    );
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "types.ts"), "export type AgentRole = string;\n");
    writeRequirementGapMappingFixture(root);

    const apiResult = checkRequirementGapMapping(root);
    const commandResult = executeRequirementGapCheckCommand(["--project-root", root]);
    const commandOutput = JSON.parse(commandResult.stdout) as typeof apiResult;
    const statusesByRequirement = new Map<string, Set<string>>();

    for (const capability of commandOutput.artifact.capabilityMappings) {
      const requirement = capability.readmeRequirement.trim();
      const statuses = statusesByRequirement.get(requirement) ?? new Set<string>();
      statuses.add(capability.status);
      statusesByRequirement.set(requirement, statuses);
    }

    assert.equal(commandResult.exitCode, 0);
    assert.deepEqual(commandOutput, apiResult);
    for (const [requirement, statuses] of statusesByRequirement) {
      assert.equal(
        statuses.size,
        1,
        `${requirement} should be classified as implemented or missing, not both`,
      );
    }
    assert.deepEqual(
      commandOutput.artifact.capabilityMappings
        .filter((capability) => capability.readmeRequirement === "Route work items to OpenClaw owner/finalizer and Hermes reviewer personas.")
        .map((capability) => capability.status),
      ["implemented"],
    );
    assert.deepEqual(
      commandOutput.artifact.capabilityMappings
        .filter((capability) => capability.readmeRequirement === "Produce final synthesis after reviewer convergence.")
        .map((capability) => capability.status),
      ["missing"],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("requirement-gap review artifact detects duplicate or overlapping requirement coverage", () => {
  const root = mkdtempSync(join(tmpdir(), "ai-agent-requirement-gap-overlap-"));
  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(
      join(root, "README.md"),
      [
        "# AI_Agent",
        "",
        "## MVP 목표",
        "",
        "```text",
        "parent channel user request",
        "  -> OpenClaw owner draft",
        "```",
      ].join("\n"),
    );
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    const mapping = loadDiagnosisReportArtifact({ projectRoot: root }).requirementToGapMappingArtifact;
    const firstGapMatch = mapping.requirementCapabilityMatches.find(
      (match) => match.requirementId === "mvp_goal_flow:002",
    );
    assert.ok(firstGapMatch);

    const overlappedMapping = {
      ...mapping,
      requirementCapabilityMatches: [
        {
          ...firstGapMatch,
          id: "requirement-capability-match:mvp_goal_flow:002:duplicate",
          status: "partial" as const,
          capabilityIds: ["openclaw-hermes-meeting-loop"],
          evidenceSourceIds: [],
        },
        {
          ...firstGapMatch,
          status: "partial" as const,
          capabilityIds: ["openclaw-hermes-meeting-loop"],
          evidenceSourceIds: [],
        },
      ],
      summary: {
        ...mapping.summary,
        readmeRequirementCount: 2,
      },
    };

    const artifact = buildRequirementGapReviewArtifact({
      mapping: overlappedMapping,
      mappingArtifactPath: join(root, "docs", "generated", "requirement-gap-mapping.json"),
      diagnosisReportPath: join(root, "docs", "diagnosis-report.md"),
      readmePath: join(root, "README.md"),
    });

    assert.equal(artifact.summary.nonOverlappingRequirementCoverage, false);
    assert.deepEqual(artifact.summary.duplicateCoverageKeys, [
      "mvp_goal_flow:002::openclaw-hermes-meeting-loop",
    ]);
    assert.deepEqual(artifact.summary.overlappingRequirementIds, ["mvp_goal_flow:002"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("requirement-gap coverage validation proves each extracted README requirement appears exactly once", () => {
  const readme = [
    "# AI_Agent",
    "",
    "## MVP 목표",
    "",
    "```text",
    "parent channel user request",
    "  -> task 생성",
    "  -> OpenClaw owner draft",
    "```",
    "",
    "## 운영 원칙",
    "",
    "- Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
    "",
  ].join("\n");
  const readmeRequirements = extractReadmeMvpRequirementList(readme);
  const root = mkdtempSync(join(tmpdir(), "ai-agent-requirement-coverage-"));

  try {
    mkdirSync(join(root, "docs"), { recursive: true });
    mkdirSync(join(root, "src"), { recursive: true });
    writeFileSync(join(root, "README.md"), readme);
    writeFileSync(join(root, "docs", "diagnosis-report.md"), "Recommendation: **partial redesign**.\n");
    writeFileSync(join(root, "src", "planning.ts"), "export function analyzeUserRequest() {}\n");
    writeFileSync(join(root, "src", "orchestrator.ts"), "export class CompanyOrchestrator {}\n");
    writeFileSync(join(root, "src", "db.ts"), "export class AiAgentDatabase {}\n");
    writeRequirementGapMappingFixture(root);

    const mappings = loadDiagnosisReportArtifact({ projectRoot: root }).requirementToGapMappingArtifact.readmeRequirementMappings;
    const validCoverage = validateReadmeRequirementCoverage({ readmeRequirements, mappings });
    const duplicateCoverage = validateReadmeRequirementCoverage({
      readmeRequirements,
      mappings: [...mappings, mappings[0]],
    });
    const missingCoverage = validateReadmeRequirementCoverage({
      readmeRequirements,
      mappings: mappings.slice(1),
    });

    assert.deepEqual(validCoverage, {
      valid: true,
      expectedCount: 4,
      mappedCount: 4,
      coveredExactlyOnce: true,
      missingRequirementIds: [],
      duplicateRequirementIds: [],
      unexpectedRequirementIds: [],
    });
    assert.deepEqual(duplicateCoverage, {
      valid: false,
      expectedCount: 4,
      mappedCount: 5,
      coveredExactlyOnce: false,
      missingRequirementIds: [],
      duplicateRequirementIds: ["mvp_goal_flow:001"],
      unexpectedRequirementIds: [],
    });
    assert.deepEqual(missingCoverage, {
      valid: false,
      expectedCount: 4,
      mappedCount: 3,
      coveredExactlyOnce: false,
      missingRequirementIds: ["mvp_goal_flow:001"],
      duplicateRequirementIds: [],
      unexpectedRequirementIds: [],
    });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function writeRequirementGapMappingFixture(root: string): void {
  const capabilityArtifact = loadDiagnosisReportArtifact({ projectRoot: root }).requirementToGapMappingArtifact;

  mkdirSync(join(root, "docs", "generated"), { recursive: true });
  writeFileSync(join(root, "docs", "generated", "requirement-gap-mapping.json"), `${JSON.stringify(capabilityArtifact, null, 2)}\n`);
}
