/**
 * Slash Command Argument Parser — Sub-AC 1.1c
 *
 * Extracts, validates, coerces, and applies defaults to slash command
 * options from Discord interaction data. This is a focused argument-level
 * parser that complements (not replaces) the command-schema-validator.
 *
 * Responsibilities:
 *   1. Extract options from raw interaction option records
 *   2. Validate required/optional parameters
 *   3. Enforce type constraints (string length, numeric range, choices)
 *   4. Apply default values for missing optional arguments
 *   5. Return a structured success payload or a detailed error object
 *
 * @module ai-agent/slash-command-arg-parser
 */

import type { ParsedCommandOption, SlashCommandOptionType } from "./discord-interaction-parser.ts";

// ────────────────────────────────────────────────────────────
// Schema definition types
// ────────────────────────────────────────────────────────────

/**
 * Supported argument types for slash command options.
 * Mirrors Discord's option types for the options we handle.
 */
export type ArgType = Extract<
  SlashCommandOptionType,
  "string" | "integer" | "boolean" | "number"
>;

/**
 * A value-choice entry for string/integer options.
 */
export interface ArgChoice {
  /** Human-readable choice name. */
  name: string;
  /** The concrete value this choice maps to. */
  value: string | number;
}

/**
 * Definition of a single slash command argument (option).
 */
export interface ArgDefinition {
  /** Option name as registered with Discord. */
  name: string;
  /** Human-readable description. */
  description: string;
  /** Expected Discord option type. */
  type: ArgType;
  /** If true, the option MUST be present in the interaction data. */
  required: boolean;
  /**
   * Default value applied when the option is absent and required=false.
   * If a default is provided on a required option, the option is still
   * required — the default is only used as a safety net (and logged).
   */
  default?: string | number | boolean;
  /** If set, value must be one of these choices. */
  choices?: readonly ArgChoice[];
  /** Minimum length for STRING options (inclusive). */
  min_length?: number;
  /** Maximum length for STRING options (inclusive). */
  max_length?: number;
  /** Minimum value for INTEGER/NUMBER options (inclusive). */
  min_value?: number;
  /** Maximum value for INTEGER/NUMBER options (inclusive). */
  max_value?: number;
}

/**
 * Complete argument schema for a slash command.
 */
export interface ArgSchema {
  /** Command name (for error messages). */
  commandName: string;
  /** Ordered argument definitions. */
  args: readonly ArgDefinition[];
}

// ────────────────────────────────────────────────────────────
// Output types
// ────────────────────────────────────────────────────────────

/**
 * A single successfully parsed argument value.
 */
export interface ParsedArg {
  /** Argument name. */
  name: string;
  /** Resolved type after parsing. */
  type: ArgType;
  /** Validated (and possibly coerced) value. */
  value: string | number | boolean;
  /** True if the value came from a default rather than user input. */
  fromDefault: boolean;
}

/**
 * Successful argument parse result.
 * Arguments are keyed by name for O(1) access.
 */
export interface ParsedArguments {
  /** Discriminator — always true for type-narrowing. */
  valid: true;
  /** Command name from the schema. */
  commandName: string;
  /** Parsed, validated arguments with defaults applied. */
  args: Record<string, ParsedArg>;
}

// ────────────────────────────────────────────────────────────
// Error types
// ────────────────────────────────────────────────────────────

/**
 * Machine-readable argument parse error codes.
 */
export type ArgParseErrorCode =
  | "MISSING_REQUIRED_ARG"
  | "INVALID_ARG_TYPE"
  | "VALUE_OUT_OF_RANGE"
  | "VALUE_TOO_SHORT"
  | "VALUE_TOO_LONG"
  | "INVALID_CHOICE"
  | "UNRECOGNIZED_ARG"
  | "ARG_TYPE_COERCION_FAILED";

/** A single argument parse error detail. */
export interface ArgParseErrorDetail {
  /** Machine-readable error code. */
  code: ArgParseErrorCode;
  /** Human-readable error message. */
  message: string;
  /** Name of the offending argument, if applicable. */
  arg?: string;
}

