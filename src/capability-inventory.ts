import type { ImplementedModuleFeatureSummary } from "./inventory-orchestration.ts";

export const capabilityInventorySchemaVersion = "capability-inventory.v1" as const;

export type CapabilityInventoryStatus = "implemented" | "partial" | "missing";

export interface CapabilityInventoryEvidence {
  implementationModules: string[];
  testFiles: string[];
  runnableEntryPointIds: string[];
  featureTags: string[];
}

export interface CapabilityInventoryEntry {
  id: string;
  name: string;
  description: string;
  status: CapabilityInventoryStatus;
  requiredForMvp: boolean;
  evidence: CapabilityInventoryEvidence;
}

export interface ImplementationCapabilityRecord {
  id: string;
  capabilityId: string;
  name: string;
  requiredForMvp: boolean;
  status: CapabilityInventoryStatus;
  discoverable: boolean;
  tested: boolean;
  evidence: CapabilityInventoryEvidence;
}

export interface CapabilityInventoryArtifact {
  schemaVersion: typeof capabilityInventorySchemaVersion;
  implementationCapabilities: ImplementationCapabilityRecord[];
  capabilities: CapabilityInventoryEntry[];
  summary: {
    capabilityCount: number;
    implementedCount: number;
    partialCount: number;
    missingCount: number;
    requiredMvpCapabilityCount: number;
    requiredMvpImplementedCount: number;
    normalizedPathSeparator: "/";
  };
}

export interface CapabilityInventoryValidationResult {
  valid: boolean;
  missingFields: string[];
  inconsistentFields: string[];
}

interface CapabilityDefinition {
  id: string;
  name: string;
  description: string;
  requiredForMvp: boolean;
  featureTags: string[];
}

const capabilityDefinitions: CapabilityDefinition[] = [
  {
    id: "request-analysis",
    name: "User request analysis",
    description: "Analyze the original request before work decomposition.",
    requiredForMvp: true,
    featureTags: ["request_analysis"],
  },
  {
    id: "task-breakdown",
    name: "Task breakdown",
    description: "Decompose analyzed requests into concrete work items.",
    requiredForMvp: true,
    featureTags: ["request_analysis"],
  },
  {
    id: "role-routing",
    name: "Role based routing",
    description: "Assign work items to job-specific execution and review personas.",
    requiredForMvp: true,
    featureTags: ["role_routing"],
  },
  {
    id: "openclaw-hermes-loop",
    name: "OpenClaw and Hermes meeting loop",
    description: "Preserve execution and review turns for the OpenClaw/Hermes meeting loop.",
    requiredForMvp: true,
    featureTags: ["meeting_loop"],
  },
  {
    id: "final-synthesis",
    name: "Final synthesis",
    description: "Produce a consolidated final result from the meeting history.",
    requiredForMvp: true,
    featureTags: ["final_synthesis"],
  },
  {
    id: "escalation",
    name: "Escalation",
    description: "Serialize user-decision escalation when the meeting cannot resolve a task.",
    requiredForMvp: true,
    featureTags: ["escalation"],
  },
  {
    id: "context-storage-boundary",
    name: "Raw context storage boundary",
    description: "Separate raw full-text persistence from summaries exposed to loop context.",
    requiredForMvp: true,
    featureTags: ["context_storage"],
  },
  {
    id: "compressed-loop-context",
    name: "Compressed loop context",
    description: "Expose compressed meeting summaries to control token cost.",
    requiredForMvp: true,
    featureTags: ["context_compression", "token_cost"],
  },
  {
    id: "evidence-driven-verification",
    name: "Evidence driven verification",
    description: "Compute verification results from concrete artifacts and validation commands.",
    requiredForMvp: false,
    featureTags: ["verification"],
  },
];

export function buildCapabilityInventoryArtifact(
  moduleFeatureSummary: ImplementedModuleFeatureSummary,
): CapabilityInventoryArtifact {
  const implementationCapabilities = extractImplementationCapabilityRecords(moduleFeatureSummary);
  const capabilities = capabilityDefinitions.map((definition) => {
    const record = implementationCapabilities.find((candidate) => candidate.capabilityId === definition.id);
    if (!record) {
      throw new Error(`Missing implementation capability record for ${definition.id}`);
    }
    return buildCapabilityInventoryEntry(definition, record);
  });
  return {
    schemaVersion: capabilityInventorySchemaVersion,
    implementationCapabilities,
    capabilities,
    summary: buildCapabilityInventorySummary(capabilities),
  };
}

