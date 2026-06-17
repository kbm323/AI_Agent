/**
 * Command Schema Validator — Sub-AC 1b
 *
 * Validates a NormalizedCommandRequest (output of Sub-AC 1a) against
 * registered slash command definition schemas. Checks required options,
 * option types, value constraints, and permission requirements.
 *
 * Returns a validated command payload or a structured validation error.
 *
 * @module ai-agent/command-schema-validator
 */

import type {
  NormalizedCommandRequest,
  ParsedCommandOption,
  SlashCommandOptionType,
} from "./discord-interaction-parser.ts";

// ────────────────────────────────────────────────────────────
// Schema definition types
// ────────────────────────────────────────────────────────────

/**
 * Discriminated union of valid permission levels.
 * - "everyone"  — any user can invoke
 * - "guild_only" — must be in a guild (server), no DM
 * - "admin_only" — user must have guild administrator permission
 * - "role"      — user must hold a specific Discord role ID
 */
export type CommandPermissionLevel =
  | "everyone"
  | "guild_only"
  | "admin_only";

/**
 * Concrete option types (same as Discord).
 */
export type CommandOptionType = SlashCommandOptionType;

/**
 * A value-choice entry for string/integer options.
 */
export interface CommandOptionChoice {
  name: string;
  value: string | number;
}

/**
 * Definition of a single slash command option.
 */
