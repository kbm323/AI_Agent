import { dirname, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";

export type LoopCompressionFieldMode = "retained" | "summarized" | "dropped";

export interface LoopCompressionFieldPolicy {
  path: string;
  mode: LoopCompressionFieldMode;
  rationale: string;
}

export interface LoopCompressionIterationBoundary {
  name: string;
  startsAfter: string;
  endsBefore: string;
  carriedForward: string[];
}

export interface LoopContextCompressionPolicyArtifact {
  schemaVersion: "loop-context-compression-policy.v1";
  deterministicOrdering: string[];
  retainedFields: LoopCompressionFieldPolicy[];
  summarizedFields: LoopCompressionFieldPolicy[];
  droppedFields: LoopCompressionFieldPolicy[];
  iterationBoundaries: LoopCompressionIterationBoundary[];
  validationSections: string[];
}

export interface LoopContextCompressionPolicyValidationResult {
  schemaVersion: "loop-context-compression-policy-validation.v1";
  passed: boolean;
  missingSections: string[];
  missingPolicyGroups: string[];
  deterministicOrderingValid: boolean;
  iterationBoundariesValid: boolean;
}

export interface WrittenLoopContextCompressionPolicyArtifact {
  path: string;
  artifact: LoopContextCompressionPolicyArtifact;
  markdown: string;
}

const VALIDATION_SECTIONS = [
  "Retained Fields",
  "Summarized Fields",
  "Dropped Fields",
  "Iteration Boundaries",
  "Deterministic Ordering",
] as const;

export function buildLoopContextCompressionPolicyArtifact(): LoopContextCompressionPolicyArtifact {
  return {
    schemaVersion: "loop-context-compression-policy.v1",
    retainedFields: [
      {
        path: "tasks.user_request",
        mode: "retained",
        rationale: "Keep the original user request as the replay and audit source of truth.",
      },
      {
        path: "turns.content",
        mode: "retained",
        rationale: "Keep complete OpenClaw, Hermes, final synthesis, and escalation text outside normal loop prompts.",
      },
      {
        path: "decisions.reasons",
        mode: "retained",
        rationale: "Keep exact escalation and convergence reasons for deterministic failure analysis.",
      },
    ],
    summarizedFields: [
      {
        path: "tasks.user_request_summary",
        mode: "summarized",
        rationale: "Expose a bounded summary of the request to each meeting iteration.",
      },
      {
        path: "turns.visibleSummary",
        mode: "summarized",
        rationale: "Expose role, kind, round, and bounded summary instead of raw turn content.",
      },
      {
        path: "compressedLoopContext.acceptedFeedback",
        mode: "summarized",
        rationale: "Carry only actionable Hermes feedback that OpenClaw accepted.",
      },
      {
        path: "compressedLoopContext.rejectedFeedback",
        mode: "summarized",
        rationale: "Carry only rejected feedback labels and rationale summaries to prevent repeated debate.",
      },
      {
        path: "compressedLoopContext.escalationReasons",
        mode: "summarized",
        rationale: "Carry concise blockers when convergence fails or user input is required.",
      },
    ],
    droppedFields: [
      {
        path: "turns.content.rawPromptEcho",
        mode: "dropped",
        rationale: "Prompt echoes are redundant after raw turn storage and visible summaries exist.",
      },
      {
        path: "turns.content.intermediateScratchpad",
        mode: "dropped",
        rationale: "Private scratchpad-style text must not be replayed into meeting context.",
      },
      {
        path: "duplicatePriorRoundFullText",
        mode: "dropped",
        rationale: "Older full-text rounds are represented by summaries and retained only in raw storage.",
      },
    ],
    iterationBoundaries: [
      {
        name: "request_analysis_to_openclaw",
        startsAfter: "task_breakdown_and_role_routing",
        endsBefore: "openclaw_owner_draft",
        carriedForward: ["tasks.user_request_summary", "role_routes", "active_task_ids"],
      },
      {
        name: "openclaw_to_hermes",
        startsAfter: "openclaw_owner_draft",
        endsBefore: "hermes_review",
        carriedForward: ["tasks.user_request_summary", "latest_openclaw_summary", "accepted_constraints"],
      },
      {
        name: "hermes_to_next_openclaw_or_final",
        startsAfter: "hermes_review",
        endsBefore: "next_openclaw_draft_or_final_synthesis",
        carriedForward: [
          "tasks.user_request_summary",
          "latest_openclaw_summary",
          "latest_hermes_verdict",
          "acceptedFeedback",
          "rejectedFeedback",
          "escalationReasons",
        ],
      },
    ],
    deterministicOrdering: [
      "schemaVersion",
      "retainedFields.path",
      "summarizedFields.path",
      "droppedFields.path",
      "iterationBoundaries.name",
      "validationSections",
    ],
    validationSections: [...VALIDATION_SECTIONS],
  };
}

export function validateLoopContextCompressionPolicyArtifact(
  artifact = buildLoopContextCompressionPolicyArtifact(),
  markdown = renderLoopContextCompressionPolicyMarkdown(artifact),
): LoopContextCompressionPolicyValidationResult {
  const missingSections = artifact.validationSections.filter((section) => !hasMarkdownSection(markdown, section));
  const missingPolicyGroups = [
    artifact.retainedFields.length === 0 ? "retainedFields" : "",
    artifact.summarizedFields.length === 0 ? "summarizedFields" : "",
    artifact.droppedFields.length === 0 ? "droppedFields" : "",
    artifact.iterationBoundaries.length === 0 ? "iterationBoundaries" : "",
  ].filter(Boolean);
  const deterministicOrderingValid =
    hasUniqueNonEmptyPaths(artifact.retainedFields) &&
    hasUniqueNonEmptyPaths(artifact.summarizedFields) &&
    hasUniqueNonEmptyPaths(artifact.droppedFields) &&
    hasUniqueNonEmptyNames(artifact.iterationBoundaries) &&
    artifact.deterministicOrdering.length > 0;
  const iterationBoundariesValid = artifact.iterationBoundaries.every(
    (boundary) =>
      boundary.name.trim().length > 0 &&
      boundary.startsAfter.trim().length > 0 &&
      boundary.endsBefore.trim().length > 0 &&
      boundary.carriedForward.length > 0,
  );

  return {
    schemaVersion: "loop-context-compression-policy-validation.v1",
    passed:
      missingSections.length === 0 &&
      missingPolicyGroups.length === 0 &&
      deterministicOrderingValid &&
      iterationBoundariesValid,
    missingSections,
    missingPolicyGroups,
    deterministicOrderingValid,
    iterationBoundariesValid,
  };
}

export function renderLoopContextCompressionPolicyMarkdown(
  artifact = buildLoopContextCompressionPolicyArtifact(),
): string {
  return [
    "# Loop Context Compression Policy",
    "",
    `Schema: \`${artifact.schemaVersion}\``,
    "",
    "## Retained Fields",
    "",
    ...artifact.retainedFields.map(renderFieldPolicy),
    "",
    "## Summarized Fields",
    "",
    ...artifact.summarizedFields.map(renderFieldPolicy),
    "",
    "## Dropped Fields",
    "",
    ...artifact.droppedFields.map(renderFieldPolicy),
    "",
    "## Iteration Boundaries",
    "",
    ...artifact.iterationBoundaries.map(
      (boundary) =>
        `- \`${boundary.name}\`: starts after \`${boundary.startsAfter}\`, ends before \`${boundary.endsBefore}\`, carries ${boundary.carriedForward.map((field) => `\`${field}\``).join(", ")}`,
    ),
    "",
    "## Deterministic Ordering",
    "",
    ...artifact.deterministicOrdering.map((rule) => `- \`${rule}\``),
    "",
  ].join("\n");
}