export function extractImplementationCapabilityRecords(
  moduleFeatureSummary: ImplementedModuleFeatureSummary,
): ImplementationCapabilityRecord[] {
  return capabilityDefinitions.map((definition) => buildImplementationCapabilityRecord(definition, moduleFeatureSummary));
}

export function validateCapabilityInventoryArtifact(artifact: unknown): CapabilityInventoryValidationResult {
  const missingFields: string[] = [];
  const inconsistentFields: string[] = [];

  if (!isRecord(artifact)) {
    return {
      valid: false,
      missingFields: ["schemaVersion", "capabilities", "summary"],
      inconsistentFields,
    };
  }

  requireLiteral(artifact, "schemaVersion", capabilityInventorySchemaVersion, missingFields);
  requireArray(artifact, "implementationCapabilities", missingFields);
  requireArray(artifact, "capabilities", missingFields);
  requireRecord(artifact, "summary", missingFields);

  if (Array.isArray(artifact.implementationCapabilities)) {
    const ids = new Set<string>();
    for (const record of artifact.implementationCapabilities) {
      if (!isRecord(record)) {
        missingFields.push("implementationCapabilities[]");
        continue;
      }
      for (const field of ["id", "capabilityId", "name", "requiredForMvp", "status", "discoverable", "tested", "evidence"]) {
        if (!(field in record)) {
          missingFields.push(`implementationCapabilities[].${field}`);
        }
      }
      if (typeof record.id === "string") {
        if (ids.has(record.id)) {
          inconsistentFields.push(`implementationCapabilities[].id:${record.id}`);
        }
        ids.add(record.id);
      }
      if (!["implemented", "partial", "missing"].includes(String(record.status))) {
        missingFields.push("implementationCapabilities[].status");
      }
      validateEvidence(record.evidence, "implementationCapabilities[].evidence", missingFields);
      validateImplementationCapabilityRecord(record, inconsistentFields);
    }
  }

  if (Array.isArray(artifact.capabilities)) {
    const ids = new Set<string>();
    for (const capability of artifact.capabilities) {
      if (!isRecord(capability)) {
        missingFields.push("capabilities[]");
        continue;
      }
      for (const field of ["id", "name", "description", "status", "requiredForMvp", "evidence"]) {
        if (!(field in capability)) {
          missingFields.push(`capabilities[].${field}`);
        }
      }
      if (typeof capability.id === "string") {
        if (ids.has(capability.id)) {
          inconsistentFields.push(`capabilities[].id:${capability.id}`);
        }
        ids.add(capability.id);
      }
      if (!["implemented", "partial", "missing"].includes(String(capability.status))) {
        missingFields.push("capabilities[].status");
      }
      validateEvidence(capability.evidence, "capabilities[].evidence", missingFields);
      validateStatusAgainstEvidence(capability, inconsistentFields);
    }
  }

  if (Array.isArray(artifact.implementationCapabilities) && Array.isArray(artifact.capabilities)) {
    validateCapabilitiesDerivedFromRecords(artifact.implementationCapabilities, artifact.capabilities, inconsistentFields);
  }

  if (Array.isArray(artifact.capabilities) && isRecord(artifact.summary)) {
    const capabilities = artifact.capabilities.filter(isCapabilityInventoryEntry);
    const expectedSummary = buildCapabilityInventorySummary(capabilities);
    for (const [field, expected] of Object.entries(expectedSummary)) {
      if (artifact.summary[field] !== expected) {
        inconsistentFields.push(`summary.${field}`);
      }
    }
  }

  return {
    valid: missingFields.length === 0 && inconsistentFields.length === 0,
    missingFields,
    inconsistentFields,
  };
}

