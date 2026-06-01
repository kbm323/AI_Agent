import { execFile, spawn } from "node:child_process";

export const gatewayServices = ["openclaw-gateway.service", "hermes-gateway.service"] as const;

export async function systemctlUser(args: string[]): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    execFile("systemctl", ["--user", ...args], (error, stdout, stderr) => {
      resolve({
        ok: !error,
        stdout: stdout.trim(),
        stderr: stderr.trim(),
      });
    });
  });
}

export async function stopGatewayServices(): Promise<void> {
  for (const service of gatewayServices) {
    const result = await systemctlUser(["stop", service]);
    console.log(`[AI_AGENT-LIVE] stop ${service}: ${result.ok ? "ok" : "failed"}`);
    if (!result.ok && result.stderr) {
      console.log(`[AI_AGENT-LIVE] ${service} stop stderr: ${result.stderr}`);
    }
  }
}

export async function startGatewayServices(): Promise<void> {
  for (const service of gatewayServices) {
    const result = await systemctlUser(["start", service]);
    console.log(`[AI_AGENT-LIVE] start ${service}: ${result.ok ? "ok" : "failed"}`);
    if (!result.ok && result.stderr) {
      console.log(`[AI_AGENT-LIVE] ${service} start stderr: ${result.stderr}`);
    }
  }
}

export async function printGatewayStatus(): Promise<void> {
  for (const service of gatewayServices) {
    const result = await systemctlUser(["is-active", service]);
    if (result.stdout) {
      console.log(`${service}: ${result.stdout}`);
      continue;
    }
    if (result.stderr) {
      console.log(`${service}: unknown (${result.stderr})`);
      continue;
    }
    console.log(`${service}: ${result.ok ? "active" : "inactive"}`);
  }
}

export async function pkillAiAgentRuntime(): Promise<void> {
  for (const pattern of ["node scripts/start-discord.ts", "node scripts/live-start.ts"]) {
    await new Promise<void>((resolve) => {
      const child = spawn("pkill", ["-f", pattern], { stdio: "ignore" });
      child.on("close", () => resolve());
      child.on("error", () => resolve());
    });
  }
}
