/**
 * Meeting-loop module — public API surface for the virtual-company
 * multi-agent meeting workflow.
 *
 * Import from `ai-agent/meeting-loop` to wire orchestrators, build
 * reviewer requests, serialize escalation results, and consume
 * meeting-loop types.
 *
 * @example
 * ```ts
 * import {
 *   CompanyOrchestrator,
 *   buildReviewerRequest,
 *   type MeetingLoopDecision,
 *   type RunTaskResult,
 * } from "ai-agent/meeting-loop";
 * ```
 *
 * @module ai-agent/meeting-loop
 */

export {
  CompanyOrchestrator,
  buildReviewerRequest,
  buildThreadName,
  buildEscalationMessage,
  serializeEscalationResult,
  emitEscalationNotification,
  type CompanyOrchestratorDeps,
  type RunTaskResult,
  type MeetingLoopDiscussionResult,
  type MeetingLoopDiscussionStatus,
  type MeetingLoopFinalRouteState,
  type MeetingLoopDecision,
  type MeetingLoopDecisionType,
  type EscalationTriggerType,
  type EscalationSerializationInput,
  type SerializedEscalationResult,
  type EscalationNotificationInput,
  type EscalationNotificationEvent,
} from "./orchestrator.ts";
