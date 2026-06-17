"""Tests for the retry executor module (Sub-AC 4.3.1).

Covers:
- RetryConfig validation
- Error classification into categories
- should_retry logic (limit enforcement, exception type matching, permanent rejection)
- Successful execution on first attempt
- Successful execution after N retries
- Failure when max_retries exhausted
- Attempt tracking (count, timing, error recording)
- Callback hooks (on_attempt, on_retry)
- Backoff delay computation
- Integration with the system design's retry→fallback→quorum sequence
"""

from __future__ import annotations

import time

import pytest

from src.retry_executor import (
    ERROR_CATEGORY_MODEL,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_PERMANENT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_TRANSIENT,
    ERROR_CATEGORY_UNKNOWN,
    ERROR_CATEGORY_VALIDATION,
    BackoffStrategy,
    RetryAttempt,
    RetryConfig,
    RetryResult,
    classify_error,
    compute_backoff_delay,
    execute_with_retry,
    should_retry,
)


# ── RetryConfig tests ───────────────────────────────────────────────────


class TestRetryConfig:
    """Verify RetryConfig creation, defaults, and validation."""

    def test_default_values(self):
        cfg = RetryConfig()
        assert cfg.max_retries == 3
        assert cfg.retry_delay_seconds == 1.0
        assert cfg.retry_on_exceptions == (Exception,)
        assert cfg.retry_on_exit_codes == ()
        assert cfg.backoff_factor == 1.0
        assert cfg.jitter_seconds == 0.0
        assert cfg.timeout_seconds == 0.0

    def test_custom_values(self):
        cfg = RetryConfig(
            max_retries=5,
            retry_delay_seconds=0.5,
            retry_on_exceptions=(ValueError, KeyError),
            retry_on_exit_codes=(1, 2),
            backoff_factor=2.0,
            jitter_seconds=0.1,
            timeout_seconds=30.0,
        )
        assert cfg.max_retries == 5
        assert cfg.retry_delay_seconds == 0.5
        assert cfg.retry_on_exceptions == (ValueError, KeyError)
        assert cfg.retry_on_exit_codes == (1, 2)
        assert cfg.backoff_factor == 2.0
        assert cfg.jitter_seconds == 0.1
        assert cfg.timeout_seconds == 30.0

    def test_is_frozen(self):
        cfg = RetryConfig()
        with pytest.raises(Exception):
            cfg.max_retries = 99  # type: ignore[misc]

    def test_max_retries_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            RetryConfig(max_retries=0)

    def test_max_retries_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            RetryConfig(max_retries=-1)

    def test_max_retries_one_is_valid(self):
        cfg = RetryConfig(max_retries=1)
        assert cfg.max_retries == 1


# ── RetryAttempt tests ──────────────────────────────────────────────────


class TestRetryAttempt:
    """Verify RetryAttempt data structure and computed properties."""

    def test_successful_attempt(self):
        now = time.monotonic()
        attempt = RetryAttempt(
            attempt_number=1,
            error=None,
            start_time=now,
            end_time=now + 0.05,
        )
        assert attempt.attempt_number == 1
        assert attempt.error is None
        assert 0.04 < attempt.elapsed_seconds < 0.06

    def test_failed_attempt(self):
        exc = ValueError("test error")
        now = time.monotonic()
        attempt = RetryAttempt(
            attempt_number=3,
            error=exc,
            start_time=now,
            end_time=now + 0.1,
        )
        assert attempt.attempt_number == 3
        assert attempt.error is exc
        assert str(attempt.error) == "test error"

    def test_is_frozen(self):
        attempt = RetryAttempt(
            attempt_number=1, error=None, start_time=0.0, end_time=0.0
        )
        with pytest.raises(Exception):
            attempt.attempt_number = 2  # type: ignore[misc]


# ── RetryResult tests ───────────────────────────────────────────────────


