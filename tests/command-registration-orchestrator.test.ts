/**
 * Tests for Command Registration Orchestrator (Sub-AC 1a-iii).
 *
 * Covers:
 *  - Mock schema provider (empty, single, multiple, async)
 *  - Mock transport (empty, pre-populated, failures)
 *  - Idempotency strategies: skip, update, force
 *  - Individual vs bulk mode
 *  - Per-command error isolation
 *  - Transport list failure handling
 *  - Transport create failure handling
 *  - Transport delete failure handling
 *  - Transport bulkOverwrite failure handling
 *  - orchestrateRegistration convenience function
 *  - Edge cases: empty schemas, all already registered
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  // Class
  CommandRegistrationOrchestrator,
  // Convenience function
  orchestrateRegistration,
  // Interfaces (imported for type use)
  type CommandSchemaProvider,
  type CommandTransport,
  type TransportCommand,
  type OrchestratorConfig,
  type OrchestratorResult,
  type OrchestratorEntry,
} from "../src/command-registration-orchestrator.ts";
import type { CommandDefinition } from "../src/command-schema-validator.ts";

// ═══════════════════════════════════════════════════════════════════════
// Test helpers
// ═══════════════════════════════════════════════════════════════════════

/** Build a minimal valid CommandDefinition for testing. */
function cmdDef(name: string, description = `Command: ${name}`): CommandDefinition {
  return { name, description, options: [] };
}

/** Build an array of command definitions. */
function cmdDefs(...names: string[]): CommandDefinition[] {
  return names.map((n) => cmdDef(n));
}

/**
 * Create a mock schema provider that returns the given definitions.
 * If `delayMs` is provided, the provider is async and resolves after
 * that delay.
 */
function mockProvider(
  defs: CommandDefinition[],
  delayMs?: number,
): CommandSchemaProvider {
  if (delayMs !== undefined) {
    return {
      getSchemas: () =>
        new Promise<readonly CommandDefinition[]>((resolve) => {
          setTimeout(() => resolve(defs), delayMs);
        }),
    };
  }
  return { getSchemas: () => defs };
}

/**
 * Create a mock transport with configurable state.
 *
 * - `existing`: pre-populated list of already-registered commands
 * - `failList`: if true, listCommands() throws
 * - `failCreate`: if true, createCommand() throws
 * - `failDelete`: if true, deleteCommand() throws
 * - `failBulk`: if true, bulkOverwriteCommands() throws
 * - `idCounter`: auto-incrementing ID counter for createCommand
 */
function mockTransport(opts: {
  existing?: TransportCommand[];
  failList?: boolean | string;
  failCreate?: boolean | string;
  failDelete?: boolean | string;
  failBulk?: boolean | string;
} = {}): CommandTransport & { idCounter: number } {
  const state = {
    idCounter: 0,
    existing: opts.existing ?? [],
  };

  return {
    idCounter: 0, // shared reference — see below fix

    async listCommands() {
      if (opts.failList) {
        throw new Error(
          typeof opts.failList === "string" ? opts.failList : "List failed",
        );
      }
      return [...state.existing];
    },

    async createCommand(def: CommandDefinition) {
      if (opts.failCreate) {
        throw new Error(
          typeof opts.failCreate === "string" ? opts.failCreate : "Create failed",
        );
      }
      state.idCounter++;
      return { id: `cmd_${state.idCounter}`, name: def.name };
    },

    async deleteCommand(_commandId: string) {
      if (opts.failDelete) {
        throw new Error(
          typeof opts.failDelete === "string" ? opts.failDelete : "Delete failed",
        );
      }
      // Remove from existing list to simulate successful deletion.
      const idx = state.existing.findIndex((c) => c.id === _commandId);
      if (idx !== -1) state.existing.splice(idx, 1);
    },

    async bulkOverwriteCommands(defs: readonly CommandDefinition[]) {
      if (opts.failBulk) {
        throw new Error(
          typeof opts.failBulk === "string" ? opts.failBulk : "Bulk overwrite failed",
        );
      }
      return defs.map((d, i) => ({ id: `bulk_${i + 1}`, name: d.name }));
    },
  };
}

