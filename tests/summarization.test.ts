import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCompressedLoopContextArtifact,
  compactPromptContext,
  summarizeMeetingHistory,
  summarizeMeetingTurn,
} from "../src/index.ts";

test("summarizeMeetingTurn produces a bounded visible summary for one public turn", () => {
  const summary = summarizeMeetingTurn(
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      content: "  OpenClaw draft\n\n\n\nKeep the launch checklist and Hermes review gate in the visible loop context.  ",
    },
    64,
  );

  assert.deepEqual(summary, {
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    summary: "OpenClaw draft\n\nKeep the launch checklist and Hermes review gat…",
  });
  assert.equal(summary.summary.length, 64);
});

test("summarizeMeetingHistory converts raw meeting turns into stable summary-only output", () => {
  const summaries = summarizeMeetingHistory(
    [
      {
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: "Owner draft: raw implementation notes stay in storage; summary carries checklist.",
      },
      {
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        content: "Hermes review: agree. The draft has enough criteria for final synthesis.",
      },
    ],
    80,
  );

  assert.deepEqual(summaries, [
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      summary: "Owner draft: raw implementation notes stay in storage; summary carries checklis…",
    },
    {
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      summary: "Hermes review: agree. The draft has enough criteria for final synthesis.",
    },
  ]);
  assert.equal(Object.hasOwn(summaries[0], "content"), false);
});

test("buildCompressedLoopContextArtifact exposes latest summaries and convergence metadata", () => {
  const meetingTurns = summarizeMeetingHistory(
    [
      {
        round: 1,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: "OpenClaw draft: define request analysis, routing, and final synthesis steps.",
      },
      {
        round: 1,
        role: "hermes-reviewer",
        kind: "review",
        content: "Hermes review: disagree. Add escalation criteria before final synthesis.",
      },
      {
        round: 2,
        role: "openclaw-owner",
        kind: "owner_draft",
        content: "OpenClaw draft round 2: adds escalation criteria and compressed context handoff.",
      },
      {
        round: 2,
        role: "hermes-reviewer",
        kind: "review",
        content: "Hermes review: agree. Escalation and summary handoff are clear.",
      },
    ],
    120,
  );

  const artifact = buildCompressedLoopContextArtifact({
    userRequestSummary: "가상 회사형 멀티 에이전트 제작 회의 MVP를 검토해줘.",
    meetingTurns,
    acceptedFeedback: ["Add escalation criteria before final synthesis."],
    rejectedFeedback: ["Replay every raw model turn in loop prompts."],
    escalationReasons: [],
  });

  assert.deepEqual(artifact, {
    schemaVersion: "compressed-loop-context.v1",
    requestSummary: "가상 회사형 멀티 에이전트 제작 회의 MVP를 검토해줘.",
    latestOpenClawSummary: "OpenClaw draft round 2: adds escalation criteria and compressed context handoff.",
    latestHermesSummary: "Hermes review: agree. Escalation and summary handoff are clear.",
    latestHermesVerdict: "agree",
    acceptedFeedback: ["Add escalation criteria before final synthesis."],
    rejectedFeedback: ["Replay every raw model turn in loop prompts."],
    escalationReasons: [],
    content: [
      "Compressed loop context",
      "- request_summary: 가상 회사형 멀티 에이전트 제작 회의 MVP를 검토해줘.",
      "- latest_openclaw: OpenClaw draft round 2: adds escalation criteria and compressed context handoff.",
      "- latest_hermes_verdict: agree",
      "- latest_hermes: Hermes review: agree. Escalation and summary handoff are clear.",
      "- accepted_feedback: Add escalation criteria before final synthesis.",
      "- rejected_feedback: Replay every raw model turn in loop prompts.",
      "- escalation_reasons: none",
    ].join("\n"),
  });
});

