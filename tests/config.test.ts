import test from "node:test";
import assert from "node:assert/strict";
import { loadRuntimeConfig } from "../src/config.ts";

test("loads Phase 2-A timeout and debug mention config", () => {
  const config = loadRuntimeConfig({
    DISCORD_BOT_TOKEN: "discord-token",
    AI_AGENT_PROJECT_CHANNEL_IDS: "parent-1",
    AI_AGENT_HERMES_TIMEOUT_SECONDS: "123",
    AI_AGENT_DEBUG_MENTIONS: "true",
  });

  assert.equal(config.hermesTimeoutSeconds, 123);
  assert.equal(config.debugMentions, true);
});

test("loads team model routing config", () => {
  const config = loadRuntimeConfig({
    DISCORD_BOT_TOKEN: "discord-token",
    AI_AGENT_PROJECT_CHANNEL_IDS: "parent-1",
    AI_AGENT_OPENCLAW_CONTENT_MODEL: "openclaw-content",
    AI_AGENT_HERMES_TECH_MODEL: "hermes-tech",
  });

  assert.equal(config.modelRouting.openclaw.content, "openclaw-content");
  assert.equal(config.modelRouting.hermes.tech, "hermes-tech");
});
