export type DiscordMessageAction =
  | { kind: "ignore"; reason: "bot_author" | "empty_content" | "non_project_channel" | "non_project_thread" }
  | { kind: "start_task"; parentChannelId: string; userRequest: string }
  | { kind: "resume_task"; threadId: string; userDecision: string };

export interface DiscordMessageRouteInput {
  authorBot: boolean;
  channelId: string;
  parentChannelId?: string;
  content: string;
  isThread: boolean;
}

export interface DiscordMessageRouteConfig {
  projectChannelIds: Set<string>;
}

export function routeDiscordMessage(input: DiscordMessageRouteInput, config: DiscordMessageRouteConfig): DiscordMessageAction {
  if (input.authorBot) {
    return { kind: "ignore", reason: "bot_author" };
  }

  const content = input.content.trim();
  if (!content) {
    return { kind: "ignore", reason: "empty_content" };
  }

  if (input.isThread) {
    if (!input.parentChannelId || !config.projectChannelIds.has(input.parentChannelId)) {
      return { kind: "ignore", reason: "non_project_thread" };
    }

    return {
      kind: "resume_task",
      threadId: input.channelId,
      userDecision: content,
    };
  }

  if (!config.projectChannelIds.has(input.channelId)) {
    return { kind: "ignore", reason: "non_project_channel" };
  }

  return {
    kind: "start_task",
    parentChannelId: input.channelId,
    userRequest: content,
  };
}
