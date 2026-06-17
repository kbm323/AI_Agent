/**
 * Tests for Command Schema Builder (Sub-AC 1a-i).
 *
 * Covers:
 *  - All option builder functions produce correct output
 *  - choice() / choices() helpers
 *  - buildCommand() assembles complete definitions
 *  - Pre-built command definitions (meeting, status, cancel, knowledge)
 *  - isDiscordApiCompatible() validation predicate
 *  - Cross-validation with command-api-registrar's commandDefToDiscordApiJson
 *  - Edge cases: empty options, boundary lengths, all option types
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  // Option builders
  stringOption,
  integerOption,
  booleanOption,
  numberOption,
  channelOption,
  roleOption,
  userOption,
  mentionableOption,
  attachmentOption,
  // Choice builders
  choice,
  choices,
  // Command builder
  buildCommand,
  // Pre-built commands
  meetingCommand,
  statusCommand,
  cancelCommand,
  knowledgeCommand,
  // Validation
  isDiscordApiCompatible,
  // Types
  type BuildCommandOpts,
} from "../src/command-schema-builder.ts";

import type {
  CommandDefinition,
  CommandOptionDefinition,
  CommandOptionChoice,
} from "../src/command-schema-validator.ts";

import {
  validateCommandDefinition,
  commandDefToDiscordApiJson,
} from "../src/command-api-registrar.ts";

// ═══════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════

/** The set of valid Discord option type integers (per Discord API spec). */
const VALID_DISCORD_TYPE_INTS = new Set([3, 4, 5, 6, 7, 8, 9, 10, 11]);

/** Discord API shape for a command (minimal fields). */
interface DiscordApiShape {
  name: string;
  description: string;
  type: number;
  options?: DiscordApiOptionShape[];
  dm_permission?: boolean;
}

interface DiscordApiOptionShape {
  type: number;
  name: string;
  description: string;
  required?: boolean;
  choices?: { name: string; value: string | number }[];
  min_length?: number;
  max_length?: number;
  min_value?: number;
  max_value?: number;
}

/** Assert that a value is present and non-empty. */
function assertPresent<T>(val: T | undefined | null, msg: string): asserts val is T {
  assert.ok(val !== undefined && val !== null, msg);
}

/** Assert a string matches Discord's command name regex. */
function assertValidName(name: string): void {
  assert.match(name, /^[a-z0-9_-]{1,32}$/,
    `"${name}" does not match Discord command name pattern`);
}

// ═══════════════════════════════════════════════════════════════════════
// 1. choice() and choices() builders
// ═══════════════════════════════════════════════════════════════════════

test("choice() returns a valid CommandOptionChoice with string value", () => {
  const c = choice("P0 — Critical", "P0");
  assert.equal(c.name, "P0 — Critical");
  assert.equal(c.value, "P0");
  assert.equal(typeof c.name, "string");
  assert.equal(typeof c.value, "string");
});

test("choice() returns a valid CommandOptionChoice with numeric value", () => {
  const c = choice("Small", 1);
  assert.equal(c.name, "Small");
  assert.equal(c.value, 1);
  assert.equal(typeof c.value, "number");
});

test("choices() builds an ordered array from tuples", () => {
  const result = choices([
    ["P0 — Critical", "P0"],
    ["P1 — High", "P1"],
    ["P2 — Normal", "P2"],
    ["P3 — Low", "P3"],
  ]);
  assert.equal(result.length, 4);
  assert.equal(result[0].name, "P0 — Critical");
  assert.equal(result[0].value, "P0");
  assert.equal(result[3].name, "P3 — Low");
  assert.equal(result[3].value, "P3");
});

test("choices() handles empty array", () => {
  const result = choices([]);
  assert.equal(result.length, 0);
});

// ═══════════════════════════════════════════════════════════════════════
// 2. Option builder functions
// ═══════════════════════════════════════════════════════════════════════

