/**
 * Command Registration Orchestrator — Sub-AC 1a-iii
 *
 * Module that iterates over command schemas and registers them via
 * a transport layer. Designed as the cohesive layer between:
 *   - CommandSchemaProvider (what to register)
 *   - CommandTransport (how to register — typically Discord REST API)
 *
 * Responsibilities:
 *   1. Fetch schemas from a provider
 *   2. Query transport for already-registered commands (idempotency check)
 *   3. Diff: determine which commands are new, changed, or unchanged
 *   4. Register only new/changed commands via transport
 *   5. Isolate per-command errors — one failure never blocks others
 *   6. Return structured, per-command result with summary
 *
 * Both the schema provider and the transport are injected interfaces,
 * making the orchestrator fully testable with mocks. No real I/O
 * occurs unless a real transport is wired in.
 *
 * @module ai-agent/command-registration-orchestrator
 */

import type { CommandDefinition } from "./command-schema-validator.ts";

// ---------------------------------------------------------------------------
// Transport interface
// ---------------------------------------------------------------------------

/**
 * A command as returned by the transport's list operation.
 *
 * The orchestrator only needs `id` and `name` for idempotency checks.
 * Additional transport-specific fields (guild_id, application_id, etc.)
 * are ignored by the orchestrator but carried through for consumers.
 */
export interface TransportCommand {
  /** Transport-assigned unique ID for this command. */
  id: string;
  /** Registered command name. */
  name: string;
  /** Opaque metadata from the transport (e.g. version, guild_id). */
  meta?: Record<string, unknown>;
}

/**
 * Abstraction over the command registration transport layer.
 *
 * Every transport backend (Discord REST API, mock, stub, etc.)
 * implements this interface so the orchestrator can operate
 * without knowing the concrete transport details.
 */
export interface CommandTransport {
  /**
   * List all commands currently registered via this transport.
   * Used by the orchestrator for idempotency checks.
   */
  listCommands(): Promise<TransportCommand[]>;

  /**
   * Register a single command definition via this transport.
   * Returns the transport-assigned command record (must include `id`).
   */
  createCommand(def: CommandDefinition): Promise<TransportCommand>;

  /**
   * Delete a single command by its transport-assigned ID.
   */
  deleteCommand(commandId: string): Promise<void>;

  /**
   * Bulk-overwrite all commands in one operation.
   *
   * When the transport supports it (e.g. Discord PUT endpoint),
   * this replaces the entire command set atomically. The orchestrator
   * uses this in `bulk` mode.
   *
   * If the transport does not support bulk overwrite, implementations
   * should throw a descriptive error so the orchestrator can fall back
   * to individual registration.
   */
  bulkOverwriteCommands(
    defs: readonly CommandDefinition[],
  ): Promise<TransportCommand[]>;
}

// ---------------------------------------------------------------------------
// Schema provider interface
// ---------------------------------------------------------------------------

/**
 * Abstraction over a command schema source.
 *
 * The orchestrator calls this to discover which commands should be
 * registered. Providers can be static arrays, YAML files, dynamic
 * builders, or any other source.
 */
export interface CommandSchemaProvider {
  /**
   * Return the complete set of command definitions that SHOULD be
   * registered via the transport.
   *
   * May be synchronous or asynchronous. The orchestrator always
   * awaits the result, so async providers work transparently.
   */
  getSchemas(): readonly CommandDefinition[] | Promise<readonly CommandDefinition[]>;
}

// ---------------------------------------------------------------------------
// Orchestrator configuration
// ---------------------------------------------------------------------------

/**
 * Registration mode.
 *
 * - `"individual"` — register commands one-by-one via createCommand.
 *   Best when the transport has rate limits or per-command error
 *   isolation is critical. Slower for large sets.
 *
 * - `"bulk"` — use bulkOverwriteCommands to replace the entire set.
 *   Best for idempotent full-sync at startup. Faster, but validation
 *   failures block the whole batch.
 *
 * Default: `"individual"`.
 */
export type OrchestratorMode = "individual" | "bulk";

/**
 * Idempotency strategy when the orchestrator detects a command
 * is already registered.
 *
 * - `"skip"` — do nothing. The existing registration stays as-is.
 * - `"update"` — delete the existing command and re-create it.
 *   Useful when the definition has changed.
 * - `"force"` — always attempt registration without checking.
 *
 * Default: `"skip"`.
 */
export type IdempotencyStrategy = "skip" | "update" | "force";

/**
 * Configuration for the {@link CommandRegistrationOrchestrator}.
 */
export interface OrchestratorConfig {
  /** Registration mode. Default: `"individual"`. */
  mode?: OrchestratorMode;
  /** Idempotency strategy. Default: `"skip"`. */
  idempotency?: IdempotencyStrategy;
}

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

