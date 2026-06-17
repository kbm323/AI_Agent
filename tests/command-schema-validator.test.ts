/**
 * Tests for Command Schema Validator (Sub-AC 1b).
 *
 * Covers: valid command validation, unknown commands, missing required
 * options, type mismatches, value constraints (length, range, choices),
 * permission checks, bot mention handling, PING, unrecognized options,
 * cancel command, registry management, and type guards.
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  registerCommand,
  unregisterCommand,
  listRegisteredCommands,
  clearCommandRegistry,
  validateCommandRequest,
  isValidCommand,
  isValidationError,
  type CommandDefinition,
  type CommandValidationResult,
  type ValidationErrorCode,
} from "../src/command-schema-validator.ts";
import type {
  NormalizedCommandRequest,
  ParsedCommandOption,
  SlashCommandOptionType,
} from "../src/discord-interaction-parser.ts";

// ────────────────────────────────────────────────────────────
// Test helpers
// ────────────────────────────────────────────────────────────

function buildRequest(overrides: Partial<NormalizedCommandRequest> = {}): NormalizedCommandRequest {
  return {
    kind: "slash_command",
    interactionId: "interaction-test-1",
    interactionToken: "token-test-abc",
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Discuss Q3 strategy" },
      },
    },
    userId: "user-111",
    channelId: "channel-012",
    guildId: "guild-789",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

/** Register a minimal test command set so the registry is populated. */
function registerTestCommands(): void {
  clearCommandRegistry();

  // /meeting — full featured
  registerCommand({
    name: "meeting",
    description: "Start a meeting",
    permission: "guild_only",
    allow_bot_mention: true,
    options: [
      { name: "agenda", description: "Topic", type: "string", required: true, min_length: 1, max_length: 4000 },
      {
        name: "priority",
        description: "Priority",
        type: "string",
        required: false,
        choices: [
          { name: "P0", value: "P0" },
          { name: "P1", value: "P1" },
          { name: "P2", value: "P2" },
          { name: "P3", value: "P3" },
        ],
      },
      { name: "rounds", description: "Rounds", type: "integer", required: false, min_value: 1, max_value: 4 },
      { name: "urgent", description: "Urgent", type: "boolean", required: false },
    ],
  });

  // /cancel
  registerCommand({
    name: "cancel",
    description: "Cancel meeting",
    permission: "everyone",
    allow_bot_mention: false,
    options: [{ name: "meeting_id", description: "Meeting ID", type: "string", required: true, min_length: 1 }],
  });
}

// ────────────────────────────────────────────────────────────
// 1. Valid command — success path
// ────────────────────────────────────────────────────────────

test("validateCommandRequest returns ValidatedCommand for a valid /meeting slash command", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Discuss Q3 strategy" },
      },
    },
  });

  const result = validateCommandRequest(request);

  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");

  assert.equal(result.commandName, "meeting");
  assert.ok(result.options["agenda"]);
  assert.equal(result.options["agenda"].value, "Discuss Q3 strategy");
  assert.equal(result.options["agenda"].type, "string");
  assert.equal(result.interactionId, "interaction-test-1");
  assert.equal(result.userId, "user-111");
  assert.equal(result.kind, "slash_command");
});

test("validateCommandRequest accepts valid /meeting with all optional options", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Budget review" },
        priority: { name: "priority", type: "string", value: "P1" },
        rounds: { name: "rounds", type: "integer", value: 3 },
        urgent: { name: "urgent", type: "boolean", value: false },
      },
    },
  });

  const result = validateCommandRequest(request);

  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");

  assert.equal(result.options["agenda"].value, "Budget review");
  assert.equal(result.options["priority"].value, "P1");
  assert.equal(result.options["rounds"].value, 3);
  assert.equal(result.options["urgent"].value, false);
});

// ────────────────────────────────────────────────────────────
// 2. Unknown command
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects unknown command names", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "nonexistent",
      options: {},
    },
  });

  const result = validateCommandRequest(request);

  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].code, "UNKNOWN_COMMAND");
  assert.match(result.errors[0].message, /nonexistent/);
});

// ────────────────────────────────────────────────────────────
// 3. Missing required option
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects /meeting without agenda", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        priority: { name: "priority", type: "string", value: "P2" },
      },
    },
  });

  const result = validateCommandRequest(request);

  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const missingError = result.errors.find((e) => e.code === "MISSING_REQUIRED_OPTION");
  assert.ok(missingError, "Expected MISSING_REQUIRED_OPTION error");
  assert.equal(missingError!.option, "agenda");
});