/** Helper to check OrchestratorResult.success and give a useful diff. */
function assertSuccess(result: OrchestratorResult, msg?: string): void {
  if (!result.success) {
    const failures = result.entries
      .filter((e) => e.status === "failed")
      .map((e) => `  ${e.name}: ${e.error}`)
      .join("\n");
    assert.fail(`${msg ?? "Expected success"} but got failures:\n${failures}\nSummary: ${result.summary}`);
  }
  assert.equal(result.success, true, msg);
}

/** Helper to assert result is failure. */
function assertFailure(result: OrchestratorResult, msg?: string): void {
  assert.equal(result.success, false, msg);
}

/** Find an entry by name. */
function findEntry(result: OrchestratorResult, name: string): OrchestratorEntry {
  const entry = result.entries.find((e) => e.name === name);
  assert.ok(entry, `Entry "${name}" not found in result. Entries: ${result.entries.map((e) => e.name).join(", ")}`);
  return entry!;
}

// ═══════════════════════════════════════════════════════════════════════
// 1. Empty / edge cases
// ═══════════════════════════════════════════════════════════════════════

test("orchestrator returns empty result when provider yields no schemas", async () => {
  const provider = mockProvider([]);
  const transport = mockTransport({ existing: [{ id: "x", name: "orphan" }] });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport);

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 0);
  assert.ok(result.summary.includes("No command schemas"));
});

test("orchestrator registers single command when transport is empty (individual, skip)", async () => {
  const provider = mockProvider(cmdDefs("meeting"));
  const transport = mockTransport();
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 1);
  assert.equal(findEntry(result, "meeting").status, "registered");
  assert.ok(findEntry(result, "meeting").id);
});

test("orchestrator handles async provider correctly", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"), 10);
  const transport = mockTransport();
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport);

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "registered");
  assert.equal(findEntry(result, "cancel").status, "registered");
});

// ═══════════════════════════════════════════════════════════════════════
// 2. Idempotency: skip mode
// ═══════════════════════════════════════════════════════════════════════

test("skip mode: skips already-registered commands (individual)", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel", "status"));
  const transport = mockTransport({
    existing: [{ id: "existing_1", name: "meeting" }],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 3);

  assert.equal(findEntry(result, "meeting").status, "skipped");
  assert.equal(findEntry(result, "meeting").id, "existing_1");

  assert.equal(findEntry(result, "cancel").status, "registered");
  assert.ok(findEntry(result, "cancel").id);

  assert.equal(findEntry(result, "status").status, "registered");
  assert.ok(findEntry(result, "status").id);
});