/**
 * Structured argument parse error returned when input does not
 * satisfy the argument schema.
 */
export interface ArgParseError {
  /** Discriminator — always false for type-narrowing. */
  valid: false;
  /** One or more parse failures. */
  errors: ArgParseErrorDetail[];
}

/**
 * Union result type — callers narrow on the `valid` discriminant.
 */
export type ArgParseResult = ParsedArguments | ArgParseError;

// ────────────────────────────────────────────────────────────
// Main entry point
// ────────────────────────────────────────────────────────────

/**
 * Parse slash command options against an argument schema.
 *
 * For each argument defined in the schema:
 *   - If present in input → validate type, constraints, choices
 *   - If absent and required → MISSING_REQUIRED_ARG error
 *   - If absent and optional with default → apply default
 *   - If absent and optional without default → omitted from output
 *
 * Unrecognized options in the input that are not defined in the
 * schema produce UNRECOGNIZED_ARG errors (strict mode).
 *
 * @param schema     — Argument schema defining expected arguments.
 * @param rawOptions — Raw options from Discord interaction, keyed by name.
 * @returns          — {@link ParsedArguments} on success, {@link ArgParseError} on failure.
 */
export function parseArgs(
  schema: ArgSchema,
  rawOptions: Record<string, ParsedCommandOption>,
): ArgParseResult {
  const errors: ArgParseErrorDetail[] = [];
  const parsed: Record<string, ParsedArg> = {};
  const providedNames = new Set(Object.keys(rawOptions));

  // Build lookup map for schema args.
  const argDefMap = new Map<string, ArgDefinition>();
  for (const def of schema.args) {
    argDefMap.set(def.name, def);
  }

  // ── 1. Check for unrecognized arguments (strict mode) ──
  for (const name of providedNames) {
    if (!argDefMap.has(name)) {
      errors.push({
        code: "UNRECOGNIZED_ARG",
        message: `/${schema.commandName}: unrecognized argument '${name}'`,
        arg: name,
      });
    }
  }

  // ── 2. Validate each defined argument ──
  for (const def of schema.args) {
    const provided = rawOptions[def.name];

    // Argument not provided.
    if (!provided || provided.value === undefined || provided.value === null) {
      if (def.required) {
        errors.push({
          code: "MISSING_REQUIRED_ARG",
          message: `/${schema.commandName}: required argument '${def.name}' is missing`,
          arg: def.name,
        });
        continue;
      }

      // Apply default if defined.
      if (def.default !== undefined) {
        const coerced = coerceValue(def.default, def.type, def.name, schema.commandName);
        if (coerced instanceof ArgCoercionError) {
          errors.push(coerced.toDetail());
          continue;
        }
        parsed[def.name] = {
          name: def.name,
          type: def.type,
          value: coerced,
          fromDefault: true,
        };
      }
      // Optional with no default → silently omitted.
      continue;
    }

    // ── Type check ──
    if (provided.type !== def.type) {
      // Special case: Discord sometimes sends number as integer or vice versa.
      // Attempt coercion for compatible numeric types.
      if (
        (def.type === "integer" && provided.type === "number") ||
        (def.type === "number" && provided.type === "integer")
      ) {
        const coerced = coerceValue(provided.value, def.type, def.name, schema.commandName);
        if (coerced instanceof ArgCoercionError) {
          errors.push(coerced.toDetail());
          continue;
        }
        // Coerced successfully — proceed with constraint checks.
        const constraintErrors = validateConstraints(def, coerced, schema.commandName);
        errors.push(...constraintErrors);
        if (constraintErrors.length === 0) {
          parsed[def.name] = {
            name: def.name,
            type: def.type,
            value: coerced,
            fromDefault: false,
          };
        }
        continue;
      }

      errors.push({
        code: "INVALID_ARG_TYPE",
        message: `/${schema.commandName} '${def.name}': expected ${def.type}, got ${provided.type}`,
        arg: def.name,
      });
      continue;
    }

    // ── Constraint checks ──
    const constraintErrors = validateConstraints(def, provided.value, schema.commandName);
    errors.push(...constraintErrors);

    if (constraintErrors.length === 0) {
      parsed[def.name] = {
        name: def.name,
        type: def.type,
        value: provided.value,
        fromDefault: false,
      };
    }
  }

  if (errors.length > 0) {
    return { valid: false, errors };
  }

  return {
    valid: true,
    commandName: schema.commandName,
    args: parsed,
  };
}

