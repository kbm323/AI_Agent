"""Retry executor for opencode-go CLI worker calls and tool-use operations.

Provides configurable retry with attempt tracking, delay strategies,
and error classification.  Designed to satisfy Sub-AC 4.3.1:

- Attempt an operation, catch errors, re-queue or re-attempt up to
  configurable ``max_retries`` limit.
- Unit-testable retry count tracking and limit enforcement.

Usage::

    from src.retry_executor import execute_with_retry, RetryConfig, RetryResult

    config = RetryConfig(max_retries=3, retry_delay_seconds=1.0)
    result = execute_with_retry(
        lambda x: x / 0,  # operation that fails
        42,
        config=config,
    )
    assert not result.success
    assert result.attempts[-1].attempt_number == 3  # final attempt
    assert result.max_retries_exceeded is True
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Backoff strategy enumeration (Sub-AC 4.3.2)
# ---------------------------------------------------------------------------


class BackoffStrategy(str, Enum):
    """Configurable backoff strategy for inter-retry delay calculation.

    Each strategy defines how the delay between retry attempts is computed.

    Values:
        FIXED: Constant delay regardless of attempt number.
        EXPONENTIAL: Delay grows exponentially: base * factor^(attempt-1).
        JITTERED: Delay equals base + random jitter, bounded by jitter_seconds.
        EXPONENTIAL_JITTER: Exponential growth plus random jitter on top.

    Examples:
        >>> BackoffStrategy.FIXED
        <BackoffStrategy.FIXED: 'fixed'>
        >>> BackoffStrategy.EXPONENTIAL.value
        'exponential'
    """

    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    JITTERED = "jittered"
    EXPONENTIAL_JITTER = "exponential_jitter"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for the retry executor.

    Attributes:
        max_retries: Maximum number of total attempts (1 initial + N retries).
                     Must be >= 1.
        retry_delay_seconds: Base delay between retry attempts in seconds.
        retry_on_exceptions: Exception types that trigger a retry.
        retry_on_exit_codes: Exit codes that trigger a retry (for subprocess
                             operations).  A non-zero exit code always counts
                             as retryable when this is empty.
        backoff_factor: Multiplicative backoff factor for exponential strategy.
                        ``1.0`` means constant delay,
                        ``2.0`` means exponential doubling.
        jitter_seconds: Maximum random jitter to add to delay (0 = none).
        strategy: Backoff strategy to use for delay computation.
                  See :class:`BackoffStrategy` for options.
                  Default: ``BackoffStrategy.FIXED``.
        timeout_seconds: Per-attempt timeout.  0 means no timeout.
    """

    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    retry_on_exceptions: tuple[type[Exception], ...] = (Exception,)
    retry_on_exit_codes: tuple[int, ...] = ()
    backoff_factor: float = 1.0
    jitter_seconds: float = 0.0
    strategy: BackoffStrategy = BackoffStrategy.FIXED
    timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_retries < 1:
            msg = f"max_retries must be >= 1, got {self.max_retries}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryAttempt:
    """A single attempt within a retry sequence.

    Attributes:
        attempt_number: 1-indexed attempt count.
        error: The exception caught, or ``None`` if the attempt succeeded.
        start_time: Monotonic timestamp when the attempt started.
        end_time: Monotonic timestamp when the attempt completed.
    """

    attempt_number: int
    error: Exception | None
    start_time: float
    end_time: float

    @property
    def elapsed_seconds(self) -> float:
        return self.end_time - self.start_time


@dataclass(frozen=True)
class RetryResult(Generic[T]):
    """Outcome of a retry execution.

    Attributes:
        success: ``True`` if the operation eventually succeeded.
        attempts: Complete list of every attempt made.
        total_attempts: Convenience alias for ``len(attempts)``.
        max_retries_exceeded: ``True`` when the limit was hit and no
                              attempt succeeded.
        final_error: The last exception if the operation failed,
                     or ``None`` on success.
        result: The operation's return value on success, ``None`` on failure.
        config: The ``RetryConfig`` used for this execution (for introspection).
    """

    success: bool
    attempts: tuple[RetryAttempt, ...]
    max_retries_exceeded: bool
    final_error: Exception | None
    result: T | None
    config: RetryConfig

    @property
    def total_attempts(self) -> int:
        return len(self.attempts)

    @property
    def first_error(self) -> Exception | None:
        """The first error encountered, useful for root-cause analysis."""
        for a in self.attempts:
            if a.error is not None:
                return a.error
        return None

    @property
    def last_attempt_number(self) -> int:
        if not self.attempts:
            return 0
        return self.attempts[-1].attempt_number


