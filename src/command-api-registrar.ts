/**
 * Command API Registrar — Sub-AC 1.1a
 *
 * Discord slash command registration module that:
 * 1. Validates command definitions against Discord API schema constraints
 *    (name format, description length, option shapes, choice limits, etc.)
 * 2. Registers commands with Discord's REST API (global and guild-scoped)
 * 3. Lists and deletes registered commands
 *
 * All API calls are HTTP requests to Discord's REST API:
 *   POST   /applications/{app_id}/commands                  (create global)
 *   POST   /applications/{app_id}/guilds/{guild_id}/commands (create guild)
 *   GET    /applications/{app_id}/commands                  (list global)
 *   GET    /applications/{app_id}/guilds/{guild_id}/commands (list guild)
 *   DELETE /applications/{app_id}/commands/{cmd_id}         (delete global)
 *   DELETE /applications/{app_id}/guilds/{guild_id}/commands/{cmd_id} (delete guild)
 *   PUT    /applications/{app_id}/commands                  (bulk overwrite global)
 *   PUT    /applications/{app_id}/guilds/{guild_id}/commands (bulk overwrite guild)
 *
 * Discord API docs:
 *   https://discord.com/developers/docs/interactions/application-commands
 *
 * @module ai-agent/command-api-registrar
 */

import type {
  CommandDefinition,
  CommandOptionDefinition,
  CommandOptionChoice,
} from "./command-schema-validator.ts";

import {
  DiscordApiClient,
  setFetchImpl,
  resetFetchImpl,
  type DiscordApiClientConfig,
  type HttpFetchFn,
} from "./discord-api-client.ts";

// Re-export for testability (backward compat)
export { setFetchImpl, resetFetchImpl };
export type { HttpFetchFn };

// ---------------------------------------------------------------------------
// Discord API JSON shapes (what we send to / receive from Discord)
// ---------------------------------------------------------------------------

/**
 * Discord Application Command Option type integers.
 * Mirror of https://discord.com/developers/docs/interactions/application-commands#application-command-object-application-command-option-type
 */
const DiscordOptionType = {
  SUB_COMMAND: 1,
  SUB_COMMAND_GROUP: 2,
  STRING: 3,
  INTEGER: 4,
  BOOLEAN: 5,
  USER: 6,
  CHANNEL: 7,
  ROLE: 8,
  MENTIONABLE: 9,
  NUMBER: 10,
  ATTACHMENT: 11,
} as const;

/** Map our string option types to Discord integer option types. */
const OPTION_TYPE_TO_DISCORD: Record<string, number> = {
  string: DiscordOptionType.STRING,
  integer: DiscordOptionType.INTEGER,
  boolean: DiscordOptionType.BOOLEAN,
  number: DiscordOptionType.NUMBER,
  channel: DiscordOptionType.CHANNEL,
  role: DiscordOptionType.ROLE,
  user: DiscordOptionType.USER,
  mentionable: DiscordOptionType.MENTIONABLE,
  attachment: DiscordOptionType.ATTACHMENT,
};

/** Map Discord integer option types back to string labels. */
const DISCORD_OPTION_TYPE_LABELS: Record<number, string> = {
  1: "SUB_COMMAND",
  2: "SUB_COMMAND_GROUP",
  3: "string",
  4: "integer",
  5: "boolean",
  6: "user",
  7: "channel",
  8: "role",
  9: "mentionable",
  10: "number",
  11: "attachment",
};

/** Discord API shape for a command option when POSTing to the API. */
interface DiscordApiCommandOption {
  type: number;
  name: string;
  name_localizations?: Record<string, string> | null;
  description: string;
  description_localizations?: Record<string, string> | null;
  required?: boolean;
  choices?: DiscordApiChoice[];
  options?: DiscordApiCommandOption[];
  channel_types?: number[];
  min_value?: number;
  max_value?: number;
  min_length?: number;
  max_length?: number;
  autocomplete?: boolean;
}

/** Discord API shape for a choice entry. */
interface DiscordApiChoice {
  name: string;
  name_localizations?: Record<string, string> | null;
  value: string | number;
}

