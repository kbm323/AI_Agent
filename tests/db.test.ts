import test from "node:test";
import assert from "node:assert/strict";
import { AiAgentDatabase } from "../src/db.ts";

test("createTask initializes a meeting session record with stable primary fields", () => {
  const db = new AiAgentDatabase();

  const task = db.createTask({
    id: "task-session-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-1",
    userRequest: "신제품 영상 제작 회의를 진행해줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  assert.deepEqual(task, {
    id: "task-session-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-1",
    userRequest: "신제품 영상 제작 회의를 진행해줘.",
    status: "created",
    createdAt: "2026-06-05T01:02:03.000Z",
    updatedAt: "2026-06-05T01:02:03.000Z",
  });
  assert.deepEqual(db.getTask("task-session-1"), task);

  db.close();
});

test("database default timestamps are controlled through runtime timestamp generation", () => {
  const db = new AiAgentDatabase();

  const task = db.createTask({
    id: "task-runtime-timestamp-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-runtime-timestamp-1",
    userRequest: "런타임 타임스탬프 기본값을 저장해줘.",
  });
  const turn = db.insertTurn({
    id: "turn-runtime-timestamp-1",
    taskId: task.id,
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "RAW draft.",
    visibleSummary: "Draft summary.",
  });
  const decision = db.insertDecision({
    id: "decision-runtime-timestamp-1",
    taskId: task.id,
    requiresUserDecision: false,
    reasons: [],
  });

  assert.match(task.createdAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  assert.equal(task.updatedAt, task.createdAt);
  assert.match(turn.createdAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  assert.match(decision.createdAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);

  db.close();
});

test("database rejects malformed injected runtime timestamps", () => {
  const db = new AiAgentDatabase();

  assert.throws(
    () =>
      db.createTask({
        id: "task-invalid-runtime-timestamp-1",
        projectChannelId: "project-channel-1",
        threadId: "meeting-thread-invalid-runtime-timestamp-1",
        userRequest: "잘못된 타임스탬프를 거부해줘.",
        now: "2026-06-05 01:02:03",
      }),
    /runtime timestamp must be an ISO-8601 UTC timestamp/,
  );

  db.close();
});

test("insertTurn appends preserved meeting loop content for an initialized session", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "task-session-2",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-2",
    userRequest: "브랜드 캠페인 회의안을 만들어줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  const turn = db.insertTurn({
    id: "turn-1",
    taskId: "task-session-2",
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "Full OpenClaw draft with raw details that remain available for audit.",
    visibleSummary: "OpenClaw draft summary for loop context.",
    createdAt: "2026-06-05T01:03:04.000Z",
  });

  assert.deepEqual(turn, {
    id: "turn-1",
    taskId: "task-session-2",
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "Full OpenClaw draft with raw details that remain available for audit.",
    visibleSummary: "OpenClaw draft summary for loop context.",
    createdAt: "2026-06-05T01:03:04.000Z",
  });
  assert.deepEqual(db.getTurns("task-session-2"), [turn]);

  db.close();
});

test("updateTaskStatus persists deterministic task status transitions", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "task-status-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-status-1",
    userRequest: "회의 상태 전이를 저장해줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  db.updateTaskStatus("task-status-1", "reviewed", "2026-06-05T01:05:06.000Z");

  assert.deepEqual(db.getTask("task-status-1"), {
    id: "task-status-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-status-1",
    userRequest: "회의 상태 전이를 저장해줘.",
    status: "reviewed",
    createdAt: "2026-06-05T01:02:03.000Z",
    updatedAt: "2026-06-05T01:05:06.000Z",
  });

  db.close();
});

test("insertDecision stores escalation decision reasons as stable JSON", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "task-decision-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-decision-1",
    userRequest: "사용자 승인이 필요한지 판단해줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  const decision = db.insertDecision({
    id: "decision-1",
    taskId: "task-decision-1",
    requiresUserDecision: true,
    reasons: ["ambiguous_scope", "brand_or_public_release"],
    createdAt: "2026-06-05T01:06:07.000Z",
  });
  const row = db.db.prepare(`SELECT * FROM decisions WHERE id = ?`).get("decision-1") as {
    id: string;
    task_id: string;
    requires_user_decision: number;
    reasons_json: string;
    created_at: string;
  };

  assert.deepEqual(decision, {
    id: "decision-1",
    taskId: "task-decision-1",
    requiresUserDecision: true,
    reasons: ["ambiguous_scope", "brand_or_public_release"],
    createdAt: "2026-06-05T01:06:07.000Z",
  });
  assert.deepEqual({ ...row }, {
    id: "decision-1",
    task_id: "task-decision-1",
    requires_user_decision: 1,
    reasons_json: "[\"ambiguous_scope\",\"brand_or_public_release\"]",
    created_at: "2026-06-05T01:06:07.000Z",
  });

  db.close();
});