# ---------------------------------------------------------------------------
# Error classification helpers (unit-testable)
# ---------------------------------------------------------------------------

#: Error categories that can inform whether a retry is appropriate.
ERROR_CATEGORY_TRANSIENT = "transient"
ERROR_CATEGORY_PERMANENT = "permanent"
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_MODEL = "model"
ERROR_CATEGORY_NETWORK = "network"
ERROR_CATEGORY_VALIDATION = "validation"
ERROR_CATEGORY_UNKNOWN = "unknown"


def classify_error(error: Exception | None) -> str:
    """Classify an exception into one of the known error categories.

    Returns one of the ``ERROR_CATEGORY_*`` constants.

    Args:
        error: The exception to classify, or ``None``.

    Returns:
        A category string (see ``ERROR_CATEGORY_*`` module constants).

    Examples:
        >>> classify_error(TimeoutError())
        'timeout'
        >>> classify_error(ValueError("invalid JSON"))
        'validation'
        >>> classify_error(Exception("generic"))
        'unknown'
    """
    if error is None:
        return ERROR_CATEGORY_UNKNOWN

    # Network / connection errors
    if isinstance(error, (ConnectionError, ConnectionRefusedError, ConnectionResetError)):
        return ERROR_CATEGORY_NETWORK

    # Timeout errors
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ERROR_CATEGORY_TIMEOUT

    error_message = str(error).lower()

    # Permanent failure types — checked BEFORE transient OSError catch-all
    if isinstance(error, (FileNotFoundError, PermissionError, FileExistsError,
                          NotADirectoryError, IsADirectoryError)):
        return ERROR_CATEGORY_PERMANENT

    # Permanent failures — keyword-based
    permanent_patterns = (
        "not found", "no such file", "permission denied",
        "syntax error", "invalid argument", "unsupported",
    )
    if any(p in error_message for p in permanent_patterns):
        return ERROR_CATEGORY_PERMANENT

    # Validation / schema errors
    if isinstance(error, (ValueError, TypeError, KeyError, IndexError)):
        if any(kw in error_message for kw in ("json", "schema", "parse", "validation", "field", "decode")):
            return ERROR_CATEGORY_VALIDATION

    # Model / provider errors — look for keywords in message
    if any(
        kw in error_message
        for kw in ("rate limit", "api key", "authentication", "model", "provider",
                    "429", "401", "403", "503", "overloaded", "capacity")
    ):
        return ERROR_CATEGORY_MODEL

    # Transient / retryable by nature
    transient_types = (
        TimeoutError,
        ConnectionError,
        ConnectionRefusedError,
        ConnectionResetError,
        OSError,
    )
    if isinstance(error, transient_types):
        return ERROR_CATEGORY_TRANSIENT

    return ERROR_CATEGORY_UNKNOWN


def should_retry(
    error: Exception,
    config: RetryConfig,
    attempt_number: int,
) -> bool:
    """Determine whether a retry should be attempted for the given error.

    Considers the error classification, configured exception types,
    and attempt number against the max_retries limit.

    Args:
        error: The exception that occurred.
        config: The retry configuration.
        attempt_number: The number of the failed attempt (1-indexed).

    Returns:
        ``True`` if a retry should be attempted.
    """
    # Check attempt limit
    if attempt_number >= config.max_retries:
        return False

    # Check if exception type is retryable
    if not isinstance(error, config.retry_on_exceptions):
        return False

    # Permanent errors should NOT be retried regardless of config
    if classify_error(error) == ERROR_CATEGORY_PERMANENT:
        return False

    return True


# ---------------------------------------------------------------------------
# Core retry executor
# ---------------------------------------------------------------------------