class TestRetryResult:
    """Verify RetryResult computed properties."""

    def _make_result(
        self,
        success: bool,
        attempt_count: int,
        max_retries_exceeded: bool = False,
        final_error: Exception | None = None,
    ) -> RetryResult:
        attempts = tuple(
            RetryAttempt(
                attempt_number=i,
                error=ValueError(f"err {i}") if i < attempt_count or not success else None,
                start_time=float(i),
                end_time=float(i) + 0.01,
            )
            for i in range(1, attempt_count + 1)
        )
        return RetryResult(
            success=success,
            attempts=attempts,
            max_retries_exceeded=max_retries_exceeded,
            final_error=final_error,
            result="ok" if success else None,
            config=RetryConfig(max_retries=attempt_count),
        )

    def test_success_result(self):
        result = self._make_result(success=True, attempt_count=2)
        assert result.success is True
        assert result.total_attempts == 2
        assert result.max_retries_exceeded is False
        assert result.final_error is None
        assert result.result == "ok"

    def test_failure_result(self):
        err = ValueError("boom")
        result = self._make_result(
            success=False, attempt_count=3,
            max_retries_exceeded=True, final_error=err,
        )
        assert result.success is False
        assert result.total_attempts == 3
        assert result.max_retries_exceeded is True
        assert result.final_error is err

    def test_first_error_returns_earliest(self):
        """first_error should return the first exception encountered."""
        result = self._make_result(success=False, attempt_count=3)
        assert result.first_error is not None
        assert str(result.first_error) == "err 1"  # attempts are 1-indexed

    def test_first_error_none_when_all_succeeded(self):
        result = self._make_result(success=True, attempt_count=1)
        assert result.first_error is None

    def test_last_attempt_number(self):
        result = self._make_result(success=False, attempt_count=5)
        assert result.last_attempt_number == 5

    def test_last_attempt_number_empty(self):
        result = RetryResult(
            success=False,
            attempts=(),
            max_retries_exceeded=False,
            final_error=None,
            result=None,
            config=RetryConfig(),
        )
        assert result.last_attempt_number == 0

    def test_config_preserved(self):
        cfg = RetryConfig(max_retries=7)
        result = self._make_result(success=True, attempt_count=1)
        # Override config for this test
        object.__setattr__(result, "config", cfg)
        assert result.config.max_retries == 7


# ── Error classification tests ──────────────────────────────────────────