/** Discord API shape for a command when POSTing/PUTing to the API. */
interface DiscordApiCommand {
  name: string;
  name_localizations?: Record<string, string> | null;
  description: string;
  description_localizations?: Record<string, string> | null;
  options?: DiscordApiCommandOption[];
  default_member_permissions?: string | null;
  dm_permission?: boolean;
  type?: number;
  nsfw?: boolean;
}

/** Discord API response shape for a single command. */
export interface DiscordApiCommandResponse {
  id: string;
  application_id: string;
  guild_id?: string;
  name: string;
  description: string;
  type: number;
  options?: DiscordApiCommandOption[];
  default_member_permissions: string | null;
  dm_permission: boolean;
  nsfw: boolean;
  version: string;
}

// ---------------------------------------------------------------------------
// Schema validation: constraint constants
// ---------------------------------------------------------------------------

/** Discord API max length for command name. */
const COMMAND_NAME_MAX_LENGTH = 32;
/** Discord API min length for command name. */
const COMMAND_NAME_MIN_LENGTH = 1;
/** Discord API max length for command description. */
const COMMAND_DESCRIPTION_MAX_LENGTH = 100;
/** Discord API min length for command description. */
const COMMAND_DESCRIPTION_MIN_LENGTH = 1;
/** Discord API max number of options per command. */
const MAX_OPTIONS_PER_COMMAND = 25;
/** Discord API max length for option name. */
const OPTION_NAME_MAX_LENGTH = 32;
/** Discord API max length for option description. */
const OPTION_DESCRIPTION_MAX_LENGTH = 100;
/** Discord API max number of choices per option. */
const MAX_CHOICES_PER_OPTION = 25;
/** Discord API max length for choice name. */
const CHOICE_NAME_MAX_LENGTH = 100;
/** Discord API max length for choice value (string). */
const CHOICE_VALUE_MAX_LENGTH = 100;

/**
 * Discord's regex for valid command names.
 * Per docs: must match ^[-_\p{L}\p{N}\p{sc=Deva}\p{sc=Thai}]{1,32}$
 * and be lowercase.
 *
 * We use a pragmatic Unicode-aware pattern that accepts lowercase
 * letters, digits, hyphens, and underscores.  Full Unicode script
 * matching is intentionally lenient here — Discord's server will
 * also validate on submission.
 */
const COMMAND_NAME_PATTERN = /^[a-z0-9_-]{1,32}$/;

/**
 * Valid option types as accepted by Discord's API.
 */
const VALID_OPTION_TYPES = new Set(Object.keys(OPTION_TYPE_TO_DISCORD));

// ---------------------------------------------------------------------------
// Validation result types
// ---------------------------------------------------------------------------

/** A single definition validation issue. */
export interface CommandDefValidationIssue {
  /** Machine-readable error code. */
  code: string;
  /** Human-readable message. */
  message: string;
  /** Field path, e.g. "name", "options[1].name", "options[0].choices[3].value". */
  path: string;
}

/**
 * Result of validating a command definition against Discord API constraints.
 */
export interface CommandDefValidationResult {
  /** True when the definition passes all API schema checks. */
  valid: boolean;
  /** List of issues found (empty when valid). */
  issues: CommandDefValidationIssue[];
}

// ---------------------------------------------------------------------------
// Registration result types
// ---------------------------------------------------------------------------

/**
 * Per-command result from a registration operation.
 */
export interface CommandRegistrationEntry {
  /** The original command definition name. */
  name: string;
  /** The Discord-assigned command ID (only on success). */
  id?: string;
  /** Whether registration was successful. */
  success: boolean;
  /** Discord API error message on failure. */
  error?: string;
  /** HTTP status code from Discord API on failure. */
  statusCode?: number;
}

/**
 * Result of a full registration operation.
 */
export interface CommandRegistrationResult {
  /** True when ALL commands in the batch were registered successfully. */
  success: boolean;
  /** Per-command results. */
  entries: CommandRegistrationEntry[];
  /** Summary message. */
  summary: string;
}

// ---------------------------------------------------------------------------
// Schema validation: command definition → Discord API compatibility
// ---------------------------------------------------------------------------