// ── 2a. stringOption() ───────────────────────────────────────────────

test("stringOption() produces a valid STRING option with defaults", () => {
  const opt = stringOption("agenda", "Meeting topic and objectives");
  assert.equal(opt.name, "agenda");
  assert.equal(opt.description, "Meeting topic and objectives");
  assert.equal(opt.type, "string");
  assert.equal(opt.required, false);
});

test("stringOption() accepts required flag", () => {
  const opt = stringOption("query", "Search terms", { required: true });
  assert.equal(opt.required, true);
});

test("stringOption() accepts minLength and maxLength", () => {
  const opt = stringOption("bio", "User bio", {
    minLength: 1,
    maxLength: 500,
  });
  assert.equal(opt.min_length, 1);
  assert.equal(opt.max_length, 500);
});

test("stringOption() accepts choices", () => {
  const ch = choices([["Option A", "a"], ["Option B", "b"]]);
  const opt = stringOption("mode", "Execution mode", { choices: ch });
  assertPresent(opt.choices);
  assert.equal(opt.choices.length, 2);
  assert.equal(opt.choices[0].value, "a");
});

test("stringOption() uses explicit description over positional", () => {
  const opt = stringOption("x", "shorter", { description: "A longer description text" });
  assert.equal(opt.description, "A longer description text");
});

// ── 2b. integerOption() ──────────────────────────────────────────────

test("integerOption() produces a valid INTEGER option", () => {
  const opt = integerOption("count", "Number of items");
  assert.equal(opt.type, "integer");
  assert.equal(opt.name, "count");
  assert.equal(opt.required, false);
});

test("integerOption() accepts minValue and maxValue", () => {
  const opt = integerOption("limit", "Max results", {
    minValue: 1,
    maxValue: 100,
  });
  assert.equal(opt.min_value, 1);
  assert.equal(opt.max_value, 100);
});

test("integerOption() accepts choices", () => {
  const ch = choices([["Small", 1], ["Medium", 5], ["Large", 10]]);
  const opt = integerOption("size", "Batch size", { choices: ch });
  assertPresent(opt.choices);
  assert.equal(opt.choices.length, 3);
  assert.equal(opt.choices[2].value, 10);
});

// ── 2c. numberOption() ───────────────────────────────────────────────

test("numberOption() produces a valid NUMBER option", () => {
  const opt = numberOption("temperature", "LLM temperature");
  assert.equal(opt.type, "number");
  assert.equal(opt.name, "temperature");
});

test("numberOption() accepts minValue and maxValue", () => {
  const opt = numberOption("temperature", "LLM temperature", {
    minValue: 0,
    maxValue: 2,
  });
  assert.equal(opt.min_value, 0);
  assert.equal(opt.max_value, 2);
});

// ── 2d. booleanOption() ──────────────────────────────────────────────

test("booleanOption() produces a valid BOOLEAN option", () => {
  const opt = booleanOption("verbose", "Enable verbose output");
  assert.equal(opt.type, "boolean");
  assert.equal(opt.name, "verbose");
  assert.equal(opt.required, false);
});

test("booleanOption() accepts required flag", () => {
  const opt = booleanOption("confirm", "Confirm destructive action", {
    required: true,
  });
  assert.equal(opt.required, true);
});

// ── 2e. channelOption() ──────────────────────────────────────────────

test("channelOption() produces a valid CHANNEL option", () => {
  const opt = channelOption("target_channel", "Channel to post results");
  assert.equal(opt.type, "channel");
  assert.equal(opt.name, "target_channel");
});

// ── 2f. roleOption() ─────────────────────────────────────────────────

test("roleOption() produces a valid ROLE option", () => {
  const opt = roleOption("required_role", "Role required to invoke");
  assert.equal(opt.type, "role");
  assert.equal(opt.required, false);
});

// ── 2g. userOption() ─────────────────────────────────────────────────

