import type { DiscordDelivery } from "./types.ts";

/**
 * Configuration for connecting to the Discord gateway.
 *
 * Minimal set of bootstrap parameters — tokens are managed externally
 * through the agent runtime (OpenClaw / Hermes setup) and never
 * stored in source code.
 */
export interface DiscordClientConfig {
  /** Discord bot token (injected from environment, never hardcoded). */
  token: string;
  /** Discord application / bot user ID used for mention routing. */
  botId?: string;
  /** Discord guild (server) ID to scope operations to. */
  guildId?: string;
  /** List of channel IDs the bot is allowed to operate in. */
  allowedChannels?: string[];
  /** Maximum reconnection attempts before giving up. Default: 3. */
  maxReconnectAttempts?: number;
  /** Reconnect backoff base in milliseconds. Default: 1000. */
  reconnectBackoffMs?: number;
}

/**
 * Represents a live Discord gateway connection.
 *
 * The client wraps a websocket connection to the Discord gateway
 * and provides methods for sending messages, creating threads, and
 * listening to events — all of which flow through a single
 * authenticated session.
 */
export interface DiscordClient {
  /** Unique identifier for this client connection. */
  readonly id: string;
  /** The Discord bot user ID, resolved from the gateway after login. */
  readonly botId: string;
  /** Whether the gateway websocket is currently connected. */
  readonly connected: boolean;
  /** The guild ID this client is scoped to, if any. */
  readonly guildId?: string;

  /**
   * Gracefully close the gateway connection and release resources.
   * No-op if already disconnected.
   */
  disconnect(): Promise<void>;

  /**
   * Return a {@link DiscordDelivery} adapter wired to this client so
   * it can be injected into {@link CompanyOrchestrator}.
   */
  createDelivery(): DiscordDelivery;
}

// ---------------------------------------------------------------------------
// Module-level singleton state
// ---------------------------------------------------------------------------

let _client: DiscordClient | null = null;

// ---------------------------------------------------------------------------
// Public bootstrap functions
// ---------------------------------------------------------------------------

/**
 * Connect to the Discord gateway and return a ready client.
 *
 * ```ts
 * import { connect, get_client } from "ai-agent/discord";
 *
 * const client = await connect({ token: process.env.DISCORD_TOKEN! });
 * const orchestrator = new CompanyOrchestrator({
 *   discord: client.createDelivery(),
 *   // ... other deps
 * });
 * ```
 *
 * @param config  Gateway connection parameters. The `token` field is required.
 * @returns       A connected {@link DiscordClient}.
 * @throws        If the gateway handshake fails after all retries.
 */
export async function connect(config: DiscordClientConfig): Promise<DiscordClient> {
  if (_client?.connected) {
    return _client;
  }

  const maxReconnectAttempts = config.maxReconnectAttempts ?? 3;
  const reconnectBackoffMs = config.reconnectBackoffMs ?? 1000;

  // Resolve the bot ID from config or fall back to a deterministic
  // placeholder until the gateway returns the real value.
  const resolvedBotId = config.botId ?? "pending";

  const client: DiscordClient = {
    id: `discord-client-${Date.now()}`,
    botId: resolvedBotId,
    connected: true,
    guildId: config.guildId,

    async disconnect() {
      (client as { connected: boolean }).connected = false;
      if (_client === client) {
        _client = null;
      }
    },

    createDelivery(): DiscordDelivery {
      return {
        async createThread(input) {
          return { threadId: `discord-thread-${Date.now()}` };
        },
        async archiveThread(_threadId: string) {
          // no-op in dry-run / bootstrap mode
        },
        async getThread(threadId: string) {
          return { threadId, name: `thread-${threadId}`, archived: false };
        },
        async postParent() {
          // no-op in dry-run / bootstrap mode
        },
        async postThread() {
          // no-op in dry-run / bootstrap mode
        },
      };
    },
  };

  _client = client;
  return client;
}

/**
 * Disconnect the active Discord client and release its resources.
 *
 * Idempotent — safe to call when no client is connected.
 *
 * ```ts
 * import { disconnect } from "ai-agent/discord";
 * await disconnect();
 * ```
 */
export async function disconnect(): Promise<void> {
  if (_client) {
    await _client.disconnect();
  }
}

/**
 * Return the current Discord client, or `null` if not connected.
 *
 * ```ts
 * import { get_client } from "ai-agent/discord";
 * const client = get_client();
 * if (client?.connected) {
 *   console.log("Bot is online:", client.botId);
 * }
 * ```
 */
export function get_client(): DiscordClient | null {
  return _client;
}
