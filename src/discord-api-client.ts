/**
 * Discord API Transport Layer — Sub-AC 1a-ii
 *
 * Dedicated HTTP client module that POSTs command schemas to Discord's
 * Create Application Command endpoint with bot token authentication.
 *
 * All API calls flow through a pluggable fetch implementation so tests
 * can inject a mock and verify request method, URL, headers, and payload.
 *
 * @module ai-agent/discord-api-client
 */

// ---------------------------------------------------------------------------
// Discord REST API base URL
// ---------------------------------------------------------------------------

export const DISCORD_API_BASE = "https://discord.com/api/v10";

// ---------------------------------------------------------------------------
// Pluggable fetch (testability)
// ---------------------------------------------------------------------------

/**
 * Type for the HTTP fetch function so tests can inject a mock.
 */
export type HttpFetchFn = (
  url: string | URL,
  init?: RequestInit,
) => Promise<Response>;

/**
 * Active fetch implementation. Tests swap this via {@link setFetchImpl}.
 */
let _fetch: HttpFetchFn = globalThis.fetch;

/**
 * Override the fetch implementation (for testing).
 *
 * ```ts
 * import { setFetchImpl } from "ai-agent/discord-api-client";
 *
 * setFetchImpl(async (url, init) => {
 *   // capture and assert on url, method, headers, body
 *   return { ok: true, status: 200, json: async () => ({}) } as Response;
 * });
 * ```
 */
export function setFetchImpl(fn: HttpFetchFn): void {
  _fetch = fn;
}

/**
 * Restore the default fetch implementation.
 */
export function resetFetchImpl(): void {
  _fetch = globalThis.fetch;
}

// ---------------------------------------------------------------------------
// Discord API client configuration
// ---------------------------------------------------------------------------

/**
 * Configuration for the Discord API HTTP client.
 */
export interface DiscordApiClientConfig {
  /** Discord application ID (from Developer Portal). */
  applicationId: string;
  /** Discord bot token (must have applications.commands scope). */
  botToken: string;
}

// ---------------------------------------------------------------------------
// Discord API client
// ---------------------------------------------------------------------------

/**
 * Stateless HTTP client for Discord's REST API.
 *
 * Handles URL construction, authentication headers, and request dispatch.
 * All URL and header logic is centralized here so callers never construct
 * them manually.
 *
 * ```ts
 * import { DiscordApiClient } from "ai-agent/discord-api-client";
 *
 * const client = new DiscordApiClient({
 *   applicationId: "123456789012345678",
 *   botToken: process.env.DISCORD_BOT_TOKEN!,
 * });
 *
 * const response = await client.postGlobalCommand(commandDef);
 * ```
 */
export class DiscordApiClient {
  /** Discord application ID. */
  readonly applicationId: string;
  /** Discord bot token (used for Authorization header). */
  readonly botToken: string;

  constructor(config: DiscordApiClientConfig) {
    this.applicationId = config.applicationId;
    this.botToken = config.botToken;
  }

  // -----------------------------------------------------------------------
  // Authentication
  // -----------------------------------------------------------------------

  /**
   * Build the Authorization header value.
   *
   * Per Discord docs, bot tokens are sent as `Bot {token}`.
   * See: https://discord.com/developers/docs/reference#authentication
   */
  get authHeader(): string {
    return `Bot ${this.botToken}`;
  }

  /**
   * Base headers for every Discord API request.
   *
   * Includes:
   *  - Authorization: Bot {token}
   *  - Content-Type: application/json
   *  - User-Agent: AI_Agent (DiscordBot, 0.1.0)  (Discord requirement)
   */
  get baseHeaders(): Record<string, string> {
    return {
      Authorization: this.authHeader,
      "Content-Type": "application/json",
      "User-Agent": "AI_Agent (DiscordBot, 0.1.0)",
    };
  }

  // -----------------------------------------------------------------------
  // URL construction
  // -----------------------------------------------------------------------

  /**
   * URL for the global application commands collection.
   *
   * POST /applications/{application.id}/commands
   */
  globalCommandsUrl(): string {
    return `${DISCORD_API_BASE}/applications/${encodeURIComponent(this.applicationId)}/commands`;
  }

  /**
   * URL for a single global application command.
   *
   * DELETE /applications/{application.id}/commands/{command.id}
   */
  globalCommandUrl(commandId: string): string {
    return `${this.globalCommandsUrl()}/${encodeURIComponent(commandId)}`;
  }

  /**
   * URL for a guild's application commands collection.
   *
   * POST /applications/{application.id}/guilds/{guild.id}/commands
   */
  guildCommandsUrl(guildId: string): string {
    return `${DISCORD_API_BASE}/applications/${encodeURIComponent(this.applicationId)}/guilds/${encodeURIComponent(guildId)}/commands`;
  }

  /**
   * URL for a single guild application command.
   *
   * DELETE /applications/{application.id}/guilds/{guild.id}/commands/{command.id}
   */
  guildCommandUrl(guildId: string, commandId: string): string {
    return `${this.guildCommandsUrl(guildId)}/${encodeURIComponent(commandId)}`;
  }

  // -----------------------------------------------------------------------
  // Request dispatch
  // -----------------------------------------------------------------------

  /**
   * Send a POST request to the Discord API.
   *
   * @param url   — Full API endpoint URL (from url construction methods above).
   * @param body  — JSON-serializable request body.
   * @returns     — The raw fetch Response.
   */
  async post(url: string, body: unknown): Promise<Response> {
    return _fetch(url, {
      method: "POST",
      headers: this.baseHeaders,
      body: JSON.stringify(body),
    });
  }

  /**
   * Send a GET request to the Discord API.
   */
  async get(url: string): Promise<Response> {
    return _fetch(url, {
      method: "GET",
      headers: this.baseHeaders,
    });
  }

  /**
   * Send a DELETE request to the Discord API.
   */
  async delete(url: string): Promise<Response> {
    return _fetch(url, {
      method: "DELETE",
      headers: this.baseHeaders,
    });
  }

  /**
   * Send a PUT request to the Discord API.
   */
  async put(url: string, body: unknown): Promise<Response> {
    return _fetch(url, {
      method: "PUT",
      headers: this.baseHeaders,
      body: JSON.stringify(body),
    });
  }
}
