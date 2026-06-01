import test from "node:test";
import assert from "node:assert/strict";
import { routeDiscordMessage } from "../src/discord/messageRouter.ts";

test("parent project channel starts a task", () => {
  const action = routeDiscordMessage({
    authorBot: false,
    channelId: "parent-1",
    content: "make a music video idea",
    isThread: false,
  }, { projectChannelIds: new Set(["parent-1"]) });

  assert.deepEqual(action, {
    kind: "start_task",
    parentChannelId: "parent-1",
    userRequest: "make a music video idea",
  });
});

test("thread under project channel resumes waiting task", () => {
  const action = routeDiscordMessage({
    authorBot: false,
    channelId: "thread-1",
    parentChannelId: "parent-1",
    content: "approved, use option B",
    isThread: true,
  }, { projectChannelIds: new Set(["parent-1"]) });

  assert.deepEqual(action, {
    kind: "resume_task",
    threadId: "thread-1",
    userDecision: "approved, use option B",
  });
});

test("thread outside project channel is ignored", () => {
  const action = routeDiscordMessage({
    authorBot: false,
    channelId: "thread-1",
    parentChannelId: "other-parent",
    content: "approved",
    isThread: true,
  }, { projectChannelIds: new Set(["parent-1"]) });

  assert.deepEqual(action, { kind: "ignore", reason: "non_project_thread" });
});

test("bot and empty messages are ignored", () => {
  assert.deepEqual(routeDiscordMessage({
    authorBot: true,
    channelId: "parent-1",
    content: "make a task",
    isThread: false,
  }, { projectChannelIds: new Set(["parent-1"]) }), { kind: "ignore", reason: "bot_author" });

  assert.deepEqual(routeDiscordMessage({
    authorBot: false,
    channelId: "parent-1",
    content: "   ",
    isThread: false,
  }, { projectChannelIds: new Set(["parent-1"]) }), { kind: "ignore", reason: "empty_content" });
});

test("non-project parent channel is ignored", () => {
  const action = routeDiscordMessage({
    authorBot: false,
    channelId: "random-channel",
    content: "make a task",
    isThread: false,
  }, { projectChannelIds: new Set(["parent-1"]) });

  assert.deepEqual(action, { kind: "ignore", reason: "non_project_channel" });
});