/**
 * Status of a single command registration attempt.
 */
export type OrchestratorEntryStatus =
  | "registered"  // newly created via transport
  | "updated"     // deleted old and re-created
  | "skipped"     // already registered (idempotency)
  | "failed";     // error during registration

/**
 * Per-command result from an orchestrator run.
 */
export interface OrchestratorEntry {
  /** Command name from the schema. */
  name: string;
  /** Final status after the orchestrator's decision. */
  status: OrchestratorEntryStatus;
  /** Transport-assigned ID (only when status is registered or updated). */
  id?: string;
  /** Error message (only when status is failed). */
  error?: string;
}

/**
 * Aggregate result from a full orchestrator run.
 */
export interface OrchestratorResult {
  /** True when ALL commands were registered or skipped without error. */
  success: boolean;
  /** Per-command entries in provider order. */
  entries: OrchestratorEntry[];
  /** Human-readable summary. */
  summary: string;
}

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

/**
 * Command Registration Orchestrator.
 *
 * Coordinates between a {@link CommandSchemaProvider} and a
 * {@link CommandTransport} to ensure the transport reflects the
 * desired command set.
 *
 * Features:
 * - Idempotency: skip already-registered commands
 * - Batch registration with per-command error isolation
 * - Individual vs bulk mode
 * - Fully testable via injected interfaces
 *
 * @example
 *   const orchestrator = new CommandRegistrationOrchestrator(
 *     provider,   // yields CommandDefinition[]
 *     transport,  // Discord REST API adapter
 *     { mode: "individual", idempotency: "skip" },
 *   );
 *   const result = await orchestrator.run();
 *   console.log(result.summary);
 */
export class CommandRegistrationOrchestrator {
  private readonly provider: CommandSchemaProvider;
  private readonly transport: CommandTransport;
  private readonly config: Required<OrchestratorConfig>;

  constructor(
    provider: CommandSchemaProvider,
    transport: CommandTransport,
    config: OrchestratorConfig = {},
  ) {
    this.provider = provider;
    this.transport = transport;
    this.config = {
      mode: config.mode ?? "individual",
      idempotency: config.idempotency ?? "skip",
    };
  }

  /**
   * Run the full registration cycle.
   *
   * 1. Fetch schemas from the provider.
   * 2. List currently registered commands from the transport.
   * 3. Diff: determine which commands need action.
   * 4. Execute registrations (individual or bulk).
   * 5. Return structured per-command results.
   */
  async run(): Promise<OrchestratorResult> {
    // ── 1. Fetch schemas ─────────────────────────────────────
    const schemas = await this.provider.getSchemas();
    const schemaArray = [...schemas];

    if (schemaArray.length === 0) {
      return {
        success: true,
        entries: [],
        summary: "No command schemas to register.",
      };
    }

    // ── 2. List existing commands ────────────────────────────
    let existing: TransportCommand[];
    try {
      existing = await this.transport.listCommands();
    } catch (err) {
      // If we can't list, we can't safely do idempotency.
      // In "force" mode, try to register anyway.
      if (this.config.idempotency === "force") {
        existing = [];
      } else {
        const entries = schemaArray.map((def) => ({
          name: def.name,
          status: "failed" as const,
          error: `Cannot list existing commands: ${String(err)}`,
        }));
        return {
          success: false,
          entries,
          summary: `Pre-flight list failed: ${String(err)}`,
        };
      }
    }

    const existingByName = new Map<string, TransportCommand>();
    for (const cmd of existing) {
      existingByName.set(cmd.name, cmd);
    }

    // ── 3. Diff ──────────────────────────────────────────────
    const toRegister: CommandDefinition[] = [];
    const toUpdate: Array<{ def: CommandDefinition; oldId: string }> = [];
    const toSkip: Array<{ name: string; id: string }> = [];

    for (const def of schemaArray) {
      const existingCmd = existingByName.get(def.name);

      if (!existingCmd) {
        // Not registered → always register.
        toRegister.push(def);
      } else if (this.config.idempotency === "force") {
        // Force mode → treat as new registration.
        toRegister.push(def);
      } else if (this.config.idempotency === "update") {
        // Update mode → delete old + register new.
        toUpdate.push({ def, oldId: existingCmd.id });
      } else {
        // "skip" mode → already there, skip.
        toSkip.push({ name: def.name, id: existingCmd.id });
      }
    }

    // ── 4. Execute ───────────────────────────────────────────
    if (this.config.mode === "bulk") {
      return this.executeBulk(schemaArray, toSkip);
    }
    return this.executeIndividual(toRegister, toUpdate, toSkip);
  }

  // ── Individual mode ────────────────────────────────────────

