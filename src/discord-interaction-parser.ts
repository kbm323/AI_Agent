/**
 * Discord Interaction Parser — Sub-AC 1a
 *
 * Receives raw Discord slash-command interaction payloads, verifies the
 * interaction signature (Ed25519), extracts the command name and options,
 * and returns a normalized command request object.
 *
 * Implements the Discord Interactions Endpoint security model:
 * https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization
 *
 * @module ai-agent/discord-interaction-parser
 */

import { verify, timingSafeEqual } from "node:crypto";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Supported Discord interaction types we handle.
 * 1 = PING (endpoint verification), 2 = APPLICATION_COMMAND (slash command).
 */
export const InteractionType = {
  PING: 1,
  APPLICATION_COMMAND: 2,
} as const;
export type InteractionType =
  (typeof InteractionType)[keyof typeof InteractionType];

/** Valid interaction type numeric values. */
const VALID_INTERACTION_TYPES: ReadonlySet<number> = new Set(
  Object.values(InteractionType),
);

/**
 * Option types for slash command options as defined by Discord.
 */
export type SlashCommandOptionType =
  | "string"
  | "integer"
  | "boolean"
  | "number"
  | "channel"
  | "role"
  | "user"
  | "mentionable"
  | "attachment";

/**
 * A single resolved option value from a Discord slash command.
 */
export interface ParsedCommandOption {
  /** Option name as defined in the command registration. */
  name: string;
  /** Option type. */
  type: SlashCommandOptionType;
  /** Resolved value — Discord sends the concrete value, not the raw text. */
  value: string | number | boolean;
  /** True if this option is focused (for autocomplete). */
  focused?: boolean;
}

/**
 * Shape of a `/meeting agenda:"..."` slash command after parsing.
 */
export interface ParsedMeetingCommand {
  /** Always "meeting" for this system's primary interaction. */
  commandName: "meeting" | string;
  /** Subcommand group name, if any (Discord nested commands). */
  subcommandGroup?: string;
  /** Subcommand name, if any. */
  subcommand?: string;
  /** Resolved options keyed by name. */
  options: Record<string, ParsedCommandOption>;
}

/**
 * Normalized command request object returned by the parser.
 *
 * This is the universal intake format — regardless of whether the
 * user typed `/meeting agenda:"foo"` or `@AI_Company foo`, the
 * Coordinator receives a NormalizedCommandRequest.
 */
