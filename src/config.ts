import type { TeamRoute } from "./types.ts";

export interface RuntimeConfig {
  discordToken: string;
  hermesDiscordToken?: string;
  projectChannelIds: Set<string>;
  dbPath: string;
  maxRounds: number;
  hermesTimeoutSeconds: number;
  debugMentions: boolean;
  threadAutoArchiveMinutes: number;
  modelRouting: {
    openclaw: Partial<Record<TeamRoute, string>>;
    hermes: Partial<Record<TeamRoute, string>>;
  };
  commands: {
    openclaw: OpenClawCommandConfig;
    hermes: HermesCommandConfig;
  };
}

export interface OpenClawCommandConfig {
  command: string;
  agentId: string;
  timeoutSeconds: number;
}

export interface HermesCommandConfig {
  command: string;
}

export function loadRuntimeConfig(env = process.env): RuntimeConfig {
  const discordToken = required(env.DISCORD_BOT_TOKEN, "DISCORD_BOT_TOKEN");
  const projectChannelIds = new Set(splitCsv(required(env.AI_AGENT_PROJECT_CHANNEL_IDS, "AI_AGENT_PROJECT_CHANNEL_IDS")));

  return {
    discordToken,
    hermesDiscordToken: optional(env.HERMES_DISCORD_BOT_TOKEN),
    projectChannelIds,
    dbPath: env.AI_AGENT_DB_PATH?.trim() || "./data/ai_agent.sqlite",
    maxRounds: parsePositiveInt(env.AI_AGENT_MAX_ROUNDS, 4),
    hermesTimeoutSeconds: parsePositiveInt(env.AI_AGENT_HERMES_TIMEOUT_SECONDS, 600),
    debugMentions: parseBoolean(env.AI_AGENT_DEBUG_MENTIONS, false),
    threadAutoArchiveMinutes: parsePositiveInt(env.AI_AGENT_THREAD_AUTO_ARCHIVE_MINUTES, 10080),
    modelRouting: {
      openclaw: loadTeamModels(env, "AI_AGENT_OPENCLAW"),
      hermes: loadTeamModels(env, "AI_AGENT_HERMES"),
    },
    commands: {
      openclaw: {
        command: env.AI_AGENT_OPENCLAW_COMMAND?.trim() || "openclaw",
        agentId: env.AI_AGENT_OPENCLAW_AGENT_ID?.trim() || "main",
        timeoutSeconds: parsePositiveInt(env.AI_AGENT_OPENCLAW_TIMEOUT_SECONDS, 600),
      },
      hermes: {
        command: env.AI_AGENT_HERMES_COMMAND?.trim() || "hermes",
      },
    },
  };
}

function optional(value: string | undefined): string | undefined {
  const normalized = value?.trim();
  return normalized || undefined;
}

function required(value: string | undefined, name: string): string {
  const normalized = value?.trim();
  if (!normalized) {
    throw new Error(`${name} is required`);
  }
  return normalized;
}

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseBoolean(value: string | undefined, fallback: boolean): boolean {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) {
    return fallback;
  }

  return ["1", "true", "yes", "on"].includes(normalized);
}

function loadTeamModels(env: NodeJS.ProcessEnv, prefix: string): Partial<Record<TeamRoute, string>> {
  return {
    content: optional(env[`${prefix}_CONTENT_MODEL`]),
    art: optional(env[`${prefix}_ART_MODEL`]),
    tech: optional(env[`${prefix}_TECH_MODEL`]),
    marketing: optional(env[`${prefix}_MARKETING_MODEL`]),
    executive: optional(env[`${prefix}_EXECUTIVE_MODEL`]),
  };
}
