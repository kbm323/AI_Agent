import type { HermesCommandConfig } from "../config.ts";
import type { ReviewerExecutor, ReviewerVerdict } from "../types.ts";
import { runProcess } from "./cliProcess.ts";

export class HermesCliReviewerExecutor implements ReviewerExecutor {
  private readonly config: HermesCommandConfig;

  constructor(config: HermesCommandConfig) {
    this.config = config;
  }

  async review(input: Parameters<ReviewerExecutor["review"]>[0]): Promise<Awaited<ReturnType<ReviewerExecutor["review"]>>> {
    const prompt = [
      "Role: Hermes reviewer",
      `Round: ${input.round}`,
      "",
      "User request:",
      input.userRequest,
      "",
      "Captured OpenClaw draft:",
      input.draft,
      "",
      "Review task:",
      "OpenClaw draft를 기준으로 비판/보완/동의 여부를 판단하라.",
      "독립 제안을 새로 만들지 말고, draft의 장점/문제/리스크/수정안을 분리하라.",
      "",
      "Include exactly one verdict line:",
      "Verdict: agree | partial_agree | disagree | needs_user_decision",
    ].join("\n");

    const content = await runProcess({
      command: this.config.command,
      args: ["-z", prompt],
    });

    return {
      content,
      verdict: parseVerdict(content),
    };
  }
}

export function parseVerdict(content: string): ReviewerVerdict {
  const english = content.match(/\bverdict\s*:\s*(partial_agree|agree_with_changes|needs_user_decision|disagree|agree)\b/i);
  if (english?.[1]) {
    return normalizeVerdict(english[1]);
  }

  const korean = content.match(/(?:판정|verdict)\s*:\s*(사용자결정필요|부분동의|비동의|동의)/i);
  if (korean?.[1]) {
    return normalizeVerdict(korean[1]);
  }

  return "partial_agree";
}

function normalizeVerdict(value: string): ReviewerVerdict {
  switch (value.toLowerCase()) {
    case "agree":
    case "동의":
      return "agree";
    case "partial_agree":
    case "agree_with_changes":
    case "부분동의":
      return "partial_agree";
    case "disagree":
    case "비동의":
      return "disagree";
    case "needs_user_decision":
    case "사용자결정필요":
      return "needs_user_decision";
    default:
      return "partial_agree";
  }
}