test("insertTurn preserves ordered OpenClaw execution persona turns with raw content separated from loop summaries", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "task-openclaw-loop-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-openclaw-1",
    userRequest: "광고 제작 회의에서 기획안 초안과 최종안을 만들어줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  const firstTurn = db.insertTurn({
    id: "turn-openclaw-draft-1",
    taskId: "task-openclaw-loop-1",
    round: 1,
    role: "openclaw-owner",
    kind: "owner_draft",
    content: "RAW OpenClaw owner draft with full research notes, constraints, rejected options, and implementation details.",
    visibleSummary: "OpenClaw owner draft summary for meeting loop context.",
    createdAt: "2026-06-05T01:03:04.000Z",
  });
  const secondTurn = db.insertTurn({
    id: "turn-openclaw-final-1",
    taskId: "task-openclaw-loop-1",
    round: 2,
    role: "openclaw-finalizer",
    kind: "final_synthesis",
    content: "RAW OpenClaw final synthesis with the complete selected campaign plan and audit trail.",
    visibleSummary: "OpenClaw final synthesis summary for meeting loop context.",
    createdAt: "2026-06-05T01:04:05.000Z",
  });

  const turns = db.getTurns("task-openclaw-loop-1");

  assert.deepEqual(turns, [firstTurn, secondTurn]);
  assert.deepEqual(
    turns.map((turn) => [turn.role, turn.kind, turn.round]),
    [
      ["openclaw-owner", "owner_draft", 1],
      ["openclaw-finalizer", "final_synthesis", 2],
    ],
  );
  assert.match(turns[0].content, /full research notes/);
  assert.equal(turns[0].visibleSummary.includes("full research notes"), false);
  assert.match(turns[1].content, /complete selected campaign plan/);
  assert.equal(turns[1].visibleSummary.includes("complete selected campaign plan"), false);

  db.close();
});

test("insertTurn preserves Hermes review persona turns with raw content separated from loop summaries", () => {
  const db = new AiAgentDatabase();
  db.createTask({
    id: "task-hermes-loop-1",
    projectChannelId: "project-channel-1",
    threadId: "meeting-thread-hermes-1",
    userRequest: "제작 회의에서 초안을 검토하고 개선 방향을 확정해줘.",
    now: "2026-06-05T01:02:03.000Z",
  });

  const firstReview = db.insertTurn({
    id: "turn-hermes-review-1",
    taskId: "task-hermes-loop-1",
    round: 1,
    role: "hermes-reviewer",
    kind: "review",
    content: "RAW Hermes review with detailed risks, unresolved assumptions, and required revision notes.",
    visibleSummary: "Hermes review summary for meeting loop context.",
    createdAt: "2026-06-05T01:03:04.000Z",
  });
  const secondReview = db.insertTurn({
    id: "turn-hermes-review-2",
    taskId: "task-hermes-loop-1",
    round: 2,
    role: "hermes-reviewer",
    kind: "review",
    content: "RAW Hermes review confirming the revised OpenClaw draft is ready for final synthesis.",
    visibleSummary: "Hermes agreement summary for meeting loop context.",
    createdAt: "2026-06-05T01:04:05.000Z",
  });

  const turns = db.getTurns("task-hermes-loop-1");

  assert.deepEqual(turns, [firstReview, secondReview]);
  assert.deepEqual(
    turns.map((turn) => [turn.role, turn.kind, turn.round]),
    [
      ["hermes-reviewer", "review", 1],
      ["hermes-reviewer", "review", 2],
    ],
  );
  assert.match(turns[0].content, /detailed risks/);
  assert.equal(turns[0].visibleSummary.includes("detailed risks"), false);
  assert.match(turns[1].content, /ready for final synthesis/);
  assert.equal(turns[1].visibleSummary.includes("ready for final synthesis"), false);

  db.close();
});