function buildCapabilityInventoryEntry(
  definition: CapabilityDefinition,
  record: ImplementationCapabilityRecord,
): CapabilityInventoryEntry {
  return {
    id: definition.id,
    name: definition.name,
    description: definition.description,
    status: record.status,
    requiredForMvp: definition.requiredForMvp,
    evidence: {
      implementationModules: [...record.evidence.implementationModules],
      testFiles: [...record.evidence.testFiles],
      runnableEntryPointIds: [...record.evidence.runnableEntryPointIds],
      featureTags: [...record.evidence.featureTags],
    },
  };
}

function buildImplementationCapabilityRecord(
  definition: CapabilityDefinition,
  moduleFeatureSummary: ImplementedModuleFeatureSummary,
): ImplementationCapabilityRecord {
  const matchingModules = moduleFeatureSummary.modules.filter((moduleEntry) =>
    moduleEntry.featureTags.some((tag) => definition.featureTags.includes(tag)),
  );
  const matchingImplementationModules = matchingModules.filter((moduleEntry) => moduleEntry.kind !== "test");
  const implementationModules = matchingImplementationModules.map((entry) => entry.relativePath).filter(unique).sort(compareStable);
  const testFiles = matchingImplementationModules.flatMap((entry) => entry.coveredByTests).filter(unique).sort(compareStable);
  const runnableEntryPointIds = matchingImplementationModules
    .flatMap((entry) => entry.runnableEntryPointIds)
    .filter(unique)
    .sort(compareStable);
  const featureTags = matchingImplementationModules
    .flatMap((entry) => entry.featureTags)
    .filter((tag) => definition.featureTags.includes(tag))
    .filter(unique)
    .sort(compareStable);
  const status = implementationModules.length > 0 && testFiles.length > 0 ? "implemented" : implementationModules.length > 0 ? "partial" : "missing";

  return {
    id: `implementation:${definition.id}`,
    capabilityId: definition.id,
    name: definition.name,
    requiredForMvp: definition.requiredForMvp,
    status,
    discoverable: implementationModules.length > 0,
    tested: testFiles.length > 0,
    evidence: {
      implementationModules,
      testFiles,
      runnableEntryPointIds,
      featureTags,
    },
  };
}

function buildCapabilityInventorySummary(capabilities: CapabilityInventoryEntry[]): CapabilityInventoryArtifact["summary"] {
  return {
    capabilityCount: capabilities.length,
    implementedCount: capabilities.filter((entry) => entry.status === "implemented").length,
    partialCount: capabilities.filter((entry) => entry.status === "partial").length,
    missingCount: capabilities.filter((entry) => entry.status === "missing").length,
    requiredMvpCapabilityCount: capabilities.filter((entry) => entry.requiredForMvp).length,
    requiredMvpImplementedCount: capabilities.filter((entry) => entry.requiredForMvp && entry.status === "implemented").length,
    normalizedPathSeparator: "/",
  };
}

function validateEvidence(value: unknown, fieldPrefix: string, missingFields: string[]): void {
  if (!isRecord(value)) {
    missingFields.push(fieldPrefix);
    return;
  }
  for (const field of ["implementationModules", "testFiles", "runnableEntryPointIds", "featureTags"]) {
    if (!Array.isArray(value[field])) {
      missingFields.push(`${fieldPrefix}.${field}`);
    }
  }
}

function validateImplementationCapabilityRecord(record: Record<string, unknown>, inconsistentFields: string[]): void {
  if (!isRecord(record.evidence) || typeof record.status !== "string") {
    return;
  }
  const implementationModules = record.evidence.implementationModules;
  const testFiles = record.evidence.testFiles;
  if (!Array.isArray(implementationModules) || !Array.isArray(testFiles)) {
    return;
  }
  if (record.discoverable !== (implementationModules.length > 0)) {
    inconsistentFields.push(`implementationCapabilities[].discoverable:${record.capabilityId ?? "unknown"}`);
  }
  if (record.tested !== (testFiles.length > 0)) {
    inconsistentFields.push(`implementationCapabilities[].tested:${record.capabilityId ?? "unknown"}`);
  }
  validateStatusAgainstEvidence(record, inconsistentFields);
}