test("skip mode: skips all when all are already registered", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({
    existing: [
      { id: "a", name: "meeting" },
      { id: "b", name: "cancel" },
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "skipped");
  assert.equal(findEntry(result, "cancel").status, "skipped");
  assert.ok(result.summary.includes("skipped"));
});

test("skip mode: skips only matching names, registers new ones even when transport has extras", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({
    existing: [
      { id: "a", name: "meeting" },
      { id: "b", name: "legacy-cmd" }, // not in schema
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "skipped");
  assert.equal(findEntry(result, "cancel").status, "registered");
});

// ═══════════════════════════════════════════════════════════════════════
// 3. Idempotency: update mode
// ═══════════════════════════════════════════════════════════════════════

test("update mode: deletes old and re-creates already-registered commands", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({
    existing: [
      { id: "old_a", name: "meeting" },
      { id: "old_b", name: "cancel" },
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "update",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "updated");
  assert.equal(findEntry(result, "cancel").status, "updated");

  // IDs should be new (not "old_a"/"old_b")
  const meetingEntry = findEntry(result, "meeting");
  assert.ok(meetingEntry.id!.startsWith("cmd_"));
  const cancelEntry = findEntry(result, "cancel");
  assert.ok(cancelEntry.id!.startsWith("cmd_"));
});

test("update mode: registers new commands normally, updates existing ones", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel", "status"));
  const transport = mockTransport({
    existing: [{ id: "old_1", name: "meeting" }],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "update",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 3);
  assert.equal(findEntry(result, "meeting").status, "updated");
  assert.equal(findEntry(result, "cancel").status, "registered");
  assert.equal(findEntry(result, "status").status, "registered");
});

// ═══════════════════════════════════════════════════════════════════════
// 4. Idempotency: force mode
// ═══════════════════════════════════════════════════════════════════════

test("force mode: registers all commands even when already present", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({
    existing: [
      { id: "a", name: "meeting" },
      { id: "b", name: "cancel" },
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "force",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 2);
  // In force mode, they are treated as new registrations.
  assert.equal(findEntry(result, "meeting").status, "registered");
  assert.equal(findEntry(result, "cancel").status, "registered");
});

test("force mode: still attempts registration even when transport list fails", async () => {
  const provider = mockProvider(cmdDefs("meeting"));
  const transport = mockTransport({ failList: true });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "force",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(findEntry(result, "meeting").status, "registered");
});

// ═══════════════════════════════════════════════════════════════════════
// 5. Per-command error isolation (individual mode)
// ═══════════════════════════════════════════════════════════════════════

test("individual mode: isolates create failures — one failure does not block others", async () => {
  // failCreate as a function would be ideal, but string is simpler.
  // We simulate by registering 3 commands where the 2nd will fail.
  // Since our mock uses a single failCreate flag, we need a smarter mock.
  let callCount = 0;
  const provider = mockProvider(cmdDefs("meeting", "bad-cmd", "status"));
  const transport: CommandTransport = {
    async listCommands() { return []; },
    async createCommand(def: CommandDefinition) {
      callCount++;
      if (def.name === "bad-cmd") throw new Error("Bad command rejected");
      return { id: `cmd_${callCount}`, name: def.name };
    },
    async deleteCommand(_id: string) {},
    async bulkOverwriteCommands(_defs) { return []; },
  };
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
  });

  const result = await orchestrator.run();
  assertFailure(result);
  assert.equal(result.entries.length, 3);

  // meeting: registered
  assert.equal(findEntry(result, "meeting").status, "registered");
  // bad-cmd: failed
  const bad = findEntry(result, "bad-cmd");
  assert.equal(bad.status, "failed");
  assert.ok(bad.error!.includes("Bad command rejected"));
  // status: still registered (error isolation)
  assert.equal(findEntry(result, "status").status, "registered");
});

test("individual mode: survives delete-failure in update mode and continues", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport: CommandTransport = {
    async listCommands() {
      return [{ id: "old_m", name: "meeting" }, { id: "old_c", name: "cancel" }];
    },
    async createCommand(def: CommandDefinition) {
      return { id: `new_${def.name}`, name: def.name };
    },
    async deleteCommand(id: string) {
      if (id === "old_m") throw new Error("Delete blocked");
    },
    async bulkOverwriteCommands(_defs) { return []; },
  };
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "update",
  });

  const result = await orchestrator.run();
  assertFailure(result);

  const m = findEntry(result, "meeting");
  assert.equal(m.status, "failed");
  assert.ok(m.error!.includes("Delete blocked"));

  // cancel should still succeed
  assert.equal(findEntry(result, "cancel").status, "updated");
});

// ═══════════════════════════════════════════════════════════════════════
// 6. Bulk mode
// ═══════════════════════════════════════════════════════════════════════

test("bulk mode: uses bulkOverwriteCommands for all schemas", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel", "status"));
  const transport = mockTransport();
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "bulk",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 3);
  assert.equal(findEntry(result, "meeting").status, "registered");
  assert.equal(findEntry(result, "cancel").status, "registered");
  assert.equal(findEntry(result, "status").status, "registered");
});

test("bulk mode: skips transport call when all commands already registered (skip idempotency)", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  let bulkCalled = false;
  const transport: CommandTransport = {
    async listCommands() {
      return [
        { id: "a", name: "meeting" },
        { id: "b", name: "cancel" },
      ];
    },
    async createCommand(_def: CommandDefinition) {
      return { id: "x", name: _def.name };
    },
    async deleteCommand(_id: string) {},
    async bulkOverwriteCommands(_defs) {
      bulkCalled = true;
      return [{ id: "y", name: "meeting" }];
    },
  };
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "bulk",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(bulkCalled, false, "bulkOverwrite should not be called when all are skipped");
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "skipped");
  assert.equal(findEntry(result, "cancel").status, "skipped");
});

test("bulk mode: reports all as failed when bulkOverwrite throws", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({ failBulk: "Bulk endpoint down" });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "bulk",
  });

  const result = await orchestrator.run();
  assertFailure(result);
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "failed");
  assert.equal(findEntry(result, "cancel").status, "failed");
  assert.ok(result.summary.includes("Bulk endpoint down"));
});

// ═══════════════════════════════════════════════════════════════════════
// 7. Pre-flight list failure
// ═══════════════════════════════════════════════════════════════════════

