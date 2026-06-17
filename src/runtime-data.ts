import { randomUUID } from "node:crypto";

const isoTimestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

export const normalizedRuntimeTimestamp = "<runtime-timestamp>";
export const normalizedExecutionId = "<execution-id>";
export const normalizedRequestId = "<request-id>";
export const normalizedThreadId = "<thread-id>";
export const normalizedInputIdentifier = "<input-identifier>";

const runtimeIdentifierPatterns: Array<{ pattern: RegExp; replacement: string }> = [
  { pattern: /^run:[a-f0-9]{16,64}$/i, replacement: normalizedExecutionId },
  { pattern: /^request:[a-f0-9]{16,64}$/i, replacement: normalizedRequestId },
];

const runtimeIdentifierKeyReplacements: Record<string, string> = {
  executionId: normalizedExecutionId,
  inputIdentifier: normalizedInputIdentifier,
  threadId: normalizedThreadId,
};

export function createRuntimeTimestamp(now?: string | Date): string {
  const timestamp = now instanceof Date ? now.toISOString() : (now ?? new Date().toISOString());
  assertRuntimeTimestamp(timestamp);
  return timestamp;
}

export function createRuntimeIdentifier(idFactory: () => string = randomUUID): string {
  const id = idFactory();
  if (typeof id !== "string" || id.trim().length === 0) {
    throw new TypeError("runtime identifier factory must return a non-empty string");
  }
  return id;
}

export function normalizeRuntimeTimestamp(value: string): string {
  return isRuntimeTimestamp(value) ? normalizedRuntimeTimestamp : value;
}

export function normalizeRuntimeIdentifier(value: string): string {
  for (const { pattern, replacement } of runtimeIdentifierPatterns) {
    if (pattern.test(value)) {
      return replacement;
    }
  }
  return value;
}

export function normalizeRuntimeIdentifierField(key: string, value: unknown): unknown {
  if (key in runtimeIdentifierKeyReplacements) {
    return runtimeIdentifierKeyReplacements[key];
  }
  return typeof value === "string" ? normalizeRuntimeIdentifier(value) : value;
}

export function isRuntimeIdentifierField(key: string): boolean {
  return key in runtimeIdentifierKeyReplacements;
}

export function isRuntimeTimestamp(value: string): boolean {
  if (!isoTimestampPattern.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString() === value;
}

function assertRuntimeTimestamp(value: string): void {
  if (!isRuntimeTimestamp(value)) {
    throw new TypeError(`runtime timestamp must be an ISO-8601 UTC timestamp with millisecond precision: ${value}`);
  }
}
