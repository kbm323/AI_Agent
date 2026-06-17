"""Tests for the rate-limit guard module.

Verifies:
- Quota check parsing from CLI output
- Rate-limit error detection in stderr/stdout
- Backoff and retry logic
- Integration with classify() pipeline (rate-limit pause results)
- Exit condition propagation through ClassificationResult

Uses inject_quota_checker() to mock real CLI calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.rate_limit_guard import (
    BACKOFF_WAIT_SECONDS,
    QUOTA_THRESHOLD_PERCENT,
    QuotaGuardResult,
    RateLimitStatus,
    _parse_quota_output,
    guard_llm_call,
    handle_rate_limit,
    inject_quota_checker,
    is_rate_limit_error,
    is_rate_limit_error_in_stdout,
)
from src.response_parser import ClassificationResult


# ═════════════════════════════════════════════════════════════════════════
# Mock quota checker (avoids real CLI calls in tests)
# ═════════════════════════════════════════════════════════════════════════


def _mock_quota_ok() -> RateLimitStatus:
    """Mock quota checker that always returns available."""
    return RateLimitStatus(
        available=True,
        remaining_percent=85.0,
        reason="mock: quota OK",
        quota_checked=True,
    )


def _mock_quota_exhausted() -> RateLimitStatus:
    """Mock quota checker that returns below threshold."""
    return RateLimitStatus(
        available=False,
        remaining_percent=3.0,
        reason="mock: quota exhausted (3.0%)",
        quota_checked=True,
    )


@pytest.fixture(autouse=True)
def _reset_quota_checker() -> None:
    """Reset the quota checker to default after each test."""
    yield
    inject_quota_checker(None)


# ═════════════════════════════════════════════════════════════════════════
# Rate-limit error detection tests
# ═════════════════════════════════════════════════════════════════════════


class TestRateLimitDetection:
    """Verify is_rate_limit_error detects rate-limit signals."""

    def test_detects_429_status(self) -> None:
        assert is_rate_limit_error("HTTP 429 Too Many Requests") is True

    def test_detects_rate_limit_keyword(self) -> None:
        assert is_rate_limit_error(
            "Rate limit exceeded for model qwen-max"
        ) is True

    def test_detects_quota_exceeded(self) -> None:
        assert is_rate_limit_error(
            "Error: quota exceeded for this billing period"
        ) is True

    def test_detects_too_many_requests(self) -> None:
        assert is_rate_limit_error(
            "Too many requests. Please try again later."
        ) is True

    def test_detects_request_limit_reached(self) -> None:
        assert is_rate_limit_error(
            "Request limit reached for today"
        ) is True

    def test_detects_api_rate_limit(self) -> None:
        assert is_rate_limit_error(
            "API rate limit has been reached"
        ) is True

    def test_detects_insufficient_quota(self) -> None:
        assert is_rate_limit_error(
            "insufficient_quota: you have 0 tokens remaining"
        ) is True

    def test_detects_billing_limit(self) -> None:
        assert is_rate_limit_error(
            "billing limit reached — upgrade your plan"
        ) is True

    def test_no_false_positive_on_normal_error(self) -> None:
        assert is_rate_limit_error("Model not found: qwen-max") is False

    def test_no_false_positive_on_timeout(self) -> None:
        assert is_rate_limit_error(
            "subprocess.TimeoutExpired: timed out after 120s"
        ) is False

    def test_empty_stderr_returns_false(self) -> None:
        assert is_rate_limit_error("") is False

    def test_stdout_detection(self) -> None:
        assert (
            is_rate_limit_error_in_stdout(
                '{"error": "rate limit exceeded"}'
            )
            is True
        )

    def test_stdout_no_false_positive(self) -> None:
        assert (
            is_rate_limit_error_in_stdout(
                '{"agenda_type": "creative_production"}'
            )
            is False
        )


# ═════════════════════════════════════════════════════════════════════════
# Quota output parsing tests
# ═════════════════════════════════════════════════════════════════════════


class TestQuotaOutputParsing:
    """Verify _parse_quota_output extracts remaining percentage."""

    def test_parses_remaining_percent(self) -> None:
        percent, parsed = _parse_quota_output(
            "quota remaining: 45.5%", ""
        )
        assert parsed is True
        assert percent == 45.5

    def test_parses_percent_remaining(self) -> None:
        percent, parsed = _parse_quota_output("75% remaining", "")
        assert parsed is True
        assert percent == 75.0

    def test_parses_token_ratio(self) -> None:
        percent, parsed = _parse_quota_output(
            "Usage: 7500 / 10000 tokens", ""
        )
        assert parsed is True
        assert percent == 25.0

    def test_fallback_on_unparseable(self) -> None:
        percent, parsed = _parse_quota_output(
            "Some unrecognised output", ""
        )
        assert parsed is False
        assert percent == 100.0

    def test_clamps_to_0_100_range(self) -> None:
        percent, parsed = _parse_quota_output("remaining: 150%", "")
        assert parsed is True
        assert percent == 100.0

        # Negative values don't match any regex pattern — falls back to 100.0
        # (safe default: assume available when parsing fails)
        percent2, parsed2 = _parse_quota_output("remaining: -5%", "")
        assert parsed2 is False  # can't parse negative
        assert percent2 == 100.0  # safe default


# ═════════════════════════════════════════════════════════════════════════
# Quota guard tests
# ═════════════════════════════════════════════════════════════════════════


class TestQuotaGuard:
    """Verify guard_llm_call returns correct go/no-go decisions."""

    def test_guard_can_proceed(self) -> None:
        inject_quota_checker(_mock_quota_ok)
        result = guard_llm_call()
        assert result.can_proceed is True
        assert result.exit_condition == ""
        assert result.rate_limit_status is not None
        assert result.rate_limit_status.remaining_percent == 85.0

    def test_guard_blocks_when_exhausted(self) -> None:
        inject_quota_checker(_mock_quota_exhausted)
        result = guard_llm_call()
        assert result.can_proceed is False
        assert result.exit_condition == "rate_limit_paused"
        assert "3.0%" in result.reason


class TestRateLimitStatus:
    """Verify RateLimitStatus dataclass."""

    def test_available_when_above_threshold(self) -> None:
        status = RateLimitStatus(
            available=True, remaining_percent=50.0, reason="OK"
        )
        assert status.is_below_threshold is False

    def test_below_threshold_when_low(self) -> None:
        status = RateLimitStatus(
            available=False, remaining_percent=5.0, reason="Low quota"
        )
        assert status.is_below_threshold is True

    def test_exactly_at_threshold_not_below(self) -> None:
        status = RateLimitStatus(
            available=True,
            remaining_percent=QUOTA_THRESHOLD_PERCENT,
            reason="Edge",
        )
        assert status.is_below_threshold is False

    def test_frozen_dataclass(self) -> None:
        status = RateLimitStatus(available=True, remaining_percent=50.0)
        with pytest.raises(Exception):
            status.available = False  # type: ignore[misc]


class TestQuotaGuardResult:
    """Verify QuotaGuardResult dataclass."""

    def test_can_proceed_result(self) -> None:
        result = QuotaGuardResult(
            can_proceed=True, exit_condition="", reason="quota OK"
        )
        assert result.can_proceed is True
        assert result.exit_condition == ""

    def test_rate_limit_paused_result(self) -> None:
        result = QuotaGuardResult(
            can_proceed=False,
            exit_condition="rate_limit_paused",
            reason="quota at 5.0%",
        )
        assert result.can_proceed is False
        assert result.exit_condition == "rate_limit_paused"


# ═════════════════════════════════════════════════════════════════════════
# ClassificationResult exit_condition tests
# ═════════════════════════════════════════════════════════════════════════


class TestClassificationResultExitCondition:
    """Verify exit_condition propagation through ClassificationResult."""

    def test_default_exit_condition_empty(self) -> None:
        result = ClassificationResult(
            agenda_type="general_planning",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",
            confidence=1.0,
            reasoning="",
            validation_score=1.0,
            validation_verdict="pass",
            validator_required=False,
            codex_required=False,
        )
        assert result.exit_condition == ""
        assert result.is_rate_limited is False

    def test_rate_limit_exit_condition(self) -> None:
        result = ClassificationResult(
            agenda_type="general_planning",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",
            confidence=0.0,
            reasoning="Rate limit paused",
            validation_score=0.0,
            validation_verdict="fail",
            validator_required=True,
            codex_required=False,
            exit_condition="rate_limit_paused",
        )
        assert result.exit_condition == "rate_limit_paused"
        assert result.is_rate_limited is True

    def test_exit_condition_preserved_through_parse(self) -> None:
        """Exit condition empty string is the default for parse_response."""
        from src.response_parser import parse_response

        valid_json = json.dumps({
            "agenda_type": "creative_production",
            "tags": ["test"],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": True,
            "codex_required": False,
            "confidence": 0.9,
            "reasoning": "OK",
        })
        result = parse_response(valid_json)
        assert result.exit_condition == ""
        assert result.is_rate_limited is False


# ═════════════════════════════════════════════════════════════════════════
# Backoff/retry tests (mock-based)
# ═════════════════════════════════════════════════════════════════════════


class TestBackoffRetry:
    """Verify handle_rate_limit backoff and retry behaviour."""

    def test_retry_succeeds_on_first_attempt(self) -> None:
        """When the retry call succeeds, the result is returned."""

        class FakeResult:
            def __init__(self, success: bool, stderr: str = ""):
                self.success = success
                self.stderr = stderr

        call_count = [0]

        def mock_runner(config: Any) -> FakeResult:
            call_count[0] += 1
            if call_count[0] == 1:
                return FakeResult(False, "429 rate limit")
            return FakeResult(True, "")

        result = handle_rate_limit(
            mock_runner,
            config=None,
            max_retries=2,
            backoff_seconds=0.0,
        )
        assert result is not None
        assert result.success is True
        assert call_count[0] == 2

    def test_retry_exhausted_returns_none(self) -> None:
        """When all retries fail, None is returned."""

        class FakeResult:
            def __init__(self, stderr: str = ""):
                self.success = False
                self.stderr = stderr

        call_count = [0]

        def mock_runner(config: Any) -> FakeResult:
            call_count[0] += 1
            return FakeResult("429 rate limit exceeded")

        result = handle_rate_limit(
            mock_runner,
            config=None,
            max_retries=2,
            backoff_seconds=0.0,
        )
        assert result is None
        # handle_rate_limit does retries only (not original call).
        # max_retries=2 → 2 calls.
        assert call_count[0] == 2

    def test_non_rate_limit_error_not_retried(self) -> None:
        """Non-rate-limit errors are returned as-is without retry."""

        class FakeResult:
            def __init__(self, success: bool, stderr: str = ""):
                self.success = success
                self.stderr = stderr

        call_count = [0]

        def mock_runner(config: Any) -> FakeResult:
            call_count[0] += 1
            return FakeResult(False, "Model not found")

        result = handle_rate_limit(
            mock_runner,
            config=None,
            max_retries=2,
            backoff_seconds=0.0,
        )
        assert result is not None
        assert result.success is False
        assert call_count[0] == 1


# ═════════════════════════════════════════════════════════════════════════
# Classify pipeline rate-limit integration tests
# ═════════════════════════════════════════════════════════════════════════


class TestClassifyRateLimitIntegration:
    """Verify classify() returns rate_limit_paused on quota/rate-limit conditions."""

    SAMPLE_TOPIC = "신규 캐릭터 '루나'의 비주얼 디자인 회의"

    def test_classify_rate_limit_paused_on_quota_exhausted(self) -> None:
        """When quota is exhausted, classify returns rate_limit_paused."""
        from src.classify import classify

        inject_quota_checker(_mock_quota_exhausted)

        # This mock runner should never be called because quota guard blocks
        def mock_runner(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
            pytest.fail("Runner should not be called when quota exhausted")

        result = classify(
            self.SAMPLE_TOPIC,
            _injected_runner=mock_runner,
        )
        assert result.exit_condition == "rate_limit_paused"
        assert result.is_rate_limited is True
        assert result.is_valid is False

    def test_classify_rate_limit_paused_on_429_retry_fail(self) -> None:
        """When CLI returns 429 and retry fails, rate_limit_paused."""
        from src.classify import classify

        inject_quota_checker(_mock_quota_ok)

        def mock_rate_limited_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (-1, "", "429 rate limit exceeded — quota exhausted")

        result = classify(
            self.SAMPLE_TOPIC,
            _injected_runner=mock_rate_limited_runner,
        )
        assert result.exit_condition == "rate_limit_paused"
        assert result.is_rate_limited is True

    def test_classify_success_has_no_exit_condition(self) -> None:
        """Normal successful classification has empty exit_condition."""
        from src.classify import classify

        inject_quota_checker(_mock_quota_ok)

        valid_json = json.dumps({
            "agenda_type": "creative_production",
            "tags": ["test"],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.9,
            "reasoning": "OK",
        })

        def mock_success_runner(
            command: list[str],
            timeout_seconds: float,
            env: dict[str, str] | None,
            workdir: str | None,
        ) -> tuple[int, str, str]:
            return (0, valid_json, "")

        result = classify(
            self.SAMPLE_TOPIC,
            _injected_runner=mock_success_runner,
        )
        assert result.exit_condition == ""
        assert result.is_rate_limited is False
        assert result.is_valid is True
