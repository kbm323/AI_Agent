import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildContextStorageBoundaryArtifact,
  demonstrateContextStorageAccessPaths,
  retrieveLoopVisibleContext,
  verifyContextStorageBoundary,
} from "../src/context-storage.ts";

export interface ContextStorageBoundaryCheckResult {
  command: "ai-agent check-context-storage-boundary";
  status: "passed" | "failed";
  artifact: {
    path: string;
    present: boolean;
    schemaVersion: string;
    missingSections: string[];
  };
  verification: ReturnType<typeof verifyContextStorageBoundary>;
  retrieval: {
    schemaVersion: ReturnType<typeof retrieveLoopVisibleContext>["schemaVersion"];
    compressedContextSchemaVersion: ReturnType<typeof retrieveLoopVisibleContext>["compressedLoopContext"]["schemaVersion"];
    rawTurnCount: number;
    rawOriginalTextRetained: boolean;
    meetingHistorySummaryOnly: boolean;
    compressedContextHiddenFromRawText: boolean;
  };
  accessPaths: ReturnType<typeof demonstrateContextStorageAccessPaths>;
}

const REQUIRED_SECTIONS = [
  "Source Of Truth",
  "Loop Visible Fields",
  "Audit Only Fields",
  "Invariants",
  "Verification Checks",
];

export function checkContextStorageBoundary(
  projectRoot = process.cwd(),
  artifactPath = "docs/context-storage-boundary.md",
): ContextStorageBoundaryCheckResult {
  const resolvedPath = resolve(projectRoot, artifactPath);
  const present = existsSync(resolvedPath);
  const missingSections = present ? findMissingSections(readFileSync(resolvedPath, "utf8")) : REQUIRED_SECTIONS;
  const turns = [
    {
      id: "context-boundary-turn-1",
      round: 1,
      role: "openclaw-owner" as const,
      kind: "owner_draft" as const,
      content: "RAW_ORIGINAL_TEXT::complete OpenClaw execution draft with private audit notes",
      visibleSummary: "OpenClaw execution draft summary.",
    },
    {
      id: "context-boundary-turn-2",
      round: 1,
      role: "hermes-reviewer" as const,
      kind: "review" as const,
      content: "RAW_ORIGINAL_TEXT::complete Hermes review with detailed critique",
      visibleSummary: "Hermes review summary.",
    },
  ];
  const retrieval = retrieveLoopVisibleContext({
    userRequestSummary: "Build the virtual-company multi-agent meeting MVP.",
    turns,
    acceptedFeedback: ["Keep summarized loop context separate from raw storage."],
    rejectedFeedback: ["Replay raw original text in the next review prompt."],
    escalationReasons: [],
  });
  const retrievalLoopVisibleContext = [
    ...retrieval.meetingHistory.map((turn) => turn.summary),
    retrieval.compressedLoopContext.content,
  ].join("\n");
  const verification = verifyContextStorageBoundary({
    turns,
    loopVisibleContext: [
      "Meeting history",
      retrievalLoopVisibleContext,
    ].join("\n"),
  });
  const rawTexts = turns.map((turn) => turn.content);
  const meetingHistorySummaryOnly = retrieval.meetingHistory.every((turn) => !Object.hasOwn(turn, "content"));
  const compressedContextHiddenFromRawText = rawTexts.every(
    (rawText) => !retrieval.compressedLoopContext.content.includes(rawText),
  );
  const accessPaths = demonstrateContextStorageAccessPaths({
    userRequestSummary: "Build the virtual-company multi-agent meeting MVP.",
    turn: turns[0],
  });
  const retrievalPassed =
    retrieval.rawOriginalTextRetained && meetingHistorySummaryOnly && compressedContextHiddenFromRawText;
  const accessPathsPassed =
    accessPaths.rawOriginalTextRetainedExactly &&
    accessPaths.loopVisibleSummaryRetrievedExactly &&
    accessPaths.observablyDifferentValues &&
    accessPaths.separateAccessPaths &&
    accessPaths.rawHiddenFromLoopVisiblePath;
  const status =
    present && missingSections.length === 0 && verification.passed && retrievalPassed && accessPathsPassed
      ? "passed"
      : "failed";

  return {
    command: "ai-agent check-context-storage-boundary",
    status,
    artifact: {
      path: resolvedPath,
      present,
      schemaVersion: buildContextStorageBoundaryArtifact().schemaVersion,
      missingSections,
    },
    verification,
    retrieval: {
      schemaVersion: retrieval.schemaVersion,
      compressedContextSchemaVersion: retrieval.compressedLoopContext.schemaVersion,
      rawTurnCount: retrieval.rawTurnCount,
      rawOriginalTextRetained: retrieval.rawOriginalTextRetained,
      meetingHistorySummaryOnly,
      compressedContextHiddenFromRawText,
    },
    accessPaths,
  };
}

export function executeContextStorageBoundaryCheckCommand(args: string[]): {
  exitCode: number;
  stdout: string;
  stderr: string;
} {
  try {
    const projectRoot = readArgValue(args, "--project-root") ?? process.cwd();
    const artifactPath = readArgValue(args, "--artifact") ?? "docs/context-storage-boundary.md";
    const result = checkContextStorageBoundary(projectRoot, artifactPath);

    return {
      exitCode: result.status === "passed" ? 0 : 1,
      stdout: `${JSON.stringify(result, null, 2)}\n`,
      stderr: "",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown context-storage check failure";
    return {
      exitCode: 2,
      stdout: "",
      stderr: `${JSON.stringify({ error: "invalid_input", message }, null, 2)}\n`,
    };
  }
}

function findMissingSections(markdown: string): string[] {
  return REQUIRED_SECTIONS.filter((section) => !hasMarkdownSection(markdown, section));
}

function hasMarkdownSection(markdown: string, section: string): boolean {
  const escapedSection = section.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^##\\s+${escapedSection}\\s*$`, "m").test(markdown);
}

function readArgValue(args: string[], flag: string): string | undefined {
  const flagIndex = args.indexOf(flag);
  if (flagIndex === -1) return undefined;
  const value = args[flagIndex + 1] ?? "";
  if (value.trim().length === 0) {
    throw new TypeError(`${flag} must be followed by a non-empty value`);
  }
  return value;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = executeContextStorageBoundaryCheckCommand(process.argv.slice(2));
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}
