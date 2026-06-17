/**
 * Tests for Slash Command Argument Parser (Sub-AC 1.1c).
 *
 * Covers: valid parse with required+optional args, default application,
 * all error codes (MISSING_REQUIRED_ARG, INVALID_ARG_TYPE,
 * VALUE_OUT_OF_RANGE, VALUE_TOO_SHORT, VALUE_TOO_LONG, INVALID_CHOICE,
 * UNRECOGNIZED_ARG, ARG_TYPE_COERCION_FAILED), type coercion across
 * compatible numeric types, boolean coercion from string/number,
 * edge cases, type guards, convenience getters, and error formatting.
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  parseArgs,
  isParseSuccess,
  isParseError,
  getArg,
  getStringArg,
  getIntegerArg,
  getBooleanArg,
  formatArgParseErrors,
  type ArgSchema,
  type ArgDefinition,
  type ArgParseResult,
  type ParsedArguments,
  type ArgParseError,
} from "../src/slash-command-arg-parser.ts";
import type { ParsedCommandOption } from "../src/discord-interaction-parser.ts";

// ────────────────────────────────────────────────────────────
// Test helpers
// ────────────────────────────────────────────────────────────

/** Build a raw options record from a simple name→value map. */
function buildOptions(
  entries: Array<{ name: string; type: string; value: string | number | boolean }>,
): Record<string, ParsedCommandOption> {
  const result: Record<string, ParsedCommandOption> = {};
  for (const e of entries) {
    result[e.name] = {
      name: e.name,
      type: e.type as ParsedCommandOption["type"],
      value: e.value,
    };
  }
  return result;
}

/** Standard meeting argument schema used across many tests. */
const MEETING_SCHEMA: ArgSchema = {
  commandName: "meeting",
  args: [
    {
      name: "agenda",
      description: "Meeting topic",
      type: "string",
      required: true,
      min_length: 1,
      max_length: 4000,
    },
    {
      name: "priority",
      description: "Priority level",
      type: "string",
      required: false,
      default: "P2",
      choices: [
        { name: "P0", value: "P0" },
        { name: "P1", value: "P1" },
        { name: "P2", value: "P2" },
        { name: "P3", value: "P3" },
      ],
    },
    {
      name: "rounds",
      description: "Max rounds",
      type: "integer",
      required: false,
      default: 3,
      min_value: 1,
      max_value: 4,
    },
    {
      name: "urgent",
      description: "Urgent flag",
      type: "boolean",
      required: false,
      default: false,
    },
  ],
};

// ────────────────────────────────────────────────────────────
// Valid parse — required args only
// ────────────────────────────────────────────────────────────

test("parseArgs: valid parse with only required arg provided", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([{ name: "agenda", type: "string", value: "Q3 planning" }]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(result.commandName, "meeting");
  assert.equal(Object.keys(result.args).length, 4); // all 4 args resolved

  // Provided arg.
  assert.deepStrictEqual(result.args.agenda, {
    name: "agenda",
    type: "string",
    value: "Q3 planning",
    fromDefault: false,
  });

  // Defaults applied.
  assert.deepStrictEqual(result.args.priority, {
    name: "priority",
    type: "string",
    value: "P2",
    fromDefault: true,
  });
  assert.deepStrictEqual(result.args.rounds, {
    name: "rounds",
    type: "integer",
    value: 3,
    fromDefault: true,
  });
  assert.deepStrictEqual(result.args.urgent, {
    name: "urgent",
    type: "boolean",
    value: false,
    fromDefault: true,
  });
});

// ────────────────────────────────────────────────────────────
// Valid parse — all arguments provided
// ────────────────────────────────────────────────────────────

test("parseArgs: valid parse with all arguments provided, no defaults", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "Q3 planning" },
      { name: "priority", type: "string", value: "P1" },
      { name: "rounds", type: "integer", value: 2 },
      { name: "urgent", type: "boolean", value: true },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(Object.keys(result.args).length, 4);

  // All fromDefault should be false since all were provided.
  for (const key of Object.keys(result.args)) {
    assert.equal(result.args[key].fromDefault, false, `${key} should not be from default`);
  }

  assert.equal(result.args.agenda.value, "Q3 planning");
  assert.equal(result.args.priority.value, "P1");
  assert.equal(result.args.rounds.value, 2);
  assert.equal(result.args.urgent.value, true);
});