export function writeLoopContextCompressionPolicyArtifact(input: {
  projectRoot?: string;
  outputPath?: string;
} = {}): WrittenLoopContextCompressionPolicyArtifact {
  const projectRoot = input.projectRoot ?? process.cwd();
  const outputPath = input.outputPath ?? "docs/loop-context-compression-policy.md";
  const resolvedPath = resolve(projectRoot, outputPath);
  const artifact = buildLoopContextCompressionPolicyArtifact();
  const markdown = renderLoopContextCompressionPolicyMarkdown(artifact);

  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, markdown);

  return {
    path: resolvedPath,
    artifact,
    markdown,
  };
}

function renderFieldPolicy(policy: LoopCompressionFieldPolicy): string {
  return `- \`${policy.path}\` (${policy.mode}): ${policy.rationale}`;
}

function hasUniqueNonEmptyPaths(values: LoopCompressionFieldPolicy[]): boolean {
  const paths = values.map((value) => value.path.trim());
  return paths.every(Boolean) && new Set(paths).size === paths.length;
}

function hasUniqueNonEmptyNames(values: LoopCompressionIterationBoundary[]): boolean {
  const names = values.map((value) => value.name.trim());
  return names.every(Boolean) && new Set(names).size === names.length;
}

function hasMarkdownSection(markdown: string, section: string): boolean {
  const escapedSection = section.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^##\\s+${escapedSection}\\s*$`, "m").test(markdown);
}
