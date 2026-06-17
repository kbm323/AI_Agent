/**
 * Tests for Discord Interaction Parser (Sub-AC 1a).
 *
 * Covers: signature verification, PING handling, slash command parsing,
 * option extraction, error cases, and normalized request shape.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import {
  parseDiscordInteraction,
  verifyDiscordSignature,
  InteractionParseError,
  type NormalizedCommandRequest,
} from "../src/discord-interaction-parser.ts";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/** Generate a fresh Ed25519 key pair for signature testing. */
function generateTestKeys(): { publicKeyHex: string; privateKeyPem: string } {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519", {
    publicKeyEncoding: { type: "spki", format: "der" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });

  // Extract raw 32-byte public key from SPKI DER.
  // SPKI for Ed25519 is: 30 2A 30 05 06 03 2B 65 70 03 21 00 <32 bytes>
  const publicKeyHex = publicKey.toString("hex").slice(-64);

  return { publicKeyHex, privateKeyPem: privateKey as string };
}

/** Sign a body + timestamp pair with a private key, returning the hex signature. */
function signPayload(
  timestamp: string,
  body: string | Buffer,
  privateKeyPem: string,
): string {
  const data = Buffer.concat([
    Buffer.from(timestamp),
    Buffer.from(body),
  ]);
  // Ed25519: algorithm=null — the key determines the algorithm.
  return sign(null, data, privateKeyPem).toString("hex");
}

/** Create a minimal valid interaction body. */
function buildInteractionBody(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    id: "interaction-123",
    application_id: "app-456",
    type: 2,
    token: "token-abc",
    version: 1,
    guild_id: "guild-789",
    channel_id: "channel-012",
    member: { user: { id: "user-111", username: "testuser" } },
    data: {
      id: "cmd-345",
      name: "meeting",
      type: 1,
      options: [{ name: "agenda", type: 3, value: "Discuss Q2 roadmap" }],
    },
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// Signature verification
// ---------------------------------------------------------------------------

test("verifyDiscordSignature accepts valid Ed25519 signatures", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());

  const signature = signPayload(timestamp, body, privateKeyPem);

  // Should not throw.
  verifyDiscordSignature(body, signature, timestamp, publicKeyHex);
});

test("verifyDiscordSignature rejects tampered body", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());

  const signature = signPayload(timestamp, body, privateKeyPem);

  // Tamper with the body.
  const tampered = Buffer.from(buildInteractionBody({
    data: { name: "evil", type: 1 },
  }));

  assert.throws(
    () => verifyDiscordSignature(tampered, signature, timestamp, publicKeyHex),
    InteractionParseError,
    /signature verification failed/,
  );
});

test("verifyDiscordSignature rejects wrong timestamp", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());

  const signature = signPayload(timestamp, body, privateKeyPem);

  assert.throws(
    () =>
      verifyDiscordSignature(body, signature, "9999999999", publicKeyHex),
    InteractionParseError,
    /signature verification failed/,
  );
});

test("verifyDiscordSignature throws on missing parameters", () => {
  assert.throws(
    () => verifyDiscordSignature(Buffer.from("{}"), "", "123", "aa".repeat(32)),
    InteractionParseError,
    /Missing one or more signature verification parameters/,
  );
});

test("verifyDiscordSignature rejects invalid hex public key", () => {
  assert.throws(
    () =>
      verifyDiscordSignature(
        Buffer.from("{}"),
        "aa".repeat(64),
        "1234567890",
        "not-hex!!",
      ),
    InteractionParseError,
    /not valid hex/,
  );
});

test("verifyDiscordSignature rejects wrong-length public key", () => {
  assert.throws(
    () =>
      verifyDiscordSignature(
        Buffer.from("{}"),
        "aa".repeat(64),
        "1234567890",
        "aa".repeat(16),
      ),
    InteractionParseError,
    /must be 32 bytes/,
  );
});

test("verifyDiscordSignature rejects wrong-length signature", () => {
  const { publicKeyHex } = generateTestKeys();
  assert.throws(
    () =>
      verifyDiscordSignature(
        Buffer.from("{}"),
        "aa".repeat(10),
        "1234567890",
        publicKeyHex,
      ),
    InteractionParseError,
    /signature length/,
  );
});

// ---------------------------------------------------------------------------
// PING handling
// ---------------------------------------------------------------------------

test("parseDiscordInteraction handles PING (type 1)", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(JSON.stringify({
    id: "ping-1",
    token: "tok-1",
    type: 1,
    application_id: "app-1",
    version: 1,
  }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.kind, "ping");
  assert.equal(result.interactionId, "ping-1");
  assert.equal(result.interactionToken, "tok-1");
});

// ---------------------------------------------------------------------------
// Slash command parsing
// ---------------------------------------------------------------------------

test("parseDiscordInteraction parses /meeting slash command with options", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.kind, "slash_command");
  assert.equal(result.interactionId, "interaction-123");
  assert.equal(result.interactionToken, "token-abc");
  assert.equal(result.userId, "user-111");
  assert.equal(result.channelId, "channel-012");
  assert.equal(result.guildId, "guild-789");

  const cmd = result.command!;
  assert.equal(cmd.commandName, "meeting");
  assert.ok(cmd.options["agenda"]);
  assert.equal(cmd.options["agenda"].name, "agenda");
  assert.equal(cmd.options["agenda"].type, "string");
  assert.equal(cmd.options["agenda"].value, "Discuss Q2 roadmap");
});