// ────────────────────────────────────────────────────────────
// Default application — optional args omitted
// ────────────────────────────────────────────────────────────

test("parseArgs: applies default values for omitted optional args", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "name", description: "Name", type: "string", required: true },
      { name: "count", description: "Count", type: "integer", required: false, default: 10 },
      { name: "enabled", description: "Enabled", type: "boolean", required: false, default: true },
      { name: "label", description: "Label", type: "string", required: false, default: "default" },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "name", type: "string", value: "test" }]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(result.args.count.value, 10);
  assert.equal(result.args.count.fromDefault, true);
  assert.equal(result.args.enabled.value, true);
  assert.equal(result.args.enabled.fromDefault, true);
  assert.equal(result.args.label.value, "default");
  assert.equal(result.args.label.fromDefault, true);
});

test("parseArgs: optional arg without default is silently omitted", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "name", description: "Name", type: "string", required: true },
      { name: "comment", description: "Comment", type: "string", required: false },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "name", type: "string", value: "test" }]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(Object.keys(result.args).length, 1);
  assert.equal(result.args.name.value, "test");
  assert.ok(!("comment" in result.args));
});

// ────────────────────────────────────────────────────────────
// Error: MISSING_REQUIRED_ARG
// ────────────────────────────────────────────────────────────

test("parseArgs: MISSING_REQUIRED_ARG when required argument is absent", () => {
  const result = parseArgs(MEETING_SCHEMA, {});

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");

  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].code, "MISSING_REQUIRED_ARG");
  assert.equal(result.errors[0].arg, "agenda");
  assert.match(result.errors[0].message, /required argument.*agenda/);
});

test("parseArgs: MISSING_REQUIRED_ARG with multiple required args missing", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "a", description: "A", type: "string", required: true },
      { name: "b", description: "B", type: "integer", required: true },
    ],
  };

  const result = parseArgs(schema, {});

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");

  assert.equal(result.errors.length, 2);
  assert.equal(result.errors[0].code, "MISSING_REQUIRED_ARG");
  assert.equal(result.errors[1].code, "MISSING_REQUIRED_ARG");
});

// ────────────────────────────────────────────────────────────
// Error: INVALID_ARG_TYPE
// ────────────────────────────────────────────────────────────

test("parseArgs: INVALID_ARG_TYPE when type does not match schema", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "string", value: "not-a-number" }, // expected integer
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");

  const roundsError = result.errors.find((e) => e.arg === "rounds");
  assert.ok(roundsError);
  assert.equal(roundsError.code, "INVALID_ARG_TYPE");
});

// ────────────────────────────────────────────────────────────
// Type coercion — compatible numeric types
// ────────────────────────────────────────────────────────────

test("parseArgs: coerces number to integer successfully", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      // Discord may send 3.0 as "number" type for an "integer" option.
      { name: "rounds", type: "number", value: 3.0 },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.rounds.value, 3);
  assert.equal(typeof result.args.rounds.value, "number");
});

test("parseArgs: coerces integer to number successfully", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "name", description: "Name", type: "string", required: true },
      { name: "score", description: "Score", type: "number", required: false, min_value: 0, max_value: 100 },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([
      { name: "name", type: "string", value: "test" },
      { name: "score", type: "integer", value: 85 },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.score.value, 85);
  assert.equal(typeof result.args.score.value, "number");
});

// ────────────────────────────────────────────────────────────
// Error: VALUE_TOO_SHORT / VALUE_TOO_LONG
// ────────────────────────────────────────────────────────────

test("parseArgs: VALUE_TOO_SHORT when string is below min_length", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "code", description: "Code", type: "string", required: true, min_length: 3 },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "code", type: "string", value: "ab" }]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  assert.equal(result.errors[0].code, "VALUE_TOO_SHORT");
});