test("compactPromptContext removes prompt echoes and compresses eligible meeting turns", () => {
  const openClawDraft =
    "OpenClaw owner draft: implement request analysis, task breakdown, role-based routing, Hermes review loop, final synthesis, and escalation. ".repeat(
      3,
    );
  const hermesReview =
    "Hermes review: agree_with_changes. Preserve raw full-text storage, expose only compressed loop context summaries, and avoid replaying duplicate prior round text. ".repeat(
      3,
    );
  const result = compactPromptContext(
    [
      {
        id: "system-1",
        kind: "system",
        content: "Route work through the virtual company meeting loop.",
      },
      {
        id: "request-1",
        kind: "user_request",
        content: "Build the multi-agent meeting MVP.",
      },
      {
        id: "echo-1",
        kind: "raw_prompt_echo",
        content: "Full prompt echo that is already represented by raw storage and must not be replayed.",
      },
      {
        id: "openclaw-1",
        kind: "meeting_turn",
        role: "openclaw-owner",
        round: 1,
        content: openClawDraft,
      },
      {
        id: "scratchpad-1",
        kind: "scratchpad",
        content: "Intermediate private notes that should be excluded from visible loop context.",
      },
      {
        id: "hermes-1",
        kind: "meeting_turn",
        role: "hermes-reviewer",
        round: 1,
        content: hermesReview,
      },
    ],
    { maxSummaryChars: 96 },
  );

  assert.deepEqual(result.removedMessageIds, ["echo-1", "scratchpad-1"]);
  assert.deepEqual(result.compressedMessageIds, ["openclaw-1", "hermes-1"]);
  assert.equal(result.originalMessageCount, 6);
  assert.equal(result.compactedMessageCount, 4);
  assert.equal(result.messages.some((message) => message.id === "echo-1"), false);
  assert.equal(result.messages.some((message) => message.id === "scratchpad-1"), false);
  assert.equal(result.compactedCharCount < result.originalCharCount, true);
  assert.equal(result.messages.find((message) => message.id === "openclaw-1")?.content.length, 96);
  assert.equal(result.messages.find((message) => message.id === "hermes-1")?.content.endsWith("…"), true);
  assert.equal(result.messages.find((message) => message.id === "system-1")?.disposition, "retained");
  assert.equal(result.messages.find((message) => message.id === "request-1")?.content, "Build the multi-agent meeting MVP.");
});

test("public compression helpers produce a stable summary-only loop context artifact", () => {
  const rawTurns = [
    {
      round: 1,
      role: "openclaw-owner" as const,
      kind: "owner_draft" as const,
      content:
        "OpenClaw draft: preserve the full raw implementation transcript in storage while exposing a short checklist to Hermes.",
    },
    {
      round: 1,
      role: "hermes-reviewer" as const,
      kind: "review" as const,
      content: "Hermes review: agree_with_changes. Keep the summary boundary and add explicit escalation evidence.",
    },
    {
      round: 2,
      role: "openclaw-finalizer" as const,
      kind: "final_synthesis" as const,
      content: "Final synthesis raw text should not become the latest OpenClaw execution summary.",
    },
  ];

  const meetingTurns = summarizeMeetingHistory(rawTurns, 72);
  const compressed = buildCompressedLoopContextArtifact({
    userRequestSummary: "  Build the multi-agent meeting MVP with raw storage separated from visible loop context.  ",
    meetingTurns,
    acceptedFeedback: [
      "Keep bounded visible summaries in meeting history and route only compressed context into the next loop turn.",
    ],
    rejectedFeedback: ["Replay the complete raw transcript in every Hermes review prompt."],
    escalationReasons: ["Reviewer asked for user decision if convergence remains unclear after max rounds."],
  });

  assert.deepEqual(meetingTurns, [
    {
      round: 1,
      role: "openclaw-owner",
      kind: "owner_draft",
      summary: "OpenClaw draft: preserve the full raw implementation transcript in stor…",
    },
    {
      round: 1,
      role: "hermes-reviewer",
      kind: "review",
      summary: "Hermes review: agree_with_changes. Keep the summary boundary and add ex…",
    },
    {
      round: 2,
      role: "openclaw-finalizer",
      kind: "final_synthesis",
      summary: "Final synthesis raw text should not become the latest OpenClaw executio…",
    },
  ]);
  assert.equal(meetingTurns.every((turn) => !Object.hasOwn(turn, "content")), true);
  assert.deepEqual(compressed, {
    schemaVersion: "compressed-loop-context.v1",
    requestSummary: "Build the multi-agent meeting MVP with raw storage separated from visible loop context.",
    latestOpenClawSummary: "OpenClaw draft: preserve the full raw implementation transcript in stor…",
    latestHermesSummary: "Hermes review: agree_with_changes. Keep the summary boundary and add ex…",
    latestHermesVerdict: "agree_with_changes",
    acceptedFeedback: [
      "Keep bounded visible summaries in meeting history and route only compressed context into the next loop turn.",
    ],
    rejectedFeedback: ["Replay the complete raw transcript in every Hermes review prompt."],
    escalationReasons: ["Reviewer asked for user decision if convergence remains unclear after max rounds."],
    content: [
      "Compressed loop context",
      "- request_summary: Build the multi-agent meeting MVP with raw storage separated from visible loop context.",
      "- latest_openclaw: OpenClaw draft: preserve the full raw implementation transcript in stor…",
      "- latest_hermes_verdict: agree_with_changes",
      "- latest_hermes: Hermes review: agree_with_changes. Keep the summary boundary and add ex…",
      "- accepted_feedback: Keep bounded visible summaries in meeting history and route only compressed context into the next loop turn.",
      "- rejected_feedback: Replay the complete raw transcript in every Hermes review prompt.",
      "- escalation_reasons: Reviewer asked for user decision if convergence remains unclear after max rounds.",
    ].join("\n"),
  });
});