def execute_with_retry(
    operation: Callable[..., T],
    *args: Any,
    config: RetryConfig | None = None,
    on_attempt: Callable[[RetryAttempt], None] | None = None,
    on_retry: Callable[[RetryAttempt], None] | None = None,
    **kwargs: Any,
) -> RetryResult[T]:
    """Execute *operation* with configurable retry semantics.

    Calls *operation(*args, **kwargs)* repeatedly up to
    ``config.max_retries`` times.  If an exception matching
    ``config.retry_on_exceptions`` is raised and the attempt limit has
    not been reached, waits ``config.retry_delay_seconds`` (with
    optional backoff and jitter) before retrying.

    Args:
        operation: The callable to execute.
        *args: Positional arguments forwarded to *operation*.
        config: ``RetryConfig`` instance.  Uses defaults when ``None``.
        on_attempt: Called after **every** attempt (success or failure)
                    with a ``RetryAttempt`` describing the outcome.
        on_retry: Called only when a retry is scheduled (i.e. an error
                  occurred and a subsequent attempt will be made).
        **kwargs: Keyword arguments forwarded to *operation*.

    Returns:
        A ``RetryResult`` describing the full execution history.

    Examples:
        >>> config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        >>> attempt_count = 0
        >>> def fail_twice() -> str:
        ...     global attempt_count
        ...     attempt_count += 1
        ...     if attempt_count < 3:
        ...         raise ValueError("temporary error")
        ...     return "success on 3rd try"
        >>> result = execute_with_retry(fail_twice, config=config)
        >>> result.success
        True
        >>> result.total_attempts
        3
        >>> result.result
        'success on 3rd try'
    """
    cfg = config if config is not None else RetryConfig()

    attempts: list[RetryAttempt] = []
    last_error: Exception | None = None

    for attempt_num in range(1, cfg.max_retries + 1):
        start = time.monotonic()

        try:
            # --- Execute the operation ---
            output: T = operation(*args, **kwargs)
            end = time.monotonic()

            attempt = RetryAttempt(
                attempt_number=attempt_num,
                error=None,
                start_time=start,
                end_time=end,
            )
            attempts.append(attempt)

            if on_attempt is not None:
                on_attempt(attempt)

            return RetryResult(
                success=True,
                attempts=tuple(attempts),
                max_retries_exceeded=False,
                final_error=None,
                result=output,
                config=cfg,
            )

        except Exception as exc:  # noqa: BLE001
            end = time.monotonic()

            attempt = RetryAttempt(
                attempt_number=attempt_num,
                error=exc,
                start_time=start,
                end_time=end,
            )
            attempts.append(attempt)
            last_error = exc

            if on_attempt is not None:
                on_attempt(attempt)

            # Decide whether to retry
            if not should_retry(exc, cfg, attempt_num):
                # Limit reached or non-retryable error — stop here
                break

            # --- Schedule retry ---
            if on_retry is not None:
                on_retry(attempt)

            delay = _compute_delay(cfg, attempt_num)
            if delay > 0:
                time.sleep(delay)

    # --- All attempts exhausted ---
    return RetryResult(
        success=False,
        attempts=tuple(attempts),
        max_retries_exceeded=len(attempts) >= cfg.max_retries and last_error is not None,
        final_error=last_error,
        result=None,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Backoff delay computation (Sub-AC 4.3.2) — pure, independently testable
# ---------------------------------------------------------------------------


def compute_backoff_delay(
    attempt_number: int,
    base_delay_seconds: float = 1.0,
    *,
    strategy: BackoffStrategy = BackoffStrategy.FIXED,
    backoff_factor: float = 2.0,
    jitter_value: float = 0.0,
) -> float:
    """Compute the inter-retry delay for a given attempt using a configurable strategy.

    This is a **pure function** — no side effects, no I/O, no random generation.
    For jittered strategies, the caller provides the random component via
    ``jitter_value`` so the function remains deterministic and independently
    testable.

    Args:
        attempt_number: The just-completed attempt number (1-indexed).
                        The delay is for the wait *before* the next attempt.
        base_delay_seconds: Base delay in seconds (must be >= 0).
        strategy: The backoff strategy to use (see :class:`BackoffStrategy`).
        backoff_factor: Multiplicative factor for exponential strategies.
                        Only relevant for ``EXPONENTIAL`` and
                        ``EXPONENTIAL_JITTER``.
        jitter_value: Random jitter component for jittered strategies.
                      Only relevant for ``JITTERED`` and
                      ``EXPONENTIAL_JITTER``.  Always added (not multiplied),
                      clamped to >= 0.

    Returns:
        Delay in seconds (always >= 0.0).

    Raises:
        ValueError: If ``attempt_number`` < 1, ``base_delay_seconds`` < 0,
                    or ``jitter_value`` < 0.

    Examples:
        *Fixed strategy — constant delay:*

        >>> compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.FIXED)
        1.0
        >>> compute_backoff_delay(3, 1.0, strategy=BackoffStrategy.FIXED)
        1.0

        *Exponential strategy — grows with attempt:*

        >>> compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        1.0
        >>> compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        2.0
        >>> compute_backoff_delay(4, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        8.0

        *Jittered strategy — base + jitter:*

        >>> compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.JITTERED, jitter_value=0.3)
        1.3

        *Exponential with jitter:*

        >>> compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.EXPONENTIAL_JITTER, backoff_factor=2.0, jitter_value=0.5)
        2.5
    """
    if attempt_number < 1:
        raise ValueError(
            f"attempt_number must be >= 1, got {attempt_number}"
        )
    if base_delay_seconds < 0:
        raise ValueError(
            f"base_delay_seconds must be >= 0, got {base_delay_seconds}"
        )
    if jitter_value < 0:
        raise ValueError(
            f"jitter_value must be >= 0, got {jitter_value}"
        )

    if strategy == BackoffStrategy.FIXED:
        return max(0.0, base_delay_seconds)

    if strategy == BackoffStrategy.EXPONENTIAL:
        if attempt_number > 1:
            base = base_delay_seconds * (backoff_factor ** (attempt_number - 1))
        else:
            base = base_delay_seconds
        return max(0.0, base)

    if strategy == BackoffStrategy.JITTERED:
        return max(0.0, base_delay_seconds + jitter_value)

    if strategy == BackoffStrategy.EXPONENTIAL_JITTER:
        if attempt_number > 1:
            base = base_delay_seconds * (backoff_factor ** (attempt_number - 1))
        else:
            base = base_delay_seconds
        return max(0.0, base + jitter_value)

    # Exhaustive check — should never reach here
    raise ValueError(f"Unknown backoff strategy: {strategy}")


def _compute_delay(config: RetryConfig, attempt_number: int) -> float:
    """Compute the delay before the next attempt (delegates to :func:`compute_backoff_delay`).

    Handles random jitter generation internally for backward compatibility.

    Args:
        config: The retry configuration.
        attempt_number: The just-completed attempt number (1-indexed).

    Returns:
        Delay in seconds (always >= 0).
    """
    import random

    jitter_value = 0.0
    if config.jitter_seconds > 0:
        jitter_value = random.uniform(0, config.jitter_seconds)

    return compute_backoff_delay(
        attempt_number=attempt_number,
        base_delay_seconds=config.retry_delay_seconds,
        strategy=config.strategy,
        backoff_factor=config.backoff_factor,
        jitter_value=jitter_value,
    )


# ---------------------------------------------------------------------------
# Async variant
# ---------------------------------------------------------------------------


async def execute_with_retry_async(
    operation: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    on_attempt: Callable[[RetryAttempt], None] | None = None,
    on_retry: Callable[[RetryAttempt], None] | None = None,
    **kwargs: Any,
) -> RetryResult[Any]:
    """Async variant of :func:`execute_with_retry`.

    Wraps a sync or async *operation* with the same retry semantics.
    If *operation* is a coroutine function or returns an awaitable,
    it will be ``await``-ed per attempt.

    Args:
        operation: Callable (sync or async) to execute.
        *args: Positional arguments forwarded.
        config: ``RetryConfig`` instance.  Uses defaults when ``None``.
        on_attempt: Called after every attempt.
        on_retry: Called only when a retry is scheduled.
        **kwargs: Keyword arguments forwarded.

    Returns:
        A ``RetryResult`` describing the full execution history.
    """
    cfg = config if config is not None else RetryConfig()

    attempts: list[RetryAttempt] = []
    last_error: Exception | None = None

    for attempt_num in range(1, cfg.max_retries + 1):
        start = time.monotonic()

        try:
            result = operation(*args, **kwargs)

            # If it's a coroutine, await it
            if asyncio.iscoroutine(result):
                output = await result
            else:
                output = result

            end = time.monotonic()

            attempt = RetryAttempt(
                attempt_number=attempt_num,
                error=None,
                start_time=start,
                end_time=end,
            )
            attempts.append(attempt)

            if on_attempt is not None:
                on_attempt(attempt)

            return RetryResult(
                success=True,
                attempts=tuple(attempts),
                max_retries_exceeded=False,
                final_error=None,
                result=output,
                config=cfg,
            )

        except Exception as exc:  # noqa: BLE001
            end = time.monotonic()

            attempt = RetryAttempt(
                attempt_number=attempt_num,
                error=exc,
                start_time=start,
                end_time=end,
            )
            attempts.append(attempt)
            last_error = exc

            if on_attempt is not None:
                on_attempt(attempt)

            if not should_retry(exc, cfg, attempt_num):
                break

            if on_retry is not None:
                on_retry(attempt)

            delay = _compute_delay(cfg, attempt_num)
            if delay > 0:
                await asyncio.sleep(delay)

    return RetryResult(
        success=False,
        attempts=tuple(attempts),
        max_retries_exceeded=len(attempts) >= cfg.max_retries and last_error is not None,
        final_error=last_error,
        result=None,
        config=cfg,
    )