// ────────────────────────────────────────────────────────────
// 4. Type mismatch
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects wrong option type (string instead of integer)", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Valid agenda" },
        rounds: { name: "rounds", type: "string", value: "three" }, // should be integer
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const typeError = result.errors.find((e) => e.code === "INVALID_OPTION_TYPE");
  assert.ok(typeError, "Expected INVALID_OPTION_TYPE error");
  assert.equal(typeError!.option, "rounds");
});

test("validateCommandRequest rejects wrong option type (integer instead of boolean)", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Valid agenda" },
        urgent: { name: "urgent", type: "integer", value: 1 }, // should be boolean
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const typeError = result.errors.find((e) => e.code === "INVALID_OPTION_TYPE");
  assert.ok(typeError, "Expected INVALID_OPTION_TYPE error");
  assert.equal(typeError!.option, "urgent");
});

// ────────────────────────────────────────────────────────────
// 5. Value constraints — choices
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects priority outside allowed choices", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        priority: { name: "priority", type: "string", value: "P5" }, // not in [P0, P1, P2, P3]
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const choiceError = result.errors.find((e) => e.code === "INVALID_CHOICE");
  assert.ok(choiceError, "Expected INVALID_CHOICE error");
  assert.equal(choiceError!.option, "priority");
});

test("validateCommandRequest accepts priority within allowed choices", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        priority: { name: "priority", type: "string", value: "P3" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");
  assert.equal(result.options["priority"].value, "P3");
});

// ────────────────────────────────────────────────────────────
// 6. Value constraints — numeric range
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects rounds below minimum", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        rounds: { name: "rounds", type: "integer", value: 0 }, // min is 1
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const rangeError = result.errors.find((e) => e.code === "VALUE_OUT_OF_RANGE");
  assert.ok(rangeError, "Expected VALUE_OUT_OF_RANGE error");
  assert.equal(rangeError!.option, "rounds");
});

test("validateCommandRequest rejects rounds above maximum", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        rounds: { name: "rounds", type: "integer", value: 5 }, // max is 4
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const rangeError = result.errors.find((e) => e.code === "VALUE_OUT_OF_RANGE");
  assert.ok(rangeError, "Expected VALUE_OUT_OF_RANGE error");
  assert.equal(rangeError!.option, "rounds");
});

test("validateCommandRequest accepts rounds at boundary values", () => {
  registerTestCommands();
  // min boundary
  const r1 = validateCommandRequest(buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        rounds: { name: "rounds", type: "integer", value: 1 },
      },
    },
  }));
  assert.equal(r1.valid, true);

  // max boundary
  const r4 = validateCommandRequest(buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        rounds: { name: "rounds", type: "integer", value: 4 },
      },
    },
  }));
  assert.equal(r4.valid, true);
});

// ────────────────────────────────────────────────────────────
// 7. Value constraints — string length
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects agenda that is too short (empty)", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const lenError = result.errors.find((e) => e.code === "VALUE_TOO_SHORT");
  assert.ok(lenError, "Expected VALUE_TOO_SHORT error");
  assert.equal(lenError!.option, "agenda");
});

test("validateCommandRequest rejects agenda exceeding max_length", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "x".repeat(4001) },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const lenError = result.errors.find((e) => e.code === "VALUE_TOO_LONG");
  assert.ok(lenError, "Expected VALUE_TOO_LONG error");
  assert.equal(lenError!.option, "agenda");
});

// ────────────────────────────────────────────────────────────
// 8. Permission checks
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects guild_only command in DM (no guildId)", () => {
  registerTestCommands();
  const request = buildRequest({
    guildId: undefined, // DM context
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const permError = result.errors.find((e) => e.code === "PERMISSION_DENIED");
  assert.ok(permError, "Expected PERMISSION_DENIED error");
  assert.match(permError!.message, /server/);
});

