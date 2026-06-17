/**
 * Message/event handling module — public API surface for Discord
 * message sending and event subscription.
 *
 * Import from `ai-agent/messages` to send messages, listen for
 * incoming messages, and register pattern-matched handlers.
 *
 * @example
 * ```ts
 * import { send_message, on_message, register_handler } from "ai-agent/messages";
 *
 * // Send a message to a Discord thread
 * const { messageId } = await send_message("channel-123", "Hello from AI Agent", "thread-456");
 *
 * // Listen for all incoming messages
 * const unsub = on_message(async (msg) => {
 *   console.log(`[${msg.authorId}] ${msg.content}`);
 * });
 *
 * // Register a handler for a command pattern
 * register_handler("/meeting", async (msg) => {
 *   // handle meeting command
 * });
 * ```
 *
 * @module ai-agent/messages
 */

import { get_client } from "./discord-client.ts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A Discord message received by the bot. */
export interface DiscordMessage {
  /** Discord message ID. */
  id: string;
  /** Channel ID where the message was sent. */
  channelId: string;
  /** Thread ID if the message is in a thread. */
  threadId?: string;
  /** Discord user ID of the message author. */
  authorId: string;
  /** Message text content. */
  content: string;
  /** ISO 8601 timestamp of when the message was created. */
  createdAt: string;
}

/** Signature for a message event handler. */
export type MessageHandler = (message: DiscordMessage) => Promise<void>;

/** An unsubscribe function returned by {@link on_message} and {@link register_handler}. */
export type Unsubscribe = () => void;

// ---------------------------------------------------------------------------
// Handler registry (module-level singleton)
// ---------------------------------------------------------------------------

const _globalHandlers: Set<MessageHandler> = new Set();
const _patternHandlers: Array<{
  pattern: string | RegExp;
  handler: MessageHandler;
}> = [];

// ---------------------------------------------------------------------------
// Public functions
// ---------------------------------------------------------------------------

/**
 * Send a message to a Discord channel or thread.
 *
 * Requires an active Discord client connection — call
 * {@link connect} from `ai-agent/discord` first.
 *
 * @param channelId  The target Discord channel ID.
 * @param content    The message text content.
 * @param threadId   Optional thread ID to post inside a thread instead of the channel.
 * @returns          The sent message's ID.
 * @throws           If the Discord client is not connected.
 *
 * @example
 * ```ts
 * import { send_message } from "ai-agent/messages";
 *
 * // Post to a channel
 * const { messageId } = await send_message("channel-123", "Hello world");
 *
 * // Post inside a thread
 * const { messageId } = await send_message("channel-123", "Reply in thread", "thread-456");
 * ```
 */
export async function send_message(
  channelId: string,
  content: string,
  threadId?: string,
): Promise<{ messageId: string }> {
  const client = get_client();
  if (!client?.connected) {
    throw new Error(
      "Discord client not connected. Call connect() from ai-agent/discord first.",
    );
  }

  const delivery = client.createDelivery();

  if (threadId) {
    await delivery.postThread({ threadId, content });
  } else {
    await delivery.postParent({ channelId, content });
  }

  return { messageId: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` };
}

/**
 * Register a global listener for every incoming Discord message the
 * bot receives.
 *
 * Returns an {@link Unsubscribe} function — call it to stop
 * receiving events from this handler.
 *
 * The handler is called for **every** message the bot can see,
 * including its own. Use {@link register_handler} if you need to
 * filter by pattern.
 *
 * @param handler  Async callback invoked for each incoming message.
 * @returns        An unsubscribe function.
 *
 * @example
 * ```ts
 * import { on_message } from "ai-agent/messages";
 *
 * const unsub = on_message(async (msg) => {
 *   console.log(`[${msg.authorId}] ${msg.content}`);
 * });
 *
 * // Later, when no longer needed:
 * unsub();
 * ```
 */
export function on_message(handler: MessageHandler): Unsubscribe {
  _globalHandlers.add(handler);
  return () => {
    _globalHandlers.delete(handler);
  };
}

/**
 * Register a handler that fires only when an incoming message
 * matches a given pattern.
 *
 * - If `pattern` is a **string**, the handler fires when the
 *   message content starts with that string (case-insensitive).
 * - If `pattern` is a **RegExp**, the handler fires when the
 *   regular expression matches the full message content.
 *
 * Returns an {@link Unsubscribe} function — call it to stop
 * receiving events from this handler.
 *
 * @param pattern  String prefix (case-insensitive) or RegExp to match against message content.
 * @param handler  Async callback invoked when a message matches the pattern.
 * @returns        An unsubscribe function.
 *
 * @example
 * ```ts
 * import { register_handler } from "ai-agent/messages";
 *
 * // Prefix match
 * register_handler("/meeting", async (msg) => {
 *   const agenda = msg.content.slice("/meeting".length).trim();
 *   console.log("Meeting requested:", agenda);
 * });
 *
 * // Regex match
 * register_handler(/^!task\s+(.+)/i, async (msg) => {
 *   const taskDesc = msg.content.match(/^!task\s+(.+)/i)![1];
 *   console.log("Task created:", taskDesc);
 * });
 * ```
 */
export function register_handler(
  pattern: string | RegExp,
  handler: MessageHandler,
): Unsubscribe {
  const entry = { pattern, handler };
  _patternHandlers.push(entry);
  return () => {
    const idx = _patternHandlers.indexOf(entry);
    if (idx !== -1) {
      _patternHandlers.splice(idx, 1);
    }
  };
}

/**
 * @internal
 * Dispatch an incoming message to all registered handlers.
 *
 * Called by the Discord gateway integration when a MESSAGE_CREATE
 * event arrives.  Global handlers fire first, then pattern-matched
 * handlers.
 */
export async function _dispatch_message(message: DiscordMessage): Promise<void> {
  // Fire global handlers concurrently
  const globalPromises = [..._globalHandlers].map((handler) =>
    Promise.resolve(handler(message)).catch(() => {
      // Swallow individual handler errors so one broken handler
      // doesn't prevent others from running.
    }),
  );

  // Fire pattern-matched handlers
  const patternPromises = _patternHandlers
    .filter(({ pattern }) => {
      if (typeof pattern === "string") {
        return message.content.toLowerCase().startsWith(pattern.toLowerCase());
      }
      return pattern.test(message.content);
    })
    .map(({ handler }) =>
      Promise.resolve(handler(message)).catch(() => {
        // Swallow individual handler errors.
      }),
    );

  await Promise.all([...globalPromises, ...patternPromises]);
}

/**
 * @internal
 * Remove all registered handlers.  Used for testing.
 */
export function _clear_handlers(): void {
  _globalHandlers.clear();
  _patternHandlers.length = 0;
}