test("skip/idempotency: fails gracefully when transport list throws (non-force)", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({ failList: "Network error" });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertFailure(result);
  // Both should be failed because we couldn't determine idempotency.
  assert.equal(result.entries.length, 2);
  assert.equal(findEntry(result, "meeting").status, "failed");
  assert.equal(findEntry(result, "cancel").status, "failed");
});

test("update/idempotency: fails gracefully when transport list throws (non-force)", async () => {
  const provider = mockProvider(cmdDefs("meeting"));
  const transport = mockTransport({ failList: "Auth error" });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    idempotency: "update",
  });

  const result = await orchestrator.run();
  assertFailure(result);
  assert.equal(result.entries.length, 1);
  assert.equal(findEntry(result, "meeting").status, "failed");
});

// ═══════════════════════════════════════════════════════════════════════
// 8. orchestrateRegistration convenience function
// ═══════════════════════════════════════════════════════════════════════

test("orchestrateRegistration convenience function works equivalently to class", async () => {
  const provider = mockProvider(cmdDefs("meeting"));
  const transport = mockTransport();

  const result = await orchestrateRegistration(provider, transport);
  assertSuccess(result);
  assert.equal(result.entries.length, 1);
  assert.equal(findEntry(result, "meeting").status, "registered");
});

test("orchestrateRegistration accepts config", async () => {
  const provider = mockProvider(cmdDefs("meeting", "cancel"));
  const transport = mockTransport({
    existing: [{ id: "old", name: "meeting" }],
  });

  const result = await orchestrateRegistration(provider, transport, {
    mode: "individual",
    idempotency: "update",
  });
  assertSuccess(result);
  assert.equal(findEntry(result, "meeting").status, "updated");
  assert.equal(findEntry(result, "cancel").status, "registered");
});

// ═══════════════════════════════════════════════════════════════════════
// 9. Summary string validation
// ═══════════════════════════════════════════════════════════════════════

test("summary correctly reports counts for mixed registration result", async () => {
  const provider = mockProvider(cmdDefs("a", "b", "c", "d"));
  const transport = mockTransport({
    existing: [{ id: "x", name: "a" }],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  // a → skipped, b,c,d → registered
  assert.ok(result.summary.includes("3 registered"));
  assert.ok(result.summary.includes("1 skipped"));
});

test("summary correctly reports updates", async () => {
  const provider = mockProvider(cmdDefs("a", "b"));
  const transport = mockTransport({
    existing: [
      { id: "x", name: "a" },
      { id: "y", name: "b" },
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "update",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.ok(result.summary.includes("2 updated"));
});

test("summary correctly reports failures", async () => {
  const provider = mockProvider(cmdDefs("meeting"));
  const transport = mockTransport({ failList: true });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport);

  const result = await orchestrator.run();
  assertFailure(result);
  assert.ok(result.summary.includes("Pre-flight list failed"));
});

// ═══════════════════════════════════════════════════════════════════════
// 10. Integration pattern: orchestrator over full command set
// ═══════════════════════════════════════════════════════════════════════

test("orchestrator handles full company command set with mixed transport state", async () => {
  const companyDefs = cmdDefs(
    "meeting",
    "cancel",
    "status",
    "knowledge",
    "diagnosis",
    "review",
  );
  const provider = mockProvider(companyDefs);
  const transport = mockTransport({
    existing: [
      { id: "g1", name: "meeting" },
      { id: "g2", name: "status" },
      // cancel, knowledge, diagnosis, review not registered
    ],
  });
  const orchestrator = new CommandRegistrationOrchestrator(provider, transport, {
    mode: "individual",
    idempotency: "skip",
  });

  const result = await orchestrator.run();
  assertSuccess(result);
  assert.equal(result.entries.length, 6);

  // Skipped
  assert.equal(findEntry(result, "meeting").status, "skipped");
  assert.equal(findEntry(result, "status").status, "skipped");

  // Registered
  for (const name of ["cancel", "knowledge", "diagnosis", "review"]) {
    assert.equal(findEntry(result, name).status, "registered");
  }

  const registeredCount = result.entries.filter((e) => e.status === "registered").length;
  const skippedCount = result.entries.filter((e) => e.status === "skipped").length;
  assert.equal(registeredCount, 4);
  assert.equal(skippedCount, 2);
});
