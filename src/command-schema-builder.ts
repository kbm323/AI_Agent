/**
 * Command Schema Builder — Sub-AC 1a-i
 *
 * Pure function(s) that construct command definition objects (name,
 * description, options, type) matching Discord's application command
 * JSON schema. Every builder is a pure function — no side effects,
 * no mutable state, no I/O.
 *
 * The output of every builder is a plain TypeScript object conforming
 * to {@link CommandDefinition} / {@link CommandOptionDefinition}
 * from ``command-schema-validator.ts``, and can be fed directly to
 * the Discord REST API via ``commandDefToDiscordApiJson`` from
 * ``command-api-registrar.ts``.
 *
 * Discord API reference:
 *   https://discord.com/developers/docs/interactions/application-commands
 *
 * @module ai-agent/command-schema-builder
 */

import type {
  CommandDefinition,
  CommandOptionDefinition,
  CommandOptionChoice,
  CommandOptionType,
  CommandPermissionLevel,
} from "./command-schema-validator.ts";

// ---------------------------------------------------------------------------
// Discord API constant constraints (mirror of command-api-registrar.ts)
// ---------------------------------------------------------------------------

/** Discord API max length for command name. */
const COMMAND_NAME_MAX = 32;
/** Discord API max length for command description. */
const COMMAND_DESC_MAX = 100;
/** Discord API max length for option name. */
const OPTION_NAME_MAX = 32;
/** Discord API max length for option description. */
const OPTION_DESC_MAX = 100;
/** Discord API max options per command. */
const MAX_OPTIONS = 25;
/** Discord API max choices per option. */
const MAX_CHOICES = 25;
/** Discord API max choice name length. */
const CHOICE_NAME_MAX = 100;
/** Discord API max choice string value length. */
const CHOICE_VALUE_MAX = 100;

// ---------------------------------------------------------------------------
// Option builder options (the per-type overrides)
// ---------------------------------------------------------------------------

/** Common options shared by all option types. */
interface BaseOptionOpts {
  /** If true, the option MUST be present in the interaction. */
  required?: boolean;
  /** Human-readable description (overrides the positional one). */
  description?: string;
}

/** Options specific to string-type options. */
interface StringOptionOpts extends BaseOptionOpts {
  /** Minimum length (inclusive). */
  minLength?: number;
  /** Maximum length (inclusive). */
  maxLength?: number;
  /** Restricted value choices. */
  choices?: readonly CommandOptionChoice[];
}

/** Options specific to integer-type options. */
interface IntegerOptionOpts extends BaseOptionOpts {
  /** Minimum value (inclusive). */
  minValue?: number;
  /** Maximum value (inclusive). */
  maxValue?: number;
  /** Restricted value choices. */
  choices?: readonly CommandOptionChoice[];
}

/** Options specific to number-type options. */
interface NumberOptionOpts extends BaseOptionOpts {
  /** Minimum value (inclusive). */
  minValue?: number;
  /** Maximum value (inclusive). */
  maxValue?: number;
  /** Restricted value choices. */
  choices?: readonly CommandOptionChoice[];
}

/** Options specific to boolean-type options. */
interface BooleanOptionOpts extends BaseOptionOpts {
  // Boolean options have no extra constraints beyond required.
}

/** Options for channel/role/user/mentionable/attachment options. */
interface EntityOptionOpts extends BaseOptionOpts {
  // Entity options have no extra builder-level constraints.
}

// ---------------------------------------------------------------------------
// Choice builder
// ---------------------------------------------------------------------------

/**
 * Build a single choice entry for a string/integer/number option.
 *
 * Pure function — returns a frozen-looking plain object.
 *
 * @param name  — Human-readable display name (max 100 chars).
 * @param value — The concrete value Discord sends back (string | number).
 * @returns     — A {@link CommandOptionChoice}.
 *
 * @example
 *   choice("P0 — Critical", "P0")
 *   choice("Small",  1)
 */
export function choice(
  name: string,
  value: string | number,
): CommandOptionChoice {
  return { name, value };
}

/**
 * Build an ordered list of choices from an array of [name, value] tuples.
 *
 * @example
 *   choices([
 *     ["P0 — Critical", "P0"],
 *     ["P1 — High",     "P1"],
 *     ["P2 — Normal",   "P2"],
 *     ["P3 — Low",      "P3"],
 *   ])
 */
export function choices(
  entries: ReadonlyArray<readonly [string, string | number]>,
): CommandOptionChoice[] {
  return entries.map(([name, value]) => choice(name, value));
}

// ---------------------------------------------------------------------------
// Option builders — one per Discord option type
// ---------------------------------------------------------------------------

/**
 * Build a STRING-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars, ``^[a-z0-9_-]+$``).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (min/max length, choices, required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"string"``.
 */
