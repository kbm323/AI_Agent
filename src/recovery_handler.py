"""Recovery handler for exhausted retry sequences (Sub-AC 4.3.4).

When retries are exhausted (``RetryResult.max_retries_exceeded == True``),
this module executes one of three recovery actions:

- **REQUEUE** — Reset the attempt counter and re-attempt the operation,
  optionally with a different ``RetryConfig`` (e.g. longer timeout,
  different backoff strategy).

- **ESCALATE** — Route the failure to a higher authority: human-in-the-loop
  approval, Codex GPT-5.5 secondary validator, or a supervisor agent.

- **DEAD_LETTER** — Move the failed operation to a dead-letter queue for
  later inspection, audit, or manual replay.  The failed state is
  preserved immutably.

The recovery **action** selection is a pure, testable function
(:func:`classify_for_recovery`).  The action **execution** is delegated to
injectable handlers (``RecoveryActionHandler`` callables) so that every
recovery path — requeue, escalate, dead-letter — can be unit-tested with
mock handlers that assert they were called with the expected payload.

Architecture
------------

::

    ExhaustedRetryState  ──►  classify_for_recovery()  ──►  RecoveryAction
                                        │
                                        ▼
                              execute_recovery()
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                      requeue       escalate     dead_letter
                      handler       handler       handler

Usage::

    from src.recovery_handler import (
        ExhaustedRetryState,
        RecoveryAction,
        RecoveryResult,
        classify_for_recovery,
        execute_recovery,
    )
    from src.retry_executor import RetryResult, classify_error

    # Simulate an exhausted retry state
    exhausted = ExhaustedRetryState(
        retry_result=some_failed_result,
        operation_name="worker-call",
        meeting_id="m-abc123",
        error_category="model",
        max_retries=3,
        fallback_attempted=True,
        quorum_reassessed=True,
    )

    # Decide what to do
    action = classify_for_recovery(exhausted)
    # action == RecoveryAction.ESCALATE

    # Execute with injectable handlers (mocks for testing)
    result = execute_recovery(
        exhausted,
        on_escalate=lambda state: print(f"Escalating {state.operation_name}"),
    )

    print(result.action, result.success)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from src.retry_executor import (
    ERROR_CATEGORY_MODEL,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_PERMANENT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_TRANSIENT,
    ERROR_CATEGORY_UNKNOWN,
    ERROR_CATEGORY_VALIDATION,
    RetryResult,
)

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Recovery action enumeration ───────────────────────────────────────────


class RecoveryAction(str, Enum):
    """Recovery action to take when retries are exhausted.

    Values:
        REQUEUE: Reset and re-attempt (fresh retry cycle, possibly different config).
        ESCALATE: Route to higher authority (human, Codex, supervisor).
        DEAD_LETTER: Move to dead-letter queue for audit/manual replay.
    """

    REQUEUE = "requeue"
    ESCALATE = "escalate"
    DEAD_LETTER = "dead_letter"


# ── Input: exhausted retry state ──────────────────────────────────────────


@dataclass(frozen=True)
class ExhaustedRetryState:
    """Immutable snapshot of an exhausted retry sequence.

    This is the input to the recovery handler.  It bundles the
    ``RetryResult`` with the operational context needed to decide — and
    then execute — the appropriate recovery action.

    Attributes:
        retry_result: The ``RetryResult`` describing every attempt and the
                      final failure.
        operation_name: Human-readable label for the failed operation
                        (e.g. ``"worker-call"``, ``"validation-run"``,
                        ``"opencode-cli-invoke"``).
        meeting_id: Meeting identifier when the operation is meeting-scoped;
                    ``None`` for system-level operations.
        error_category: Classified error category from
                        :func:`~src.retry_executor.classify_error`.
        max_retries: The ``max_retries`` value that was configured for the
                     exhausted retry cycle.
        fallback_attempted: ``True`` if a fallback model/provider was
                            already attempted and also failed.
        quorum_reassessed: ``True`` if quorum reassessment already occurred
                           (e.g. remaining required roles checked).
        metadata: Arbitrary key-value pairs for logging, auditing, and
                  downstream routing (e.g. ``{"role_id": "strategist"}``).
    """

    retry_result: RetryResult[Any]
    operation_name: str
    error_category: str
    max_retries: int
    meeting_id: Optional[str] = None
    fallback_attempted: bool = False
    quorum_reassessed: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate that the state genuinely represents exhausted retries
        if not self.retry_result.max_retries_exceeded:
            logger.warning(
                "ExhaustedRetryState created but max_retries_exceeded=False "
                "for operation %r — recovery may be premature",
                self.operation_name,
            )
        known_categories = {
            ERROR_CATEGORY_TRANSIENT,
            ERROR_CATEGORY_PERMANENT,
            ERROR_CATEGORY_TIMEOUT,
            ERROR_CATEGORY_MODEL,
            ERROR_CATEGORY_NETWORK,
            ERROR_CATEGORY_VALIDATION,
            ERROR_CATEGORY_UNKNOWN,
        }
        if self.error_category not in known_categories:
            logger.warning(
                "Unknown error_category=%r for operation %r — "
                "will be treated as UNKNOWN",
                self.error_category,
                self.operation_name,
            )

    @property
    def final_error_message(self) -> str:
        """Human-readable version of the final error."""
        if self.retry_result.final_error is not None:
            return str(self.retry_result.final_error)
        return "unknown error"

    @property
    def total_attempts(self) -> int:
        """Total attempts made before exhaustion."""
        return self.retry_result.total_attempts


# ── Output: recovery result ───────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryResult:
    """Immutable result of a recovery action execution.

    Attributes:
        action: The recovery action that was taken.
        success: ``True`` if the recovery action completed without error.
        message: Human-readable summary of what happened.
        details: Structured key-value data for audit/logging
                 (e.g. requeue config, escalation target, dead-letter path).
        error: Exception raised during recovery execution, or ``None``
               if recovery succeeded.
    """

    action: RecoveryAction
    success: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[Exception] = None

    @property
    def is_requeue(self) -> bool:
        return self.action == RecoveryAction.REQUEUE

    @property
    def is_escalate(self) -> bool:
        return self.action == RecoveryAction.ESCALATE

    @property
    def is_dead_letter(self) -> bool:
        return self.action == RecoveryAction.DEAD_LETTER

    def as_log_dict(self) -> dict[str, Any]:
        """Convert to a log-friendly dictionary for manifest error_log."""
        entry: dict[str, Any] = {
            "recovery_action": self.action.value,
            "recovery_success": self.success,
            "recovery_message": self.message,
        }
        if self.details:
            entry["recovery_details"] = self.details
        if self.error is not None:
            entry["recovery_error"] = str(self.error)
        return entry


# ── Recovery action handler type alias ────────────────────────────────────

RecoveryActionHandler = Callable[[ExhaustedRetryState], RecoveryResult]
"""A handler that executes a specific recovery action.

