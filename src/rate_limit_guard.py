"""Rate-limit guard for opencode-go LLM calls.

Seed constraint implementation:
- Before any LLM call, check opencode-go quota. If below 10% remaining,
  pause execution with state saved.
- On 429 or quota-exceeded error, wait 60s and retry once.
- If still failing, save state and exit with exit condition rate_limit_paused.
- All LLM-calling sub-ACs must trap rate-limit errors and surface them
  to the orchestrator as non-fatal, resumable events.

Usage::

    from src.rate_limit_guard import (
        RateLimitStatus,
        check_quota,
        is_rate_limit_error,
        handle_rate_limit,
    )

    status = check_quota()
    if not status.available:
        # Pause execution, save state, return rate_limit_paused
        ...

    result = invoke_qwen(config)
    if is_rate_limit_error(result.stderr):
        backoff_result = handle_rate_limit(config)
        if backoff_result is not None:
            result = backoff_result  # retry succeeded
        else:
            # Still failing, pause and save state
            ...
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Any, Callable


# ── Constants ─────────────────────────────────────────────────────────

QUOTA_CHECK_TIMEOUT_SECONDS: float = 10.0
"""Maximum time to wait for quota check subprocess."""

BACKOFF_WAIT_SECONDS: float = 60.0
"""Wait time before retrying after a rate-limit error."""

QUOTA_THRESHOLD_PERCENT: float = 10.0
"""Threshold below which quota is considered exhausted."""

MAX_RETRY_ATTEMPTS: int = 1
"""Maximum retry attempts after a rate-limit error."""

# Regex patterns to detect rate-limit errors in stderr/stdout
_RATE_LIMIT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"quota.?exceeded", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"request limit reached", re.IGNORECASE),
    re.compile(r"api rate limit", re.IGNORECASE),
    re.compile(r"insufficient_quota", re.IGNORECASE),
    re.compile(r"billing.?limit", re.IGNORECASE),
)

# Regex patterns to extract remaining quota percentage from CLI output
_QUOTA_PERCENT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"remaining[:\s]+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE),
    re.compile(r"quota[:\s]+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*%\s*remaining", re.IGNORECASE),
    re.compile(r"tokens remaining[:\s]+(\d+)", re.IGNORECASE),
    re.compile(r"requests remaining[:\s]+(\d+)", re.IGNORECASE),
)


# ── Data types ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RateLimitStatus:
    """Result of a quota check before making an LLM call.

    Attributes:
        available: True when calls can proceed (quota above threshold).
        remaining_percent: Estimated remaining quota 0-100.
        reason: Human-readable status description.
        quota_checked: True when a real quota check was performed
                       (False means check skipped/failed — assume available).
    """

    available: bool
    remaining_percent: float = 100.0
    reason: str = ""
    quota_checked: bool = False

    @property
    def is_below_threshold(self) -> bool:
        """True when remaining quota is below the 10% threshold."""
        return self.remaining_percent < QUOTA_THRESHOLD_PERCENT


@dataclass(frozen=True)
class RateLimitBackoffResult:
    """Result of a rate-limit backoff + retry cycle.

    Attributes:
        succeeded: True when the retry succeeded.
        retry_stdout: Raw stdout from the retry call (empty if failed).
        retry_stderr: Raw stderr from the retry call.
        attempts_made: Number of retry attempts made.
        wait_seconds: Total time spent waiting.
    """

    succeeded: bool
    retry_stdout: str = ""
    retry_stderr: str = ""
    attempts_made: int = 0
    wait_seconds: float = 0.0


# ── Quota check ───────────────────────────────────────────────────────

def _try_opencode_quota_command() -> tuple[int, str, str]:
    """Attempt to run ``opencode-go quota`` to check remaining quota.

    Returns:
        (exit_code, stdout, stderr) tuple.
    """
    try:
        completed = subprocess.run(
            ["opencode-go", "quota"],
            capture_output=True,
            text=True,
            timeout=QUOTA_CHECK_TIMEOUT_SECONDS,
        )
        return (completed.returncode, completed.stdout, completed.stderr)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return (-1, "", "opencode-go not available for quota check")


def _parse_quota_output(stdout: str, stderr: str) -> tuple[float, bool]:
    """Parse quota percentage from opencode-go quota output.

    Returns:
        (remaining_percent, successfully_parsed) — percent in 0-100 range.
        When parsing fails, returns (100.0, False).
    """
    combined = stdout + "\n" + stderr

    # Try percentage patterns first
    for pattern in _QUOTA_PERCENT_PATTERNS:
        match = pattern.search(combined)
        if match:
            try:
                percent = float(match.group(1))
                return (min(max(percent, 0.0), 100.0), True)
            except (ValueError, IndexError):
                continue

    # Try token/request counts (needs a known limit — heuristic)
    token_match = re.search(
        r"(\d+)\s*/\s*(\d+)\s*tokens", combined, re.IGNORECASE
    )
    if token_match:
        try:
            used = int(token_match.group(1))
            limit = int(token_match.group(2))
            if limit > 0:
                remaining = 100.0 * (limit - used) / limit
                return (min(max(remaining, 0.0), 100.0), True)
        except (ValueError, IndexError):
            pass

    return (100.0, False)


def check_quota() -> RateLimitStatus:
    """Check opencode-go quota before making an LLM call.

    Attempts to run ``opencode-go quota`` to get real remaining quota.
    Falls back to assuming available if the CLI is unreachable.

    Returns:
        ``RateLimitStatus`` — check ``.available`` before proceeding.

    Example:
        >>> status = check_quota()
        >>> if not status.available:
        ...     print(f"Pause: {status.reason}")
    """
    exit_code, stdout, stderr = _try_opencode_quota_command()

    if exit_code != 0:
        # CLI not available — assume quota is fine
        return RateLimitStatus(
            available=True,
            remaining_percent=100.0,
            reason="quota check skipped: opencode-go not available",
            quota_checked=False,
        )

    remaining, parsed = _parse_quota_output(stdout, stderr)

    if not parsed:
        return RateLimitStatus(
            available=True,
            remaining_percent=100.0,
            reason="quota check succeeded but could not parse percentage",
            quota_checked=True,
        )

    if remaining < QUOTA_THRESHOLD_PERCENT:
        return RateLimitStatus(
            available=False,
            remaining_percent=remaining,
            reason=(
                f"quota at {remaining:.1f}% — below {QUOTA_THRESHOLD_PERCENT}% "
                f"threshold"
            ),
            quota_checked=True,
        )

    return RateLimitStatus(
        available=True,
        remaining_percent=remaining,
        reason=f"quota at {remaining:.1f}% — OK",
        quota_checked=True,
    )


# ── Rate-limit detection ──────────────────────────────────────────────

def is_rate_limit_error(stderr: str) -> bool:
    """Check if stderr contains rate-limit signals (429, quota exceeded, etc.).

    Args:
        stderr: Raw stderr output from an opencode-go call.

    Returns:
        True when the error indicates a rate-limit condition.
    """
    if not stderr:
        return False

    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern.search(stderr):
            return True

    return False


def is_rate_limit_error_in_stdout(stdout: str) -> bool:
    """Check if stdout also contains rate-limit signals.

    Some LLM providers return rate-limit errors in the response body
    rather than stderr. This catches those cases.
    """
    if not stdout:
        return False

    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern.search(stdout):
            return True

    return False


# ── Backoff and retry ─────────────────────────────────────────────────

def handle_rate_limit(
    runner_fn,  # Callable[[OpencodeCallConfig], OpencodeCallResult]
    config,     # OpencodeCallConfig
    *,
    max_retries: int = MAX_RETRY_ATTEMPTS,
    backoff_seconds: float = BACKOFF_WAIT_SECONDS,
) -> Optional[Any]:  # OpencodeCallResult | None
    """Handle a rate-limit error with backoff and retry.

    Waits for *backoff_seconds* then retries the call up to
    *max_retries* times. Returns the first successful result,
    or None if all retries fail.

    Args:
        runner_fn: The function to call for retries (same signature as
                   ``invoke_qwen``).
        config: The original ``OpencodeCallConfig``.
        max_retries: Maximum retry attempts (default: 1).
        backoff_seconds: Wait time between attempts (default: 60s).

    Returns:
        ``OpencodeCallResult`` on retry success, None on exhaustion.

    Example:
        >>> from src.opencode_qwen_wrapper import invoke_qwen
        >>> result = invoke_qwen(config)
        >>> if is_rate_limit_error(result.stderr):
        ...     retry = handle_rate_limit(invoke_qwen, config)
        ...     if retry is not None:
        ...         result = retry
        ...     else:
        ...         # Pause and save state
        ...         pass
    """
    for attempt in range(1, max_retries + 1):
        time.sleep(backoff_seconds)

        try:
            result = runner_fn(config)
        except Exception:
            result = None

        if result is not None and result.success:
            return result

        if result is not None and not is_rate_limit_error(result.stderr):
            # Different error type — don't keep retrying rate-limit backoff
            return result

    return None


# ── Combined guard (convenience) ───────────────────────────────────────

@dataclass(frozen=True)
class QuotaGuardResult:
    """Combined result of quota check before making an LLM call.

    Use this to decide whether to proceed, pause, or handle rate limits.
    """

    can_proceed: bool
    """True when it's safe to make the LLM call."""

    exit_condition: str = ""
    """When *can_proceed* is False, the reason to surface:
    'rate_limit_paused', 'quota_exhausted', or empty."""

    reason: str = ""
    """Human-readable explanation."""

    rate_limit_status: Optional[RateLimitStatus] = None
    """The quota check result (for logging)."""