export function stringOption(
  name: string,
  desc: string,
  opts: StringOptionOpts = {},
): CommandOptionDefinition {
  const def: CommandOptionDefinition = {
    name,
    description: opts.description ?? desc,
    type: "string",
    required: opts.required ?? false,
  };
  if (opts.minLength !== undefined) def.min_length = opts.minLength;
  if (opts.maxLength !== undefined) def.max_length = opts.maxLength;
  if (opts.choices && opts.choices.length > 0) def.choices = opts.choices;
  return def;
}

/**
 * Build an INTEGER-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (min/max value, choices, required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"integer"``.
 */
export function integerOption(
  name: string,
  desc: string,
  opts: IntegerOptionOpts = {},
): CommandOptionDefinition {
  const def: CommandOptionDefinition = {
    name,
    description: opts.description ?? desc,
    type: "integer",
    required: opts.required ?? false,
  };
  if (opts.minValue !== undefined) def.min_value = opts.minValue;
  if (opts.maxValue !== undefined) def.max_value = opts.maxValue;
  if (opts.choices && opts.choices.length > 0) def.choices = opts.choices;
  return def;
}

/**
 * Build a NUMBER-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (min/max value, choices, required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"number"``.
 */
export function numberOption(
  name: string,
  desc: string,
  opts: NumberOptionOpts = {},
): CommandOptionDefinition {
  const def: CommandOptionDefinition = {
    name,
    description: opts.description ?? desc,
    type: "number",
    required: opts.required ?? false,
  };
  if (opts.minValue !== undefined) def.min_value = opts.minValue;
  if (opts.maxValue !== undefined) def.max_value = opts.maxValue;
  if (opts.choices && opts.choices.length > 0) def.choices = opts.choices;
  return def;
}

/**
 * Build a BOOLEAN-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"boolean"``.
 */
export function booleanOption(
  name: string,
  desc: string,
  opts: BooleanOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "boolean",
    required: opts.required ?? false,
  };
}

/**
 * Build a CHANNEL-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"channel"``.
 */
export function channelOption(
  name: string,
  desc: string,
  opts: EntityOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "channel",
    required: opts.required ?? false,
  };
}

/**
 * Build a ROLE-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"role"``.
 */
export function roleOption(
  name: string,
  desc: string,
  opts: EntityOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "role",
    required: opts.required ?? false,
  };
}

/**
 * Build a USER-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"user"``.
 */
export function userOption(
  name: string,
  desc: string,
  opts: EntityOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "user",
    required: opts.required ?? false,
  };
}

/**
 * Build a MENTIONABLE-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"mentionable"``.
 */
export function mentionableOption(
  name: string,
  desc: string,
  opts: EntityOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "mentionable",
    required: opts.required ?? false,
  };
}

/**
 * Build an ATTACHMENT-type option definition.
 *
 * @param name  — Option name (lowercase, max 32 chars).
 * @param desc  — Human-readable description (1–100 chars).
 * @param opts  — Optional constraints (required).
 * @returns     — A {@link CommandOptionDefinition} with type ``"attachment"``.
 */
export function attachmentOption(
  name: string,
  desc: string,
  opts: EntityOptionOpts = {},
): CommandOptionDefinition {
  return {
    name,
    description: opts.description ?? desc,
    type: "attachment",
    required: opts.required ?? false,
  };
}

// ---------------------------------------------------------------------------
// Command builder
// ---------------------------------------------------------------------------

/** Options for ``buildCommand``. */
export interface BuildCommandOpts {
  /**
   * Minimum permission level required to invoke this command.
   * @default "everyone"
   */
  permission?: CommandPermissionLevel;
  /**
   * Whether the command can be invoked via bot mention / text fallback.
   * @default false
   */
  allowBotMention?: boolean;
}

/**
 * Build a complete slash command definition.
 *
 * Pure function — returns a plain {@link CommandDefinition} object
 * compatible with the command registry, validator, and Discord API
 * registrar.
 *
 * @param name        — Command name (lowercase, max 32 chars, ``^[a-z0-9_-]+$``).
 * @param description — Human-readable description (1–100 chars).
 * @param options     — Ordered array of option definitions.
 * @param opts        — Additional overrides (permission, bot mention).
 * @returns           — A {@link CommandDefinition} ready for registration.
 *
 * @example
 *   buildCommand("meeting", "Start a multi-agent meeting", [
 *     stringOption("agenda", "Meeting topic and objectives", { required: true, maxLength: 4000 }),
 *     stringOption("priority", "Meeting priority", {
 *       choices: choices([["P0 — Critical", "P0"], ["P1 — High", "P1"]]),
 *     }),
 *   ], { permission: "guild_only" })
 */
export function buildCommand(
  name: string,
  description: string,
  options: readonly CommandOptionDefinition[] = [],
  opts: BuildCommandOpts = {},
): CommandDefinition {
  const def: CommandDefinition = {
    name,
    description,
    options,
  };

  if (opts.permission !== undefined) {
    def.permission = opts.permission;
  }
  if (opts.allowBotMention !== undefined) {
    def.allow_bot_mention = opts.allowBotMention;
  }

  return def;
}

// ---------------------------------------------------------------------------
// Pre-built command definitions for the AI_Agent meeting system
// ---------------------------------------------------------------------------