export interface NormalizedCommandRequest {
  /** Discriminator for dispatch. */
  kind: "slash_command" | "bot_mention" | "ping";
  /** Unique Discord interaction ID. */
  interactionId: string;
  /** Discord interaction token (valid for 15 minutes, used for follow-ups). */
  interactionToken: string;
  /** The Discord application/guild command data, if present. */
  command?: ParsedMeetingCommand;
  /** Raw text content from a bot-mention message. */
  mentionContent?: string;
  /** The Discord user who issued the command/mention. */
  userId: string;
  /** The channel where the command was invoked. */
  channelId: string;
  /** Guild (server) ID, if in a guild context. */
  guildId?: string;
  /** ISO 8601 timestamp of the interaction. */
  timestamp: string;
  /** Any options appended by the routing layer later (empty at parse time). */
  appData?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Parsing entry point
// ---------------------------------------------------------------------------

/**
 * Parse and verify a raw Discord interaction HTTP POST body.
 *
 * @param rawBody     — The raw request body bytes (Buffer or Uint8Array).
 * @param signature   — Value of the `X-Signature-Ed25519` header.
 * @param timestamp   — Value of the `X-Signature-Timestamp` header.
 * @param publicKey   — Your Discord application's public key (from Developer Portal).
 * @returns           — A {@link NormalizedCommandRequest} ready for the Coordinator.
 * @throws            — {@link InteractionParseError} on signature mismatch, bad JSON, or unknown type.
 */
export function parseDiscordInteraction(
  rawBody: Buffer | Uint8Array,
  signature: string,
  timestamp: string,
  publicKey: string,
): NormalizedCommandRequest {
  // 1. Verify the Ed25519 signature before touching the body.
  const bodyStr = decodeBody(rawBody);
  verifyDiscordSignature(rawBody, signature, timestamp, publicKey);

  // 2. Parse the JSON payload.
  let interaction: RawDiscordInteraction;
  try {
    interaction = JSON.parse(bodyStr) as RawDiscordInteraction;
  } catch (cause) {
    throw new InteractionParseError("Failed to parse interaction JSON body", {
      cause,
    });
  }

  // 3. Validate required envelope fields.
  if (!interaction || typeof interaction !== "object") {
    throw new InteractionParseError(
      "Interaction payload is not a JSON object",
    );
  }
  if (
    typeof interaction.id !== "string" ||
    interaction.id.length === 0
  ) {
    throw new InteractionParseError("Interaction payload missing valid 'id'");
  }
  if (
    typeof interaction.token !== "string" ||
    interaction.token.length === 0
  ) {
    throw new InteractionParseError(
      "Interaction payload missing valid 'token'",
    );
  }
  if (
    typeof interaction.type !== "number" ||
    !VALID_INTERACTION_TYPES.has(interaction.type)
  ) {
    throw new InteractionParseError(
      `Unknown interaction type: ${String(interaction.type)}`,
    );
  }

  // 4. Handle PING (Discord endpoint verification).
  if (interaction.type === InteractionType.PING) {
    return {
      kind: "ping",
      interactionId: interaction.id,
      interactionToken: interaction.token,
      userId: interaction.member?.user?.id ??
        interaction.user?.id ?? "unknown",
      channelId: interaction.channel_id ?? "unknown",
      guildId: interaction.guild_id,
      timestamp: new Date().toISOString(),
    };
  }

  // 5. Handle APPLICATION_COMMAND (slash command).
  if (interaction.type === InteractionType.APPLICATION_COMMAND) {
    return parseApplicationCommand(interaction);
  }

  // Should never reach here — type guard above catches unknown types.
  throw new InteractionParseError(
    `Unhandled interaction type: ${interaction.type}`,
  );
}

// ---------------------------------------------------------------------------
// Signature verification
// ---------------------------------------------------------------------------

/**
 * Verify an Ed25519 Discord interaction signature.
 *
 * Replicates the algorithm documented at:
 * https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization
 *
 * @throws {@link InteractionParseError} if the signature is invalid.
 */
export function verifyDiscordSignature(
  rawBody: Buffer | Uint8Array,
  signature: string,
  timestamp: string,
  publicKey: string,
): void {
  if (!signature || !timestamp || !publicKey) {
    throw new InteractionParseError(
      "Missing one or more signature verification parameters",
    );
  }

  // Discord public keys are hex-encoded.
  // Node's crypto expects the key in either PEM (SPKI) or raw DER format.
  // We convert the hex key to raw 32-byte buffer and construct a PEM key.
  let pubKeyBuf: Buffer;
  try {
    pubKeyBuf = Buffer.from(publicKey, "hex");
  } catch {
    throw new InteractionParseError(
      "Discord public key is not valid hex",
    );
  }

  if (pubKeyBuf.length !== 32) {
    throw new InteractionParseError(
      `Discord public key must be 32 bytes (got ${pubKeyBuf.length})`,
    );
  }

  // Convert hex public key to PEM (SPKI) format for Node's crypto.
  const publicKeyPem = ed25519PublicKeyToPem(pubKeyBuf);

  // Verify using Ed25519 via crypto.verify().
  // Ed25519 uses algorithm=null (key determines the algorithm).
  const sigBuf = Buffer.from(signature, "hex");
  if (sigBuf.length !== 64) {
    throw new InteractionParseError(
      `Invalid Ed25519 signature length: ${sigBuf.length} (expected 64)`,
    );
  }

  const data = Buffer.concat([Buffer.from(timestamp), rawBody]);
  const isValid = verify(null, data, publicKeyPem, sigBuf);
  if (!isValid) {
    throw new InteractionParseError(
      "Discord interaction signature verification failed",
    );
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Raw shape of a Discord Interaction object as received over HTTP.
 * Only the fields we actually use are modeled; other fields are ignored.
 */
interface RawDiscordInteraction {
  id: string;
  application_id: string;
  type: number;
  token: string;
  version: number;
  guild_id?: string;
  channel_id?: string;
  channel?: { id: string };
  member?: {
    user?: { id: string; username?: string };
    permissions?: string;
  };
  user?: { id: string; username?: string };
  data?: RawApplicationCommandData;
  message?: unknown;
  locale?: string;
  guild_locale?: string;
}

interface RawApplicationCommandData {
  id: string;
  name: string;
  type: number;
  options?: RawCommandOption[];
  resolved?: Record<string, unknown>;
  guild_id?: string;
  target_id?: string;
}

interface RawCommandOption {
  name: string;
  type: number;
  value?: string | number | boolean;
  options?: RawCommandOption[];
  focused?: boolean;
}

/** Map Discord option type integers to string labels. */
const OPTION_TYPE_MAP: Record<number, SlashCommandOptionType> = {
  3: "string",
  4: "integer",
  5: "boolean",
  10: "number",
  7: "channel",
  8: "role",
  6: "user",
  9: "mentionable",
  11: "attachment",
};

function parseApplicationCommand(
  interaction: RawDiscordInteraction,
): NormalizedCommandRequest {
  const data = interaction.data;

  // Handle message-command (user right-clicks a message) — no command data.
  if (!data) {
    // Treat as a bot mention / context menu invocation.
    return {
      kind: "bot_mention",
      interactionId: interaction.id,
      interactionToken: interaction.token,
      userId: interaction.member?.user?.id ??
        interaction.user?.id ?? "unknown",
      channelId: interaction.channel_id ??
        interaction.channel?.id ?? "unknown",
      guildId: interaction.guild_id,
      timestamp: new Date().toISOString(),
    };
  }

  // Flatten recursive options into a flat name→value map.
  const options: Record<string, ParsedCommandOption> = {};
  if (Array.isArray(data.options)) {
    for (const raw of data.options) {
      // Subcommand or subcommand-group: options are nested.
      if (
        (raw.type === 1 || raw.type === 2) &&
        Array.isArray(raw.options)
      ) {
        for (const nested of raw.options) {
          if (nested.name !== undefined) {
            options[nested.name] = normalizeOption(nested);
          }
        }
      } else if (raw.name !== undefined) {
        options[raw.name] = normalizeOption(raw);
      }
    }
  }

  const command: ParsedMeetingCommand = {
    commandName: data.name,
    options,
  };

  return {
    kind: "slash_command",
    interactionId: interaction.id,
    interactionToken: interaction.token,
    command,
    userId: interaction.member?.user?.id ??
      interaction.user?.id ?? "unknown",
    channelId: interaction.channel_id ??
      interaction.channel?.id ?? "unknown",
    guildId: interaction.guild_id,
    timestamp: new Date().toISOString(),
  };
}

function normalizeOption(raw: RawCommandOption): ParsedCommandOption {
  return {
    name: raw.name,
    type: OPTION_TYPE_MAP[raw.type] ?? "string",
    value: raw.value ?? "",
    focused: raw.focused,
  };
}

function decodeBody(rawBody: Buffer | Uint8Array): string {
  const buf = Buffer.isBuffer(rawBody)
    ? rawBody
    : Buffer.from(rawBody);
  return buf.toString("utf-8");
}

/**
 * Convert a raw 32-byte Ed25519 public key to PEM (SubjectPublicKeyInfo) format.
 *
 * Ed25519 OID: 1.3.101.112
 * SPKI structure: SEQUENCE { SEQUENCE { OID 1.3.101.112 }, BIT STRING <key> }
 *
 * See RFC 8410 section 4.
 */
function ed25519PublicKeyToPem(keyBytes: Buffer): string {
  // OID 1.3.101.112 encoded in DER: 06 03 2B 65 70
  const ed25519Oid = Buffer.from([0x06, 0x03, 0x2B, 0x65, 0x70]);

  // AlgorithmIdentifier: SEQUENCE { OID }
  const algoSeq = derSequence(ed25519Oid);

  // BIT STRING: tag 0x03, length, unused bits (0x00), key bytes
  const bitString = Buffer.concat([
    Buffer.from([0x03, keyBytes.length + 1, 0x00]),
    keyBytes,
  ]);

  // SubjectPublicKeyInfo: SEQUENCE { AlgorithmIdentifier, BIT STRING }
  const spki = derSequence(Buffer.concat([algoSeq, bitString]));

  const base64 = spki.toString("base64");
  // PEM line wrapping at 64 characters.
  const wrapped = base64.match(/.{1,64}/g)?.join("\n") ?? base64;
  return `-----BEGIN PUBLIC KEY-----\n${wrapped}\n-----END PUBLIC KEY-----`;
}

/** DER-encode a SEQUENCE (tag 0x30). */
function derSequence(content: Buffer): Buffer {
  const len = encodeDerLength(content.length);
  return Buffer.concat([Buffer.from([0x30]), len, content]);
}

/** DER length encoding: short form (< 128) or long form. */
function encodeDerLength(length: number): Buffer {
  if (length < 128) {
    return Buffer.from([length]);
  }
  const bytes: number[] = [];
  let remaining = length;
  while (remaining > 0) {
    bytes.unshift(remaining & 0xff);
    remaining >>>= 8;
  }
  return Buffer.from([0x80 | bytes.length, ...bytes]);
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/**
 * Structured error thrown when interaction parsing or signature
 * verification fails.
 */
export class InteractionParseError extends Error {
  public readonly code: string;

  constructor(
    message: string,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = "InteractionParseError";
    this.code = "INTERACTION_PARSE_ERROR";
  }
}