/**
 * Validate a single {@link CommandDefinition} against Discord API constraints.
 *
 * Checks:
 *  - name: 1–32 chars, lowercase, matches `^[a-z0-9_-]+$`
 *  - description: 1–100 chars
 *  - options: at most 25
 *  - option.name: 1–32 chars, lowercase
 *  - option.description: 1–100 chars
 *  - option.type: must be a valid Discord option type
 *  - option.choices: at most 25
 *  - choice.name: 1–100 chars
 *  - choice.value: string ≤ 100 chars, or integer
 *
 * @param def — The command definition to validate.
 * @returns — A {@link CommandDefValidationResult} with a list of issues.
 */
export function validateCommandDefinition(
  def: CommandDefinition,
): CommandDefValidationResult {
  const issues: CommandDefValidationIssue[] = [];

  // ── name ────────────────────────────────────────────────
  if (!def.name || def.name.length < COMMAND_NAME_MIN_LENGTH) {
    issues.push({
      code: "NAME_TOO_SHORT",
      message: `Command name must be at least ${COMMAND_NAME_MIN_LENGTH} character`,
      path: "name",
    });
  } else if (def.name.length > COMMAND_NAME_MAX_LENGTH) {
    issues.push({
      code: "NAME_TOO_LONG",
      message: `Command name must be at most ${COMMAND_NAME_MAX_LENGTH} characters (got ${def.name.length})`,
      path: "name",
    });
  } else if (!COMMAND_NAME_PATTERN.test(def.name)) {
    issues.push({
      code: "NAME_INVALID_FORMAT",
      message: `Command name "${def.name}" must be lowercase and match ^[a-z0-9_-]{1,32}$`,
      path: "name",
    });
  }

  // ── description ─────────────────────────────────────────
  if (!def.description || def.description.length < COMMAND_DESCRIPTION_MIN_LENGTH) {
    issues.push({
      code: "DESCRIPTION_TOO_SHORT",
      message: `Command description must be at least ${COMMAND_DESCRIPTION_MIN_LENGTH} character`,
      path: "description",
    });
  } else if (def.description.length > COMMAND_DESCRIPTION_MAX_LENGTH) {
    issues.push({
      code: "DESCRIPTION_TOO_LONG",
      message: `Command description must be at most ${COMMAND_DESCRIPTION_MAX_LENGTH} characters (got ${def.description.length})`,
      path: "description",
    });
  }

  // ── options ─────────────────────────────────────────────
  if (def.options && def.options.length > MAX_OPTIONS_PER_COMMAND) {
    issues.push({
      code: "TOO_MANY_OPTIONS",
      message: `Command has ${def.options.length} options, but Discord allows at most ${MAX_OPTIONS_PER_COMMAND}`,
      path: "options",
    });
  }

  if (def.options && def.options.length > 0) {
    for (let i = 0; i < def.options.length; i++) {
      const opt = def.options[i];
      const optPath = `options[${i}]`;

      // option.name
      if (!opt.name || opt.name.length === 0) {
        issues.push({
          code: "OPTION_NAME_EMPTY",
          message: `Option at ${optPath}: name must not be empty`,
          path: `${optPath}.name`,
        });
      } else if (opt.name.length > OPTION_NAME_MAX_LENGTH) {
        issues.push({
          code: "OPTION_NAME_TOO_LONG",
          message: `Option "${opt.name}" name must be at most ${OPTION_NAME_MAX_LENGTH} characters (got ${opt.name.length})`,
          path: `${optPath}.name`,
        });
      } else if (!/^[a-z0-9_-]{1,32}$/.test(opt.name)) {
        issues.push({
          code: "OPTION_NAME_INVALID_FORMAT",
          message: `Option name "${opt.name}" must be lowercase and match ^[a-z0-9_-]{1,32}$`,
          path: `${optPath}.name`,
        });
      }

      // option.description
      if (!opt.description || opt.description.length === 0) {
        issues.push({
          code: "OPTION_DESCRIPTION_EMPTY",
          message: `Option "${opt.name}" description must not be empty`,
          path: `${optPath}.description`,
        });
      } else if (opt.description.length > OPTION_DESCRIPTION_MAX_LENGTH) {
        issues.push({
          code: "OPTION_DESCRIPTION_TOO_LONG",
          message: `Option "${opt.name}" description must be at most ${OPTION_DESCRIPTION_MAX_LENGTH} characters (got ${opt.description.length})`,
          path: `${optPath}.description`,
        });
      }

      // option.type
      if (!VALID_OPTION_TYPES.has(opt.type)) {
        issues.push({
          code: "OPTION_TYPE_INVALID",
          message: `Option "${opt.name}" has invalid type "${opt.type}". Must be one of: ${[...VALID_OPTION_TYPES].join(", ")}`,
          path: `${optPath}.type`,
        });
      }

      // option.choices
      if (opt.choices && opt.choices.length > MAX_CHOICES_PER_OPTION) {
        issues.push({
          code: "TOO_MANY_CHOICES",
          message: `Option "${opt.name}" has ${opt.choices.length} choices, but Discord allows at most ${MAX_CHOICES_PER_OPTION}`,
          path: `${optPath}.choices`,
        });
      }

      if (opt.choices && opt.choices.length > 0) {
        for (let j = 0; j < opt.choices.length; j++) {
          const choice = opt.choices[j];
          const chPath = `${optPath}.choices[${j}]`;

          if (!choice.name || choice.name.length === 0) {
            issues.push({
              code: "CHOICE_NAME_EMPTY",
              message: `Choice at ${chPath}: name must not be empty`,
              path: `${chPath}.name`,
            });
          } else if (choice.name.length > CHOICE_NAME_MAX_LENGTH) {
            issues.push({
              code: "CHOICE_NAME_TOO_LONG",
              message: `Choice name "${choice.name}" must be at most ${CHOICE_NAME_MAX_LENGTH} characters (got ${choice.name.length})`,
              path: `${chPath}.name`,
            });
          }

          // choice.value validation
          if (choice.value === undefined || choice.value === null) {
            issues.push({
              code: "CHOICE_VALUE_MISSING",
              message: `Choice "${choice.name}" at ${chPath}: value must be provided`,
              path: `${chPath}.value`,
            });
          } else if (typeof choice.value === "string" && choice.value.length > CHOICE_VALUE_MAX_LENGTH) {
            issues.push({
              code: "CHOICE_VALUE_TOO_LONG",
              message: `Choice "${choice.name}" string value must be at most ${CHOICE_VALUE_MAX_LENGTH} characters (got ${choice.value.length})`,
              path: `${chPath}.value`,
            });
          } else if (typeof choice.value !== "string" && typeof choice.value !== "number") {
            issues.push({
              code: "CHOICE_VALUE_INVALID_TYPE",
              message: `Choice "${choice.name}" value must be a string or number (got ${typeof choice.value})`,
              path: `${chPath}.value`,
            });
          }
        }
      }

      // min/max constraints — Discord requires min_value ≤ max_value
      if (
        opt.min_value !== undefined &&
        opt.max_value !== undefined &&
        opt.min_value > opt.max_value
      ) {
        issues.push({
          code: "OPTION_RANGE_INVALID",
          message: `Option "${opt.name}": min_value (${opt.min_value}) must not exceed max_value (${opt.max_value})`,
          path: `${optPath}.range`,
        });
      }

      if (
        opt.min_length !== undefined &&
        opt.max_length !== undefined &&
        opt.min_length > opt.max_length
      ) {
        issues.push({
          code: "OPTION_RANGE_INVALID",
          message: `Option "${opt.name}": min_length (${opt.min_length}) must not exceed max_length (${opt.max_length})`,
          path: `${optPath}.range`,
        });
      }
    }
  }

  return {
    valid: issues.length === 0,
    issues,
  };
}

