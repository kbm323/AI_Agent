import "dotenv/config";
import { startDiscordRuntime } from "../src/runtime.ts";
import { startGatewayServices, stopGatewayServices } from "./live-process.ts";

let restoring = false;

async function restoreAndExit(code: number): Promise<void> {
  if (restoring) {
    return;
  }
  restoring = true;
  console.log("[AI_AGENT-LIVE] restoring gateway services");
  await startGatewayServices();
  process.exit(code);
}

process.once("SIGINT", () => {
  void restoreAndExit(0);
});
process.once("SIGTERM", () => {
  void restoreAndExit(0);
});
process.once("uncaughtException", (error) => {
  console.error("[AI_AGENT-LIVE] uncaught exception", error);
  void restoreAndExit(1);
});
process.once("unhandledRejection", (error) => {
  console.error("[AI_AGENT-LIVE] unhandled rejection", error);
  void restoreAndExit(1);
});

console.log("[AI_AGENT-LIVE] preparing exclusive Discord bot tokens");
await stopGatewayServices();
console.log("[AI_AGENT-LIVE] starting AI_Agent Discord runtime");
await startDiscordRuntime();