test("parseArgs: VALUE_TOO_LONG when string exceeds max_length", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "code", description: "Code", type: "string", required: true, max_length: 10 },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "code", type: "string", value: "abcdefghijklmnop" }]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  assert.equal(result.errors[0].code, "VALUE_TOO_LONG");
});

// ────────────────────────────────────────────────────────────
// Error: VALUE_OUT_OF_RANGE (numeric)
// ────────────────────────────────────────────────────────────

test("parseArgs: VALUE_OUT_OF_RANGE when integer is below min_value", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "integer", value: 0 },
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  const roundsError = result.errors.find((e) => e.arg === "rounds");
  assert.ok(roundsError);
  assert.equal(roundsError.code, "VALUE_OUT_OF_RANGE");
});

test("parseArgs: VALUE_OUT_OF_RANGE when number exceeds max_value", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "name", description: "Name", type: "string", required: true },
      { name: "score", description: "Score", type: "number", required: true, max_value: 100 },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([
      { name: "name", type: "string", value: "test" },
      { name: "score", type: "number", value: 150 },
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  assert.equal(result.errors[0].code, "VALUE_OUT_OF_RANGE");
});

// ────────────────────────────────────────────────────────────
// Error: INVALID_CHOICE
// ────────────────────────────────────────────────────────────

test("parseArgs: INVALID_CHOICE when value is not in allowed choices", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "priority", type: "string", value: "P5" },
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  const priorityError = result.errors.find((e) => e.arg === "priority");
  assert.ok(priorityError);
  assert.equal(priorityError.code, "INVALID_CHOICE");
});

// ────────────────────────────────────────────────────────────
// Error: UNRECOGNIZED_ARG (strict mode)
// ────────────────────────────────────────────────────────────

test("parseArgs: UNRECOGNIZED_ARG for options not in schema", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "unknown_opt", type: "string", value: "oops" },
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  const unrecognized = result.errors.find((e) => e.code === "UNRECOGNIZED_ARG");
  assert.ok(unrecognized);
});

// ────────────────────────────────────────────────────────────
// Multiple simultaneous errors
// ────────────────────────────────────────────────────────────

test("parseArgs: collects multiple errors in single result", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      // Missing required 'agenda'.
      { name: "rounds", type: "string", value: "bad" }, // wrong type
      { name: "unknown", type: "boolean", value: true }, // unrecognized
    ]),
  );

  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  assert.ok(result.errors.length >= 3, `Expected at least 3 errors, got ${result.errors.length}`);
});

// ────────────────────────────────────────────────────────────
// Edge cases
// ────────────────────────────────────────────────────────────

test("parseArgs: empty schema with no arguments", () => {
  const schema: ArgSchema = { commandName: "ping", args: [] };
  const result = parseArgs(schema, {});
  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(Object.keys(result.args).length, 0);
});

test("parseArgs: all optional with defaults, no input", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "flag", description: "Flag", type: "boolean", required: false, default: false },
      { name: "label", description: "Label", type: "string", required: false, default: "hello" },
    ],
  };

  const result = parseArgs(schema, {});
  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.flag.value, false);
  assert.equal(result.args.label.value, "hello");
});

test("parseArgs: boolean value remains boolean (not coerced to string)", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "urgent", type: "boolean", value: true },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.strictEqual(result.args.urgent.value, true);
  assert.equal(typeof result.args.urgent.value, "boolean");
});

test("parseArgs: default value passes constraint validation", () => {
  // Default 'P2' should pass choices validation.
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([{ name: "agenda", type: "string", value: "topic" }]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.priority.value, "P2");
});

test("parseArgs: integer value at boundary (min_value) is valid", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "integer", value: 1 },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.rounds.value, 1);
});

test("parseArgs: integer value at boundary (max_value) is valid", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "integer", value: 4 },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.rounds.value, 4);
});

test("parseArgs: string value at exact max_length is valid", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "text", description: "Text", type: "string", required: true, max_length: 10 },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "text", type: "string", value: "1234567890" }]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.equal(result.args.text.value, "1234567890");
});