test("parseDiscordInteraction handles multiple slash command options", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody({
    data: {
      id: "cmd-345",
      name: "meeting",
      type: 1,
      options: [
        { name: "agenda", type: 3, value: "Budget review" },
        { name: "priority", type: 3, value: "P1" },
        { name: "rounds", type: 4, value: 3 },
      ],
    },
  }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  const cmd = result.command!;
  assert.equal(cmd.commandName, "meeting");
  assert.equal(Object.keys(cmd.options).length, 3);
  assert.equal(cmd.options["agenda"].value, "Budget review");
  assert.equal(cmd.options["priority"].value, "P1");
  assert.equal(cmd.options["rounds"].value, 3);
  assert.equal(cmd.options["rounds"].type, "integer");
});

test("parseDiscordInteraction extracts guild_id from guild context", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody({
    guild_id: "my-guild-42",
  }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.guildId, "my-guild-42");
});

test("parseDiscordInteraction missing guild_id yields undefined guildId", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody({ guild_id: undefined }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.guildId, undefined);
});

test("parseDiscordInteraction falls back to interaction.user when member.user missing", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  // DM interactions have no member, only user.
  const body = Buffer.from(buildInteractionBody({
    member: undefined,
    user: { id: "dm-user-999", username: "dmuser" },
    guild_id: undefined,
  }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.userId, "dm-user-999");
  assert.equal(result.guildId, undefined);
});

// ---------------------------------------------------------------------------
// Bot mention / context menu handling
// ---------------------------------------------------------------------------

test("parseDiscordInteraction handles message-command (data missing)", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody({ data: undefined }));
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  assert.equal(result.kind, "bot_mention");
  assert.equal(result.command, undefined);
});

// ---------------------------------------------------------------------------
// Error cases
// ---------------------------------------------------------------------------

test("parseDiscordInteraction throws on non-JSON body", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from("not json at all !!!");
  const signature = signPayload(timestamp, body, privateKeyPem);

  assert.throws(
    () =>
      parseDiscordInteraction(body, signature, timestamp, publicKeyHex),
    InteractionParseError,
    /Failed to parse interaction JSON body/,
  );
});

test("parseDiscordInteraction throws on missing id", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(
    JSON.stringify({ type: 2, token: "tok", application_id: "app", version: 1 }),
  );
  const signature = signPayload(timestamp, body, privateKeyPem);

  assert.throws(
    () =>
      parseDiscordInteraction(body, signature, timestamp, publicKeyHex),
    InteractionParseError,
    /missing valid 'id'/,
  );
});

test("parseDiscordInteraction throws on missing token", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(
    JSON.stringify({ id: "abc", type: 2, application_id: "app", version: 1 }),
  );
  const signature = signPayload(timestamp, body, privateKeyPem);

  assert.throws(
    () =>
      parseDiscordInteraction(body, signature, timestamp, publicKeyHex),
    InteractionParseError,
    /missing valid 'token'/,
  );
});

test("parseDiscordInteraction throws on unknown interaction type", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(
    JSON.stringify({
      id: "abc",
      token: "tok",
      type: 99, // Invalid type
      application_id: "app",
      version: 1,
    }),
  );
  const signature = signPayload(timestamp, body, privateKeyPem);

  assert.throws(
    () =>
      parseDiscordInteraction(body, signature, timestamp, publicKeyHex),
    InteractionParseError,
    /Unknown interaction type/,
  );
});

test("parseDiscordInteraction throws on signature mismatch", () => {
  const { publicKeyHex } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());

  assert.throws(
    () =>
      parseDiscordInteraction(
        body,
        "aa".repeat(64), // bogus signature
        timestamp,
        publicKeyHex,
      ),
    InteractionParseError,
    /signature verification failed/,
  );
});

// ---------------------------------------------------------------------------
// NormalizedCommandRequest shape
// ---------------------------------------------------------------------------

test("NormalizedCommandRequest contains all required fields after slash command parse", () => {
  const { publicKeyHex, privateKeyPem } = generateTestKeys();
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = Buffer.from(buildInteractionBody());
  const signature = signPayload(timestamp, body, privateKeyPem);

  const result: NormalizedCommandRequest = parseDiscordInteraction(
    body,
    signature,
    timestamp,
    publicKeyHex,
  );

  // Verify every field on the normalized request.
  assert.equal(typeof result.kind, "string");
  assert.ok(["slash_command", "bot_mention", "ping"].includes(result.kind));
  assert.equal(typeof result.interactionId, "string");
  assert.ok(result.interactionId.length > 0);
  assert.equal(typeof result.interactionToken, "string");
  assert.ok(result.interactionToken.length > 0);
  assert.equal(typeof result.userId, "string");
  assert.ok(result.userId.length > 0);
  assert.equal(typeof result.channelId, "string");
  assert.ok(result.channelId.length > 0);
  assert.equal(typeof result.timestamp, "string");
  assert.ok(result.timestamp.length > 0);

  if (result.kind === "slash_command") {
    assert.ok(result.command);
    assert.equal(typeof result.command.commandName, "string");
    assert.equal(typeof result.command.options, "object");
  }
});

test("InteractionParseError carries proper name and code", () => {
  const err = new InteractionParseError("test message");
  assert.equal(err.name, "InteractionParseError");
  assert.equal(err.code, "INTERACTION_PARSE_ERROR");
  assert.equal(err.message, "test message");
});
