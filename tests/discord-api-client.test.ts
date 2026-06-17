/**
 * Tests for Discord API Transport Layer (Sub-AC 1a-ii).
 *
 * Covers:
 *  - setFetchImpl / resetFetchImpl — mock injection and restoration
 *  - DiscordApiClient URL construction (global, guild, with commandId)
 *  - POST request: method, URL, headers (Authorization, Content-Type, User-Agent), body
 *  - GET request: method, URL, headers
 *  - DELETE request: method, URL, headers
 *  - PUT request: method, URL, headers, body
 *  - authHeader — Bot token format
 *  - baseHeaders — all three headers present and correct
 *  - DISCORD_API_BASE constant
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  // Constants
  DISCORD_API_BASE,
  // Test injection
  setFetchImpl,
  resetFetchImpl,
  // Client
  DiscordApiClient,
  type DiscordApiClientConfig,
  type HttpFetchFn,
} from "../src/discord-api-client.ts";

// ═══════════════════════════════════════════════════════════════════════
// Test helpers
// ═══════════════════════════════════════════════════════════════════════

/** Standard test config with fake credentials. */
function testConfig(): DiscordApiClientConfig {
  return {
    applicationId: "123456789012345678",
    botToken: "fake-bot-token-abc123",
  };
}

/** A sample JSON body for POST/PUT tests. */
function sampleBody(): Record<string, unknown> {
  return {
    name: "meeting",
    description: "Start a multi-agent meeting",
    type: 1,
    options: [
      {
        type: 3,
        name: "agenda",
        description: "Meeting topic and objectives",
        required: true,
      },
    ],
  };
}

// ═══════════════════════════════════════════════════════════════════════
// 1. DISCORD_API_BASE constant
// ═══════════════════════════════════════════════════════════════════════

test("DISCORD_API_BASE is the correct v10 URL", () => {
  assert.equal(DISCORD_API_BASE, "https://discord.com/api/v10");
});

// ═══════════════════════════════════════════════════════════════════════
// 2. setFetchImpl / resetFetchImpl
// ═══════════════════════════════════════════════════════════════════════

test("setFetchImpl injects mock, resetFetchImpl restores default", async () => {
  let callCount = 0;
  const mock: HttpFetchFn = async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => ({}) } as Response;
  };

  setFetchImpl(mock);

  // Use a real client call to confirm injection is active
  const client = new DiscordApiClient(testConfig());
  await client.get(client.globalCommandsUrl());
  assert.equal(callCount, 1, "Mock should have been called once after injection");

  resetFetchImpl();

  // After reset, the default fetch is restored (real fetch will fail without network)
  try {
    await client.get(client.globalCommandsUrl());
  } catch {
    // Expected — real globalThis.fetch fails without network in test environment
  }
  assert.equal(callCount, 1, "Mock must not be called after resetFetchImpl");
});

// ═══════════════════════════════════════════════════════════════════════
// 3. DiscordApiClient — URL construction
// ═══════════════════════════════════════════════════════════════════════

test("globalCommandsUrl returns correct URL", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.globalCommandsUrl();
  assert.equal(
    url,
    "https://discord.com/api/v10/applications/123456789012345678/commands",
  );
});

test("globalCommandsUrl encodes application ID", () => {
  const client = new DiscordApiClient({
    applicationId: "app|with|pipes",
    botToken: "tok",
  });
  const url = client.globalCommandsUrl();
  assert.ok(url.includes("app%7Cwith%7Cpipes"), "Pipe chars should be encoded");
});

test("globalCommandUrl returns correct URL with command ID", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.globalCommandUrl("cmd-abc-123");
  assert.equal(
    url,
    "https://discord.com/api/v10/applications/123456789012345678/commands/cmd-abc-123",
  );
});

test("globalCommandUrl encodes command ID", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.globalCommandUrl("cmd/with/slashes");
  assert.ok(url.endsWith("cmd%2Fwith%2Fslashes"), "Slash chars should be encoded");
});

test("guildCommandsUrl returns correct guild-scoped URL", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.guildCommandsUrl("guild-xyz");
  assert.equal(
    url,
    "https://discord.com/api/v10/applications/123456789012345678/guilds/guild-xyz/commands",
  );
});

test("guildCommandsUrl encodes guild ID", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.guildCommandsUrl("guild|id");
  assert.ok(url.includes("guild%7Cid"), "Pipe chars in guild ID should be encoded");
});

test("guildCommandUrl returns correct URL with guild and command IDs", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.guildCommandUrl("guild-1", "cmd-2");
  assert.equal(
    url,
    "https://discord.com/api/v10/applications/123456789012345678/guilds/guild-1/commands/cmd-2",
  );
});

test("guildCommandUrl encodes both guild and command IDs", () => {
  const client = new DiscordApiClient(testConfig());
  const url = client.guildCommandUrl("guild/a", "cmd/b");
  assert.ok(url.includes("guild%2Fa"));
  assert.ok(url.endsWith("cmd%2Fb"));
});

