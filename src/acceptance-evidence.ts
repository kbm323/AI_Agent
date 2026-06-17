import type { VerificationArtifactEvidence, VerificationOutputDocument } from "./verification-output.ts";

const requiredArtifactEvidenceIds = [
  "diagnosis_report",
  "requirement_gap_mapping",
  "dry_run_final_output",
  "meeting_loop_transcript",
  "token_cost_control",
  "typecheck_check",
  "dry_run_fixture_harness",
  "verification_workflow_runner",
] as const;

type ArtifactEvidenceId = (typeof requiredArtifactEvidenceIds)[number];
type ArtifactEvidenceValues = VerificationArtifactEvidence["evidence"];

export function deriveAcceptanceEvidenceFromArtifactEvidence(
  artifactEvidence: VerificationArtifactEvidence[],
): VerificationOutputDocument["acceptanceEvidence"] {
  const evidenceById = buildValidatedEvidenceIndex(artifactEvidence);
  const diagnosis = evidenceById.diagnosis_report;
  const requirements = evidenceById.requirement_gap_mapping;
  const dryRun = evidenceById.dry_run_final_output;
  const meetingLoop = evidenceById.meeting_loop_transcript;
  const tokenCost = evidenceById.token_cost_control;
  const typecheck = evidenceById.typecheck_check;
  const fixtures = evidenceById.dry_run_fixture_harness;
  const workflowRunner = evidenceById.verification_workflow_runner;

  const acceptanceEvidence = {
    workflowRunnerPassed:
      workflowRunner.status === "passed" &&
      workflowRunner.mvpWorkflowExecuted === true &&
      workflowRunner.escalationWorkflowExecuted === true &&
      workflowRunner.rawStorageSeparatedFromLoopContext === true &&
      typeof workflowRunner.caseCount === "number" &&
      workflowRunner.caseCount >= 2 &&
      workflowRunner.passedCaseCount === workflowRunner.caseCount,
    mvpObservable:
      requirements.implementedCount === 6 &&
      requirements.missingCount === 0 &&
      dryRun.command === "ai-agent dry-run" &&
      dryRun.status === "finalized" &&
      typeof dryRun.meetingTurnCount === "number" &&
      dryRun.meetingTurnCount >= 2 &&
      meetingLoop.transcriptSummaryOnly === true &&
      workflowRunner.mvpWorkflowExecuted === true,
    diagnosisComplete: diagnosis.decision === "partial_redesign" && diagnosis.recommendation === "partial_redesign",
    invalidInputHandled: fixtures.invalidInputFixtureExitCode === 2 && fixtures.invalidInputFixtureError === "invalid_input",
    escalationHandled:
      dryRun.escalationRequired === false &&
      fixtures.escalationFixtureStatus === "waiting_for_user" &&
      workflowRunner.escalationWorkflowExecuted === true,
    tokenStrategyDefined:
      tokenCost.pass === true &&
      typeof tokenCost.percentSavings === "number" &&
      tokenCost.percentSavings >= 40 &&
      typecheck.exitCode === 0,
  };

  for (const [key, value] of Object.entries(acceptanceEvidence)) {
    if (value !== true) {
      throw new Error(`acceptanceEvidence.${key} could not be computed from passing artifact evidence`);
    }
  }

  return acceptanceEvidence;
}

function buildValidatedEvidenceIndex(
  artifactEvidence: VerificationArtifactEvidence[],
): Record<ArtifactEvidenceId, ArtifactEvidenceValues> {
  const evidenceById = new Map<string, ArtifactEvidenceValues>();
  for (const entry of artifactEvidence) {
    if (evidenceById.has(entry.id)) {
      throw new Error(`artifactEvidence.${entry.id} must appear only once`);
    }
    if (entry.requiredFieldsPresent !== true) {
      throw new Error(`artifactEvidence.${entry.id} required fields must be validated before acceptance evidence derivation`);
    }
    evidenceById.set(entry.id, entry.evidence);
  }

  const missing = requiredArtifactEvidenceIds.filter((id) => !evidenceById.has(id));
  if (missing.length > 0) {
    throw new Error(`artifactEvidence missing required evidence ids: ${missing.join(", ")}`);
  }

  return Object.fromEntries(
    requiredArtifactEvidenceIds.map((id) => [id, evidenceById.get(id) as ArtifactEvidenceValues]),
  ) as Record<ArtifactEvidenceId, ArtifactEvidenceValues>;
}