Receives the exhausted retry state and must return a ``RecoveryResult``.
For testing, replace with a mock that asserts the handler was called
with the expected state.

Example mock::

    def mock_escalate(state: ExhaustedRetryState) -> RecoveryResult:
        assert state.operation_name == "worker-call"
        return RecoveryResult(
            action=RecoveryAction.ESCALATE,
            success=True,
            message="Escalated to Codex GPT-5.5",
        )
"""


# ── Action classification (pure, testable) ────────────────────────────────


def classify_for_recovery(state: ExhaustedRetryState) -> RecoveryAction:
    """Decide which recovery action to take given an exhausted retry state.

    This is a **pure function** — no I/O, no side effects.  The decision
    logic reflects the Seed's failure-handling sequence:

    *retry(1×) → fallback(1×) → quorum reassessment → escalation/fail*

    And the Seed exit condition:

    *Worker failures follow retry(1×)→fallback(1×)→quorum
    reassessment→escalation/fail sequence*

    Decision matrix:

    ==================== ===================== =========== ==============
    Error Category       Fallback Attempted    Quorum      Action
    ==================== ===================== =========== ==============
    transient/network     No                    —           REQUEUE
    transient/network     Yes                   Yes         ESCALATE
    timeout               No                    —           REQUEUE
    timeout               Yes                   —           ESCALATE
    model                 No                    —           REQUEUE
    model                 Yes                   —           ESCALATE
    validation            —                     —           ESCALATE
    permanent             —                     —           DEAD_LETTER
    unknown               No                    —           REQUEUE
    unknown               Yes                   —           ESCALATE
    ==================== ===================== =========== ==============

    Args:
        state: The exhausted retry state to classify.

    Returns:
        The recommended recovery action.

    Examples:
        *Transient error, no fallback yet → requeue:*

        >>> from src.retry_executor import (
        ...     RetryResult, RetryAttempt, RetryConfig, ERROR_CATEGORY_TRANSIENT,
        ... )
        >>> cfg = RetryConfig(max_retries=3)
        >>> rr = RetryResult(
        ...     success=False, attempts=(), max_retries_exceeded=True,
        ...     final_error=ConnectionError("timeout"), result=None, config=cfg,
        ... )
        >>> state = ExhaustedRetryState(
        ...     retry_result=rr, operation_name="test",
        ...     error_category=ERROR_CATEGORY_TRANSIENT, max_retries=3,
        ...     fallback_attempted=False,
        ... )
        >>> classify_for_recovery(state)
        <RecoveryAction.REQUEUE: 'requeue'>

        *Permanent error → dead-letter:*

        >>> pf = RetryResult(
        ...     success=False, attempts=(), max_retries_exceeded=True,
        ...     final_error=FileNotFoundError("config missing"),
        ...     result=None, config=cfg,
        ... )
        >>> pf_state = ExhaustedRetryState(
        ...     retry_result=pf, operation_name="read-config",
        ...     error_category=ERROR_CATEGORY_PERMANENT, max_retries=3,
        ... )
        >>> classify_for_recovery(pf_state)
        <RecoveryAction.DEAD_LETTER: 'dead_letter'>
    """
    category = state.error_category

    # ── Permanent errors → dead-letter, always ─────────────────────────
    if category == ERROR_CATEGORY_PERMANENT:
        return RecoveryAction.DEAD_LETTER

    # ── Validation errors → escalate (can't self-heal) ─────────────────
    if category == ERROR_CATEGORY_VALIDATION:
        return RecoveryAction.ESCALATE

    # ── Transient / timeout / network — requeue if fallback not yet ────
    if category in (ERROR_CATEGORY_TRANSIENT, ERROR_CATEGORY_NETWORK):
        if not state.fallback_attempted:
            return RecoveryAction.REQUEUE
        # Both retry and fallback exhausted → escalate
        return RecoveryAction.ESCALATE

    # ── Timeout — requeue with longer timeout if no fallback yet ───────
    if category == ERROR_CATEGORY_TIMEOUT:
        if not state.fallback_attempted:
            return RecoveryAction.REQUEUE
        return RecoveryAction.ESCALATE

    # ── Model errors — requeue if fallback model not yet tried ─────────
    if category == ERROR_CATEGORY_MODEL:
        if not state.fallback_attempted:
            return RecoveryAction.REQUEUE
        return RecoveryAction.ESCALATE

    # ── Unknown — requeue once, then escalate ──────────────────────────
    if category == ERROR_CATEGORY_UNKNOWN:
        if not state.fallback_attempted:
            return RecoveryAction.REQUEUE
        return RecoveryAction.ESCALATE

    # ── Should not reach here (exhaustive check) ───────────────────────
    logger.warning(
        "Unhandled error_category=%r for operation %r — defaulting to ESCALATE",
        category,
        state.operation_name,
    )
    return RecoveryAction.ESCALATE


# ── Default (production) recovery action handlers ─────────────────────────


def default_requeue_handler(state: ExhaustedRetryState) -> RecoveryResult:
    """Production handler for the REQUEUE action.

    Logs a structured message and returns a successful result.  In a full
    implementation this would re-enqueue the operation in the meeting's
    work queue with a fresh ``RetryConfig`` (e.g. longer timeout, different
    backoff).

    For testing, replace this with a mock via ``execute_recovery``.
    """
    msg = (
        f"REQUEUE: operation={state.operation_name} "
        f"meeting_id={state.meeting_id or 'N/A'} "
        f"error={state.final_error_message[:120]} "
        f"fallback_attempted={state.fallback_attempted} "
        f"quorum_reassessed={state.quorum_reassessed}"
    )
    logger.info(msg)
    return RecoveryResult(
        action=RecoveryAction.REQUEUE,
        success=True,
        message=msg,
        details={
            "operation_name": state.operation_name,
            "meeting_id": state.meeting_id,
            "previous_max_retries": state.max_retries,
            "suggested_new_max_retries": min(state.max_retries * 2, 10),
            "fallback_attempted": state.fallback_attempted,
        },
    )


def default_escalate_handler(state: ExhaustedRetryState) -> RecoveryResult:
    """Production handler for the ESCALATE action.

    Determines the escalation target based on the error category and
    meeting context, then logs a structured escalation message.

    Escalation targets (per Seed design):
    - model errors → Codex GPT-5.5 secondary validator
    - validation errors → Codex GPT-5.5 or human review
    - transient/network after fallback → supervisor agent or human
    - timeout after fallback → supervisor agent
    - unknown after fallback → human-in-the-loop
    """
    category = state.error_category

    if category in (ERROR_CATEGORY_MODEL, ERROR_CATEGORY_VALIDATION):
        target = "codex-gpt-5.5"
    elif category in (ERROR_CATEGORY_TRANSIENT, ERROR_CATEGORY_NETWORK, ERROR_CATEGORY_TIMEOUT):
        target = "supervisor-agent"
    else:
        target = "human-in-the-loop"

    msg = (
        f"ESCALATE[{target}]: operation={state.operation_name} "
        f"meeting_id={state.meeting_id or 'N/A'} "
        f"category={category} "
        f"error={state.final_error_message[:120]}"
    )
    logger.warning(msg)
    return RecoveryResult(
        action=RecoveryAction.ESCALATE,
        success=True,
        message=msg,
        details={
            "operation_name": state.operation_name,
            "meeting_id": state.meeting_id,
            "error_category": category,
            "escalation_target": target,
            "fallback_attempted": state.fallback_attempted,
            "quorum_reassessed": state.quorum_reassessed,
        },
    )


def default_dead_letter_handler(state: ExhaustedRetryState) -> RecoveryResult:
    """Production handler for the DEAD_LETTER action.

    Logs the failure and returns a result indicating the operation was
    moved to the dead-letter queue.  In a full implementation this would
    write the operation state to a dead-letter storage location for later
    inspection and manual replay.

    Per the Seed constraint "Append-only immutable records", the dead-letter
    entry must preserve the full failure state without modification.
    """
    msg = (
        f"DEAD_LETTER: operation={state.operation_name} "
        f"meeting_id={state.meeting_id or 'N/A'} "
        f"category={state.error_category} "
        f"error={state.final_error_message[:120]}"
    )
    logger.error(msg)
    return RecoveryResult(
        action=RecoveryAction.DEAD_LETTER,
        success=True,
        message=msg,
        details={
            "operation_name": state.operation_name,
            "meeting_id": state.meeting_id,
            "error_category": state.error_category,
            "final_error": state.final_error_message,
            "total_attempts": state.total_attempts,
            "immutable": True,
        },
    )


# ── Main entry point ──────────────────────────────────────────────────────


def execute_recovery(
    state: ExhaustedRetryState,
    *,
    on_requeue: RecoveryActionHandler = default_requeue_handler,
    on_escalate: RecoveryActionHandler = default_escalate_handler,
    on_dead_letter: RecoveryActionHandler = default_dead_letter_handler,
) -> RecoveryResult:
    """Classify the exhausted retry state and execute the appropriate recovery action.

    This is the primary entry point for Sub-AC 4.3.4.  It:

    1. Calls :func:`classify_for_recovery` to determine the recovery action.
    2. Dispatches to the corresponding injectable handler.
    3. Returns the ``RecoveryResult``.

    All three handlers (``on_requeue``, ``on_escalate``, ``on_dead_letter``)
    are injectable so that every recovery path can be tested with mock
    handlers that assert they were invoked with the expected payload.

    Args:
        state: The exhausted retry state to recover from.
        on_requeue: Handler for ``REQUEUE`` action (default: production).
        on_escalate: Handler for ``ESCALATE`` action (default: production).
        on_dead_letter: Handler for ``DEAD_LETTER`` action (default: production).

    Returns:
        A ``RecoveryResult`` describing the action taken and its outcome.

    Raises:
        Nothing directly — handler exceptions are caught and returned as
        failed ``RecoveryResult`` objects so the caller can log and continue.

    Examples:
        *Test with a mock requeue handler:*

        >>> from src.retry_executor import (
        ...     RetryResult, RetryConfig, ERROR_CATEGORY_TRANSIENT,
        ... )
        >>> cfg = RetryConfig(max_retries=3)
        >>> rr = RetryResult(
        ...     success=False, attempts=(), max_retries_exceeded=True,
        ...     final_error=ConnectionError(), result=None, config=cfg,
        ... )
        >>> state = ExhaustedRetryState(
        ...     retry_result=rr, operation_name="worker-1",
        ...     error_category=ERROR_CATEGORY_TRANSIENT, max_retries=3,
        ...     fallback_attempted=False,
        ... )
        >>> mock_called = []
        >>> def mock_requeue(s):
        ...     mock_called.append(s.operation_name)
        ...     return RecoveryResult(
        ...         action=RecoveryAction.REQUEUE, success=True,
        ...         message="Mock requeue",
        ...     )
        >>> result = execute_recovery(state, on_requeue=mock_requeue)
        >>> result.action.value
        'requeue'
        >>> result.success
        True
        >>> mock_called
        ['worker-1']
    """
    # ── Step 1: Classify ───────────────────────────────────────────────
    action = classify_for_recovery(state)
    logger.debug(
        "Recovery classified as %s for operation=%r meeting_id=%s",
        action.value,
        state.operation_name,
        state.meeting_id or "N/A",
    )

    # ── Step 2: Dispatch ───────────────────────────────────────────────
    handler_map: dict[RecoveryAction, RecoveryActionHandler] = {
        RecoveryAction.REQUEUE: on_requeue,
        RecoveryAction.ESCALATE: on_escalate,
        RecoveryAction.DEAD_LETTER: on_dead_letter,
    }
    handler = handler_map.get(action)
    if handler is None:
        # Defensive — should never happen with exhaustive enum dispatch
        msg = f"No handler registered for recovery action: {action}"
        logger.critical(msg)
        return RecoveryResult(
            action=action,
            success=False,
            message=msg,
            error=ValueError(msg),
        )

    try:
        result = handler(state)
    except Exception as exc:
        logger.exception(
            "Recovery handler for action=%s raised %s: %s",
            action.value,
            type(exc).__name__,
            exc,
        )
        return RecoveryResult(
            action=action,
            success=False,
            message=f"Recovery handler '{action.value}' raised {type(exc).__name__}: {exc}",
            error=exc,
        )

    # Guard against handlers that mistakenly return None
    if result is None:
        logger.error(
            "Recovery handler for action=%s returned None — "
            "treating as failed recovery",
            action.value,
        )
        return RecoveryResult(
            action=action,
            success=False,
            message=(
                f"Recovery handler '{action.value}' returned None "
                f"instead of a RecoveryResult"
            ),
            error=ValueError("Handler returned None"),
        )

    # ── Step 3: Log the outcome ────────────────────────────────────────
    if result.success:
        logger.info(
            "Recovery %s succeeded for operation=%r meeting_id=%s",
            action.value,
            state.operation_name,
            state.meeting_id or "N/A",
        )
    else:
        logger.error(
            "Recovery %s FAILED for operation=%r meeting_id=%s: %s",
            action.value,
            state.operation_name,
            state.meeting_id or "N/A",
            result.message,
        )

    return result


# ── Convenience: build ExhaustedRetryState from a RetryResult ─────────────


def exhausted_state_from_result(
    retry_result: RetryResult[Any],
    operation_name: str,
    *,
    meeting_id: Optional[str] = None,
    fallback_attempted: bool = False,
    quorum_reassessed: bool = False,
    metadata: Optional[dict[str, str]] = None,
) -> ExhaustedRetryState:
    """Build an ``ExhaustedRetryState`` directly from a ``RetryResult``.

    Convenience constructor that derives ``error_category`` and
    ``max_retries`` from the result, reducing boilerplate at call sites.

    Args:
        retry_result: The exhausted retry result.
        operation_name: Label for the failed operation.
        meeting_id: Optional meeting identifier.
        fallback_attempted: Whether a fallback was already tried.
        quorum_reassessed: Whether quorum reassessment occurred.
        metadata: Optional key-value metadata.

    Returns:
        A new ``ExhaustedRetryState``.

    Raises:
        ValueError: If ``retry_result.max_retries_exceeded`` is ``False``
                    (the result does not represent an exhausted state).

    Examples:
        >>> from src.retry_executor import (
        ...     RetryResult, RetryConfig, classify_error,
        ... )
        >>> cfg = RetryConfig(max_retries=3)
        >>> rr = RetryResult(
        ...     success=False, attempts=(), max_retries_exceeded=True,
        ...     final_error=TimeoutError("call timed out"),
        ...     result=None, config=cfg,
        ... )
        >>> state = exhausted_state_from_result(
        ...     rr, "worker-call", meeting_id="m-test",
        ... )
        >>> state.error_category
        'timeout'
        >>> state.max_retries
        3
    """
    if not retry_result.max_retries_exceeded:
        raise ValueError(
            "Cannot build ExhaustedRetryState from a RetryResult where "
            "max_retries_exceeded=False — the result does not represent "
            "an exhausted state"
        )

    from src.retry_executor import classify_error

    error_category = classify_error(retry_result.final_error)

    return ExhaustedRetryState(
        retry_result=retry_result,
        operation_name=operation_name,
        error_category=error_category,
        max_retries=retry_result.config.max_retries,
        meeting_id=meeting_id,
        fallback_attempted=fallback_attempted,
        quorum_reassessed=quorum_reassessed,
        metadata=metadata or {},
    )
