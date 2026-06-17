import {
  isRuntimeIdentifierField,
  normalizeRuntimeIdentifier,
  normalizeRuntimeIdentifierField,
  normalizeRuntimeTimestamp,
} from "./runtime-data.ts";

export interface CapturedCommandResponse {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export interface NormalizedCapturedResponse {
  exitCode: number;
  stdout: NormalizedCapturedStream;
  stderr: NormalizedCapturedStream;
}

export type NormalizedCapturedStream =
  | {
      parseableJson: true;
      json: unknown;
    }
  | {
      parseableJson: false;
      text: string;
    };

const volatileStringPatterns: Array<{ pattern: RegExp; replacement: string }> = [
  { pattern: /^https?:\/\/discord\.test\/[^/\s]+$/i, replacement: "<thread-url>" },
  { pattern: /^[^:\n]*ai-agent-[A-Za-z0-9_.-]+[^:\n]*\/prior-review\.json$/, replacement: "<prior-review-artifact-path>" },
];

const volatileKeyReplacements: Record<string, string> = {
  path: "<path>",
  artifactPath: "<path>",
  priorReviewArtifactPath: "<prior-review-artifact-path>",
  dryRunCommand: "<dry-run-command>",
};

export function normalizeCapturedResponse(response: CapturedCommandResponse): NormalizedCapturedResponse {
  return {
    exitCode: response.exitCode,
    stdout: normalizeCapturedStream(response.stdout),
    stderr: normalizeCapturedStream(response.stderr),
  };
}

export function normalizeCapturedStream(value: string): NormalizedCapturedStream {
  const canonicalText = value.replace(/\r\n/g, "\n").trimEnd();
  if (canonicalText.length === 0) {
    return { parseableJson: false, text: "" };
  }

  try {
    return {
      parseableJson: true,
      json: normalizeJsonValue(JSON.parse(canonicalText)),
    };
  } catch {
    return {
      parseableJson: false,
      text: normalizeVolatileString(canonicalText),
    };
  }
}

export function normalizeJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return normalizeJsonArrayOrder(value.map((item) => normalizeJsonValue(item)));
  }
  if (!isRecord(value)) {
    return typeof value === "string" ? normalizeVolatileString(value) : value;
  }

  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, normalizeJsonRecordField(key, item)]),
  );
}

export function formatStableJsonForComparison(value: unknown): string {
  return `${JSON.stringify(normalizeJsonValue(value), null, 2)}\n`;
}

export function normalizeJsonArrayOrder(values: unknown[]): unknown[] {
  if (values.every(hasExplicitSequenceOrder)) {
    return values;
  }
  if (!values.every(hasStableUnorderedArrayKey)) {
    return values;
  }

  return [...values].sort((left, right) => stableJsonOrderKey(left).localeCompare(stableJsonOrderKey(right), "en"));
}

function normalizeJsonRecordField(key: string, value: unknown): unknown {
  if (key === "version" && typeof value === "string") {
    return "<runtime-version>";
  }
  if (isRuntimeIdentifierField(key)) {
    return normalizeRuntimeIdentifierField(key, value);
  }
  if (key in volatileKeyReplacements) {
    return volatileKeyReplacements[key];
  }
  return normalizeJsonValue(value);
}

function normalizeVolatileString(value: string): string {
  for (const { pattern, replacement } of volatileStringPatterns) {
    if (pattern.test(value)) {
      return replacement;
    }
  }
  return normalizeRuntimeTimestamp(normalizeRuntimeIdentifier(value)).replace(
    /\/tmp\/ai-agent-[A-Za-z0-9_.-]+\/prior-review\.json/g,
    "<prior-review-artifact-path>",
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype;
}

function hasExplicitSequenceOrder(value: unknown): boolean {
  return isRecord(value) && typeof value.order === "number";
}

function hasStableUnorderedArrayKey(value: unknown): boolean {
  return isRecord(value) && (typeof value.id === "string" || typeof value.relativePath === "string");
}

function stableJsonOrderKey(value: unknown): string {
  if (isRecord(value) && typeof value.id === "string") {
    return `0:${value.id}`;
  }
  if (isRecord(value) && typeof value.relativePath === "string") {
    return `1:${value.relativePath}`;
  }
  return `2:${JSON.stringify(value)}`;
}
