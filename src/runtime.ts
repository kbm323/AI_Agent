import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { Client, Events, GatewayIntentBits, Partials } from "discord.js";
import { loadRuntimeConfig } from "./config.ts";
import { AiAgentDatabase } from "./db.ts";
import { DiscordJsDelivery } from "./discord/discordDelivery.ts";
import { routeDiscordMessage } from "./discord/messageRouter.ts";
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
    const action = routeDiscordMessage({
      authorBot: message.author.bot,
      channelId: message.channelId,
      parentChannelId: message.channel.isThread() ? message.channel.parentId ?? undefined : undefined,
      content: message.content,
      isThread: message.channel.isThread(),
    }, { projectChannelIds: config.projectChannelIds });

    if (action.kind === "ignore") return;

    try {
      if (action.kind === "start_task") {
        console.log(`[AI_AGENT-LIVE] parent channel request detected channelId=${action.parentChannelId} messageId=${message.id}`);
        const result = await orchestrator.runUserRequest({
          project: { channelId: action.parentChannelId, name: message.guild?.name },
          userRequest: action.userRequest,
        });
        console.log(`[AI_AGENT] task completed status=${result.status} threadId=${result.threadId}`);
        return;
      }

      console.log(`[AI_AGENT-LIVE] thread user decision detected threadId=${action.threadId} messageId=${message.id}`);
      const result = await orchestrator.resumeFromUserDecision({
        threadId: action.threadId,
        userDecision: action.userDecision,
      });
      console.log(`[AI_AGENT] task resumed status=${result.status} threadId=${result.threadId}`);
    } catch (error) {
      console.error("[AI_AGENT] task failed", error);
    }
  });

  if (hermesClient && config.hermesDiscordToken) {
    await hermesClient.login(config.hermesDiscordToken);
  }
  await client.login(config.discordToken);
}
