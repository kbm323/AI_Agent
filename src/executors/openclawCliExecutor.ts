import type { OpenClawCommandConfig } from "../config.ts";
import type { FinalizerExecutor, OwnerExecutor } from "../types.ts";
import { runProcess } from "./cliProcess.ts";

export class OpenClawCliExecutor implements OwnerExecutor, FinalizerExecutor {
  private readonly config: OpenClawCommandConfig;

  constructor(config: OpenClawCommandConfig) {
    this.config = config;
  }

  async createDraft(input: Parameters<OwnerExecutor["createDraft"]>[0]): Promise<string> {
    const prompt = [
      "Role: OpenClaw owner",
      `Round: ${input.round}`,
      "",
      "User request:",
      input.userRequest,
      "",
      "Create the actual OpenClaw draft. Do not return the user request unchanged.",
      "Return only the draft content.",
    ].join("\n");

    return this.runOpenClaw(prompt);
  }

  async synthesize(input: Parameters<FinalizerExecutor["synthesize"]>[0]): Promise<string> {
    const prompt = [
      "Role: OpenClaw finalizer",
      "",
      "User request:",
      input.userRequest,
      "",
      "Final source isolation:",
      "Use only the captured draft, Hermes review, and accepted/rejected feedback below.",
      "",
      "Captured OpenClaw draft:",
      input.draft,
      "",
      "Hermes review:",
      input.review,
      "",
      `Hermes verdict: ${input.reviewerVerdict}`,
      "",
      "Accepted feedback:",
      input.acceptedFeedback.join("\n") || "(none)",
      "",
      "Rejected feedback:",
      input.rejectedFeedback.join("\n") || "(none)",
      "",
      "Return only the final synthesis.",
    ].join("\n");

    return this.runOpenClaw(prompt);
  }

  private async runOpenClaw(message: string): Promise<string> {
    const stdout = await runProcess({
      command: this.config.command,
      args: [
        "agent",
        "--agent",
        this.config.agentId,
        "--message",
        message,
        "--json",
        "--timeout",
        String(this.config.timeoutSeconds),
      ],
      timeoutMs: (this.config.timeoutSeconds + 30) * 1000,
    });

    return parseOpenClawAgentOutput(stdout);
  }
}

export function parseOpenClawAgentOutput(stdout: string): string {
  const json = extractLastJsonObject(stdout);
  if (!json) {
    return stdout.trim();
  }

  const parsed = JSON.parse(json) as {
    payloads?: Array<{ text?: string }>;
    meta?: {
      finalAssistantVisibleText?: string;
      finalAssistantRawText?: string;
    };
  };

  const payloadText = parsed.payloads
    ?.map((payload) => payload.text?.trim())
    .filter(Boolean)
    .join("\n\n");
  return payloadText || parsed.meta?.finalAssistantVisibleText?.trim() || parsed.meta?.finalAssistantRawText?.trim() || stdout.trim();
}

function extractLastJsonObject(value: string): string | undefined {
  const end = value.lastIndexOf("}");
  if (end < 0) {
    return undefined;
  }

  for (let start = value.lastIndexOf("{", end); start >= 0; start = value.lastIndexOf("{", start - 1)) {
    const candidate = value.slice(start, end + 1);
    try {
      JSON.parse(candidate);
      return candidate;
    } catch {
      continue;
    }
  }

  return undefined;
}
