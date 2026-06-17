import { DatabaseSync } from "node:sqlite";
import { createRuntimeIdentifier, createRuntimeTimestamp } from "./runtime-data.ts";
import type { DecisionRecord, TaskRecord, TaskStatus, TurnRecord } from "./types.ts";

export class AiAgentDatabase {
  readonly db: DatabaseSync;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path);
    if (path !== ":memory:") {
      this.db.exec("PRAGMA journal_mode = WAL");
    }
    this.db.exec("PRAGMA foreign_keys = ON");
    this.migrate();
  }

  close(): void {
    this.db.close();
  }

  createTask(input: { id: string; projectChannelId: string; threadId: string; userRequest: string; now?: string }): TaskRecord {
    const now = createRuntimeTimestamp(input.now);
    const task: TaskRecord = {
      id: input.id,
      projectChannelId: input.projectChannelId,
      threadId: input.threadId,
      userRequest: input.userRequest,
      status: "created",
      createdAt: now,
      updatedAt: now,
    };
    this.db.prepare(
      `INSERT INTO tasks (id, project_channel_id, thread_id, user_request, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run(task.id, task.projectChannelId, task.threadId, task.userRequest, task.status, task.createdAt, task.updatedAt);
    return task;
  }

  updateTaskStatus(id: string, status: TaskStatus, now?: string): void {
    this.db.prepare(`UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?`).run(status, createRuntimeTimestamp(now), id);
  }

  getTask(id: string): TaskRecord | undefined {
    const row = this.db.prepare(`SELECT * FROM tasks WHERE id = ?`).get(id) as StoredTask | undefined;
    return row ? mapTask(row) : undefined;
  }

  insertTurn(input: Omit<TurnRecord, "id" | "createdAt"> & { id?: string; createdAt?: string }): TurnRecord {
    const turn: TurnRecord = {
      id: input.id ?? createRuntimeIdentifier(),
      taskId: input.taskId,
      round: input.round,
      role: input.role,
      kind: input.kind,
      content: input.content,
      visibleSummary: input.visibleSummary,
      createdAt: createRuntimeTimestamp(input.createdAt),
    };
    this.db.prepare(
      `INSERT INTO turns (id, task_id, round, role, kind, content, visible_summary, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(turn.id, turn.taskId, turn.round, turn.role, turn.kind, turn.content, turn.visibleSummary, turn.createdAt);
    return turn;
  }

  getTurns(taskId: string): TurnRecord[] {
    const rows = this.db.prepare(`SELECT * FROM turns WHERE task_id = ? ORDER BY rowid ASC`).all(taskId) as StoredTurn[];
    return rows.map(mapTurn);
  }

  insertDecision(input: Omit<DecisionRecord, "id" | "createdAt"> & { id?: string; createdAt?: string }): DecisionRecord {
    const decision: DecisionRecord = {
      id: input.id ?? createRuntimeIdentifier(),
      taskId: input.taskId,
      requiresUserDecision: input.requiresUserDecision,
      reasons: input.reasons,
      createdAt: createRuntimeTimestamp(input.createdAt),
    };
    this.db.prepare(
      `INSERT INTO decisions (id, task_id, requires_user_decision, reasons_json, created_at)
       VALUES (?, ?, ?, ?, ?)`,
    ).run(decision.id, decision.taskId, decision.requiresUserDecision ? 1 : 0, JSON.stringify(decision.reasons), decision.createdAt);
    return decision;
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        project_channel_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        user_request TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        round INTEGER NOT NULL,
        role TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        visible_summary TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS decisions (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        requires_user_decision INTEGER NOT NULL,
        reasons_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
    `);
  }
}

interface StoredTask {
  id: string;
  project_channel_id: string;
  thread_id: string;
  user_request: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
}

interface StoredTurn {
  id: string;
  task_id: string;
  round: number;
  role: TurnRecord["role"];
  kind: TurnRecord["kind"];
  content: string;
  visible_summary: string;
  created_at: string;
}

function mapTask(row: StoredTask): TaskRecord {
  return {
    id: row.id,
    projectChannelId: row.project_channel_id,
    threadId: row.thread_id,
    userRequest: row.user_request,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function mapTurn(row: StoredTurn): TurnRecord {
  return {
    id: row.id,
    taskId: row.task_id,
    round: row.round,
    role: row.role,
    kind: row.kind,
    content: row.content,
    visibleSummary: row.visible_summary,
    createdAt: row.created_at,
  };
}
