import { DatabaseSync } from "node:sqlite";
import type { DecisionRecord, TaskRecord, TaskStatus, TeamRoute, TurnRecord } from "./types.ts";

export class AiAgentDatabase {
  readonly db: DatabaseSync;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.db.exec("PRAGMA foreign_keys = ON");
    this.migrate();
  }

  close(): void {
    this.db.close();
  }

  createTask(input: {
    id: string;
    projectChannelId: string;
    threadId: string;
    userRequest: string;
    teamRoute?: TeamRoute;
    now?: string;
  }): TaskRecord {
    const now = input.now ?? new Date().toISOString();
    const task: TaskRecord = {
      id: input.id,
      projectChannelId: input.projectChannelId,
      threadId: input.threadId,
      userRequest: input.userRequest,
      teamRoute: input.teamRoute ?? "content",
      status: "created",
      createdAt: now,
      updatedAt: now,
    };
    this.db.prepare(
      `INSERT INTO tasks (id, project_channel_id, thread_id, user_request, team_route, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(task.id, task.projectChannelId, task.threadId, task.userRequest, task.teamRoute, task.status, task.createdAt, task.updatedAt);
    return task;
  }

  updateTaskStatus(id: string, status: TaskStatus, now = new Date().toISOString()): void {
    this.db.prepare(`UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?`).run(status, now, id);
  }

  getTask(id: string): TaskRecord | undefined {
    const row = this.db.prepare(`SELECT * FROM tasks WHERE id = ?`).get(id) as StoredTask | undefined;
    return row ? mapTask(row) : undefined;
  }

  getTaskByThreadId(threadId: string): TaskRecord | undefined {
    const row = this.db.prepare(`SELECT * FROM tasks WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1`).get(threadId) as StoredTask | undefined;
    return row ? mapTask(row) : undefined;
  }

  insertTurn(input: Omit<TurnRecord, "id" | "createdAt"> & { id?: string; createdAt?: string }): TurnRecord {
    const turn: TurnRecord = {
      id: input.id ?? crypto.randomUUID(),
      taskId: input.taskId,
      round: input.round,
      role: input.role,
      kind: input.kind,
      content: input.content,
      visibleSummary: input.visibleSummary,
      createdAt: input.createdAt ?? new Date().toISOString(),
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
      id: input.id ?? crypto.randomUUID(),
      taskId: input.taskId,
      requiresUserDecision: input.requiresUserDecision,
      reasons: input.reasons,
      createdAt: input.createdAt ?? new Date().toISOString(),
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
        team_route TEXT NOT NULL DEFAULT 'content',
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);

    addColumnIfMissing(this.db, "tasks", "team_route", "TEXT NOT NULL DEFAULT 'content'");

    this.db.exec(`

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

      CREATE TABLE IF NOT EXISTS lore_entries (
        id TEXT PRIMARY KEY,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS brand_decisions (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        decision TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS approval_records (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        approval_type TEXT NOT NULL,
        decision TEXT NOT NULL,
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
  team_route: TeamRoute;
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
    teamRoute: row.team_route ?? "content",
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

function addColumnIfMissing(db: DatabaseSync, table: string, column: string, definition: string): void {
  const columns = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  if (columns.some((item) => item.name === column)) {
    return;
  }

  db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
}
