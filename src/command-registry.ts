/**
 * Command Registry — Sub-AC 1b
 *
 * Central registration of all supported slash commands for the
 * AI Virtual Entertainment Company multi-agent meeting system.
 *
 * This file defines the canonical command schemas and auto-registers
 * them on import. The Coordinator uses {@link validateCommandRequest}
 * from command-schema-validator.ts to check incoming interactions.
 *
 * @module ai-agent/command-registry
 */

import {
  registerCommand,
  clearCommandRegistry,
  type CommandDefinition,
} from "./command-schema-validator.ts";

// ────────────────────────────────────────────────────────────
// Command definitions
// ────────────────────────────────────────────────────────────

/**
 * `/meeting` — Primary meeting command.
 *
 * Required options:
 *   - agenda (STRING) — The meeting topic / objective.
 *
 * Optional options:
 *   - priority (STRING) — P0 | P1 | P2 | P3 (default: P2)
 *   - rounds   (INTEGER) — Max rounds, 1–4 (default: 3)
 *   - urgent   (BOOLEAN) — Elevate to P0 regardless of content
 *
 * Permissions: everyone can invoke. Guild-only context.
 * bot_mention: allowed — @AI_Company <text> also routes here.
 */
const MEETING_COMMAND: CommandDefinition = {
  name: "meeting",
  description: "Start a multi-agent meeting to discuss a topic",
  permission: "guild_only",
  allow_bot_mention: true,
  options: [
    {
      name: "agenda",
      description: "Meeting topic and objectives",
      type: "string",
      required: true,
      min_length: 1,
      max_length: 4000,
    },
    {
      name: "priority",
      description: "Meeting priority level",
      type: "string",
      required: false,
      choices: [
        { name: "P0 — Critical", value: "P0" },
        { name: "P1 — High", value: "P1" },
        { name: "P2 — Normal", value: "P2" },
        { name: "P3 — Low", value: "P3" },
      ],
    },
    {
      name: "rounds",
      description: "Maximum discussion rounds (1–4)",
      type: "integer",
      required: false,
      min_value: 1,
      max_value: 4,
    },
    {
      name: "urgent",
      description: "Elevate to P0 critical priority",
      type: "boolean",
      required: false,
    },
  ],
};

/**
 * `/cancel` — Cancel a running or queued meeting.
 *
 * Required options:
 *   - meeting_id (STRING) — The meeting to cancel.
 *
 * Permissions: everyone can invoke their own meetings;
 *   admin_only may cancel any meeting.
 */
const CANCEL_COMMAND: CommandDefinition = {
  name: "cancel",
  description: "Cancel a running or queued meeting",
  permission: "everyone",
  allow_bot_mention: false,
  options: [
    {
      name: "meeting_id",
      description: "The meeting ID to cancel",
      type: "string",
      required: true,
      min_length: 1,
    },
  ],
};

// ────────────────────────────────────────────────────────────
// Registration
// ────────────────────────────────────────────────────────────

/**
 * All commands that the system supports.
 * Add new commands here.
 */
const ALL_COMMANDS: readonly CommandDefinition[] = [
  MEETING_COMMAND,
  CANCEL_COMMAND,
];

/**
 * Register all commands into the global registry.
 * Call once at startup. Idempotent — clears then re-registers.
 */
export function registerAllCommands(): void {
  clearCommandRegistry();
  for (const def of ALL_COMMANDS) {
    registerCommand(def);
  }
}

// Auto-register on first import.
registerAllCommands();

// Re-export for consumers that want the raw definitions.
export { MEETING_COMMAND, CANCEL_COMMAND };
