import { spawn } from "node:child_process";

export async function runProcess(input: { command: string; args: string[]; stdin?: string; timeoutMs?: number }): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(input.command, input.args, {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    const timer = input.timeoutMs
      ? setTimeout(() => {
          child.kill("SIGTERM");
          reject(new Error(`Command timed out after ${input.timeoutMs}ms: ${input.command}`));
        }, input.timeoutMs)
      : undefined;

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      if (timer) {
        clearTimeout(timer);
      }
      reject(error);
    });
    child.on("close", (code) => {
      if (timer) {
        clearTimeout(timer);
      }
      if (code !== 0) {
        reject(new Error(`Command failed with code ${code}: ${stderr.trim() || stdout.trim()}`));
        return;
      }
      resolve(stdout.trim());
    });

    child.stdin.end(input.stdin ?? "");
  });
}
