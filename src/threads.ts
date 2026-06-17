/**
 * Thread management module — public API surface for Discord thread
 * lifecycle operations.
 *
 * Import from `ai-agent/threads` to create, archive, and inspect
 * Discord threads through the active Discord client connection.
 *
 * @example
 * ```ts
 * import { create_thread, archive_thread, get_thread } from "ai-agent/threads";
 *
 * const { threadId } = await create_thread({
 *   parentChannelId: "channel-123",
 *   name: "Task: Brand campaign review",
 *   initialMessage: "Starting multi-agent meeting...",
 * });
 *
 * const info = await get_thread(threadId);
 * await archive_thread(threadId);
 * ```
 *
 * @module ai-agent/threads
 */

import { get_client } from "./discord-client.ts";

/**
 * Create a new thread in a Discord channel and optionally post an
 * initial message.
 *
 * Requires an active Discord client connection — call
 * {@link connect} from `ai-agent/discord` first.
 *
 * @param input.parentChannelId  The Discord channel ID where the thread is created.
 * @param input.name             Thread name (max 100 characters).
 * @param input.initialMessage   First message posted in the new thread.
 * @returns                      The created thread's ID and optional URL.
 * @throws                       If the Discord client is not connected.
 */
export async function create_thread(input: {
  parentChannelId: string;
  name: string;
  initialMessage: string;
}): Promise<{ threadId: string; url?: string }> {
  const client = get_client();
  if (!client?.connected) {
    throw new Error("Discord client not connected. Call connect() from ai-agent/discord first.");
  }
  const delivery = client.createDelivery();
  return delivery.createThread(input);
}

/**
 * Archive a Discord thread, making it read-only.
 *
 * Idempotent — archiving an already-archived thread is a no-op.
 *
 * @param threadId  The Discord thread ID to archive.
 * @throws          If the Discord client is not connected.
 */
export async function archive_thread(threadId: string): Promise<void> {
  const client = get_client();
  if (!client?.connected) {
    throw new Error("Discord client not connected. Call connect() from ai-agent/discord first.");
  }
  const delivery = client.createDelivery();
  return delivery.archiveThread(threadId);
}

/**
 * Retrieve the current state of a Discord thread.
 *
 * @param threadId  The Discord thread ID to inspect.
 * @returns         Thread metadata including ID, name, and archive status.
 * @throws          If the Discord client is not connected.
 */
export async function get_thread(
  threadId: string,
): Promise<{ threadId: string; name: string; archived: boolean }> {
  const client = get_client();
  if (!client?.connected) {
    throw new Error("Discord client not connected. Call connect() from ai-agent/discord first.");
  }
  const delivery = client.createDelivery();
  return delivery.getThread(threadId);
}
