import {
  ChannelType,
  type Client,
  type GuildTextBasedChannel,
  type MessageCreateOptions,
  type TextBasedChannel,
} from "discord.js";
import type { AgentRole, DiscordDelivery } from "../types.ts";

export interface DiscordDeliveryOptions {
  autoArchiveDuration?: number;
  roleClients?: Partial<Record<AgentRole, Client>>;
}

export class DiscordJsDelivery implements DiscordDelivery {
  private readonly client: Client;
  private readonly options: DiscordDeliveryOptions;

  constructor(client: Client, options: DiscordDeliveryOptions = {}) {
    this.client = client;
    this.options = options;
  }

  async createThread(input: { parentChannelId: string; name: string; initialMessage: string }): Promise<{ threadId: string; url?: string }> {
    const channel = await this.fetchTextChannel(input.parentChannelId);
    if (!("threads" in channel)) {
      throw new Error(`Channel ${input.parentChannelId} cannot create threads`);
    }

    const thread = await channel.threads.create({
      name: input.name,
      autoArchiveDuration: this.options.autoArchiveDuration,
      reason: "AI_Agent task thread",
    });

    await sendChunked(thread, `User request\n\n${input.initialMessage}`);
    return { threadId: thread.id, url: thread.url };
  }

  async postParent(input: { channelId: string; content: string }): Promise<void> {
    const channel = await this.fetchTextChannel(input.channelId);
    await sendChunked(channel, input.content);
  }

  async postThread(input: { threadId: string; content: string }): Promise<void> {
    const channel = await this.fetchTextChannel(input.threadId, this.clientForRole(input.role));
    await sendChunked(channel, input.content);
  }

  private clientForRole(role?: AgentRole): Client {
    return (role ? this.options.roleClients?.[role] : undefined) ?? this.client;
  }

  private async fetchTextChannel(channelId: string, client = this.client): Promise<GuildTextBasedChannel> {
    const channel = await client.channels.fetch(channelId);
    if (!channel || !isGuildTextBased(channel)) {
      throw new Error(`Channel ${channelId} is not a guild text channel`);
    }
    return channel;
  }
}

function isGuildTextBased(channel: TextBasedChannel): channel is GuildTextBasedChannel {
  return [
    ChannelType.GuildText,
    ChannelType.GuildAnnouncement,
    ChannelType.PublicThread,
    ChannelType.PrivateThread,
    ChannelType.AnnouncementThread,
  ].includes(channel.type);
}

async function sendChunked(channel: GuildTextBasedChannel, content: string): Promise<void> {
  for (const chunk of chunkDiscordMessage(content)) {
    const payload: MessageCreateOptions = { content: chunk };
    await channel.send(payload);
  }
}

export function chunkDiscordMessage(content: string, limit = 1900): string[] {
  const normalized = content.trim() || "(empty)";
  const chunks: string[] = [];
  let remaining = normalized;

  while (remaining.length > limit) {
    const splitAt = findSplitPoint(remaining, limit);
    chunks.push(remaining.slice(0, splitAt).trimEnd());
    remaining = remaining.slice(splitAt).trimStart();
  }

  chunks.push(remaining);
  return chunks;
}

function findSplitPoint(value: string, limit: number): number {
  const window = value.slice(0, limit);
  const paragraph = window.lastIndexOf("\n\n");
  if (paragraph > limit * 0.5) {
    return paragraph;
  }

  const line = window.lastIndexOf("\n");
  if (line > limit * 0.5) {
    return line;
  }

  const space = window.lastIndexOf(" ");
  return space > limit * 0.5 ? space : limit;
}
