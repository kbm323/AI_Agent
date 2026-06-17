"""Shared infrastructure module for the AI_Agent multi-agent meeting system.

This package provides Python implementations of core infrastructure concerns:
- context_compression: Context compression, summarization, and loop context policy
- token_budget: Token counting, baseline measurement, and savings estimation
- utilities: Text processing, fingerprinting, and helper functions
- config: Configuration management for the shared infrastructure
- lifecycle: Meeting lifecycle state enum and helper functions
"""

from .config import (
    CompressionConfig,
    SharedConfig,
    TokenBudgetConfig,
    default_config,
    load_config,
)
from .context_compression import (
    CompressedLoopContext,
    CompressedLoopContextArtifact,
    LoopCompressionFieldPolicy,
    LoopCompressionIterationBoundary,
    LoopContextCompressionPolicy,
    MeetingTurnSummary,
    build_compressed_loop_context,
    build_loop_context_compression_policy,
    compact_prompt_context,
    summarize_meeting_turn,
)
from .lifecycle import (
    ACTIVE_STATES,
    ALL_LIFECYCLE_STATES,
    MEETING_TRANSITIONS,
    STATE_TRANSITION_MATRIX,
    StateTransitionMatrix,
    TERMINAL_STATES,
    LifecycleState,
    is_active,
    is_terminal,
    validate_transition,
)
from .lifecycle_progression import (
    HAPPY_PATH_STATES,
    NORMAL_LIFECYCLE_STEPS,
    LifecycleProgressionResult,
    NormalLifecycleStep,
    guard_agenda_non_empty,
    guard_agenda_type_set,
    guard_consensus_non_empty,
    guard_context_populated,
    guard_manifest_path_valid,
    guard_meeting_has_id,
    guard_not_already_terminal,
    guard_roles_assigned,
    guard_rounds_completed,
    guard_validation_passed,
    is_happy_path_state,
    progress_lifecycle,
    progress_one_step,
)
from .token_budget import (
    TokenAccountingResult,
    TokenBaselineInput,
    TokenBaselineMeasurement,
    TokenReductionSavingsMeasurement,
    TokenReductionTargetRange,
    TokenSavingsEstimate,
    account_token_reduction,
    estimate_token_count,
    measure_token_baseline,
    measure_token_reduction_savings,
    reduction_percent,
)
from .utilities import (
    contains_exact_text,
    fingerprint_text,
    format_list,
    format_message,
    resolve_token_value,
    summarize_for_thread,
    summarize_list,
    validate_token,
)

__all__ = [
    # config
    "SharedConfig",
    "CompressionConfig",
    "TokenBudgetConfig",
    "default_config",
    "load_config",
    # context_compression
    "MeetingTurnSummary",
    "CompressedLoopContext",
    "CompressedLoopContextArtifact",
    "LoopCompressionFieldPolicy",
    "LoopCompressionIterationBoundary",
    "LoopContextCompressionPolicy",
    "summarize_meeting_turn",
    "build_compressed_loop_context",
    "compact_prompt_context",
    "build_loop_context_compression_policy",
    # lifecycle
    "LifecycleState",
    "ALL_LIFECYCLE_STATES",
    "ACTIVE_STATES",
    "TERMINAL_STATES",
    "MEETING_TRANSITIONS",
    "StateTransitionMatrix",
    "STATE_TRANSITION_MATRIX",
    "is_active",
    "is_terminal",
    "validate_transition",
    # lifecycle_progression (Sub-AC 4.2)
    "HAPPY_PATH_STATES",
    "NORMAL_LIFECYCLE_STEPS",
    "LifecycleProgressionResult",
    "NormalLifecycleStep",
    "guard_agenda_non_empty",
    "guard_agenda_type_set",
    "guard_consensus_non_empty",
    "guard_context_populated",
    "guard_manifest_path_valid",
    "guard_meeting_has_id",
    "guard_not_already_terminal",
    "guard_roles_assigned",
    "guard_rounds_completed",
    "guard_validation_passed",
    "is_happy_path_state",
    "progress_lifecycle",
    "progress_one_step",
    # token_budget
    "estimate_token_count",
    "TokenBaselineInput",
    "TokenBaselineMeasurement",
    "measure_token_baseline",
    "reduction_percent",
    "TokenSavingsEstimate",
    "TokenAccountingResult",
    "account_token_reduction",
    "TokenReductionTargetRange",
    "TokenReductionSavingsMeasurement",
    "measure_token_reduction_savings",
    # utilities
    "summarize_for_thread",
    "contains_exact_text",
    "fingerprint_text",
    "format_list",
    "format_message",
    "resolve_token_value",
    "summarize_list",
    "validate_token",
]