# ── Quota checker injection (for testing) ─────────────────────────────
_quota_checker = check_quota


def inject_quota_checker(checker: Callable[[], RateLimitStatus] | None) -> None:
    """Replace the active quota checker (for testing).

    Pass ``None`` to restore the default production checker.

    Args:
        checker: The test double, or ``None`` to reset to default.
    """
    global _quota_checker
    if checker is None:
        _quota_checker = check_quota
    else:
        _quota_checker = checker


def get_quota_checker() -> Callable[[], RateLimitStatus]:
    """Return the currently active quota checker."""
    return _quota_checker


def guard_llm_call() -> QuotaGuardResult:
    """Pre-call guard: check quota and return a go/no-go decision.

    Call this before every opencode-go LLM invocation.  When
    ``can_proceed`` is False, the caller should save state to manifest
    and return with exit condition ``rate_limit_paused``.

    Returns:
        ``QuotaGuardResult`` — inspect ``.can_proceed`` before calling.

    Example:
        >>> guard = guard_llm_call()
        >>> if not guard.can_proceed:
        ...     # Save state, return ClassificationResult with
        ...     # exit_condition = "rate_limit_paused"
        ...     return make_paused_result(guard.reason)
        ... # Safe to proceed
        ... result = invoke_qwen(config)
    """
    status = _quota_checker()

    if not status.available:
        return QuotaGuardResult(
            can_proceed=False,
            exit_condition="rate_limit_paused",
            reason=status.reason,
            rate_limit_status=status,
        )

    return QuotaGuardResult(
        can_proceed=True,
        exit_condition="",
        reason=status.reason,
        rate_limit_status=status,
    )
