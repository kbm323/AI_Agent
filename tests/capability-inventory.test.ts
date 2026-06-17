import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCapabilityInventoryArtifact,
  capabilityInventorySchemaVersion,
  extractImplementationCapabilityRecords,
  validateCapabilityInventoryArtifact,
} from "../src/capability-inventory.ts";
import type { ImplementedModuleFeatureSummary } from "../src/inventory-orchestration.ts";

test("capability inventory builds structured MVP capability evidence", () => {
  const artifact = buildCapabilityInventoryArtifact(featureSummaryFixture);

  assert.equal(artifact.schemaVersion, capabilityInventorySchemaVersion);
  assert.equal(artifact.implementationCapabilities.length, 9);
  assert.equal(artifact.capabilities.length, 9);
  assert.deepEqual(artifact.summary, {
    capabilityCount: 9,
    implementedCount: 4,
    partialCount: 2,
    missingCount: 3,
    requiredMvpCapabilityCount: 8,
    requiredMvpImplementedCount: 4,
    normalizedPathSeparator: "/",
  });

  assert.deepEqual(artifact.capabilities.find((entry) => entry.id === "role-routing"), {
    id: "role-routing",
    name: "Role based routing",
    description: "Assign work items to job-specific execution and review personas.",
    status: "implemented",
    requiredForMvp: true,
    evidence: {
      implementationModules: ["src/planning.ts"],
      testFiles: ["tests/planning.test.ts"],
      runnableEntryPointIds: [],
      featureTags: ["role_routing"],
    },
  });

  const validation = validateCapabilityInventoryArtifact(artifact);
  assert.deepEqual(validation, {
    valid: true,
    missingFields: [],
    inconsistentFields: [],
  });
});

test("implementation capability records expose discoverable and absent capabilities", () => {
  const records = extractImplementationCapabilityRecords(featureSummaryFixture);
  const requestAnalysis = records.find((entry) => entry.capabilityId === "request-analysis");
  const compressedLoopContext = records.find((entry) => entry.capabilityId === "compressed-loop-context");

  assert.deepEqual(requestAnalysis, {
    id: "implementation:request-analysis",
    capabilityId: "request-analysis",
    name: "User request analysis",
    requiredForMvp: true,
    status: "implemented",
    discoverable: true,
    tested: true,
    evidence: {
      implementationModules: ["src/planning.ts"],
      testFiles: ["tests/planning.test.ts"],
      runnableEntryPointIds: [],
      featureTags: ["request_analysis"],
    },
  });
  assert.deepEqual(compressedLoopContext, {
    id: "implementation:compressed-loop-context",
    capabilityId: "compressed-loop-context",
    name: "Compressed loop context",
    requiredForMvp: true,
    status: "missing",
    discoverable: false,
    tested: false,
    evidence: {
      implementationModules: [],
      testFiles: [],
      runnableEntryPointIds: [],
      featureTags: [],
    },
  });
});

test("capability inventory validation rejects malformed evidence and stale summary counts", () => {
  const artifact = buildCapabilityInventoryArtifact(featureSummaryFixture);
  const malformed = {
    ...artifact,
    capabilities: artifact.capabilities.map((capability) =>
      capability.id === "role-routing"
        ? {
            ...capability,
            status: "implemented",
            evidence: {
              ...capability.evidence,
              testFiles: [],
            },
          }
        : capability,
    ),
    summary: {
      ...artifact.summary,
      implementedCount: artifact.summary.implementedCount + 1,
    },
  };

  const validation = validateCapabilityInventoryArtifact(malformed);

  assert.equal(validation.valid, false);
  assert.deepEqual(validation.missingFields, []);
  assert.equal(validation.inconsistentFields.includes("capabilities[].status:role-routing"), true);
  assert.equal(validation.inconsistentFields.includes("summary.implementedCount"), true);
});

test("capability inventory validation rejects missing required schema fields", () => {
  const validation = validateCapabilityInventoryArtifact({
    schemaVersion: "capability-inventory.v1",
    capabilities: [
      {
        id: "request-analysis",
        name: "User request analysis",
        description: "Analyze the original request before work decomposition.",
        status: "partial",
        requiredForMvp: true,
        evidence: {
          implementationModules: ["src/planning.ts"],
          featureTags: ["request_analysis"],
        },
      },
    ],
    summary: {
      capabilityCount: 1,
      implementedCount: 0,
      partialCount: 1,
      missingCount: 0,
      requiredMvpCapabilityCount: 1,
      requiredMvpImplementedCount: 0,
      normalizedPathSeparator: "/",
    },
  });

  assert.equal(validation.valid, false);
  assert.equal(validation.missingFields.includes("capabilities[].evidence.testFiles"), true);
  assert.equal(validation.missingFields.includes("capabilities[].evidence.runnableEntryPointIds"), true);
});

const featureSummaryFixture: ImplementedModuleFeatureSummary = {
  schemaVersion: "implemented-module-features.v1",
  modules: [
    {
      id: "existing:src/planning.ts",
      relativePath: "src/planning.ts",
      moduleName: "src.planning",
      kind: "source",
      exportedSymbols: ["analyzeUserRequest", "buildRoleRoutes", "decomposeUserRequest"],
      localDependencies: [],
      coveredByTests: ["tests/planning.test.ts"],
      runnableEntryPointIds: [],
      featureTags: ["request_analysis", "role_routing"],
    },
    {
      id: "existing:src/orchestrator.ts",
      relativePath: "src/orchestrator.ts",
      moduleName: "src.orchestrator",
      kind: "source",
      exportedSymbols: ["CompanyOrchestrator"],
      localDependencies: ["src/planning.ts"],
      coveredByTests: [],
      runnableEntryPointIds: [],
      featureTags: ["meeting_loop", "escalation"],
    },
    {
      id: "existing:src/final-synthesis.ts",
      relativePath: "src/final-synthesis.ts",
      moduleName: "src.final-synthesis",
      kind: "source",
      exportedSymbols: ["generateFinalSynthesisFromMeetingLoopArtifact"],
      localDependencies: [],
      coveredByTests: ["tests/final-synthesis.test.ts"],
      runnableEntryPointIds: [],
      featureTags: ["final_synthesis"],
    },
  ],
  summary: {
    moduleCount: 3,
    modulesWithExports: 3,
    modulesWithTestCoverage: 2,
    runnableModuleCount: 0,
    featureTags: ["escalation", "final_synthesis", "meeting_loop", "request_analysis", "role_routing"],
    normalizedPathSeparator: "/",
  },
};