// ────────────────────────────────────────────────────────────
// Type coercion
// ────────────────────────────────────────────────────────────

/** Internal wrapper for coercion failures. */
class ArgCoercionError {
  public readonly code: ArgParseErrorCode;
  public readonly message: string;
  public readonly arg?: string;

  constructor(
    code: ArgParseErrorCode,
    message: string,
    arg?: string,
  ) {
    this.code = code;
    this.message = message;
    this.arg = arg;
  }

  toDetail(): ArgParseErrorDetail {
    return { code: this.code, message: this.message, arg: this.arg };
  }
}

/**
 * Coerce a raw value to the target argument type.
 * Returns the coerced value or an ArgCoercionError.
 */
function coerceValue(
  raw: string | number | boolean,
  targetType: ArgType,
  argName: string,
  commandName: string,
): string | number | boolean | ArgCoercionError {
  const prefix = `/${commandName} '${argName}'`;

  switch (targetType) {
    case "string":
      // Any scalar can be stringified.
      return typeof raw === "string" ? raw : String(raw);

    case "integer":
      if (typeof raw === "number") {
        if (!Number.isFinite(raw) || !Number.isInteger(raw)) {
          return new ArgCoercionError(
            "ARG_TYPE_COERCION_FAILED",
            `${prefix}: cannot coerce ${raw} to integer`,
            argName,
          );
        }
        return Math.trunc(raw);
      }
      if (typeof raw === "string") {
        const parsed = Number(raw);
        if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
          return new ArgCoercionError(
            "ARG_TYPE_COERCION_FAILED",
            `${prefix}: cannot coerce "${raw}" to integer`,
            argName,
          );
        }
        return Math.trunc(parsed);
      }
      return new ArgCoercionError(
        "ARG_TYPE_COERCION_FAILED",
        `${prefix}: cannot coerce boolean to integer`,
        argName,
      );

    case "number":
      if (typeof raw === "number") {
        if (!Number.isFinite(raw)) {
          return new ArgCoercionError(
            "ARG_TYPE_COERCION_FAILED",
            `${prefix}: value must be a finite number`,
            argName,
          );
        }
        return raw;
      }
      if (typeof raw === "string") {
        const parsed = Number(raw);
        if (!Number.isFinite(parsed)) {
          return new ArgCoercionError(
            "ARG_TYPE_COERCION_FAILED",
            `${prefix}: cannot coerce "${raw}" to number`,
            argName,
          );
        }
        return parsed;
      }
      return new ArgCoercionError(
        "ARG_TYPE_COERCION_FAILED",
        `${prefix}: cannot coerce boolean to number`,
        argName,
      );

    case "boolean":
      if (typeof raw === "boolean") {
        return raw;
      }
      if (typeof raw === "string") {
        const lower = raw.toLowerCase().trim();
        if (lower === "true" || lower === "1") return true;
        if (lower === "false" || lower === "0") return false;
        return new ArgCoercionError(
          "ARG_TYPE_COERCION_FAILED",
          `${prefix}: cannot coerce "${raw}" to boolean (expected "true"/"false" or 1/0)`,
          argName,
        );
      }
      if (typeof raw === "number") {
        if (raw === 1) return true;
        if (raw === 0) return false;
        return new ArgCoercionError(
          "ARG_TYPE_COERCION_FAILED",
          `${prefix}: cannot coerce ${raw} to boolean (expected 0 or 1)`,
          argName,
        );
      }
      return new ArgCoercionError(
        "ARG_TYPE_COERCION_FAILED",
        `${prefix}: cannot coerce to boolean`,
        argName,
      );

    default:
      return new ArgCoercionError(
        "ARG_TYPE_COERCION_FAILED",
        `${prefix}: unsupported target type ${targetType}`,
        argName,
      );
  }
}