/**
 * Return the canonical ``/meeting`` command definition for the
 * AI virtual entertainment company meeting system.
 *
 * This is the primary user-facing entry point that initiates all
 * multi-agent meetings. All fields are hard-coded per the Seed spec
 * and constrained to Discord API limits.
 *
 * Options:
 *   - ``agenda`` (required string, 1–4000 chars) — Meeting topic and objectives.
 *   - ``priority`` (optional string, choices: P0/P1/P2/P3) — Priority level.
 */
export function meetingCommand(): CommandDefinition {
  return buildCommand("meeting", "Start a multi-agent company meeting", [
    stringOption("agenda", "Meeting topic and objectives", {
      required: true,
      minLength: 1,
      maxLength: 4000,
    }),
    stringOption("priority", "Meeting priority level", {
      choices: choices([
        ["P0 — Critical", "P0"],
        ["P1 — High", "P1"],
        ["P2 — Normal", "P2"],
        ["P3 — Low", "P3"],
      ]),
    }),
  ], {
    permission: "guild_only",
    allowBotMention: true,
  });
}

/**
 * Return the canonical ``/status`` command definition.
 *
 * Displays the current system status: active meetings, queue depth,
 * rate-limit status, and opencode-go availability.
 */
export function statusCommand(): CommandDefinition {
  return buildCommand("status", "Show system status and active meetings", [], {
    permission: "guild_only",
  });
}

/**
 * Return the canonical ``/cancel`` command definition.
 *
 * Cancels a running meeting by its meeting ID.
 */
export function cancelCommand(): CommandDefinition {
  return buildCommand("cancel", "Cancel a running meeting", [
    stringOption("meeting_id", "Meeting ID to cancel", {
      required: true,
      minLength: 1,
      maxLength: 64,
    }),
    stringOption("reason", "Reason for cancellation", {
      maxLength: 1000,
    }),
  ], {
    permission: "guild_only",
  });
}

/**
 * Return the canonical ``/knowledge`` command definition.
 *
 * Searches the 4-layer knowledge base accumulated across meetings.
 */
export function knowledgeCommand(): CommandDefinition {
  return buildCommand("knowledge", "Search accumulated meeting knowledge", [
    stringOption("query", "Search terms", {
      required: true,
      minLength: 1,
      maxLength: 500,
    }),
    stringOption("layer", "Knowledge layer to search", {
      choices: choices([
        ["L0 — Raw meeting transcripts", "L0"],
        ["L1 — Summarized decisions", "L1"],
        ["L2 — Cross-meeting patterns", "L2"],
        ["L3 — Strategic insights", "L3"],
      ]),
    }),
  ], {
    permission: "guild_only",
  });
}

// ---------------------------------------------------------------------------
// Schema validation helpers
// ---------------------------------------------------------------------------

/** Re-exported types for convenience. */
export type {
  CommandDefinition,
  CommandOptionDefinition,
  CommandOptionChoice,
  CommandOptionType,
  CommandPermissionLevel,
};

// ---------------------------------------------------------------------------
// Discord API JSON compatibility check
// ---------------------------------------------------------------------------

/**
 * The set of valid Discord application command option type integers
 * (matching the Discord API spec). Used to validate that our builder
 * output can be serialized to Discord API JSON.
 */
const DISCORD_OPTION_TYPE_MAP: Record<string, number> = {
  string: 3,
  integer: 4,
  boolean: 5,
  number: 10,
  channel: 7,
  role: 8,
  user: 6,
  mentionable: 9,
  attachment: 11,
};

/**
 * Checks whether a command definition can be serialized to a
 * Discord API-compatible JSON object.
 *
 * Pure predicate — no side effects.
 *
 * This performs a lightweight structural check rather than the full
 * ``validateCommandDefinition`` from ``command-api-registrar``.
 * It verifies:
 *   - All required fields are present (name, description, options array)
 *   - Every option type maps to a valid Discord integer type
 *   - Choice values are string or number (not boolean or object)
 *
 * @param def — The command definition to check.
 * @returns — ``true`` if the definition is structurally compatible.
 */
export function isDiscordApiCompatible(def: CommandDefinition): boolean {
  // Envelope checks.
  if (!def.name || typeof def.name !== "string") return false;
  if (!def.description || typeof def.description !== "string") return false;
  if (!Array.isArray(def.options)) return false;

  // Check each option.
  for (const opt of def.options) {
    if (!opt.name || typeof opt.name !== "string") return false;
    if (!opt.description || typeof opt.description !== "string") return false;
    if (!DISCORD_OPTION_TYPE_MAP[opt.type]) return false;

    // Choice checks.
    if (opt.choices) {
      if (!Array.isArray(opt.choices)) return false;
      for (const c of opt.choices) {
        if (!c.name || typeof c.name !== "string") return false;
        if (typeof c.value !== "string" && typeof c.value !== "number") {
          return false;
        }
      }
    }
  }

  return true;
}