  private async executeIndividual(
    toRegister: CommandDefinition[],
    toUpdate: Array<{ def: CommandDefinition; oldId: string }>,
    toSkip: Array<{ name: string; id: string }>,
  ): Promise<OrchestratorResult> {
    const entries: OrchestratorEntry[] = [];

    // Phase 1: skip entries (no transport calls)
    for (const s of toSkip) {
      entries.push({ name: s.name, status: "skipped", id: s.id });
    }

    // Phase 2: update entries (delete old, then create)
    for (const u of toUpdate) {
      try {
        await this.transport.deleteCommand(u.oldId);
      } catch (err) {
        // Deletion failed → record failure and continue
        entries.push({
          name: u.def.name,
          status: "failed",
          error: `Update: delete old command ${u.oldId} failed: ${String(err)}`,
        });
        continue;
      }

      try {
        const created = await this.transport.createCommand(u.def);
        entries.push({
          name: u.def.name,
          status: "updated",
          id: created.id,
        });
      } catch (err) {
        entries.push({
          name: u.def.name,
          status: "failed",
          error: `Update: create after delete failed: ${String(err)}`,
        });
      }
    }

    // Phase 3: register new entries
    for (const def of toRegister) {
      try {
        const created = await this.transport.createCommand(def);
        entries.push({
          name: def.name,
          status: "registered",
          id: created.id,
        });
      } catch (err) {
        entries.push({
          name: def.name,
          status: "failed",
          error: `Register: ${String(err)}`,
        });
      }
    }

    return this.buildResult(entries);
  }

  // ── Bulk mode ──────────────────────────────────────────────

  private async executeBulk(
    allSchemas: readonly CommandDefinition[],
    toSkip: Array<{ name: string; id: string }>,
  ): Promise<OrchestratorResult> {
    // In bulk mode, if every command can be skipped, skip the call.
    if (toSkip.length === allSchemas.length) {
      const entries: OrchestratorEntry[] = toSkip.map((s) => ({
        name: s.name,
        status: "skipped" as const,
        id: s.id,
      }));
      return {
        success: true,
        entries,
        summary: `All ${toSkip.length} command(s) already registered (bulk mode, skipped).`,
      };
    }

    try {
      const created = await this.transport.bulkOverwriteCommands(allSchemas);

      const createdByName = new Map<string, TransportCommand>();
      for (const cmd of created) {
        createdByName.set(cmd.name, cmd);
      }

      const entries: OrchestratorEntry[] = [];
      for (const def of allSchemas) {
        const cmd = createdByName.get(def.name);
        if (cmd) {
          const wasSkipped = toSkip.some((s) => s.name === def.name);
          entries.push({
            name: def.name,
            status: wasSkipped ? "updated" : "registered",
            id: cmd.id,
          });
        } else {
          entries.push({
            name: def.name,
            status: "failed",
            error: "Transport did not return this command after bulk overwrite",
          });
        }
      }

      return this.buildResult(entries);
    } catch (err) {
      // Bulk overwrite failed entirely. Report all as failed.
      const entries: OrchestratorEntry[] = allSchemas.map((def) => ({
        name: def.name,
        status: "failed" as const,
        error: `Bulk overwrite failed: ${String(err)}`,
      }));
      return {
        success: false,
        entries,
        summary: `Bulk overwrite failed: ${String(err)}`,
      };
    }
  }

  // ── Helpers ────────────────────────────────────────────────

  private buildResult(entries: OrchestratorEntry[]): OrchestratorResult {
    const allOk = entries.every(
      (e) => e.status === "registered" || e.status === "updated" || e.status === "skipped",
    );
    const registered = entries.filter((e) => e.status === "registered").length;
    const updated = entries.filter((e) => e.status === "updated").length;
    const skipped = entries.filter((e) => e.status === "skipped").length;
    const failed = entries.filter((e) => e.status === "failed").length;

    const parts: string[] = [];
    if (registered > 0) parts.push(`${registered} registered`);
    if (updated > 0) parts.push(`${updated} updated`);
    if (skipped > 0) parts.push(`${skipped} skipped`);
    if (failed > 0) parts.push(`${failed} failed`);

    return {
      success: allOk,
      entries,
      summary: parts.length > 0 ? parts.join(", ") : "No actions taken.",
    };
  }
}

// ---------------------------------------------------------------------------
// Convenience: pure-function entry point (no class needed)
// ---------------------------------------------------------------------------

/**
 * Run the orchestrator without instantiating the class.
 *
 * Equivalent to `new CommandRegistrationOrchestrator(provider, transport, config).run()`.
 */
export async function orchestrateRegistration(
  provider: CommandSchemaProvider,
  transport: CommandTransport,
  config?: OrchestratorConfig,
): Promise<OrchestratorResult> {
  return new CommandRegistrationOrchestrator(provider, transport, config).run();
}
