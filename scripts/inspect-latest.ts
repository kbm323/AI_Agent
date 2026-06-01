import "dotenv/config";
import { loadRuntimeConfig } from "../src/config.ts";
import { AiAgentDatabase } from "../src/db.ts";

const config = loadRuntimeConfig();
const db = new AiAgentDatabase(config.dbPath);

const task = db.db.prepare("SELECT * FROM tasks ORDER BY rowid DESC LIMIT 1").get() as LatestTask | undefined;
if (!task) {
  console.log("[AI_AGENT] no tasks found");
  db.close();
  process.exit(0);
}

const turns = db.getTurns(task.id);
console.log(`[AI_AGENT] latest task id=${task.id} status=${task.status} threadId=${task.thread_id}`);
console.log(`[AI_AGENT] userRequest=${preview(task.user_request, 160)}`);

for (const turn of turns) {
  console.log(
    [
      `- round=${turn.round}`,
      `role=${turn.role}`,
      `kind=${turn.kind}`,
      `contentChars=${turn.content.length}`,
      `preview=${preview(turn.content, 180)}`,
    ].join(" "),
  );
}

db.close();

interface LatestTask {
  id: string;
  thread_id: string;
  user_request: string;
  status: string;
}

function preview(value: string, maxChars: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > maxChars ? `${compact.slice(0, maxChars)}...` : compact;
}
