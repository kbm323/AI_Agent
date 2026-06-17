import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  extractReadmeMvpRequirementList,
  parseReadmeDerivedMvpRequirements,
  parseReadmeMvpRequirements,
  validateReadmeMvpRequirementExtraction,
} from "../src/inspection.ts";

test("README MVP requirement parser produces a structured requirement list from the project README", () => {
  const readme = readFileSync(join(process.cwd(), "README.md"), "utf8");
  const extraction = parseReadmeMvpRequirements(readme);

  assert.equal(extraction.schemaVersion, "readme-mvp-requirements.v1");
  assert.deepEqual(extraction.source, {
    document: "README.md",
    sections: ["## MVP 목표", "## 운영 원칙", "## 실행", "## Public API"],
  });
  assert.deepEqual(extraction.summary.countByCategory, {
    mvp_goal_flow: 9,
    operating_principle: 6,
    execution_command: 2,
    public_api_symbol: 16,
  });
  assert.equal(extraction.summary.totalCount, 33);
  assert.deepEqual(
    extraction.requirements.slice(0, 9).map((requirement) => requirement.text),
    [
      "parent channel user request",
      "-> task 생성",
      "-> Discord thread 생성",
      '-> parent에는 "Agent discussion started -> <thread>"만 게시',
      "-> OpenClaw owner draft",
      "-> Hermes reviewer request",
      "-> Hermes review",
      "-> OpenClaw final synthesis",
      "-> thread timeline 게시",
    ],
  );
  assert.deepEqual(
    extraction.requirements
      .filter((requirement) => requirement.category === "operating_principle")
      .map((requirement) => requirement.text),
    [
      "Channel = project",
      "Thread = task",
      "OpenClaw = orchestrator / owner / finalizer",
      "Hermes = reviewer-only, mention/reply when requested",
      "Thread에는 요약 timeline을 남기고, 전문은 SQLite에 저장한다.",
      "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
    ],
  );
  assert.deepEqual(
    extraction.requirements
      .filter((requirement) => requirement.category === "execution_command")
      .map((requirement) => requirement.text),
    [
      "npm test",
      'npm run dry-run -- --request "뮤직비디오 오프닝 아이디어를 회의해줘."',
    ],
  );
  assert.deepEqual(
    extraction.requirements
      .filter((requirement) => requirement.category === "public_api_symbol")
      .map((requirement) => requirement.text),
    [
      "AiAgentDatabase",
      "CompanyOrchestrator",
      "ExecPersona",
      "ReviewPersona",
      "analyzeUserRequest",
      "buildCompressedLoopContextArtifact",
      "buildDefaultTokenStrategy",
      "buildReviewerRequest",
      "buildRoleRoutes",
      "buildTaskGraph",
      "buildThreadName",
      "decomposeUserRequest",
      "run_execution",
      "run_review",
      "serializeEscalationResult",
      "summarizeForThread",
    ],
  );
  assert.equal(extraction.requirements.at(0)?.id, "mvp_goal_flow:001");
  assert.equal(extraction.requirements.at(-1)?.id, "public_api_symbol:016");
  assert.deepEqual(validateReadmeMvpRequirementExtraction(extraction), {
    valid: true,
    errors: [],
    computed: {
      totalCount: 33,
      countByCategory: {
        mvp_goal_flow: 9,
        operating_principle: 6,
        execution_command: 2,
        public_api_symbol: 16,
      },
      sections: ["## MVP 목표", "## 운영 원칙", "## 실행", "## Public API"],
    },
  });
});

test("README MVP requirement parser remains the source for derived and flat requirement APIs", () => {
  const readme = [
    "# AI_Agent",
    "",
    "## MVP 목표",
    "",
    "```text",
    "parent channel user request",
    "  -> task 생성",
    "  -> Hermes review",
    "```",
    "",
    "## 운영 원칙",
    "",
    "- OpenClaw = orchestrator / owner / finalizer",
    "- 사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
    "",
    "## 실행",
    "",
    "```bash",
    "npm test",
    "```",
    "",
    "## Public API",
    "",
    "```ts",
    'import { CompanyOrchestrator, analyzeUserRequest } from "ai-agent";',
    "```",
    "",
  ].join("\n");

  const derived = parseReadmeDerivedMvpRequirements(readme);
  const flat = extractReadmeMvpRequirementList(readme);
  const structured = parseReadmeMvpRequirements(readme);

  assert.deepEqual(derived, {
    mvpGoalFlow: ["parent channel user request", "-> task 생성", "-> Hermes review"],
    operatingPrinciples: [
      "OpenClaw = orchestrator / owner / finalizer",
      "사용자 결정이 필요한 경우에는 진행을 멈추고 escalation을 남긴다.",
    ],
    executionCommands: ["npm test"],
    publicApiSymbols: ["CompanyOrchestrator", "analyzeUserRequest"],
  });
  assert.deepEqual(flat, structured.requirements);
  assert.deepEqual(structured.summary.countByCategory, {
    mvp_goal_flow: 3,
    operating_principle: 2,
    execution_command: 1,
    public_api_symbol: 2,
  });
});

