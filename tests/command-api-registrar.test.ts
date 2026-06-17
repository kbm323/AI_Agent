/**
 * Tests for Command API Registrar (Sub-AC 1.1a).
 *
 * Covers:
 *  - validateCommandDefinition / validateCommandDefinitions
 *    - valid command definitions pass
 *    - name constraints (length, format, case)
 *    - description constraints (length)
 *    - option constraints (name, description, type, choices)
 *    - choice constraints (name, value)
 *    - min_value/max_value and min_length/max_length cross-validation
 *    - too-many-options (>25) rejection
 *    - too-many-choices (>25) per option rejection
 *  - commandDefToDiscordApiJson conversion
 *  - createGlobalCommand / createGuildCommand (via mock fetch)
 *  - listGlobalCommands / listGuildCommands (via mock fetch)
 *  - deleteGlobalCommand / deleteGuildCommand (via mock fetch)
 *  - bulkOverwriteGlobalCommands / bulkOverwriteGuildCommands (via mock fetch)
 *  - registerGlobalCommands / registerGuildCommands batch registration
 *  - DiscordApiError construction and fromResponse
 *  - isRateLimitError / isAuthError type guards
 *  - setFetchImpl / resetFetchImpl testability
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  // Validation
  validateCommandDefinition,
  validateCommandDefinitions,
  // Conversion
  commandDefToDiscordApiJson,
  // Registration (single)
  createGlobalCommand,
  createGuildCommand,
  listGlobalCommands,
  listGuildCommands,
  deleteGlobalCommand,
  deleteGuildCommand,
  bulkOverwriteGlobalCommands,
  bulkOverwriteGuildCommands,
  // Batch registration
  registerGlobalCommands,
  registerGuildCommands,
  // Error handling
  DiscordApiError,
  isRateLimitError,
  isAuthError,
  // Test injection
  setFetchImpl,
  resetFetchImpl,
  // Types
  type CommandRegistrationResult,
  type CommandDefValidationResult,
  type DiscordApiCommandResponse,
  type DiscordRegistrarConfig,
} from "../src/command-api-registrar.ts";
import type {
  CommandDefinition,
  CommandOptionDefinition,
} from "../src/command-schema-validator.ts";

// ═══════════════════════════════════════════════════════════════════════
// Test helpers
// ═══════════════════════════════════════════════════════════════════════

/** Minimal valid command definition. */
function validMeetingDef(): CommandDefinition {
  return {
    name: "meeting",
    description: "Start a multi-agent meeting",
    permission: "guild_only",
    allow_bot_mention: true,
    options: [
      {
        name: "agenda",
        description: "Meeting topic and objectives",
        type: "string",
        required: true,
        min_length: 1,
        max_length: 4000,
      },
      {
        name: "priority",
        description: "Meeting priority level",
        type: "string",
        required: false,
        choices: [
          { name: "P0 — Critical", value: "P0" },
          { name: "P1 — High", value: "P1" },
          { name: "P2 — Normal", value: "P2" },
          { name: "P3 — Low", value: "P3" },
        ],
      },
    ],
  };
}

/** Minimal valid command (no options). */
function validMinimalDef(): CommandDefinition {
  return {
    name: "ping",
    description: "Check if the bot is alive",
    permission: "everyone",
    options: [],
  };
}

/** A valid registrar config for tests (fake token). */
function testConfig(): DiscordRegistrarConfig {
  return {
    applicationId: "123456789012345678",
    botToken: "fake-bot-token-for-testing",
  };
}

/** Create a mock fetch that returns a successful JSON response. */
function mockFetchOk<T>(data: T, status = 200) {
  return async (_url: string | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: true,
      status,
      statusText: "OK",
      json: async () => data as unknown as Record<string, unknown>,
      headers: new Headers({ "content-type": "application/json" }),
    } as Response;
  };
}

/** Create a mock fetch that returns an error response. */
function mockFetchError(status: number, body?: Record<string, unknown>) {
  return async (_url: string | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: false,
      status,
      statusText: status === 429 ? "Too Many Requests" : "Error",
      json: async () => body ?? { message: `Error ${status}` },
      headers: new Headers({ "content-type": "application/json" }),
    } as Response;
  };
}