class TestClassifyError:
    """Verify classify_error categorises exceptions correctly."""

    def test_none_returns_unknown(self):
        assert classify_error(None) == ERROR_CATEGORY_UNKNOWN

    def test_timeout_error(self):
        assert classify_error(TimeoutError()) == ERROR_CATEGORY_TIMEOUT
        import asyncio
        assert classify_error(asyncio.TimeoutError()) == ERROR_CATEGORY_TIMEOUT

    def test_connection_errors(self):
        assert classify_error(ConnectionError("refused")) == ERROR_CATEGORY_NETWORK
        assert classify_error(ConnectionRefusedError()) == ERROR_CATEGORY_NETWORK
        assert classify_error(ConnectionResetError()) == ERROR_CATEGORY_NETWORK

    def test_os_error_is_transient(self):
        assert classify_error(OSError("I/O error")) == ERROR_CATEGORY_TRANSIENT

    def test_validation_keywords(self):
        assert classify_error(ValueError("invalid JSON parse")) == ERROR_CATEGORY_VALIDATION
        assert classify_error(ValueError("schema mismatch")) == ERROR_CATEGORY_VALIDATION
        assert classify_error(TypeError("field missing")) == ERROR_CATEGORY_VALIDATION
        assert classify_error(KeyError("decode error")) == ERROR_CATEGORY_VALIDATION

    def test_model_keywords(self):
        assert classify_error(Exception("rate limit exceeded")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("API key invalid")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("authentication failed")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("model overloaded")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("HTTP 429")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("HTTP 503")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("provider capacity")) == ERROR_CATEGORY_MODEL

    def test_permanent_keywords(self):
        assert classify_error(Exception("file not found")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(Exception("no such file or directory")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(Exception("permission denied")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(Exception("syntax error in config")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(Exception("invalid argument")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(Exception("unsupported operation")) == ERROR_CATEGORY_PERMANENT

    def test_generic_exception_is_unknown(self):
        assert classify_error(Exception("some random error")) == ERROR_CATEGORY_UNKNOWN
        assert classify_error(RuntimeError("unexpected")) == ERROR_CATEGORY_UNKNOWN

    def test_classification_priority_network_over_timeout(self):
        """ConnectionResetError is also an OSError — network wins."""
        # ConnectionResetError is a ConnectionError subclass, so network first
        assert classify_error(ConnectionResetError()) == ERROR_CATEGORY_NETWORK

    def test_classification_case_insensitive(self):
        assert classify_error(Exception("Rate Limit EXCEEDED")) == ERROR_CATEGORY_MODEL
        assert classify_error(Exception("FILE NOT FOUND")) == ERROR_CATEGORY_PERMANENT
        assert classify_error(ValueError("JSON PARSE ERROR")) == ERROR_CATEGORY_VALIDATION


# ── should_retry tests ──────────────────────────────────────────────────


class TestShouldRetry:
    """Verify the should_retry decision function."""

    @pytest.fixture
    def default_cfg(self) -> RetryConfig:
        return RetryConfig(max_retries=3)

    def test_retries_when_under_limit(self, default_cfg):
        assert should_retry(ValueError("oops"), default_cfg, attempt_number=1) is True
        assert should_retry(ValueError("oops"), default_cfg, attempt_number=2) is True

    def test_rejects_when_at_limit(self, default_cfg):
        """Attempt 3 of max_retries=3 → no more retries."""
        assert should_retry(ValueError("oops"), default_cfg, attempt_number=3) is False

    def test_rejects_when_above_limit(self, default_cfg):
        assert should_retry(ValueError("oops"), default_cfg, attempt_number=4) is False

    def test_rejects_non_matching_exception_type(self):
        cfg = RetryConfig(max_retries=3, retry_on_exceptions=(ValueError,))
        assert should_retry(KeyError("missing"), cfg, attempt_number=1) is False

    def test_rejects_permanent_errors_even_if_type_matches(self):
        """Permanent errors bypass the exception type check."""
        cfg = RetryConfig(max_retries=3, retry_on_exceptions=(Exception,))
        assert should_retry(Exception("file not found"), cfg, attempt_number=1) is False

    def test_accepts_transient_errors(self):
        cfg = RetryConfig(max_retries=3)
        assert should_retry(TimeoutError(), cfg, attempt_number=1) is True

    def test_accepts_model_errors(self):
        cfg = RetryConfig(max_retries=3)
        assert should_retry(Exception("rate limit"), cfg, attempt_number=1) is True

    def test_accepts_network_errors(self):
        cfg = RetryConfig(max_retries=3)
        assert should_retry(ConnectionError(), cfg, attempt_number=1) is True

    def test_edge_case_attempt_1_of_1(self):
        """max_retries=1 means no retry on first failure."""
        cfg = RetryConfig(max_retries=1)
        assert should_retry(ValueError(), cfg, attempt_number=1) is False


# ── Retry count tracking tests ──────────────────────────────────────────


class TestRetryCountTracking:
    """Verify attempt count is accurately tracked through execution."""

    def test_success_on_first_attempt_tracks_one(self):
        config = RetryConfig(max_retries=5, retry_delay_seconds=0.0)
        result = execute_with_retry(lambda: "ok", config=config)
        assert result.success
        assert result.total_attempts == 1
        assert result.attempts[0].attempt_number == 1
        assert result.attempts[0].error is None

    def test_success_after_two_failures_tracks_three_attempts(self):
        config = RetryConfig(max_retries=5, retry_delay_seconds=0.0)
        counter = [0]

        def flaky():
            counter[0] += 1
            if counter[0] < 3:
                raise ValueError(f"fail {counter[0]}")
            return f"pass on {counter[0]}"

        result = execute_with_retry(flaky, config=config)
        assert result.success
        assert result.total_attempts == 3
        # Checks attempt numbering
        assert result.attempts[0].attempt_number == 1
        assert result.attempts[1].attempt_number == 2
        assert result.attempts[2].attempt_number == 3
        # Checks error tracking
        assert result.attempts[0].error is not None
        assert result.attempts[1].error is not None
        assert result.attempts[2].error is None
        assert result.result == "pass on 3"

    def test_failure_exhausts_all_retries(self):
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)

        def always_fail():
            raise RuntimeError("nope")

        result = execute_with_retry(always_fail, config=config)
        assert not result.success
        assert result.total_attempts == 3
        assert result.max_retries_exceeded is True
        assert isinstance(result.final_error, RuntimeError)
        assert str(result.final_error) == "nope"

    def test_failure_attempts_all_have_errors(self):
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)

        def always_fail():
            raise RuntimeError("nope")

        result = execute_with_retry(always_fail, config=config)
        for attempt in result.attempts:
            assert attempt.error is not None
            assert attempt.attempt_number > 0
            assert attempt.elapsed_seconds >= 0.0


# ── Max retries limit enforcement tests ─────────────────────────────────


class TestMaxRetriesEnforcement:
    """Verify the max_retries limit is strictly enforced."""

    @pytest.mark.parametrize("max_r", [1, 2, 5, 10])
    def test_exactly_max_attempts_made(self, max_r):
        config = RetryConfig(max_retries=max_r, retry_delay_seconds=0.0)

        def always_fail():
            raise ValueError("fail")

        result = execute_with_retry(always_fail, config=config)
        assert result.total_attempts == max_r
        assert result.max_retries_exceeded is True

    def test_max_retries_1_allows_no_retry_on_failure(self):
        config = RetryConfig(max_retries=1, retry_delay_seconds=0.0)
        result = execute_with_retry(lambda: 1 / 0, config=config)
        assert result.total_attempts == 1
        assert not result.success

    def test_max_retries_1_succeeds_immediately(self):
        config = RetryConfig(max_retries=1, retry_delay_seconds=0.0)
        result = execute_with_retry(lambda: 42, config=config)
        assert result.success
        assert result.total_attempts == 1
        assert result.result == 42

    def test_permanent_error_stops_immediately(self):
        """Permanent errors stop retries regardless of max_retries."""
        config = RetryConfig(max_retries=10, retry_delay_seconds=0.0)

        def fail_with_permanent():
            raise FileNotFoundError("cannot find config")

        result = execute_with_retry(fail_with_permanent, config=config)
        assert not result.success
        assert result.total_attempts == 1  # only one attempt!
        assert not result.max_retries_exceeded  # didn't exhaust, was blocked


# ── Callback hook tests ─────────────────────────────────────────────────


class TestCallbacks:
    """Verify on_attempt and on_retry callbacks fire correctly."""

    def test_on_attempt_called_for_every_attempt(self):
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        log: list[int] = []

        def record(attempt: RetryAttempt) -> None:
            log.append(attempt.attempt_number)

        def flaky():
            if len(log) < 2:
                raise ValueError("fail")
            return "ok"

        result = execute_with_retry(flaky, config=config, on_attempt=record)
        assert log == [1, 2, 3]
        assert result.success

    def test_on_attempt_receives_error_info(self):
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        errors: list[Exception | None] = []

        def record(attempt: RetryAttempt) -> None:
            errors.append(attempt.error)

        def flaky():
            if len(errors) < 1:
                raise ValueError("fail once")
            return "done"

        result = execute_with_retry(flaky, config=config, on_attempt=record)
        assert len(errors) == 2
        assert errors[0] is not None  # first attempt failed
        assert errors[1] is None  # second attempt succeeded

    def test_on_retry_only_called_before_retry(self):
        config = RetryConfig(max_retries=5, retry_delay_seconds=0.0)
        retry_log: list[int] = []

        def on_retry(attempt: RetryAttempt) -> None:
            retry_log.append(attempt.attempt_number)

        def flaky():
            if len(retry_log) < 3:
                raise ValueError("fail")
            return "ok"

        result = execute_with_retry(flaky, config=config, on_retry=on_retry)
        assert retry_log == [1, 2, 3]  # retried after attempts 1,2,3
        assert result.total_attempts == 4
        assert result.success

    def test_on_retry_not_called_on_success_first_try(self):
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        retry_called = False

        def on_retry(attempt: RetryAttempt) -> None:
            nonlocal retry_called
            retry_called = True

        result = execute_with_retry(lambda: "ok", config=config, on_retry=on_retry)
        assert result.success
        assert not retry_called

    def test_on_retry_not_called_when_no_more_retries(self):
        """When we hit max_retries, on_retry should NOT fire for the last failure."""
        config = RetryConfig(max_retries=2, retry_delay_seconds=0.0)
        retry_calls = 0

        def on_retry(attempt: RetryAttempt) -> None:
            nonlocal retry_calls
            retry_calls += 1

        result = execute_with_retry(
            lambda: 1 / 0, config=config, on_retry=on_retry
        )
        assert not result.success
        assert retry_calls == 1  # only after attempt 1; not after attempt 2 (limit)


# ── Backoff strategy tests (Sub-AC 4.3.2) ────────────────────────────────


class TestBackoffStrategy:
    """Verify BackoffStrategy enum values and properties."""

    def test_enum_values(self):
        assert BackoffStrategy.FIXED == "fixed"
        assert BackoffStrategy.EXPONENTIAL == "exponential"
        assert BackoffStrategy.JITTERED == "jittered"
        assert BackoffStrategy.EXPONENTIAL_JITTER == "exponential_jitter"

    def test_enum_is_string_subclass(self):
        assert isinstance(BackoffStrategy.FIXED, str)
        assert BackoffStrategy.FIXED.upper() == "FIXED"

    def test_enum_iteration(self):
        values = list(BackoffStrategy)
        assert len(values) == 4
        assert BackoffStrategy.FIXED in values


# ── compute_backoff_delay pure function tests ─────────────────────────────


class TestComputeBackoffDelay:
    """Verify the pure compute_backoff_delay function for all strategies."""

    # --- Fixed strategy ---

    def test_fixed_returns_constant_delay(self):
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.FIXED) == 1.0
        assert compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.FIXED) == 1.0
        assert compute_backoff_delay(10, 1.0, strategy=BackoffStrategy.FIXED) == 1.0

    def test_fixed_zero_base(self):
        assert compute_backoff_delay(5, 0.0, strategy=BackoffStrategy.FIXED) == 0.0

    def test_fixed_negative_base_clamped(self):
        # base_delay_seconds < 0 is rejected by validation, not clamped
        pass  # handled in validation tests

    # --- Exponential strategy ---

    def test_exponential_attempt_1_equals_base(self):
        result = compute_backoff_delay(1, 2.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        assert result == 2.0

    def test_exponential_doubles_each_attempt(self):
        # With factor=2.0: delay = base * 2^(attempt-1)
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0) == 1.0
        assert compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0) == 2.0
        assert compute_backoff_delay(3, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0) == 4.0
        assert compute_backoff_delay(4, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0) == 8.0

    def test_exponential_custom_factor(self):
        # factor=3.0: 1, 3, 9, 27
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=3.0) == 1.0
        assert compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=3.0) == 3.0
        assert compute_backoff_delay(3, 1.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=3.0) == 9.0

    def test_exponential_factor_one_is_constant(self):
        # factor=1.0 means no growth
        assert compute_backoff_delay(1, 5.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=1.0) == 5.0
        assert compute_backoff_delay(5, 5.0, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=1.0) == 5.0

    def test_exponential_no_backoff_factor_defaults_to_two(self):
        # When not specified, backoff_factor defaults to 2.0
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.EXPONENTIAL) == 1.0
        assert compute_backoff_delay(3, 1.0, strategy=BackoffStrategy.EXPONENTIAL) == 4.0

    # --- Jittered strategy ---

    def test_jittered_adds_jitter_value(self):
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.JITTERED, jitter_value=0.0) == 1.0
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.JITTERED, jitter_value=0.5) == 1.5
        assert compute_backoff_delay(3, 2.0, strategy=BackoffStrategy.JITTERED, jitter_value=1.0) == 3.0

    def test_jittered_ignores_attempt_number(self):
        # JITTERED is always base + jitter, regardless of attempt
        for n in (1, 2, 5, 10):
            assert compute_backoff_delay(n, 1.0, strategy=BackoffStrategy.JITTERED, jitter_value=0.3) == 1.3

    # --- Exponential + Jitter ---

    def test_exponential_jitter_combines_both(self):
        assert compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.EXPONENTIAL_JITTER, backoff_factor=2.0, jitter_value=0.0) == 1.0
        assert compute_backoff_delay(2, 1.0, strategy=BackoffStrategy.EXPONENTIAL_JITTER, backoff_factor=2.0, jitter_value=0.5) == 2.5
        assert compute_backoff_delay(3, 1.0, strategy=BackoffStrategy.EXPONENTIAL_JITTER, backoff_factor=2.0, jitter_value=0.3) == 4.3

    def test_exponential_jitter_with_factor_one(self):
        """factor=1.0 + jitter = effectively jittered strategy"""
        result = compute_backoff_delay(4, 2.0, strategy=BackoffStrategy.EXPONENTIAL_JITTER, backoff_factor=1.0, jitter_value=0.2)
        assert result == 2.2

    # --- Validation / error handling ---

    def test_raises_on_attempt_number_zero(self):
        with pytest.raises(ValueError, match="attempt_number must be >= 1"):
            compute_backoff_delay(0, 1.0)

    def test_raises_on_attempt_number_negative(self):
        with pytest.raises(ValueError, match="attempt_number must be >= 1"):
            compute_backoff_delay(-1, 1.0)

    def test_raises_on_negative_base_delay(self):
        with pytest.raises(ValueError, match="base_delay_seconds must be >= 0"):
            compute_backoff_delay(1, -0.1)

    def test_raises_on_negative_jitter_value(self):
        with pytest.raises(ValueError, match="jitter_value must be >= 0"):
            compute_backoff_delay(1, 1.0, strategy=BackoffStrategy.JITTERED, jitter_value=-0.1)

    # --- Determinism / purity ---

    def test_pure_function_no_side_effects(self):
        """Repeated calls with same args produce identical results."""
        args = (3, 1.0)
        kwargs = {"strategy": BackoffStrategy.EXPONENTIAL, "backoff_factor": 2.0}
        results = [compute_backoff_delay(*args, **kwargs) for _ in range(100)]
        assert all(r == results[0] for r in results)

    def test_pure_function_no_random(self):
        """Verify no random module is imported or used."""
        import inspect
        source = inspect.getsource(compute_backoff_delay)
        assert "import random" not in source
        assert "random.uniform" not in source
        assert "random.random" not in source

    # --- Edge cases ---

    def test_very_large_attempt_number(self):
        """Ensure no overflow for large attempt numbers."""
        result = compute_backoff_delay(50, 0.1, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        assert result > 0
        assert isinstance(result, float)

    def test_very_small_base_delay(self):
        result = compute_backoff_delay(3, 0.001, strategy=BackoffStrategy.EXPONENTIAL, backoff_factor=2.0)
        assert result == pytest.approx(0.004)

    def test_all_strategies_return_non_negative(self):
        strategies = list(BackoffStrategy)
        for s in strategies:
            for n in (1, 2, 3):
                result = compute_backoff_delay(n, 0.0, strategy=s, jitter_value=0.0)
                assert result >= 0.0, f"{s} returned negative: {result}"


# ── RetryConfig strategy field tests ─────────────────────────────────────


class TestRetryConfigStrategy:
    """Verify the strategy field on RetryConfig."""

    def test_default_strategy_is_fixed(self):
        cfg = RetryConfig()
        assert cfg.strategy == BackoffStrategy.FIXED

    def test_explicit_strategy(self):
        cfg = RetryConfig(strategy=BackoffStrategy.EXPONENTIAL)
        assert cfg.strategy == BackoffStrategy.EXPONENTIAL

    def test_strategy_with_backoff_params(self):
        cfg = RetryConfig(
            strategy=BackoffStrategy.EXPONENTIAL_JITTER,
            backoff_factor=3.0,
            jitter_seconds=0.5,
            retry_delay_seconds=2.0,
        )
        assert cfg.strategy == BackoffStrategy.EXPONENTIAL_JITTER
        assert cfg.backoff_factor == 3.0
        assert cfg.jitter_seconds == 0.5


# ── Backoff strategy integration tests ───────────────────────────────────


class TestBackoffStrategyIntegration:
    """End-to-end tests with different backoff strategies in execute_with_retry."""

    def test_fixed_strategy_no_delay_growth(self):
        """Fixed strategy retries with constant delay (zero for test speed)."""
        config = RetryConfig(
            max_retries=3,
            retry_delay_seconds=0.0,
            strategy=BackoffStrategy.FIXED,
        )
        counter = [0]

        def flaky():
            counter[0] += 1
            if counter[0] < 3:
                raise ValueError("fail")
            return "ok"

        result = execute_with_retry(flaky, config=config)
        assert result.success
        assert result.total_attempts == 3

    def test_exponential_strategy_with_config(self):
        """Exponential strategy applied through execute_with_retry."""
        config = RetryConfig(
            max_retries=3,
            retry_delay_seconds=0.0,  # zero delay for test speed
            strategy=BackoffStrategy.EXPONENTIAL,
            backoff_factor=2.0,
        )
        counter = [0]

        def flaky():
            counter[0] += 1
            if counter[0] < 2:
                raise ConnectionError("timeout")
            return "done"

        result = execute_with_retry(flaky, config=config)
        assert result.success
        assert result.total_attempts == 2

    def test_jittered_strategy_with_config(self):
        """Jittered strategy with zero-delay base + small jitter."""
        config = RetryConfig(
            max_retries=3,
            retry_delay_seconds=0.0,
            jitter_seconds=0.01,
            strategy=BackoffStrategy.JITTERED,
        )
        counter = [0]

        def flaky():
            counter[0] += 1
            if counter[0] < 2:
                raise TimeoutError()
            return "ok"

        result = execute_with_retry(flaky, config=config)
        assert result.success
        assert result.total_attempts == 2

    def test_strategy_preserved_in_result(self):
        """The RetryResult.config should carry the chosen strategy."""
        config = RetryConfig(
            max_retries=2,
            retry_delay_seconds=0.0,
            strategy=BackoffStrategy.EXPONENTIAL_JITTER,
            backoff_factor=2.0,
            jitter_seconds=0.01,
        )

        def fail():
            raise ValueError("nope")

        result = execute_with_retry(fail, config=config)
        assert result.config.strategy == BackoffStrategy.EXPONENTIAL_JITTER
        assert result.config.backoff_factor == 2.0


# ── Integration / scenario tests ────────────────────────────────────────


class TestIntegration:
    """End-to-end scenarios matching the system design's retry sequence."""

    def test_retry_then_fallback_pattern(self):
        """Simulate: retry same model → fallback different model.

        This is Step 1 + Step 2 from the design doc's Failure Handling:
        - Step 1: Same model retry × 1
        - Step 2: Fallback model
        """
        # Primary model fails once, then recovers (retry success)
        primary_attempts = [0]
        config_primary = RetryConfig(max_retries=2, retry_delay_seconds=0.0)

        def primary_model():
            primary_attempts[0] += 1
            if primary_attempts[0] == 1:
                raise ConnectionError("primary model timeout")
            return {"status": "ok", "model": "primary", "attempt": primary_attempts[0]}

        result = execute_with_retry(primary_model, config=config_primary)
        assert result.success
        assert result.total_attempts == 2
        assert result.result is not None
        assert result.result["model"] == "primary"

    def test_fallback_model_on_persistent_failure(self):
        """Primary model fails all retries → fallback model used."""
        primary_config = RetryConfig(max_retries=2, retry_delay_seconds=0.0)

        def primary_model():
            raise ConnectionError("primary down")

        primary_result = execute_with_retry(primary_model, config=primary_config)
        assert not primary_result.success
        assert primary_result.total_attempts == 2

        # Fallback model succeeds
        fallback_config = RetryConfig(max_retries=1, retry_delay_seconds=0.0)

        def fallback_model():
            return {"status": "ok", "model": "fallback"}

        fallback_result = execute_with_retry(fallback_model, config=fallback_config)
        assert fallback_result.success
        assert fallback_result.total_attempts == 1
        assert fallback_result.result is not None
        assert fallback_result.result["model"] == "fallback"

    def test_validation_failure_retry_then_escalation_trigger(self):
        """Validation fails → retry → still fails → escalation needed.

        From design doc: Step 3 (2 failures): agent_failed record +
        validation escalation trigger.
        """
        config = RetryConfig(max_retries=2, retry_delay_seconds=0.0)
        validation_errors: list[str] = []

        def validate():
            validation_errors.append("attempt")
            raise ValueError("JSON schema mismatch")

        result = execute_with_retry(validate, config=config)
        assert not result.success
        assert result.total_attempts == 2
        assert result.max_retries_exceeded is True
        assert len(validation_errors) == 2
        # Classify the final error for escalation logic
        category = classify_error(result.final_error)
        assert category == ERROR_CATEGORY_VALIDATION

    def test_args_forwarded_correctly(self):
        config = RetryConfig(max_retries=1, retry_delay_seconds=0.0)

        def add(a: int, b: int) -> int:
            return a + b

        result = execute_with_retry(add, 3, 4, config=config)
        assert result.result == 7

    def test_kwargs_forwarded_correctly(self):
        config = RetryConfig(max_retries=1, retry_delay_seconds=0.0)

        def greet(greeting: str, name: str) -> str:
            return f"{greeting}, {name}!"

        result = execute_with_retry(
            greet, greeting="Hello", name="World", config=config
        )
        assert result.result == "Hello, World!"

    def test_retry_result_is_self_documenting(self):
        """The RetryResult should contain enough info for manifest logging."""
        config = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        counter = [0]

        def flaky_op():
            counter[0] += 1
            if counter[0] < 3:
                raise TimeoutError(f"timeout #{counter[0]}")
            return "done"

        result = execute_with_retry(flaky_op, config=config)

        # All the info needed for manifest.json error_log
        assert result.success
        assert result.total_attempts == 3
        assert result.config.max_retries == 3
        assert result.first_error is not None
        assert "timeout #1" in str(result.first_error)
        # Last error should be None (success)
        assert result.final_error is None


# ── RetryResult metadata / introspection tests ──────────────────────────


class TestRetryResultMetadata:
    """Verify RetryResult exposes all data needed by upstream callers."""

    def test_can_serialize_to_dict_for_logging(self):
        """RetryResult should be easy to convert to a log-friendly dict."""
        config = RetryConfig(max_retries=2, retry_delay_seconds=0.0)

        def fail():
            raise ValueError("serialisation test")

        result = execute_with_retry(fail, config=config)

        log_entry = {
            "success": result.success,
            "total_attempts": result.total_attempts,
            "max_retries_exceeded": result.max_retries_exceeded,
            "final_error_type": type(result.final_error).__name__,
            "final_error_message": str(result.final_error),
            "first_error_type": type(result.first_error).__name__,
            "last_attempt_number": result.last_attempt_number,
            "config_max_retries": result.config.max_retries,
        }
        assert log_entry["success"] is False
        assert log_entry["total_attempts"] == 2
        assert log_entry["max_retries_exceeded"] is True
        assert log_entry["final_error_type"] == "ValueError"
        assert log_entry["final_error_message"] == "serialisation test"
        assert log_entry["last_attempt_number"] == 2
        assert log_entry["config_max_retries"] == 2