test("userOption() produces a valid USER option", () => {
  const opt = userOption("assignee", "User to assign the task to");
  assert.equal(opt.type, "user");
  assert.equal(opt.required, false);
});

// ── 2h. mentionableOption() ──────────────────────────────────────────

test("mentionableOption() produces a valid MENTIONABLE option", () => {
  const opt = mentionableOption("target", "User or role to notify");
  assert.equal(opt.type, "mentionable");
});

// ── 2i. attachmentOption() ───────────────────────────────────────────

test("attachmentOption() produces a valid ATTACHMENT option", () => {
  const opt = attachmentOption("file", "File to process");
  assert.equal(opt.type, "attachment");
});

// ── 2j. All 9 option types map to valid Discord int types ────────────

test("every option builder produces a type that maps to a Discord API integer", () => {
  const builders: [string, () => CommandOptionDefinition][] = [
    ["stringOption", () => stringOption("x", "d")],
    ["integerOption", () => integerOption("x", "d")],
    ["booleanOption", () => booleanOption("x", "d")],
    ["numberOption", () => numberOption("x", "d")],
    ["channelOption", () => channelOption("x", "d")],
    ["roleOption", () => roleOption("x", "d")],
    ["userOption", () => userOption("x", "d")],
    ["mentionableOption", () => mentionableOption("x", "d")],
    ["attachmentOption", () => attachmentOption("x", "d")],
  ];

  for (const [name, fn] of builders) {
    const opt = fn();
    // Convert via the registrar's mapping.
    const json = commandDefToDiscordApiJson(
      buildCommand("test", "test", [opt]),
    );
    assertPresent(json.options, `${name}: command should have options`);
    assert.ok(
      VALID_DISCORD_TYPE_INTS.has(json.options[0].type),
      `${name}: type int ${json.options[0].type} not in valid Discord set`,
    );
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 3. buildCommand() — command assembly
// ═══════════════════════════════════════════════════════════════════════

test("buildCommand() produces a minimal valid CommandDefinition", () => {
  const def = buildCommand("ping", "Check if alive");
  assert.equal(def.name, "ping");
  assert.equal(def.description, "Check if alive");
  assert.deepEqual(def.options, []);
  assert.equal(def.permission, undefined);
});

test("buildCommand() includes options in order", () => {
  const def = buildCommand("search", "Search the knowledge base", [
    stringOption("query", "Search terms", { required: true }),
    integerOption("limit", "Max results", { minValue: 1, maxValue: 50 }),
  ]);

  assert.equal(def.options.length, 2);
  assert.equal(def.options[0].name, "query");
  assert.equal(def.options[1].name, "limit");
});

test("buildCommand() accepts permission override", () => {
  const def = buildCommand("admin", "Admin command", [], {
    permission: "admin_only",
  });
  assert.equal(def.permission, "admin_only");
});

test("buildCommand() accepts allowBotMention", () => {
  const def = buildCommand("meeting", "Start meeting", [], {
    allowBotMention: true,
  });
  assert.equal(def.allow_bot_mention, true);
});

test("buildCommand() with permission='guild_only'", () => {
  const def = buildCommand("meeting", "Start meeting", [], {
    permission: "guild_only",
  });
  assert.equal(def.permission, "guild_only");
});

// ═══════════════════════════════════════════════════════════════════════
// 4. Pre-built command definitions
// ═══════════════════════════════════════════════════════════════════════

// ── 4a. meetingCommand() ─────────────────────────────────────────────

test("meetingCommand() has correct name and description", () => {
  const def = meetingCommand();
  assert.equal(def.name, "meeting");
  assertValidName(def.name);
  assert.ok(def.description.length >= 1 && def.description.length <= 100,
    `description length ${def.description.length} out of range`);
});

test("meetingCommand() has required 'agenda' option first", () => {
  const def = meetingCommand();
  const agenda = def.options[0];
  assert.equal(agenda.name, "agenda");
  assert.equal(agenda.type, "string");
  assert.equal(agenda.required, true);
  assert.equal(agenda.min_length, 1);
  assert.equal(agenda.max_length, 4000);
});

test("meetingCommand() has optional 'priority' option with choices", () => {
  const def = meetingCommand();
  const prio = def.options[1];
  assert.equal(prio.name, "priority");
  assert.equal(prio.type, "string");
  assert.equal(prio.required, false);
  assertPresent(prio.choices);
  assert.equal(prio.choices.length, 4);
  assert.equal(prio.choices[0].value, "P0");
  assert.equal(prio.choices[3].value, "P3");
});

test("meetingCommand() is guild_only with bot mention allowed", () => {
  const def = meetingCommand();
  assert.equal(def.permission, "guild_only");
  assert.equal(def.allow_bot_mention, true);
});

test("meetingCommand() passes Discord API schema validation", () => {
  const result = validateCommandDefinition(meetingCommand());
  assert.equal(result.valid, true,
    `meetingCommand failed validation: ${JSON.stringify(result.issues)}`);
});

test("meetingCommand() converts to Discord API JSON without error", () => {
  const json = commandDefToDiscordApiJson(meetingCommand());
  assert.equal(json.name, "meeting");
  assert.equal(json.type, 1); // CHAT_INPUT
  assert.equal(json.dm_permission, false); // guild_only → DM false
  assertPresent(json.options);
  assert.equal(json.options.length, 2);
  // First option (agenda) should be type 3 (STRING) and required.
  assert.equal(json.options[0].type, 3);
  assert.equal(json.options[0].required, true);
  assert.equal(json.options[1].type, 3);
  assertPresent(json.options[1].choices);
  assert.equal(json.options[1].choices.length, 4);
});

// ── 4b. statusCommand() ──────────────────────────────────────────────

test("statusCommand() has correct name and description", () => {
  const def = statusCommand();
  assert.equal(def.name, "status");
  assertValidName(def.name);
  assert.ok(def.description.length >= 1);
});

test("statusCommand() has no options", () => {
  const def = statusCommand();
  assert.deepEqual(def.options, []);
});

test("statusCommand() passes Discord API schema validation", () => {
  const result = validateCommandDefinition(statusCommand());
  assert.equal(result.valid, true,
    `statusCommand failed: ${JSON.stringify(result.issues)}`);
});

// ── 4c. cancelCommand() ──────────────────────────────────────────────

test("cancelCommand() has required meeting_id option", () => {
  const def = cancelCommand();
  assert.equal(def.name, "cancel");
  const mid = def.options[0];
  assert.equal(mid.name, "meeting_id");
  assert.equal(mid.type, "string");
  assert.equal(mid.required, true);
  assert.equal(mid.max_length, 64);
});

test("cancelCommand() has optional reason option", () => {
  const def = cancelCommand();
  const reason = def.options[1];
  assert.equal(reason.name, "reason");
  assert.equal(reason.required, false);
  assert.equal(reason.max_length, 1000);
});

test("cancelCommand() passes Discord API schema validation", () => {
  const result = validateCommandDefinition(cancelCommand());
  assert.equal(result.valid, true,
    `cancelCommand failed: ${JSON.stringify(result.issues)}`);
});

// ── 4d. knowledgeCommand() ───────────────────────────────────────────

test("knowledgeCommand() has query option with required=true", () => {
  const def = knowledgeCommand();
  assert.equal(def.name, "knowledge");
  const query = def.options[0];
  assert.equal(query.name, "query");
  assert.equal(query.required, true);
  assert.equal(query.max_length, 500);
});

test("knowledgeCommand() has layer option with 4 choices", () => {
  const def = knowledgeCommand();
  const layer = def.options[1];
  assert.equal(layer.name, "layer");
  assert.equal(layer.required, false);
  assertPresent(layer.choices);
  assert.equal(layer.choices.length, 4);
  assert.equal(layer.choices[0].value, "L0");
  assert.equal(layer.choices[3].value, "L3");
});

test("knowledgeCommand() passes Discord API schema validation", () => {
  const result = validateCommandDefinition(knowledgeCommand());
  assert.equal(result.valid, true,
    `knowledgeCommand failed: ${JSON.stringify(result.issues)}`);
});

// ═══════════════════════════════════════════════════════════════════════
// 5. All pre-built commands pass validation and convert to Discord JSON
// ═══════════════════════════════════════════════════════════════════════

test("all four pre-built commands pass validateCommandDefinition", () => {
  const commands = [
    meetingCommand(),
    statusCommand(),
    cancelCommand(),
    knowledgeCommand(),
  ];
  for (const def of commands) {
    const result = validateCommandDefinition(def);
    assert.equal(result.valid, true,
      `${def.name}: ${JSON.stringify(result.issues)}`);
  }
});

test("all four pre-built commands convert to Discord API JSON", () => {
  const commands = [
    meetingCommand(),
    statusCommand(),
    cancelCommand(),
    knowledgeCommand(),
  ];
  for (const def of commands) {
    const json = commandDefToDiscordApiJson(def);
    assert.equal(json.name, def.name);
    assert.equal(json.type, 1); // CHAT_INPUT
    assert.equal(typeof json.description, "string");
    if (def.options.length > 0) {
      assertPresent(json.options);
      assert.equal(json.options.length, def.options.length);
    }
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 6. isDiscordApiCompatible() predicate
// ═══════════════════════════════════════════════════════════════════════

test("isDiscordApiCompatible returns true for meetingCommand", () => {
  assert.equal(isDiscordApiCompatible(meetingCommand()), true);
});

test("isDiscordApiCompatible returns true for statusCommand", () => {
  assert.equal(isDiscordApiCompatible(statusCommand()), true);
});

test("isDiscordApiCompatible returns true for command with all option types", () => {
  const def = buildCommand("alltypes", "All option types", [
    stringOption("s", "string"),
    integerOption("i", "integer"),
    booleanOption("b", "boolean"),
    numberOption("n", "number"),
    channelOption("ch", "channel"),
    roleOption("r", "role"),
    userOption("u", "user"),
    mentionableOption("m", "mentionable"),
    attachmentOption("a", "attachment"),
  ]);
  assert.equal(isDiscordApiCompatible(def), true);
});

test("isDiscordApiCompatible returns false for missing name", () => {
  const def = { description: "no name", options: [] } as CommandDefinition;
  assert.equal(isDiscordApiCompatible(def), false);
});

test("isDiscordApiCompatible returns false for missing description", () => {
  const def = { name: "test", options: [] } as CommandDefinition;
  assert.equal(isDiscordApiCompatible(def), false);
});

test("isDiscordApiCompatible returns false for non-array options", () => {
  const def = {
    name: "test",
    description: "desc",
    options: "not-an-array",
  } as unknown as CommandDefinition;
  assert.equal(isDiscordApiCompatible(def), false);
});

test("isDiscordApiCompatible returns false for option with invalid type", () => {
  const def = buildCommand("bad", "bad option type", [
    { name: "x", description: "x", type: "float" as any, required: false },
  ]);
  assert.equal(isDiscordApiCompatible(def), false);
});

test("isDiscordApiCompatible returns false for option with missing name", () => {
  const def = {
    name: "bad",
    description: "bad",
    options: [{ description: "no name", type: "string", required: false }],
  } as unknown as CommandDefinition;
  assert.equal(isDiscordApiCompatible(def), false);
});

test("isDiscordApiCompatible returns false for choice with boolean value", () => {
  const def = buildCommand("bad", "bool choice", [
    {
      name: "x",
      description: "x",
      type: "string",
      required: false,
      choices: [{ name: "c", value: true } as unknown as CommandOptionChoice],
    },
  ]);
  assert.equal(isDiscordApiCompatible(def), false);
});

// ═══════════════════════════════════════════════════════════════════════
// 7. Builder output purity — multiple calls produce independent objects
// ═══════════════════════════════════════════════════════════════════════

test("builders produce independent objects (no shared references)", () => {
  const a = meetingCommand();
  const b = meetingCommand();
  // Mutating one should not affect the other.
  const opts = a.options as CommandOptionDefinition[];
  (opts[0] as any).mutated = true;
  assert.equal((b.options[0] as any).mutated, undefined,
    "builder output should be independent");
});

test("choice() produces independent objects", () => {
  const c1 = choice("A", 1);
  const c2 = choice("A", 1);
  (c1 as any).extra = true;
  assert.equal((c2 as any).extra, undefined);
});

// ═══════════════════════════════════════════════════════════════════════
// 8. Boundary / edge cases
// ═══════════════════════════════════════════════════════════════════════

test("buildCommand accepts 25 options (Discord max)", () => {
  const opts = Array.from({ length: 25 }, (_, i) =>
    stringOption(`opt_${i}`, `Option ${i}`));
  const def = buildCommand("bulk", "Bulk command", opts);
  assert.equal(def.options.length, 25);
});

test("buildCommand with 0 options produces empty array", () => {
  const def = buildCommand("empty", "No options");
  assert.deepEqual(def.options, []);
});

test("stringOption with empty choices array produces no choices field", () => {
  const opt = stringOption("x", "x", { choices: [] });
  assert.equal(opt.choices, undefined);
});

test("integerOption with no constraints has no min/max fields", () => {
  const opt = integerOption("x", "x");
  assert.equal(opt.min_value, undefined);
  assert.equal(opt.max_value, undefined);
});

test("booleanOption has no min/max/choices fields", () => {
  const opt = booleanOption("x", "x");
  assert.equal((opt as any).min_value, undefined);
  assert.equal((opt as any).max_value, undefined);
  assert.equal((opt as any).choices, undefined);
});

test("channelOption has no choices field", () => {
  const opt = channelOption("x", "x");
  // Channel options can't have choices in Discord API.
  assert.equal(opt.choices, undefined);
});

// ═══════════════════════════════════════════════════════════════════════
// 9. Discord API JSON shape conformance
// ═══════════════════════════════════════════════════════════════════════

test("Discord API JSON has all required command fields", () => {
  const json = commandDefToDiscordApiJson(meetingCommand());
  // Required fields per Discord API spec for CHAT_INPUT commands:
  assert.equal(typeof json.name, "string");
  assert.equal(typeof json.description, "string");
  assert.equal(json.type, 1);
});

test("Discord API JSON options have correct structure", () => {
  const json = commandDefToDiscordApiJson(meetingCommand());
  assertPresent(json.options);
  for (const opt of json.options) {
    assert.equal(typeof opt.type, "number");
    assert.ok(VALID_DISCORD_TYPE_INTS.has(opt.type),
      `option type ${opt.type} not a valid Discord type integer`);
    assert.equal(typeof opt.name, "string");
    assert.equal(typeof opt.description, "string");
  }
});

test("Discord API JSON: choices have name and value", () => {
  const json = commandDefToDiscordApiJson(meetingCommand());
  assertPresent(json.options);
  const prioOpt = json.options[1];
  assertPresent(prioOpt.choices);
  for (const c of prioOpt.choices) {
    assert.equal(typeof c.name, "string");
    assert.ok(c.name.length > 0);
    assert.ok(typeof c.value === "string" || typeof c.value === "number");
  }
});

test("Discord API JSON: all four pre-built commands have type=1 (CHAT_INPUT)", () => {
  const commands = [meetingCommand(), statusCommand(), cancelCommand(), knowledgeCommand()];
  for (const def of commands) {
    const json = commandDefToDiscordApiJson(def);
    assert.equal(json.type, 1,
      `${def.name} should be type 1 (CHAT_INPUT)`);
  }
});