test("validateCommandRequest accepts guild_only command in guild context", () => {
  registerTestCommands();
  const request = buildRequest({
    guildId: "guild-123",
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, true);
});

test("validateCommandRequest rejects admin_only command without ADMINISTRATOR", () => {
  clearCommandRegistry();
  registerCommand({
    name: "admin-cmd",
    description: "Admin only",
    permission: "admin_only",
    options: [{ name: "arg", description: "Arg", type: "string", required: true }],
  });

  const request = buildRequest({
    guildId: "guild-123",
    command: {
      commandName: "admin-cmd",
      options: { arg: { name: "arg", type: "string", value: "test" } },
    },
  });

  const result = validateCommandRequest(request, new Set(["SEND_MESSAGES"]));
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const permError = result.errors.find((e) => e.code === "PERMISSION_DENIED");
  assert.ok(permError, "Expected PERMISSION_DENIED error");
});

test("validateCommandRequest accepts admin_only command with ADMINISTRATOR permission", () => {
  clearCommandRegistry();
  registerCommand({
    name: "admin-cmd",
    description: "Admin only",
    permission: "admin_only",
    options: [{ name: "arg", description: "Arg", type: "string", required: true }],
  });

  const request = buildRequest({
    guildId: "guild-123",
    command: {
      commandName: "admin-cmd",
      options: { arg: { name: "arg", type: "string", value: "test" } },
    },
  });

  const result = validateCommandRequest(request, new Set(["ADMINISTRATOR", "SEND_MESSAGES"]));
  assert.equal(result.valid, true);
});

// ────────────────────────────────────────────────────────────
// 9. Bot mention handling
// ────────────────────────────────────────────────────────────

test("validateCommandRequest passes through bot_mention with no command data", () => {
  registerTestCommands();
  const request: NormalizedCommandRequest = {
    kind: "bot_mention",
    interactionId: "mention-1",
    interactionToken: "tok-1",
    userId: "user-1",
    channelId: "channel-1",
    guildId: "guild-1",
    timestamp: new Date().toISOString(),
    mentionContent: "Hey @AI_Company, discuss Q3 strategy",
  };

  const result = validateCommandRequest(request);
  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");
  assert.equal(result.commandName, "mention");
  assert.equal(result.kind, "bot_mention");
});

// ────────────────────────────────────────────────────────────
// 10. PING handling
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects PING as invalid invocation kind", () => {
  registerTestCommands();
  const request: NormalizedCommandRequest = {
    kind: "ping",
    interactionId: "ping-1",
    interactionToken: "tok-1",
    userId: "user-1",
    channelId: "channel-1",
    timestamp: new Date().toISOString(),
  };

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");
  assert.equal(result.errors[0].code, "INVALID_INVOCATION_KIND");
});

// ────────────────────────────────────────────────────────────
// 11. Unrecognized options (strict mode)
// ────────────────────────────────────────────────────────────

test("validateCommandRequest flags unrecognized options", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        agenda: { name: "agenda", type: "string", value: "Test" },
        extra_unknown: { name: "extra_unknown", type: "string", value: "bad" },
        another_bad: { name: "another_bad", type: "boolean", value: true },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const unrecognizedErrors = result.errors.filter((e) => e.code === "UNRECOGNIZED_OPTION");
  assert.equal(unrecognizedErrors.length, 2);
  assert.ok(unrecognizedErrors.some((e) => e.option === "extra_unknown"));
  assert.ok(unrecognizedErrors.some((e) => e.option === "another_bad"));
});

// ────────────────────────────────────────────────────────────
// 12. Cancel command
// ────────────────────────────────────────────────────────────

test("validateCommandRequest accepts valid /cancel command", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "cancel",
      options: {
        meeting_id: { name: "meeting_id", type: "string", value: "meeting-abc-123" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");
  assert.equal(result.commandName, "cancel");
  assert.equal(result.options["meeting_id"].value, "meeting-abc-123");
});

test("validateCommandRequest rejects /cancel without meeting_id", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "cancel",
      options: {},
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  const missingError = result.errors.find((e) => e.code === "MISSING_REQUIRED_OPTION");
  assert.ok(missingError, "Expected MISSING_REQUIRED_OPTION error");
  assert.equal(missingError!.option, "meeting_id");
});

// ────────────────────────────────────────────────────────────
// 13. No command data on slash_command kind
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects slash_command with no command data", () => {
  registerTestCommands();
  const request: NormalizedCommandRequest = {
    kind: "slash_command",
    interactionId: "int-1",
    interactionToken: "tok-1",
    userId: "user-1",
    channelId: "channel-1",
    timestamp: new Date().toISOString(),
    // command is undefined
  };

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");
  assert.equal(result.errors[0].code, "NO_COMMAND_DATA");
});

// ────────────────────────────────────────────────────────────
// 14. Multiple simultaneous errors
// ────────────────────────────────────────────────────────────

test("validateCommandRequest aggregates all validation errors", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: {
        // Missing required "agenda"
        priority: { name: "priority", type: "string", value: "INVALID" },
        rounds: { name: "rounds", type: "integer", value: 99 },
        unknown_opt: { name: "unknown_opt", type: "string", value: "x" },
      },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");

  // Should have: MISSING_REQUIRED_OPTION (agenda), INVALID_CHOICE (priority),
  // VALUE_OUT_OF_RANGE (rounds), UNRECOGNIZED_OPTION (unknown_opt)
  const codes = result.errors.map((e) => e.code);
  assert.ok(codes.includes("MISSING_REQUIRED_OPTION"));
  assert.ok(codes.includes("INVALID_CHOICE"));
  assert.ok(codes.includes("VALUE_OUT_OF_RANGE"));
  assert.ok(codes.includes("UNRECOGNIZED_OPTION"));
  assert.ok(result.errors.length >= 4);
});