function validateCapabilitiesDerivedFromRecords(
  records: unknown[],
  capabilities: unknown[],
  inconsistentFields: string[],
): void {
  const recordsByCapabilityId = new Map(
    records.filter(isImplementationCapabilityRecord).map((record) => [record.capabilityId, record]),
  );
  for (const capability of capabilities.filter(isCapabilityInventoryEntry)) {
    const record = recordsByCapabilityId.get(capability.id);
    if (!record) {
      inconsistentFields.push(`implementationCapabilities.capabilityId:${capability.id}`);
      continue;
    }
    if (record.status !== capability.status) {
      inconsistentFields.push(`capabilities[].status:${capability.id}`);
    }
    if (!sameArray(record.evidence.implementationModules, capability.evidence.implementationModules)) {
      inconsistentFields.push(`capabilities[].evidence.implementationModules:${capability.id}`);
    }
    if (!sameArray(record.evidence.testFiles, capability.evidence.testFiles)) {
      inconsistentFields.push(`capabilities[].evidence.testFiles:${capability.id}`);
    }
    if (!sameArray(record.evidence.runnableEntryPointIds, capability.evidence.runnableEntryPointIds)) {
      inconsistentFields.push(`capabilities[].evidence.runnableEntryPointIds:${capability.id}`);
    }
    if (!sameArray(record.evidence.featureTags, capability.evidence.featureTags)) {
      inconsistentFields.push(`capabilities[].evidence.featureTags:${capability.id}`);
    }
  }
}

function validateStatusAgainstEvidence(capability: Record<string, unknown>, inconsistentFields: string[]): void {
  if (!isRecord(capability.evidence) || typeof capability.status !== "string") {
    return;
  }
  const implementationModules = capability.evidence.implementationModules;
  const testFiles = capability.evidence.testFiles;
  if (!Array.isArray(implementationModules) || !Array.isArray(testFiles)) {
    return;
  }
  if (capability.status === "implemented" && (implementationModules.length === 0 || testFiles.length === 0)) {
    inconsistentFields.push(`capabilities[].status:${capability.id ?? "unknown"}`);
  }
  if (capability.status === "missing" && implementationModules.length > 0) {
    inconsistentFields.push(`capabilities[].status:${capability.id ?? "unknown"}`);
  }
}

function isCapabilityInventoryEntry(value: unknown): value is CapabilityInventoryEntry {
  if (!isRecord(value) || !isRecord(value.evidence)) {
    return false;
  }
  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.description === "string" &&
    ["implemented", "partial", "missing"].includes(String(value.status)) &&
    typeof value.requiredForMvp === "boolean" &&
    Array.isArray(value.evidence.implementationModules) &&
    Array.isArray(value.evidence.testFiles) &&
    Array.isArray(value.evidence.runnableEntryPointIds) &&
    Array.isArray(value.evidence.featureTags)
  );
}

function isImplementationCapabilityRecord(value: unknown): value is ImplementationCapabilityRecord {
  if (!isRecord(value) || !isRecord(value.evidence)) {
    return false;
  }
  return (
    typeof value.id === "string" &&
    typeof value.capabilityId === "string" &&
    typeof value.name === "string" &&
    ["implemented", "partial", "missing"].includes(String(value.status)) &&
    typeof value.requiredForMvp === "boolean" &&
    typeof value.discoverable === "boolean" &&
    typeof value.tested === "boolean" &&
    Array.isArray(value.evidence.implementationModules) &&
    Array.isArray(value.evidence.testFiles) &&
    Array.isArray(value.evidence.runnableEntryPointIds) &&
    Array.isArray(value.evidence.featureTags)
  );
}

function sameArray(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function requireLiteral(record: Record<string, unknown>, field: string, expected: string, missingFields: string[]): void {
  if (record[field] !== expected) {
    missingFields.push(field);
  }
}

function requireArray(record: Record<string, unknown>, field: string, missingFields: string[]): void {
  if (!Array.isArray(record[field])) {
    missingFields.push(field);
  }
}

function requireRecord(record: Record<string, unknown>, field: string, missingFields: string[]): void {
  if (!isRecord(record[field])) {
    missingFields.push(field);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function compareStable(left: string, right: string): number {
  return left.localeCompare(right, "en");
}

function unique<T>(value: T, index: number, values: T[]): boolean {
  return values.indexOf(value) === index;
}
