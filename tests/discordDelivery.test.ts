import test from "node:test";
import assert from "node:assert/strict";
import { chunkDiscordMessage } from "../src/discord/discordDelivery.ts";
import { parseVerdict } from "../src/executors/hermesCliExecutor.ts";
import { parseOpenClawAgentOutput } from "../src/executors/openclawCliExecutor.ts";

test("discord message chunking keeps short messages intact", () => {
  assert.deepEqual(chunkDiscordMessage("hello", 10), ["hello"]);
});

test("discord message chunking splits long content", () => {
  const chunks = chunkDiscordMessage("alpha beta gamma", 10);
  assert.deepEqual(chunks, ["alpha beta", "gamma"]);
});

test("Hermes verdict parser falls back to revision when missing", () => {
  assert.equal(parseVerdict("Verdict: agree\nLooks good."), "agree");
  assert.equal(parseVerdict("Verdict: needs_user_decision"), "needs_user_decision");
  assert.equal(parseVerdict("No explicit verdict."), "partial_agree");
});

test("Hermes verdict parser supports Phase 2-A Korean and English verdicts", () => {
  assert.equal(parseVerdict("Verdict: agree"), "agree");
  assert.equal(parseVerdict("Verdict: partial_agree"), "partial_agree");
  assert.equal(parseVerdict("Verdict: disagree"), "disagree");
  assert.equal(parseVerdict("Verdict: needs_user_decision"), "needs_user_decision");
  assert.equal(parseVerdict("판정: 동의"), "agree");
  assert.equal(parseVerdict("판정: 부분동의"), "partial_agree");
  assert.equal(parseVerdict("판정: 비동의"), "disagree");
  assert.equal(parseVerdict("판정: 사용자결정필요"), "needs_user_decision");
});

test("legacy agree_with_changes maps to partial agreement", () => {
  assert.equal(parseVerdict("Verdict: agree_with_changes"), "partial_agree");
});

test("OpenClaw JSON parser extracts payload text after gateway fallback logs", () => {
  const stdout = [
    "gateway connect failed: scope upgrade pending approval",
    '{"payloads":[{"text":"OPENCLAW_OK"}],"meta":{"finalAssistantVisibleText":"fallback"}}',
  ].join("\n");
  assert.equal(parseOpenClawAgentOutput(stdout), "OPENCLAW_OK");
});
