import { execFile } from "node:child_process";
import { printGatewayStatus } from "./live-process.ts";

await printGatewayStatus();

const aiAgent = await new Promise<string>((resolve) => {
  execFile("pgrep", ["-af", "node scripts/(start-discord|live-start).ts"], (error, stdout) => {
    resolve(error ? "" : stdout.trim());
  });
});

console.log("AI_Agent runtime:");
console.log(aiAgent || "inactive");