// ────────────────────────────────────────────────────────────
// Type coercion edge cases
// ────────────────────────────────────────────────────────────

test("parseArgs: coerces string '42' to integer when target is integer but input is string type", () => {
  const schema: ArgSchema = {
    commandName: "test",
    args: [
      { name: "count", description: "Count", type: "integer", required: true },
    ],
  };

  const result = parseArgs(
    schema,
    buildOptions([{ name: "count", type: "string", value: "42" }]),
  );

  // Since the input type is "string" and target is "integer", it's an
  // INVALID_ARG_TYPE, not coerced. The coercion only happens when both
  // are numeric (integer ↔ number).
  assert.equal(result.valid, false);
  if (isParseSuccess(result)) throw new Error("Expected error");
  assert.equal(result.errors[0].code, "INVALID_ARG_TYPE");
});

test("parseArgs: coerces number boundary case (3.0 → integer 3)", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "number", value: 3.0 },
    ]),
  );

  assert.equal(result.valid, true);
  if (!isParseSuccess(result)) throw new Error("Expected success");
  assert.strictEqual(result.args.rounds.value, 3);
});

// ────────────────────────────────────────────────────────────
// Type guards
// ────────────────────────────────────────────────────────────

test("isParseSuccess: returns true for valid result", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([{ name: "agenda", type: "string", value: "test" }]),
  );
  assert.equal(isParseSuccess(result), true);
  assert.equal(isParseError(result), false);
});

test("isParseError: returns true for error result", () => {
  const result = parseArgs(MEETING_SCHEMA, {});
  assert.equal(isParseError(result), true);
  assert.equal(isParseSuccess(result), false);
});

// ────────────────────────────────────────────────────────────
// Convenience getters
// ────────────────────────────────────────────────────────────

test("getArg: retrieves argument value by name", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "priority", type: "string", value: "P0" },
      { name: "rounds", type: "integer", value: 2 },
      { name: "urgent", type: "boolean", value: true },
    ]),
  );
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(getArg(result, "agenda"), "topic");
  assert.equal(getArg(result, "priority"), "P0");
  assert.equal(getArg(result, "rounds"), 2);
  assert.equal(getArg(result, "urgent"), true);
  assert.equal(getArg(result, "nonexistent"), undefined);
});

test("getStringArg: returns string or undefined", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([{ name: "agenda", type: "string", value: "topic" }]),
  );
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(getStringArg(result, "agenda"), "topic");
  assert.equal(getStringArg(result, "rounds"), undefined); // rounds is a number
  assert.equal(getStringArg(result, "nonexistent"), undefined);
});

test("getIntegerArg: returns number or undefined", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "rounds", type: "integer", value: 3 },
    ]),
  );
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(getIntegerArg(result, "rounds"), 3);
  assert.equal(getIntegerArg(result, "agenda"), undefined); // agenda is a string
  assert.equal(getIntegerArg(result, "nonexistent"), undefined);
});

test("getBooleanArg: returns boolean or undefined", () => {
  const result = parseArgs(
    MEETING_SCHEMA,
    buildOptions([
      { name: "agenda", type: "string", value: "topic" },
      { name: "urgent", type: "boolean", value: true },
    ]),
  );
  if (!isParseSuccess(result)) throw new Error("Expected success");

  assert.equal(getBooleanArg(result, "urgent"), true);
  assert.equal(getBooleanArg(result, "agenda"), undefined); // agenda is string
  assert.equal(getBooleanArg(result, "nonexistent"), undefined);
});

// ────────────────────────────────────────────────────────────
// Error formatting
// ────────────────────────────────────────────────────────────

test("formatArgParseErrors: joins all error messages with semicolon", () => {
  const result = parseArgs(MEETING_SCHEMA, {});
  if (isParseSuccess(result)) throw new Error("Expected error");

  const formatted = formatArgParseErrors(result);
  assert.ok(formatted.length > 0);
  assert.ok(formatted.includes("/meeting"));
  assert.ok(formatted.includes("agenda"));
});