// ═══════════════════════════════════════════════════════════════════════
// 4. DiscordApiClient — authHeader and baseHeaders
// ═══════════════════════════════════════════════════════════════════════

test("authHeader uses Bot token format", () => {
  const client = new DiscordApiClient(testConfig());
  assert.equal(client.authHeader, "Bot fake-bot-token-abc123");
});

test("authHeader works with different tokens", () => {
  const client = new DiscordApiClient({
    applicationId: "app",
    botToken: "my-secret-token",
  });
  assert.equal(client.authHeader, "Bot my-secret-token");
});

test("baseHeaders contains Authorization, Content-Type, and User-Agent", () => {
  const client = new DiscordApiClient(testConfig());
  const headers = client.baseHeaders;

  assert.equal(headers["Authorization"], "Bot fake-bot-token-abc123");
  assert.equal(headers["Content-Type"], "application/json");
  assert.ok(
    headers["User-Agent"]!.includes("AI_Agent"),
    `User-Agent should mention AI_Agent, got: ${headers["User-Agent"]}`,
  );
  assert.ok(
    headers["User-Agent"]!.includes("DiscordBot"),
    `User-Agent should mention DiscordBot, got: ${headers["User-Agent"]}`,
  );
});

// ═══════════════════════════════════════════════════════════════════════
// 5. DiscordApiClient — POST request
// ═══════════════════════════════════════════════════════════════════════

test("client.post sends correct method, URL, headers, and body", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  let capturedHeaders: Record<string, string> = {};
  let capturedBody = "";

  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "UNKNOWN";
    // Convert Headers to plain object
    if (init?.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { capturedHeaders[k] = v; });
      } else if (Array.isArray(init.headers)) {
        for (const [k, v] of init.headers) capturedHeaders[k] = v;
      } else {
        Object.assign(capturedHeaders, init.headers);
      }
    }
    capturedBody = (init?.body as string) ?? "";
    return {
      ok: true,
      status: 201,
      statusText: "Created",
      json: async () => ({ id: "cmd-1", name: "meeting" }),
    } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    const targetUrl = client.globalCommandsUrl();
    const body = sampleBody();

    const response = await client.post(targetUrl, body);

    // ── Verify response ──
    assert.equal(response.ok, true);
    assert.equal(response.status, 201);

    // ── Verify method ──
    assert.equal(capturedMethod, "POST");

    // ── Verify URL ──
    assert.ok(
      capturedUrl.includes("/applications/123456789012345678/commands"),
      `URL should contain application ID path, got: ${capturedUrl}`,
    );

    // ── Verify headers ──
    assert.equal(
      capturedHeaders["Authorization"],
      "Bot fake-bot-token-abc123",
      "Authorization header must use Bot token format",
    );
    assert.equal(
      capturedHeaders["Content-Type"],
      "application/json",
      "Content-Type must be application/json",
    );
    assert.ok(
      capturedHeaders["User-Agent"]?.includes("AI_Agent"),
      "User-Agent must identify as AI_Agent",
    );

    // ── Verify payload ──
    const parsed = JSON.parse(capturedBody);
    assert.equal(parsed.name, "meeting");
    assert.equal(parsed.description, "Start a multi-agent meeting");
    assert.equal(parsed.type, 1);
    assert.equal(parsed.options.length, 1);
    assert.equal(parsed.options[0].name, "agenda");
  } finally {
    resetFetchImpl();
  }
});

test("client.post sends correct body when posting a command schema", async () => {
  let capturedBody = "";

  setFetchImpl(async (_url, init) => {
    capturedBody = (init?.body as string) ?? "";
    return { ok: true, status: 201, json: async () => ({}) } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    const cmdDef = {
      name: "cancel",
      description: "Cancel a running meeting",
      type: 1,
      options: [
        {
          type: 3,
          name: "meeting_id",
          description: "ID of the meeting to cancel",
          required: true,
        },
      ],
    };

    await client.post(client.globalCommandsUrl(), cmdDef);

    const parsed = JSON.parse(capturedBody);
    assert.equal(parsed.name, "cancel");
    assert.equal(parsed.description, "Cancel a running meeting");
    assert.equal(parsed.options[0].name, "meeting_id");
    assert.equal(parsed.options[0].required, true);
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 6. DiscordApiClient — GET request
// ═══════════════════════════════════════════════════════════════════════

test("client.get sends correct method, URL, and headers", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  let capturedHeaders: Record<string, string> = {};

  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "UNKNOWN";
    if (init?.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { capturedHeaders[k] = v; });
      } else if (Array.isArray(init.headers)) {
        for (const [k, v] of init.headers) capturedHeaders[k] = v;
      } else {
        Object.assign(capturedHeaders, init.headers);
      }
    }
    return { ok: true, status: 200, json: async () => [] } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    const url = client.globalCommandsUrl();
    await client.get(url);

    assert.equal(capturedMethod, "GET");
    assert.ok(capturedUrl.includes("/commands"));
    assert.equal(capturedHeaders["Authorization"], "Bot fake-bot-token-abc123");
    assert.equal(capturedHeaders["Content-Type"], "application/json");
    // GET should NOT have a body
  } finally {
    resetFetchImpl();
  }
});