test("README MVP requirement artifact validator fails malformed structured lists", () => {
  const extraction = parseReadmeMvpRequirements(
    [
      "# AI_Agent",
      "",
      "## MVP 목표",
      "",
      "```text",
      "parent channel user request",
      "  -> task 생성",
      "```",
      "",
      "## 운영 원칙",
      "",
      "- Channel = project",
      "",
    ].join("\n"),
  );

  const malformed = {
    ...extraction,
    source: {
      ...extraction.source,
      sections: ["## MVP 목표"],
    },
    requirements: extraction.requirements.map((requirement, index) =>
      index === 1
        ? {
            ...requirement,
            id: "mvp_goal_flow:999",
            order: 99,
            text: "",
          }
        : requirement,
    ),
    summary: {
      totalCount: 999,
      countByCategory: {
        ...extraction.summary.countByCategory,
        mvp_goal_flow: 999,
      },
    },
  };

  const validation = validateReadmeMvpRequirementExtraction(malformed);

  assert.equal(validation.valid, false);
  assert.deepEqual(validation.computed, {
    totalCount: 3,
    countByCategory: {
      mvp_goal_flow: 2,
      operating_principle: 1,
      execution_command: 0,
      public_api_symbol: 0,
    },
    sections: ["## MVP 목표", "## 운영 원칙"],
  });
  assert.deepEqual(validation.errors, [
    "source.sections must match sections derived from requirements",
    "summary.totalCount must match requirement count",
    "summary.countByCategory.mvp_goal_flow must match derived count",
    "mvp_goal_flow:999 must use stable id mvp_goal_flow:002",
    "mvp_goal_flow:999 must use stable order 2",
    "mvp_goal_flow:999 must include non-empty text",
  ]);
});

test("README MVP requirement artifact validator rejects incomplete canonical requirement records", () => {
  const incompleteExtraction = {
    schemaVersion: "readme-mvp-requirements.v1",
    source: {
      document: "README.md",
      sections: ["## MVP 목표"],
    },
    requirements: [
      {
        id: "mvp_goal_flow:001",
        category: "mvp_goal_flow",
        sourceSection: "## MVP 목표",
        order: 1,
        text: "parent channel user request",
      },
      {
        id: "mvp_goal_flow:002",
      },
      null,
    ],
    summary: {
      totalCount: 3,
      countByCategory: {
        mvp_goal_flow: 3,
        operating_principle: 0,
        execution_command: 0,
        public_api_symbol: 0,
      },
    },
  };

  const validation = validateReadmeMvpRequirementExtraction(incompleteExtraction);

  assert.equal(validation.valid, false);
  assert.deepEqual(validation.computed, {
    totalCount: 3,
    countByCategory: {
      mvp_goal_flow: 1,
      operating_principle: 0,
      execution_command: 0,
      public_api_symbol: 0,
    },
    sections: ["## MVP 목표"],
  });
  assert.deepEqual(validation.errors, [
    "summary.countByCategory.mvp_goal_flow must match derived count",
    "mvp_goal_flow:002.category must be one of mvp_goal_flow, operating_principle, execution_command, public_api_symbol",
    "mvp_goal_flow:002.sourceSection must be a non-empty string",
    "mvp_goal_flow:002.order must be a positive integer",
    "mvp_goal_flow:002 must include non-empty text",
    "requirements[2] must be an object",
  ]);
});

test("README MVP requirement parser returns an empty structured list when MVP sections are absent", () => {
  const extraction = parseReadmeMvpRequirements("# AI_Agent\n\nNo MVP contract here.\n");

  assert.deepEqual(extraction.requirements, []);
  assert.deepEqual(extraction.source.sections, []);
  assert.deepEqual(extraction.summary, {
    totalCount: 0,
    countByCategory: {
      mvp_goal_flow: 0,
      operating_principle: 0,
      execution_command: 0,
      public_api_symbol: 0,
    },
  });
});