// ────────────────────────────────────────────────────────────
// 15. Type guards
// ────────────────────────────────────────────────────────────

test("isValidCommand returns true for ValidatedCommand", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "meeting",
      options: { agenda: { name: "agenda", type: "string", value: "Test" } },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(isValidCommand(result), true);
  assert.equal(isValidationError(result), false);
});

test("isValidationError returns true for CommandValidationError", () => {
  registerTestCommands();
  const request = buildRequest({
    command: {
      commandName: "nonexistent",
      options: {},
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(isValidationError(result), true);
  assert.equal(isValidCommand(result), false);
});

// ────────────────────────────────────────────────────────────
// 16. Registry management
// ────────────────────────────────────────────────────────────

test("command registry: register, list, unregister, clear", () => {
  clearCommandRegistry();

  // Initially empty.
  assert.equal(listRegisteredCommands().length, 0);

  // Register two commands.
  registerCommand({
    name: "cmd-a",
    description: "Test A",
    options: [{ name: "x", description: "X", type: "string", required: true }],
  });
  registerCommand({
    name: "cmd-b",
    description: "Test B",
    options: [],
  });

  assert.equal(listRegisteredCommands().length, 2);

  // Unregister one.
  const removed = unregisterCommand("cmd-a");
  assert.equal(removed, true);
  assert.equal(listRegisteredCommands().length, 1);
  assert.equal(listRegisteredCommands()[0].name, "cmd-b");

  // Unregister non-existent.
  assert.equal(unregisterCommand("not-there"), false);

  // Clear all.
  clearCommandRegistry();
  assert.equal(listRegisteredCommands().length, 0);
});

test("command registry: re-register overwrites", () => {
  clearCommandRegistry();

  registerCommand({
    name: "cmd",
    description: "v1",
    options: [],
  });

  registerCommand({
    name: "cmd",
    description: "v2",
    options: [{ name: "arg", description: "A", type: "string", required: true }],
  });

  const commands = listRegisteredCommands();
  assert.equal(commands.length, 1);
  assert.equal(commands[0].description, "v2");
  assert.equal(commands[0].options.length, 1);
});

// ────────────────────────────────────────────────────────────
// 17. Result structure fields
// ────────────────────────────────────────────────────────────

test("ValidatedCommand preserves all NormalizedCommandRequest metadata", () => {
  registerTestCommands();
  const timestamp = "2026-06-10T12:00:00.000Z";
  const request = buildRequest({
    kind: "slash_command",
    interactionId: "int-meta-1",
    interactionToken: "tok-meta-1",
    userId: "user-meta-1",
    channelId: "channel-meta-1",
    guildId: "guild-meta-1",
    timestamp,
    command: {
      commandName: "meeting",
      options: { agenda: { name: "agenda", type: "string", value: "Meta test" } },
    },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, true);
  if (!result.valid) throw new Error("Expected valid result");

  assert.equal(result.interactionId, "int-meta-1");
  assert.equal(result.interactionToken, "tok-meta-1");
  assert.equal(result.userId, "user-meta-1");
  assert.equal(result.channelId, "channel-meta-1");
  assert.equal(result.guildId, "guild-meta-1");
  assert.equal(result.timestamp, timestamp);
  assert.equal(result.kind, "slash_command");
});

test("CommandValidationError preserves interactionId", () => {
  registerTestCommands();
  const request = buildRequest({
    interactionId: "error-int-1",
    command: { commandName: "nonexistent", options: {} },
  });

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");
  assert.equal(result.interactionId, "error-int-1");
});

// ────────────────────────────────────────────────────────────
// 18. bot_mention kind with allow_bot_mention=false
// ────────────────────────────────────────────────────────────

test("validateCommandRequest rejects bot_mention for cancel (allow_bot_mention=false)", () => {
  registerTestCommands();
  const request: NormalizedCommandRequest = {
    kind: "bot_mention",
    interactionId: "mention-cancel-1",
    interactionToken: "tok-1",
    userId: "user-1",
    channelId: "channel-1",
    guildId: "guild-1",
    timestamp: new Date().toISOString(),
    command: {
      commandName: "cancel",
      options: { meeting_id: { name: "meeting_id", type: "string", value: "m-1" } },
    },
  };

  const result = validateCommandRequest(request);
  assert.equal(result.valid, false);
  if (result.valid) throw new Error("Expected error result");
  assert.equal(result.errors[0].code, "INVALID_INVOCATION_KIND");
});