/**
 * Validate multiple command definitions at once.
 * Returns the first failure or a combined valid result.
 */
export function validateCommandDefinitions(
  defs: readonly CommandDefinition[],
): CommandDefValidationResult {
  const allIssues: CommandDefValidationIssue[] = [];

  for (const def of defs) {
    const result = validateCommandDefinition(def);
    allIssues.push(...result.issues);
  }

  return {
    valid: allIssues.length === 0,
    issues: allIssues,
  };
}

// ---------------------------------------------------------------------------
// Conversion: CommandDefinition → Discord API JSON shape
// ---------------------------------------------------------------------------

/**
 * Convert our internal {@link CommandDefinition} to the Discord API JSON shape
 * for POST/PUT requests.
 *
 * Fields not applicable to CHAT_INPUT (slash) commands are omitted.
 */
export function commandDefToDiscordApiJson(def: CommandDefinition): DiscordApiCommand {
  const apiCommand: DiscordApiCommand = {
    name: def.name,
    description: def.description,
    type: 1, // CHAT_INPUT
    dm_permission: def.permission !== "guild_only",
  };

  if (def.options && def.options.length > 0) {
    apiCommand.options = def.options.map(optionDefToDiscordApiJson);
  }

  return apiCommand;
}

function optionDefToDiscordApiJson(opt: CommandOptionDefinition): DiscordApiCommandOption {
  const apiOpt: DiscordApiCommandOption = {
    type: OPTION_TYPE_TO_DISCORD[opt.type] ?? DiscordOptionType.STRING,
    name: opt.name,
    description: opt.description,
    required: opt.required ?? false,
  };

  if (opt.choices && opt.choices.length > 0) {
    apiOpt.choices = opt.choices.map((c) => ({
      name: c.name,
      value: c.value,
    }));
  }

  if (opt.min_length !== undefined) apiOpt.min_length = opt.min_length;
  if (opt.max_length !== undefined) apiOpt.max_length = opt.max_length;
  if (opt.min_value !== undefined) apiOpt.min_value = opt.min_value;
  if (opt.max_value !== undefined) apiOpt.max_value = opt.max_value;

  return apiOpt;
}

