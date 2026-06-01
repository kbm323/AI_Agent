import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { Client, Events, GatewayIntentBits, Partials } from "discord.js";
import { loadRuntimeConfig } from "./config.ts";
import { AiAgentDatabase } from "./db.ts";
import { DiscordJsDelivery } from "./discord/discordDelivery.ts";
import { HermesCliReviewerExecutor } from "./executors/hermesCliExecutor.ts";
import { OpenClawCliExecutor } from "./executors/openclawCliExecutor.ts";
import { CompanyOrchestrator } from "./orchestrator.ts";

export async function startDiscordRuntime(): Promise<void> {
  const config = loadRuntimeConfig();
  mkdirSync(dirname(config.dbPath), { recursive: true });

  const client = new Client({
    intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],
    partials: [Partials.Channel],
  });
  const hermesClient = config.hermesDiscordToken
    ? new Client({
        intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages],
        partials: [Partials.Channel],
      })
    : undefined;

  const openclaw = new OpenClawCliExecutor(config.commands.openclaw);
  const db = new AiAgentDatabase(config.dbPath);
  const orchestrator = new CompanyOrchestrator({
    db,
    discord: new DiscordJsDelivery(client, {
      autoArchiveDuration: config.threadAutoArchiveMinutes,
      roleClients: hermesClient ? { "hermes-reviewer": hermesClient } : undefined,
    }),
    owner: openclaw,
    reviewer: new HermesCliReviewerExecutor(config.commands.hermes),
    finalizer: openclaw,
    config: { maxRounds: config.maxRounds },
  });

  client.once(Events.ClientReady, (readyClient) => {
    console.log(`[AI_AGENT] Discord runtime ready as ${readyClient.user.tag}`);
  });
  hermesClient?.once(Events.ClientReady, (readyClient) => {
    console.log(`[AI_AGENT] Hermes Discord poster ready as ${readyClient.user.tag}`);
  });

  client.on(Events.MessageCreate, async (message) => {
    if (message.author.bot) {
      return;
    }
    if (!config.projectChannelIds.has(message.channelId)) {
      return;
    }
    if (message.channel.isThread()) {
      return;
    }

    console.log(`[AI_AGENT-LIVE] parent channel request detected channelId=${message.channelId} messageId=${message.id}`);
    try {
      const result = await orchestrator.runUserRequest({
        project: { channelId: message.channelId, name: message.guild?.name },
        userRequest: message.content,
      });
      console.log(`[AI_AGENT] task completed status=${result.status} threadId=${result.threadId}`);
    } catch (error) {
      console.error("[AI_AGENT] task failed", error);
    }
  });

  if (hermesClient && config.hermesDiscordToken) {
    await hermesClient.login(config.hermesDiscordToken);
  }
  await client.login(config.discordToken);
}
