import { pkillAiAgentRuntime, startGatewayServices } from "./live-process.ts";

console.log("[AI_AGENT-LIVE] stopping AI_Agent Discord runtime");
await pkillAiAgentRuntime();
console.log("[AI_AGENT-LIVE] restoring gateway services");
await startGatewayServices();