// ---------------------------------------------------------------------------
// HTTP helpers (re-exported from discord-api-client for testability)
// ---------------------------------------------------------------------------
// setFetchImpl / resetFetchImpl / HttpFetchFn are imported and re-exported
// at the top of this file for backward compatibility.

// ---------------------------------------------------------------------------
// Discord REST API client helper
// ---------------------------------------------------------------------------

/**
 * Configuration for the Discord command registrar.
 */
export interface DiscordRegistrarConfig {
  /** Discord application ID (from Developer Portal). */
  applicationId: string;
  /** Discord bot token (must have applications.commands scope). */
  botToken: string;
}

/**
 * Build a DiscordApiClient from a registrar config.
 *
 * All HTTP transport (URL construction, auth headers, request dispatch)
 * is delegated to {@link DiscordApiClient}.
 */
function _clientFor(config: DiscordRegistrarConfig): DiscordApiClient {
  return new DiscordApiClient(config);
}

// ---------------------------------------------------------------------------
// Global command operations
// ---------------------------------------------------------------------------

/**
 * Register a single command globally (visible in all guilds).
 *
 * @param config   — Registrar config with application ID and bot token.
 * @param def      — The command definition to register.
 * @returns        — The Discord API response with the assigned command ID.
 * @throws         — {@link DiscordApiError} on API failure.
 */
export async function createGlobalCommand(
  config: DiscordRegistrarConfig,
  def: CommandDefinition,
): Promise<DiscordApiCommandResponse> {
  const client = _clientFor(config);
  const body = commandDefToDiscordApiJson(def);
  const response = await client.post(client.globalCommandsUrl(), body);

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `create global /${def.name}`);
  }

  return (await response.json()) as DiscordApiCommandResponse;
}

/**
 * Register a single command for a specific guild (instant update, no 1-hour cache).
 *
 * @param config   — Registrar config.
 * @param guildId  — Target Discord guild (server) ID.
 * @param def      — The command definition to register.
 * @returns        — The Discord API response with the assigned command ID.
 */
export async function createGuildCommand(
  config: DiscordRegistrarConfig,
  guildId: string,
  def: CommandDefinition,
): Promise<DiscordApiCommandResponse> {
  const client = _clientFor(config);
  const body = commandDefToDiscordApiJson(def);
  const response = await client.post(client.guildCommandsUrl(guildId), body);

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `create guild /${def.name}`);
  }

  return (await response.json()) as DiscordApiCommandResponse;
}

/**
 * List all global commands.
 */
export async function listGlobalCommands(
  config: DiscordRegistrarConfig,
): Promise<DiscordApiCommandResponse[]> {
  const client = _clientFor(config);
  const response = await client.get(client.globalCommandsUrl());

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, "list global commands");
  }

  return (await response.json()) as DiscordApiCommandResponse[];
}

/**
 * List all guild commands for a specific guild.
 */
export async function listGuildCommands(
  config: DiscordRegistrarConfig,
  guildId: string,
): Promise<DiscordApiCommandResponse[]> {
  const client = _clientFor(config);
  const response = await client.get(client.guildCommandsUrl(guildId));

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `list guild commands for ${guildId}`);
  }

  return (await response.json()) as DiscordApiCommandResponse[];
}