/** Discriminated union check helper. */
function assertValid(result: CommandDefValidationResult): void {
  assert.equal(result.valid, true, `Expected valid, got issues: ${JSON.stringify(result.issues)}`);
}

function assertInvalid(result: CommandDefValidationResult, expectedCode?: string): void {
  assert.equal(result.valid, false, "Expected invalid but got valid");
  if (expectedCode) {
    const found = result.issues.some((i) => i.code === expectedCode);
    assert.ok(found, `Expected issue with code "${expectedCode}", got: ${result.issues.map((i) => i.code).join(", ")}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// 1. validateCommandDefinition — valid definitions
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition accepts a valid meeting command definition", () => {
  const result = validateCommandDefinition(validMeetingDef());
  assertValid(result);
});

test("validateCommandDefinition accepts a minimal valid command (no options)", () => {
  const result = validateCommandDefinition(validMinimalDef());
  assertValid(result);
});

test("validateCommandDefinition accepts valid optional fields (min/max)", () => {
  const def: CommandDefinition = {
    name: "search",
    description: "Search the knowledge base",
    options: [
      {
        name: "query",
        description: "Search terms",
        type: "string",
        required: true,
        min_length: 1,
        max_length: 200,
      },
      {
        name: "limit",
        description: "Max results",
        type: "integer",
        required: false,
        min_value: 1,
        max_value: 100,
      },
    ],
  };
  assertValid(validateCommandDefinition(def));
});

test("validateCommandDefinition accepts all option types", () => {
  const types = ["string", "integer", "boolean", "number", "channel", "role", "user", "mentionable", "attachment"];
  for (const t of types) {
    const def: CommandDefinition = {
      name: "test-cmd",
      description: "Test",
      options: [{ name: "opt", description: "An option", type: t as CommandOptionDefinition["type"], required: false }],
    };
    assertValid(validateCommandDefinition(def));
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 2. validateCommandDefinition — name constraints
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition rejects empty name", () => {
  const def: CommandDefinition = { name: "", description: "Test" };
  assertInvalid(validateCommandDefinition(def), "NAME_TOO_SHORT");
});

test("validateCommandDefinition rejects name > 32 chars", () => {
  const def: CommandDefinition = { name: "a".repeat(33), description: "Test" };
  assertInvalid(validateCommandDefinition(def), "NAME_TOO_LONG");
});

test("validateCommandDefinition rejects uppercase in name", () => {
  const def: CommandDefinition = { name: "Meeting", description: "Test" };
  assertInvalid(validateCommandDefinition(def), "NAME_INVALID_FORMAT");
});

test("validateCommandDefinition rejects spaces in name", () => {
  const def: CommandDefinition = { name: "my command", description: "Test" };
  assertInvalid(validateCommandDefinition(def), "NAME_INVALID_FORMAT");
});

test("validateCommandDefinition rejects special characters in name", () => {
  const defs = ["my@command", "my#cmd", "cmd!", "cmd$"];
  for (const name of defs) {
    const def: CommandDefinition = { name, description: "Test" };
    assertInvalid(validateCommandDefinition(def), "NAME_INVALID_FORMAT");
  }
});

test("validateCommandDefinition accepts valid name formats", () => {
  const names = ["meeting", "cancel", "my-command", "my_command", "cmd123", "a", "z".repeat(32)];
  for (const name of names) {
    const def: CommandDefinition = { name, description: "Test" };
    assertValid(validateCommandDefinition(def));
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 3. validateCommandDefinition — description constraints
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition rejects empty description", () => {
  const def: CommandDefinition = { name: "test", description: "" };
  assertInvalid(validateCommandDefinition(def), "DESCRIPTION_TOO_SHORT");
});

test("validateCommandDefinition rejects description > 100 chars", () => {
  const def: CommandDefinition = { name: "test", description: "d".repeat(101) };
  assertInvalid(validateCommandDefinition(def), "DESCRIPTION_TOO_LONG");
});

test("validateCommandDefinition accepts description at boundary (100 chars)", () => {
  const def: CommandDefinition = { name: "test", description: "d".repeat(100) };
  assertValid(validateCommandDefinition(def));
});

// ═══════════════════════════════════════════════════════════════════════
// 4. validateCommandDefinition — option constraints
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition rejects > 25 options", () => {
  const options: CommandOptionDefinition[] = [];
  for (let i = 0; i < 26; i++) {
    options.push({ name: `opt${i}`, description: `Option ${i}`, type: "string", required: false });
  }
  const def: CommandDefinition = { name: "test", description: "Too many options", options };
  assertInvalid(validateCommandDefinition(def), "TOO_MANY_OPTIONS");
});

test("validateCommandDefinition accepts exactly 25 options", () => {
  const options: CommandOptionDefinition[] = [];
  for (let i = 0; i < 25; i++) {
    options.push({ name: `opt${i}`, description: `Option ${i}`, type: "string", required: false });
  }
  const def: CommandDefinition = { name: "test", description: "Max options", options };
  assertValid(validateCommandDefinition(def));
});

test("validateCommandDefinition rejects empty option name", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "", description: "Bad option", type: "string", required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_NAME_EMPTY");
});

test("validateCommandDefinition rejects option name > 32 chars", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "a".repeat(33), description: "Long name", type: "string", required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_NAME_TOO_LONG");
});

test("validateCommandDefinition rejects uppercase in option name", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "Agenda", description: "Caps", type: "string", required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_NAME_INVALID_FORMAT");
});

test("validateCommandDefinition rejects empty option description", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "opt", description: "", type: "string", required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_DESCRIPTION_EMPTY");
});

test("validateCommandDefinition rejects option description > 100 chars", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "opt", description: "d".repeat(101), type: "string", required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_DESCRIPTION_TOO_LONG");
});

test("validateCommandDefinition rejects invalid option type", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "opt", description: "Bad type", type: "array" as never, required: false }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_TYPE_INVALID");
});

// ═══════════════════════════════════════════════════════════════════════
// 5. validateCommandDefinition — choice constraints
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition rejects > 25 choices per option", () => {
  const choices = Array.from({ length: 26 }, (_, i) => ({ name: `C${i}`, value: `v${i}` }));
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "opt", description: "Too many choices", type: "string", required: false, choices }],
  };
  assertInvalid(validateCommandDefinition(def), "TOO_MANY_CHOICES");
});

test("validateCommandDefinition accepts exactly 25 choices", () => {
  const choices = Array.from({ length: 25 }, (_, i) => ({ name: `C${i}`, value: `v${i}` }));
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "opt", description: "Max choices", type: "string", required: false, choices }],
  };
  assertValid(validateCommandDefinition(def));
});

test("validateCommandDefinition rejects empty choice name", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{
      name: "opt", description: "Bad choice", type: "string", required: false,
      choices: [{ name: "", value: "v" }],
    }],
  };
  assertInvalid(validateCommandDefinition(def), "CHOICE_NAME_EMPTY");
});

test("validateCommandDefinition rejects choice name > 100 chars", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{
      name: "opt", description: "Long choice name", type: "string", required: false,
      choices: [{ name: "n".repeat(101), value: "v" }],
    }],
  };
  assertInvalid(validateCommandDefinition(def), "CHOICE_NAME_TOO_LONG");
});

test("validateCommandDefinition rejects missing choice value", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{
      name: "opt", description: "Missing value", type: "string", required: false,
      choices: [{ name: "c", value: undefined as unknown as string }],
    }],
  };
  assertInvalid(validateCommandDefinition(def), "CHOICE_VALUE_MISSING");
});

test("validateCommandDefinition rejects string choice value > 100 chars", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{
      name: "opt", description: "Long value", type: "string", required: false,
      choices: [{ name: "c", value: "v".repeat(101) }],
    }],
  };
  assertInvalid(validateCommandDefinition(def), "CHOICE_VALUE_TOO_LONG");
});

test("validateCommandDefinition rejects non-string non-number choice value", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{
      name: "opt", description: "Bad value type", type: "string", required: false,
      choices: [{ name: "c", value: true as unknown as string }],
    }],
  };
  assertInvalid(validateCommandDefinition(def), "CHOICE_VALUE_INVALID_TYPE");
});

// ═══════════════════════════════════════════════════════════════════════
// 6. validateCommandDefinition — range constraints
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinition rejects min_value > max_value", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "n", description: "Bad range", type: "integer", required: false, min_value: 10, max_value: 5 }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_RANGE_INVALID");
});

test("validateCommandDefinition rejects min_length > max_length", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "s", description: "Bad range", type: "string", required: false, min_length: 10, max_length: 5 }],
  };
  assertInvalid(validateCommandDefinition(def), "OPTION_RANGE_INVALID");
});

test("validateCommandDefinition accepts min_value === max_value", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Test",
    options: [{ name: "n", description: "Equal range", type: "integer", required: false, min_value: 5, max_value: 5 }],
  };
  assertValid(validateCommandDefinition(def));
});

// ═══════════════════════════════════════════════════════════════════════
// 7. validateCommandDefinitions — batch
// ═══════════════════════════════════════════════════════════════════════

test("validateCommandDefinitions returns valid when all defs pass", () => {
  const result = validateCommandDefinitions([validMeetingDef(), validMinimalDef()]);
  assertValid(result);
});

test("validateCommandDefinitions aggregates errors from multiple defs", () => {
  const bad1: CommandDefinition = { name: "Bad1", description: "" };
  const bad2: CommandDefinition = { name: "", description: "No name" };
  const result = validateCommandDefinitions([bad1, bad2, validMinimalDef()]);
  assertInvalid(result);
  assert.ok(result.issues.length >= 2, `Expected at least 2 issues, got ${result.issues.length}`);
});

// ═══════════════════════════════════════════════════════════════════════
// 8. commandDefToDiscordApiJson — conversion
// ═══════════════════════════════════════════════════════════════════════

test("commandDefToDiscordApiJson converts a command definition to Discord API shape", () => {
  const json = commandDefToDiscordApiJson(validMeetingDef());

  assert.equal(json.name, "meeting");
  assert.equal(json.description, "Start a multi-agent meeting");
  assert.equal(json.type, 1); // CHAT_INPUT
  assert.ok(Array.isArray(json.options));
  assert.equal(json.options!.length, 2);

  // First option: agenda (string)
  assert.equal(json.options![0].name, "agenda");
  assert.equal(json.options![0].type, 3); // STRING
  assert.equal(json.options![0].required, true);
  assert.equal(json.options![0].min_length, 1);
  assert.equal(json.options![0].max_length, 4000);

  // Second option: priority (string with choices)
  assert.equal(json.options![1].name, "priority");
  assert.equal(json.options![1].type, 3); // STRING
  assert.equal(json.options![1].required, false);
  assert.equal(json.options![1].choices!.length, 4);
});

test("commandDefToDiscordApiJson sets dm_permission correctly", () => {
  const guildOnly = commandDefToDiscordApiJson({ name: "cmd", description: "d", permission: "guild_only" });
  assert.equal(guildOnly.dm_permission, false);

  const everyone = commandDefToDiscordApiJson({ name: "cmd", description: "d", permission: "everyone" });
  assert.equal(everyone.dm_permission, true);

  const defaultPerm = commandDefToDiscordApiJson({ name: "cmd", description: "d" });
  assert.equal(defaultPerm.dm_permission, true);
});

test("commandDefToDiscordApiJson handles empty options", () => {
  const json = commandDefToDiscordApiJson(validMinimalDef());
  assert.equal(json.name, "ping");
  assert.equal(json.options, undefined);
});

test("commandDefToDiscordApiJson converts all option types to Discord integers", () => {
  const def: CommandDefinition = {
    name: "test",
    description: "Type mapping test",
    options: [
      { name: "s", description: "string", type: "string", required: false },
      { name: "i", description: "integer", type: "integer", required: false },
      { name: "b", description: "boolean", type: "boolean", required: false },
      { name: "n", description: "number", type: "number", required: false },
      { name: "u", description: "user", type: "user", required: false },
      { name: "c", description: "channel", type: "channel", required: false },
      { name: "r", description: "role", type: "role", required: false },
      { name: "m", description: "mentionable", type: "mentionable", required: false },
      { name: "a", description: "attachment", type: "attachment", required: false },
    ],
  };
  const json = commandDefToDiscordApiJson(def);
  const types = json.options!.map((o) => o.type);
  assert.deepEqual(types, [3, 4, 5, 10, 6, 7, 8, 9, 11]);
});

// ═══════════════════════════════════════════════════════════════════════
// 9. createGlobalCommand — mock fetch
// ═══════════════════════════════════════════════════════════════════════

test("createGlobalCommand sends POST to correct URL and returns response", async () => {
  const fakeResponse: DiscordApiCommandResponse = {
    id: "cmd-12345",
    application_id: "123456789012345678",
    name: "meeting",
    description: "Start a multi-agent meeting",
    type: 1,
    default_member_permissions: null,
    dm_permission: true,
    nsfw: false,
    version: "1",
  };

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = "";

  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "GET";
    capturedBody = (init?.body as string) ?? "";
    return {
      ok: true,
      status: 201,
      json: async () => fakeResponse as unknown as Record<string, unknown>,
    } as Response;
  });

  try {
    const result = await createGlobalCommand(testConfig(), validMeetingDef());
    assert.equal(result.id, "cmd-12345");
    assert.equal(result.name, "meeting");
    assert.ok(capturedUrl.includes("/applications/123456789012345678/commands"));
    assert.equal(capturedMethod, "POST");

    const parsed = JSON.parse(capturedBody);
    assert.equal(parsed.name, "meeting");
    assert.equal(parsed.type, 1);
  } finally {
    resetFetchImpl();
  }
});

test("createGlobalCommand throws DiscordApiError on failure", async () => {
  setFetchImpl(mockFetchError(401, { code: 0, message: "401: Unauthorized" }));

  try {
    await assert.rejects(
      () => createGlobalCommand(testConfig(), validMeetingDef()),
      (err: unknown) => {
        assert.ok(err instanceof DiscordApiError);
        assert.equal((err as DiscordApiError).statusCode, 401);
        return true;
      },
    );
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 10. createGuildCommand — mock fetch
// ═══════════════════════════════════════════════════════════════════════

test("createGuildCommand sends POST to guild-scoped URL", async () => {
  const fakeResponse: DiscordApiCommandResponse = {
    id: "guild-cmd-999",
    application_id: "123456789012345678",
    guild_id: "guild-111",
    name: "meeting",
    description: "Start a multi-agent meeting",
    type: 1,
    default_member_permissions: null,
    dm_permission: true,
    nsfw: false,
    version: "1",
  };

  let capturedUrl = "";
  setFetchImpl(async (url) => {
    capturedUrl = String(url);
    return {
      ok: true,
      status: 201,
      json: async () => fakeResponse as unknown as Record<string, unknown>,
    } as Response;
  });

  try {
    const result = await createGuildCommand(testConfig(), "guild-111", validMeetingDef());
    assert.equal(result.id, "guild-cmd-999");
    assert.equal(result.guild_id, "guild-111");
    assert.ok(capturedUrl.includes("/guilds/guild-111/commands"));
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 11. listGlobalCommands / listGuildCommands
// ═══════════════════════════════════════════════════════════════════════

test("listGlobalCommands sends GET and returns command list", async () => {
  const commands: DiscordApiCommandResponse[] = [
    { id: "1", application_id: "app", name: "meeting", description: "d", type: 1, default_member_permissions: null, dm_permission: true, nsfw: false, version: "1" },
  ];
  setFetchImpl(mockFetchOk(commands));
  try {
    const result = await listGlobalCommands(testConfig());
    assert.equal(result.length, 1);
    assert.equal(result[0].name, "meeting");
  } finally {
    resetFetchImpl();
  }
});

test("listGuildCommands sends GET to guild-scoped URL", async () => {
  let capturedUrl = "";
  setFetchImpl(async (url) => {
    capturedUrl = String(url);
    return { ok: true, status: 200, json: async () => [] } as Response;
  });
  try {
    await listGuildCommands(testConfig(), "guild-abc");
    assert.ok(capturedUrl.includes("/guilds/guild-abc/commands"));
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 12. deleteGlobalCommand / deleteGuildCommand
// ═══════════════════════════════════════════════════════════════════════

test("deleteGlobalCommand sends DELETE with correct URL", async () => {
  let capturedUrl = "";
  let capturedMethod = "";
  setFetchImpl(async (url, init) => {
    capturedUrl = String(url);
    capturedMethod = init?.method ?? "GET";
    return { ok: true, status: 204 } as Response;
  });
  try {
    await deleteGlobalCommand(testConfig(), "cmd-to-delete");
    assert.ok(capturedUrl.endsWith("/commands/cmd-to-delete"));
    assert.equal(capturedMethod, "DELETE");
  } finally {
    resetFetchImpl();
  }
});

test("deleteGuildCommand sends DELETE to guild-scoped URL", async () => {
  let capturedUrl = "";
  setFetchImpl(async (url) => {
    capturedUrl = String(url);
    return { ok: true, status: 204 } as Response;
  });
  try {
    await deleteGuildCommand(testConfig(), "guild-xyz", "cmd-to-delete");
    assert.ok(capturedUrl.includes("/guilds/guild-xyz/commands/cmd-to-delete"));
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 13. bulkOverwriteGlobalCommands / bulkOverwriteGuildCommands
// ═══════════════════════════════════════════════════════════════════════

test("bulkOverwriteGlobalCommands sends PUT with all definitions", async () => {
  let capturedBody = "";
  setFetchImpl(async (_url, init) => {
    capturedBody = (init?.body as string) ?? "";
    return { ok: true, status: 200, json: async () => [] } as Response;
  });
  try {
    await bulkOverwriteGlobalCommands(testConfig(), [validMeetingDef(), validMinimalDef()]);
    const parsed = JSON.parse(capturedBody);
    assert.equal(parsed.length, 2);
    assert.equal(parsed[0].name, "meeting");
    assert.equal(parsed[1].name, "ping");
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 14. registerGlobalCommands — batch with validation
// ═══════════════════════════════════════════════════════════════════════

test("registerGlobalCommands rejects when validation fails", async () => {
  const badDef: CommandDefinition = { name: "BAD", description: "" };
  const result = await registerGlobalCommands(testConfig(), [badDef]);
  assert.equal(result.success, false);
  assert.ok(result.summary.includes("Validation failed"));
});

test("registerGlobalCommands reports per-command success", async () => {
  setFetchImpl(mockFetchOk({
    id: "new-cmd-1",
    application_id: "app",
    name: "meeting",
    description: "d",
    type: 1,
    default_member_permissions: null,
    dm_permission: true,
    nsfw: false,
    version: "1",
  }, 201));
  try {
    const result = await registerGlobalCommands(testConfig(), [validMeetingDef()]);
    assert.equal(result.success, true);
    assert.equal(result.entries.length, 1);
    assert.equal(result.entries[0].name, "meeting");
    assert.equal(result.entries[0].success, true);
    assert.ok(result.entries[0].id);
  } finally {
    resetFetchImpl();
  }
});

test("registerGlobalCommands reports per-command failure", async () => {
  setFetchImpl(mockFetchError(403, { code: 50001, message: "Missing Access" }));
  try {
    const result = await registerGlobalCommands(testConfig(), [validMeetingDef()]);
    assert.equal(result.success, false);
    assert.equal(result.entries.length, 1);
    assert.equal(result.entries[0].success, false);
    assert.ok(result.entries[0].error!.includes("Missing Access"));
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 15. registerGuildCommands — batch with validation
// ═══════════════════════════════════════════════════════════════════════

test("registerGuildCommands rejects when validation fails", async () => {
  const badDef: CommandDefinition = { name: "!!!bad!!!", description: "Test" };
  const result = await registerGuildCommands(testConfig(), "guild-1", [badDef]);
  assert.equal(result.success, false);
});

test("registerGuildCommands reports success when all pass", async () => {
  setFetchImpl(mockFetchOk({
    id: "gcmd-1",
    application_id: "app",
    guild_id: "guild-1",
    name: "ping",
    description: "d",
    type: 1,
    default_member_permissions: null,
    dm_permission: true,
    nsfw: false,
    version: "1",
  }, 201));
  try {
    const result = await registerGuildCommands(testConfig(), "guild-1", [validMinimalDef()]);
    assert.equal(result.success, true);
    assert.equal(result.entries[0].id, "gcmd-1");
  } finally {
    resetFetchImpl();
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 16. DiscordApiError
// ═══════════════════════════════════════════════════════════════════════

test("DiscordApiError carries status code, discord code, and operation", () => {
  const err = new DiscordApiError("Test error", 429, 10001, "create global /test");
  assert.equal(err.statusCode, 429);
  assert.equal(err.discordCode, 10001);
  assert.equal(err.operation, "create global /test");
  assert.ok(err.message.includes("Test error"));
});

test("DiscordApiError.fromResponse parses JSON error body", async () => {
  const response = {
    ok: false,
    status: 403,
    json: async () => ({ code: 50001, message: "Missing Access" }),
  } as Response;
  const err = await DiscordApiError.fromResponse(response, "test op");
  assert.equal(err.statusCode, 403);
  assert.equal(err.discordCode, 50001);
  assert.ok(err.message.includes("Missing Access"));
  assert.equal(err.operation, "test op");
});

test("DiscordApiError.fromResponse handles non-JSON response body", async () => {
  const response = {
    ok: false,
    status: 500,
    statusText: "Internal Server Error",
    json: async () => { throw new Error("not json"); },
  } as Response;
  const err = await DiscordApiError.fromResponse(response, "test");
  assert.equal(err.statusCode, 500);
  assert.ok(err.message.includes("500"));
});

// ═══════════════════════════════════════════════════════════════════════
// 17. isRateLimitError / isAuthError type guards
// ═══════════════════════════════════════════════════════════════════════

test("isRateLimitError returns true for 429", () => {
  assert.equal(isRateLimitError(new DiscordApiError("rate", 429)), true);
  assert.equal(isRateLimitError(new DiscordApiError("other", 403)), false);
  assert.equal(isRateLimitError(new Error("not api error")), false);
});

test("isAuthError returns true for 401 and 403", () => {
  assert.equal(isAuthError(new DiscordApiError("unauth", 401)), true);
  assert.equal(isAuthError(new DiscordApiError("forbidden", 403)), true);
  assert.equal(isAuthError(new DiscordApiError("not found", 404)), false);
  assert.equal(isAuthError(new Error("not api error")), false);
});

// ═══════════════════════════════════════════════════════════════════════
// 18. setFetchImpl / resetFetchImpl
// ═══════════════════════════════════════════════════════════════════════

test("setFetchImpl injects mock and resetFetchImpl restores default fetch", async () => {
  // Inject a mock that always fails, confirming injection works.
  let callCount = 0;
  setFetchImpl(async () => {
    callCount++;
    return { ok: true, status: 200, json: async () => [] } as Response;
  });

  // Use a list operation to verify the mock is used.
  await listGlobalCommands(testConfig());
  assert.equal(callCount, 1, "Mock fetch should have been called once");

  // Reset and call again — the mock should not be called again after reset.
  resetFetchImpl();

  // After reset, the module uses globalThis.fetch which will fail
  // (no real network), so we just verify the mock counter did not increase
  // from another module call.
  try {
    await listGlobalCommands(testConfig());
  } catch {
    // Expected — real fetch fails without network.
  }
  assert.equal(callCount, 1, "Mock should not be called after resetFetchImpl");
});