test("client.get to guild-scoped URL sends correct URL", async () => {
  let capturedUrl = "";

  setFetchImpl(async (url) => {
    capturedUrl = String(url);
    return { ok: true, status: 200, json: async () => [] } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    await client.get(client.guildCommandsUrl("guild-999"));

    assert.ok(capturedUrl.includes("/guilds/guild-999/commands"));
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 7. DiscordApiClient — DELETE request
// ═══════════════════════════════════════════════════════════════════════

test("client.delete sends correct method, URL, and headers", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  let capturedHeaders: Record<string, string> = {};

  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "UNKNOWN";
    if (init?.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { capturedHeaders[k] = v; });
      } else if (Array.isArray(init.headers)) {
        for (const [k, v] of init.headers) capturedHeaders[k] = v;
      } else {
        Object.assign(capturedHeaders, init.headers);
      }
    }
    return { ok: true, status: 204 } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    await client.delete(client.globalCommandUrl("cmd-to-delete"));

    assert.equal(capturedMethod, "DELETE");
    assert.ok(capturedUrl.endsWith("/commands/cmd-to-delete"));
    assert.equal(capturedHeaders["Authorization"], "Bot fake-bot-token-abc123");
    assert.equal(capturedHeaders["Content-Type"], "application/json");
    // DELETE should NOT have a body
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 8. DiscordApiClient — PUT request
// ═══════════════════════════════════════════════════════════════════════

test("client.put sends correct method, URL, headers, and body", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  let capturedHeaders: Record<string, string> = {};
  let capturedBody = "";

  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "UNKNOWN";
    if (init?.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { capturedHeaders[k] = v; });
      } else if (Array.isArray(init.headers)) {
        for (const [k, v] of init.headers) capturedHeaders[k] = v;
      } else {
        Object.assign(capturedHeaders, init.headers);
      }
    }
    capturedBody = (init?.body as string) ?? "";
    return { ok: true, status: 200, json: async () => [] } as Response;
  });

  try {
    const client = new DiscordApiClient(testConfig());
    const body = [sampleBody(), { name: "ping", description: "Check bot", type: 1 }];
    await client.put(client.globalCommandsUrl(), body);

    assert.equal(capturedMethod, "PUT");
    assert.ok(capturedUrl.endsWith("/commands"));
    assert.equal(capturedHeaders["Authorization"], "Bot fake-bot-token-abc123");
    assert.equal(capturedHeaders["Content-Type"], "application/json");

    const parsed = JSON.parse(capturedBody);
    assert.equal(parsed.length, 2);
    assert.equal(parsed[0].name, "meeting");
    assert.equal(parsed[1].name, "ping");
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 9. DiscordApiClient — properties
// ═══════════════════════════════════════════════════════════════════════

test("DiscordApiClient exposes applicationId and botToken", () => {
  const config = testConfig();
  const client = new DiscordApiClient(config);

  assert.equal(client.applicationId, config.applicationId);
  assert.equal(client.botToken, config.botToken);
});

// ═══════════════════════════════════════════════════════════════════════
// 10. Integration: multiple clients with different configs
// ═══════════════════════════════════════════════════════════════════════

test("different DiscordApiClient instances use different tokens", async () => {
  let capturedTokens: string[] = [];

  setFetchImpl(async (_url, init) => {
    const headers = init?.headers;
    if (headers) {
      if (headers instanceof Headers) {
        capturedTokens.push(headers.get("Authorization") ?? "");
      } else if (Array.isArray(headers)) {
        for (const [k, v] of headers) {
          if (k === "Authorization") capturedTokens.push(v);
        }
      } else {
        capturedTokens.push(headers["Authorization"] ?? "");
      }
    }
    return { ok: true, status: 200, json: async () => ({}) } as Response;
  });

  try {
    const clientA = new DiscordApiClient({
      applicationId: "app-1",
      botToken: "token-aaa",
    });
    const clientB = new DiscordApiClient({
      applicationId: "app-2",
      botToken: "token-bbb",
    });

    await clientA.post(clientA.globalCommandsUrl(), { name: "a" });
    await clientB.post(clientB.globalCommandsUrl(), { name: "b" });

    assert.equal(capturedTokens.length, 2);
    assert.equal(capturedTokens[0], "Bot token-aaa");
    assert.equal(capturedTokens[1], "Bot token-bbb");
  } finally {
    resetFetchImpl();
  }
});