/**
 * Delete a global command by ID.
 */
export async function deleteGlobalCommand(
  config: DiscordRegistrarConfig,
  commandId: string,
): Promise<void> {
  const client = _clientFor(config);
  const response = await client.delete(client.globalCommandUrl(commandId));

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `delete global command ${commandId}`);
  }
}

/**
 * Delete a guild command by ID.
 */
export async function deleteGuildCommand(
  config: DiscordRegistrarConfig,
  guildId: string,
  commandId: string,
): Promise<void> {
  const client = _clientFor(config);
  const response = await client.delete(client.guildCommandUrl(guildId, commandId));

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `delete guild command ${commandId}`);
  }
}

/**
 * Bulk overwrite all global commands. Replaces the ENTIRE global command set.
 *
 * @param config  — Registrar config.
 * @param defs    — The complete set of command definitions.
 * @returns       — The new command list from Discord.
 */
export async function bulkOverwriteGlobalCommands(
  config: DiscordRegistrarConfig,
  defs: readonly CommandDefinition[],
): Promise<DiscordApiCommandResponse[]> {
  const client = _clientFor(config);
  const body = defs.map(commandDefToDiscordApiJson);
  const response = await client.put(client.globalCommandsUrl(), body);

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, "bulk overwrite global commands");
  }

  return (await response.json()) as DiscordApiCommandResponse[];
}

/**
 * Bulk overwrite all guild commands for a specific guild.
 */
export async function bulkOverwriteGuildCommands(
  config: DiscordRegistrarConfig,
  guildId: string,
  defs: readonly CommandDefinition[],
): Promise<DiscordApiCommandResponse[]> {
  const client = _clientFor(config);
  const body = defs.map(commandDefToDiscordApiJson);
  const response = await client.put(client.guildCommandsUrl(guildId), body);

  if (!response.ok) {
    throw await DiscordApiError.fromResponse(response, `bulk overwrite guild commands for ${guildId}`);
  }

  return (await response.json()) as DiscordApiCommandResponse[];
}

// ---------------------------------------------------------------------------
// Batch registration with per-command reporting
// ---------------------------------------------------------------------------

/**
 * Register multiple commands globally, reporting per-command results.
 *
 * Validation is performed first — if any definition fails schema
 * validation, no API calls are made and the result contains the
 * validation issues.
 *
 * @param config  — Registrar config.
 * @param defs    — Command definitions to register.
 * @returns       — A {@link CommandRegistrationResult} with per-command details.
 */
export async function registerGlobalCommands(
  config: DiscordRegistrarConfig,
  defs: readonly CommandDefinition[],
): Promise<CommandRegistrationResult> {
  // ── Step 1: validate all definitions ─────────────────────
  const validation = validateCommandDefinitions(defs);
  if (!validation.valid) {
    const entries = defs.map((def) => {
      const defIssues = validation.issues.filter((iss) =>
        iss.path.startsWith(def.name) || iss.path === "name" || iss.path === "description",
      );
      // Simplify: attach first matching issue to the command
      const relevant = validation.issues.find((iss) => {
        // Check if issue path relates to this definition
        return true; // broad match for batch validation
      });

      const singleResult = validateCommandDefinition(def);
      return {
        name: def.name,
        success: false,
        error: singleResult.valid
          ? "Batch validation failed — see summary"
          : singleResult.issues.map((i) => i.message).join("; "),
      };
    });

    return {
      success: false,
      entries,
      summary: `Validation failed: ${validation.issues.length} issue(s) across ${defs.length} command(s)`,
    };
  }

  // ── Step 2: register each command individually ───────────
  const entries: CommandRegistrationEntry[] = [];

  for (const def of defs) {
    try {
      const created = await createGlobalCommand(config, def);
      entries.push({
        name: def.name,
        id: created.id,
        success: true,
      });
    } catch (err) {
      const apiErr = err instanceof DiscordApiError
        ? err
        : new DiscordApiError(`Unexpected: ${String(err)}`, 0);
      entries.push({
        name: def.name,
        success: false,
        error: apiErr.message,
        statusCode: apiErr.statusCode,
      });
    }
  }

  const allOk = entries.every((e) => e.success);
  const okCount = entries.filter((e) => e.success).length;

  return {
    success: allOk,
    entries,
    summary: allOk
      ? `All ${okCount} global command(s) registered successfully`
      : `${okCount}/${entries.length} global command(s) registered, ${entries.length - okCount} failed`,
  };
}