export interface CommandOptionDefinition {
  /** Option name as registered with Discord. */
  name: string;
  /** Human-readable description. */
  description: string;
  /** Expected Discord option type. */
  type: CommandOptionType;
  /** If true, the option MUST be present in the interaction. */
  required: boolean;
  /** If set, value must be one of these choices. */
  choices?: readonly CommandOptionChoice[];
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
 * Full definition of a supported slash command.
 */
export interface CommandDefinition {
  /** Command name as registered with Discord (e.g. "meeting"). */
  name: string;
  /** Human-readable description. */
  description: string;
  /** Options the command expects. */
  options: readonly CommandOptionDefinition[];
  /**
   * Minimum permission level required to invoke this command.
   * Defaults to "everyone" if omitted.
   */
  permission?: CommandPermissionLevel;
  /**
   * Whether the command can be invoked via bot mention / text fallback.
   * Defaults to false — only slash_command kind passes by default.
   */
  allow_bot_mention?: boolean;
}

// ────────────────────────────────────────────────────────────
// Validation output types
// ────────────────────────────────────────────────────────────

/**
 * A single option value after successful validation.
 */
export interface ValidatedOption {
  /** Option name. */
  name: string;
  /** Resolved concrete type. */
  type: CommandOptionType;
  /** Validated value. */
  value: string | number | boolean;
}

/**
 * A successfully validated command ready for the Coordinator.
 */
export interface ValidatedCommand {
  /** Always true — discriminator for type-narrowing. */
  valid: true;
  /** The command name. */
  commandName: string;
  /** Validated options keyed by name. */
  options: Record<string, ValidatedOption>;
  /** Original interaction metadata preserved for routing. */
  interactionId: string;
  interactionToken: string;
  userId: string;
  channelId: string;
  guildId?: string;
  timestamp: string;
  /** Original request kind preserved. */
  kind: NormalizedCommandRequest["kind"];
}

/**
 * Machine-readable validation error codes.
 */
export type ValidationErrorCode =
  | "UNKNOWN_COMMAND"
  | "MISSING_REQUIRED_OPTION"
  | "INVALID_OPTION_TYPE"
  | "VALUE_OUT_OF_RANGE"
  | "VALUE_TOO_SHORT"
  | "VALUE_TOO_LONG"
  | "INVALID_CHOICE"
  | "UNRECOGNIZED_OPTION"
  | "PERMISSION_DENIED"
  | "INVALID_INVOCATION_KIND"
  | "NO_COMMAND_DATA";

/** Single validation failure detail. */
export interface ValidationErrorDetail {
  code: ValidationErrorCode;
  message: string;
  /** Name of the offending option, if applicable. */
  option?: string;
}

/**
 * Structured validation error returned when the command request
 * does not satisfy the schema.
 */
export interface CommandValidationError {
  /** Always false — discriminator for type-narrowing. */
  valid: false;
  /** One or more validation failures. */
  errors: ValidationErrorDetail[];
  /** Original interaction ID for error logging. */
  interactionId: string;
}

/**
 * Union result type — callers narrow on the `valid` discriminant.
 */
export type CommandValidationResult = ValidatedCommand | CommandValidationError;

// ────────────────────────────────────────────────────────────
// Registry
// ────────────────────────────────────────────────────────────

/**
 * In-memory registry of registered slash commands.
 * Commands are keyed by name for O(1) lookup.
 */
const commandRegistry = new Map<string, CommandDefinition>();

/**
 * Register a command definition so it can be validated.
 * Re-registering the same name overwrites the previous definition.
 */
export function registerCommand(definition: CommandDefinition): void {
  commandRegistry.set(definition.name, definition);
}

/**
 * Remove a command from the registry.
 */
export function unregisterCommand(commandName: string): boolean {
  return commandRegistry.delete(commandName);
}

/**
 * Return a snapshot of all registered commands.
 */
export function listRegisteredCommands(): CommandDefinition[] {
  return [...commandRegistry.values()];
}

/**
 * Clear the entire registry (useful for testing).
 */
export function clearCommandRegistry(): void {
  commandRegistry.clear();
}

// ────────────────────────────────────────────────────────────
// Main validation entry point
// ────────────────────────────────────────────────────────────

/**
 * Validate a NormalizedCommandRequest against the registered
 * command definitions.
 *
 * @param request  — Normalized request from Sub-AC 1a parser.
 * @param memberPermissions — Optional set of permission strings the
 *   invoking Discord member holds (e.g. "ADMINISTRATOR").
 *   When absent, admin_only permission checks always **deny**.
 * @returns — Either a {@link ValidatedCommand} or a {@link CommandValidationError}.
 */
export function validateCommandRequest(
  request: NormalizedCommandRequest,
  memberPermissions?: ReadonlySet<string>,
): CommandValidationResult {
  const errors: ValidationErrorDetail[] = [];

  // ── 1. Validate invocation kind ────────────────────────
  if (request.kind === "ping") {
    return {
      valid: false,
      errors: [{ code: "INVALID_INVOCATION_KIND", message: "PING interactions are not commands" }],
      interactionId: request.interactionId,
    };
  }

  if (request.kind === "bot_mention" || !request.command) {
    // Bot mentions that carry no structured command are
    // handled by the Coordinator at a higher level and
    // may still be valid. If the registered command does not
    // allow bot mentions, this is caught below.
    if (!request.command && request.kind === "slash_command") {
      return {
        valid: false,
        errors: [{ code: "NO_COMMAND_DATA", message: "Slash command interaction missing command data" }],
        interactionId: request.interactionId,
      };
    }
    // Bot mention without structured command — let it pass
    // through as a mention-handled flow.
    if (!request.command) {
      return {
        valid: true,
        commandName: "mention",
        options: {},
        interactionId: request.interactionId,
        interactionToken: request.interactionToken,
        userId: request.userId,
        channelId: request.channelId,
        guildId: request.guildId,
        timestamp: request.timestamp,
        kind: request.kind,
      };
    }
  }

  // At this point we have request.command guaranteed.
  const cmd = request.command!;

  // ── 2. Look up command definition ──────────────────────
  const definition = commandRegistry.get(cmd.commandName);
  if (!definition) {
    return {
      valid: false,
      errors: [{
        code: "UNKNOWN_COMMAND",
        message: `Unknown command: /${cmd.commandName}`,
      }],
      interactionId: request.interactionId,
    };
  }

  // ── 3. Permission check ────────────────────────────────
  const permissionError = checkPermissions(definition, request, memberPermissions);
  if (permissionError) {
    errors.push(permissionError);
    // Permission errors are fatal — do not proceed.
    return { valid: false, errors, interactionId: request.interactionId };
  }

  // ── 4. Validate invocation kind for this command ───────
  if (request.kind === "bot_mention" && !definition.allow_bot_mention) {
    return {
      valid: false,
      errors: [{
        code: "INVALID_INVOCATION_KIND",
        message: `/${definition.name} does not support bot-mention invocation`,
      }],
      interactionId: request.interactionId,
    };
  }

  // ── 5. Validate required options ───────────────────────
  const validatedOptions: Record<string, ValidatedOption> = {};
  const providedNames = new Set(Object.keys(cmd.options));

  // Build a map of definition options for quick lookup.
  const optionDefs = new Map<string, CommandOptionDefinition>();
  for (const opt of definition.options) {
    optionDefs.set(opt.name, opt);
  }

  // Check for unrecognized options (strict mode).
  for (const name of providedNames) {
    if (!optionDefs.has(name)) {
      errors.push({
        code: "UNRECOGNIZED_OPTION",
        message: `/${definition.name} does not accept option: ${name}`,
        option: name,
      });
    }
  }

  // Check every defined option.
  for (const optDef of definition.options) {
    const provided = cmd.options[optDef.name];

    // Required check.
    if (!provided) {
      if (optDef.required) {
        errors.push({
          code: "MISSING_REQUIRED_OPTION",
          message: `/${definition.name} requires option: ${optDef.name}`,
          option: optDef.name,
        });
      }
      continue;
    }

    // Type check.
    if (provided.type !== optDef.type) {
      errors.push({
        code: "INVALID_OPTION_TYPE",
        message: `/${definition.name} ${optDef.name}: expected ${optDef.type}, got ${provided.type}`,
        option: optDef.name,
      });
      continue; // Skip further validation if type is wrong.
    }

    // Value constraint checks by type.
    const valueErrors = validateOptionValue(optDef, provided, definition.name);
    errors.push(...valueErrors);

    // If value checks passed, include in output.
    if (valueErrors.length === 0) {
      validatedOptions[optDef.name] = {
        name: optDef.name,
        type: optDef.type,
        value: provided.value,
      };
    }
  }

  if (errors.length > 0) {
    return { valid: false, errors, interactionId: request.interactionId };
  }

  return {
    valid: true,
    commandName: definition.name,
    options: validatedOptions,
    interactionId: request.interactionId,
    interactionToken: request.interactionToken,
    userId: request.userId,
    channelId: request.channelId,
    guildId: request.guildId,
    timestamp: request.timestamp,
    kind: request.kind,
  };
}

// ────────────────────────────────────────────────────────────
// Permission checking
// ────────────────────────────────────────────────────────────

function checkPermissions(
  definition: CommandDefinition,
  request: NormalizedCommandRequest,
  memberPermissions?: ReadonlySet<string>,
): ValidationErrorDetail | null {
  const perm = definition.permission ?? "everyone";

  switch (perm) {
    case "everyone":
      return null;

    case "guild_only":
      if (!request.guildId) {
        return {
          code: "PERMISSION_DENIED",
          message: `/${definition.name} can only be used in a server (guild)`,
        };
      }
      return null;

    case "admin_only":
      if (!memberPermissions || !memberPermissions.has("ADMINISTRATOR")) {
        return {
          code: "PERMISSION_DENIED",
          message: `/${definition.name} requires administrator permission`,
        };
      }
      return null;

    default:
      return null;
  }
}

// ────────────────────────────────────────────────────────────
// Option value validation
// ────────────────────────────────────────────────────────────

function validateOptionValue(
  def: CommandOptionDefinition,
  provided: ParsedCommandOption,
  commandName: string,
): ValidationErrorDetail[] {
  const errors: ValidationErrorDetail[] = [];
  const prefix = `/${commandName} ${def.name}`;

  // ── Choices validation ──
  if (def.choices && def.choices.length > 0) {
    const allowedValues = new Set(def.choices.map((c) => c.value));
    if (!allowedValues.has(provided.value)) {
      errors.push({
        code: "INVALID_CHOICE",
        message: `${prefix}: "${String(provided.value)}" is not an allowed choice`,
        option: def.name,
      });
    }
  }

  // ── String length constraints ──
  if (def.type === "string" && typeof provided.value === "string") {
    if (def.min_length !== undefined && provided.value.length < def.min_length) {
      errors.push({
        code: "VALUE_TOO_SHORT",
        message: `${prefix}: must be at least ${def.min_length} characters (got ${provided.value.length})`,
        option: def.name,
      });
    }
    if (def.max_length !== undefined && provided.value.length > def.max_length) {
      errors.push({
        code: "VALUE_TOO_LONG",
        message: `${prefix}: must be at most ${def.max_length} characters (got ${provided.value.length})`,
        option: def.name,
      });
    }
  }

  // ── Numeric range constraints ──
  if (
    (def.type === "integer" || def.type === "number") &&
    typeof provided.value === "number"
  ) {
    if (def.min_value !== undefined && provided.value < def.min_value) {
      errors.push({
        code: "VALUE_OUT_OF_RANGE",
        message: `${prefix}: must be at least ${def.min_value} (got ${provided.value})`,
        option: def.name,
      });
    }
    if (def.max_value !== undefined && provided.value > def.max_value) {
      errors.push({
        code: "VALUE_OUT_OF_RANGE",
        message: `${prefix}: must be at most ${def.max_value} (got ${provided.value})`,
        option: def.name,
      });
    }
  }

  return errors;
}

// ────────────────────────────────────────────────────────────
// Convenience: narrow on validation result
// ────────────────────────────────────────────────────────────

/**
 * Type guard: returns true when the result is a valid command.
 */
export function isValidCommand(
  result: CommandValidationResult,
): result is ValidatedCommand {
  return result.valid;
}

/**
 * Type guard: returns true when the result is a validation error.
 */
export function isValidationError(
  result: CommandValidationResult,
): result is CommandValidationError {
  return !result.valid;
}