// ────────────────────────────────────────────────────────────
// Constraint validation
// ────────────────────────────────────────────────────────────

function validateConstraints(
  def: ArgDefinition,
  value: string | number | boolean,
  commandName: string,
): ArgParseErrorDetail[] {
  const errors: ArgParseErrorDetail[] = [];
  const prefix = `/${commandName} '${def.name}'`;

  // ── Choices validation ──
  if (def.choices && def.choices.length > 0) {
    const allowedValues = new Set(def.choices.map((c) => c.value));
    if (!allowedValues.has(value as string | number)) {
      errors.push({
        code: "INVALID_CHOICE",
        message: `${prefix}: "${String(value)}" is not an allowed choice`,
        arg: def.name,
      });
    }
  }

  // ── String length constraints ──
  if (def.type === "string" && typeof value === "string") {
    if (def.min_length !== undefined && value.length < def.min_length) {
      errors.push({
        code: "VALUE_TOO_SHORT",
        message: `${prefix}: must be at least ${def.min_length} characters (got ${value.length})`,
        arg: def.name,
      });
    }
    if (def.max_length !== undefined && value.length > def.max_length) {
      errors.push({
        code: "VALUE_TOO_LONG",
        message: `${prefix}: must be at most ${def.max_length} characters (got ${value.length})`,
        arg: def.name,
      });
    }
  }

  // ── Numeric range constraints ──
  if ((def.type === "integer" || def.type === "number") && typeof value === "number") {
    if (!Number.isFinite(value)) {
      errors.push({
        code: "VALUE_OUT_OF_RANGE",
        message: `${prefix}: value must be a finite number`,
        arg: def.name,
      });
    } else {
      if (def.min_value !== undefined && value < def.min_value) {
        errors.push({
          code: "VALUE_OUT_OF_RANGE",
          message: `${prefix}: must be at least ${def.min_value} (got ${value})`,
          arg: def.name,
        });
      }
      if (def.max_value !== undefined && value > def.max_value) {
        errors.push({
          code: "VALUE_OUT_OF_RANGE",
          message: `${prefix}: must be at most ${def.max_value} (got ${value})`,
          arg: def.name,
        });
      }
    }
  }

  return errors;
}

// ────────────────────────────────────────────────────────────
// Convenience helpers
// ────────────────────────────────────────────────────────────

/**
 * Type guard: returns true when the result is a successful parse.
 */
export function isParseSuccess(
  result: ArgParseResult,
): result is ParsedArguments {
  return result.valid;
}

/**
 * Type guard: returns true when the result is a parse error.
 */
export function isParseError(
  result: ArgParseResult,
): result is ArgParseError {
  return !result.valid;
}

/**
 * Convenience: extract a single argument value by name.
 * Returns undefined if the argument is not present.
 */
export function getArg(
  result: ParsedArguments,
  name: string,
): string | number | boolean | undefined {
  return result.args[name]?.value;
}

/**
 * Convenience: extract a string argument value.
 * Returns undefined if the argument is not present or not a string.
 */
export function getStringArg(
  result: ParsedArguments,
  name: string,
): string | undefined {
  const val = result.args[name]?.value;
  return typeof val === "string" ? val : undefined;
}

/**
 * Convenience: extract an integer argument value.
 * Returns undefined if the argument is not present or not a number.
 */
export function getIntegerArg(
  result: ParsedArguments,
  name: string,
): number | undefined {
  const val = result.args[name]?.value;
  return typeof val === "number" ? val : undefined;
}

/**
 * Convenience: extract a boolean argument value.
 * Returns undefined if the argument is not present or not a boolean.
 */
export function getBooleanArg(
  result: ParsedArguments,
  name: string,
): boolean | undefined {
  const val = result.args[name]?.value;
  return typeof val === "boolean" ? val : undefined;
}

/**
 * Format all parse errors into a single human-readable string.
 */
export function formatArgParseErrors(result: ArgParseError): string {
  return result.errors.map((e) => e.message).join("; ");
}