/**
 * Register multiple commands for a specific guild, reporting per-command results.
 */
export async function registerGuildCommands(
  config: DiscordRegistrarConfig,
  guildId: string,
  defs: readonly CommandDefinition[],
): Promise<CommandRegistrationResult> {
  // ── Step 1: validate all definitions ─────────────────────
  const validation = validateCommandDefinitions(defs);
  if (!validation.valid) {
    const entries = defs.map((def) => {
      const singleResult = validateCommandDefinition(def);
      return {
        name: def.name,
        success: false,
        error: singleResult.valid
          ? "Batch validation failed — see summary"
          : singleResult.issues.map((i) => i.message).join("; "),
      };
    });

    return {
      success: false,
      entries,
      summary: `Validation failed: ${validation.issues.length} issue(s) across ${defs.length} command(s)`,
    };
  }

  // ── Step 2: register each command individually ───────────
  const entries: CommandRegistrationEntry[] = [];

  for (const def of defs) {
    try {
      const created = await createGuildCommand(config, guildId, def);
      entries.push({
        name: def.name,
        id: created.id,
        success: true,
      });
    } catch (err) {
      const apiErr = err instanceof DiscordApiError
        ? err
        : new DiscordApiError(`Unexpected: ${String(err)}`, 0);
      entries.push({
        name: def.name,
        success: false,
        error: apiErr.message,
        statusCode: apiErr.statusCode,
      });
    }
  }

  const allOk = entries.every((e) => e.success);
  const okCount = entries.filter((e) => e.success).length;

  return {
    success: allOk,
    entries,
    summary: allOk
      ? `All ${okCount} guild command(s) registered successfully`
      : `${okCount}/${entries.length} guild command(s) registered, ${entries.length - okCount} failed`,
  };
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/**
 * Structured error for Discord REST API failures.
 *
 * Carries the HTTP status code and, when available, the error payload
 * returned by Discord (which includes a machine-readable `code` and
 * human-readable `message`).
 */
export class DiscordApiError extends Error {
  /** Discord HTTP status code (e.g. 401, 403, 429). */
  public readonly statusCode: number;
  /** Discord API error code (e.g. 50001 for "Missing Access"). */
  public readonly discordCode?: number;
  /** The operation being attempted when the error occurred. */
  public readonly operation: string;

  constructor(
    message: string,
    statusCode: number,
    discordCode?: number,
    operation?: string,
  ) {
    super(message);
    this.name = "DiscordApiError";
    this.statusCode = statusCode;
    this.discordCode = discordCode;
    this.operation = operation ?? "unknown";
  }

  /**
   * Build a {@link DiscordApiError} from a non-ok fetch Response.
   */
  static async fromResponse(
    response: Response,
    operation?: string,
  ): Promise<DiscordApiError> {
    let message = `Discord API returned ${response.status}`;
    let discordCode: number | undefined;

    try {
      const body = await response.json() as Record<string, unknown>;
      if (typeof body.message === "string") {
        message = body.message;
      }
      if (typeof body.code === "number") {
        discordCode = body.code;
        message = `[${body.code}] ${message}`;
      }
    } catch {
      // Response body is not JSON — use status text.
      if (response.statusText) {
        message = `${response.status} ${response.statusText}`;
      }
    }

    return new DiscordApiError(message, response.status, discordCode, operation);
  }
}

// ---------------------------------------------------------------------------
// Convenience: is the error a rate-limit?
// ---------------------------------------------------------------------------

/**
 * Returns true when a {@link DiscordApiError} is a 429 rate-limit response.
 */
export function isRateLimitError(err: unknown): boolean {
  return err instanceof DiscordApiError && err.statusCode === 429;
}

/**
 * Returns true when a {@link DiscordApiError} is an authentication error
 * (401 Unauthorized or 403 Forbidden).
 */
export function isAuthError(err: unknown): boolean {
  return (
    err instanceof DiscordApiError &&
    (err.statusCode === 401 || err.statusCode === 403)
  );
}
