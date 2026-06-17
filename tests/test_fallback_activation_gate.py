"""Tests for the fallback activation gate (Sub-AC 3.2b).

Covers:
- All 8 RouterStatus values → correct GateDecision
- routing_reason correctness for each fallback status
- exit_condition correctness for rate-limit statuses
- RouterStatus helper properties (is_failure, is_rate_limit_related,
  triggers_fallback, triggers_pause)
- GateDecision enum values
- FallbackActivationResult convenience properties
- derive_router_status() with all priority-ordered scenarios
- derive_router_status() with None/empty inputs → SUCCESS
- Determinism: same status → same result
- TypeError on invalid status input
- Edge cases: unknown status defensive behavior
"""

from __future__ import annotations

import pytest

from src.fallback_activation_gate import (
    GateDecision,
    RouterStatus,
    FallbackActivationResult,
    derive_router_status,
    evaluate_fallback_activation,
)


# ═══════════════════════════════════════════════════════════════════════════
# RouterStatus enum tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterStatusEnum:
    """Test the RouterStatus enum values and helper properties."""

    def test_all_values_present(self):
        """All 8 status values must be defined."""
        expected = {
            "success",
            "timeout",
            "error",
            "unavailable",
            "parse_failure",
            "empty_response",
            "rate_limited",
            "quota_exhausted",
        }
        actual = {s.value for s in RouterStatus}
        assert actual == expected

    def test_success_is_not_failure(self):
        assert RouterStatus.SUCCESS.is_failure is False

    def test_all_non_success_are_failure(self):
        for status in RouterStatus:
            if status is RouterStatus.SUCCESS:
                continue
            assert status.is_failure, f"{status} should be a failure"

    def test_rate_limited_is_rate_limit_related(self):
        assert RouterStatus.RATE_LIMITED.is_rate_limit_related is True

    def test_quota_exhausted_is_rate_limit_related(self):
        assert RouterStatus.QUOTA_EXHAUSTED.is_rate_limit_related is True

    def test_non_rate_statuses_not_rate_related(self):
        non_rate = {
            RouterStatus.SUCCESS,
            RouterStatus.TIMEOUT,
            RouterStatus.ERROR,
            RouterStatus.UNAVAILABLE,
            RouterStatus.PARSE_FAILURE,
            RouterStatus.EMPTY_RESPONSE,
        }
        for status in non_rate:
            assert status.is_rate_limit_related is False, (
                f"{status} should not be rate-limit related"
            )

    def test_triggers_fallback_statuses(self):
        """Only the 5 fallback-triggering statuses return True."""
        fallback_statuses = {
            RouterStatus.TIMEOUT,
            RouterStatus.ERROR,
            RouterStatus.UNAVAILABLE,
            RouterStatus.PARSE_FAILURE,
            RouterStatus.EMPTY_RESPONSE,
        }
        for status in RouterStatus:
            expected = status in fallback_statuses
            assert status.triggers_fallback == expected, (
                f"{status}.triggers_fallback should be {expected}"
            )

    def test_triggers_pause_statuses(self):
        """Only RATE_LIMITED and QUOTA_EXHAUSTED trigger pause."""
        pause_statuses = {
            RouterStatus.RATE_LIMITED,
            RouterStatus.QUOTA_EXHAUSTED,
        }
        for status in RouterStatus:
            expected = status in pause_statuses
            assert status.triggers_pause == expected, (
                f"{status}.triggers_pause should be {expected}"
            )

    def test_str_value(self):
        """RouterStatus string values match the routing_rules.yaml convention."""
        assert str(RouterStatus.TIMEOUT.value) == "timeout"
        assert str(RouterStatus.UNAVAILABLE.value) == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
# GateDecision enum tests
# ═══════════════════════════════════════════════════════════════════════════


class TestGateDecisionEnum:
    """Test the GateDecision enum values."""

    def test_all_decisions_present(self):
        expected = {"use_primary", "activate_fallback", "pause_rate_limit"}
        actual = {d.value for d in GateDecision}
        assert actual == expected

    def test_decisions_are_strings(self):
        for decision in GateDecision:
            assert isinstance(decision.value, str)

    def test_decisions_are_exclusive(self):
        """Each decision value should be unique."""
        values = [d.value for d in GateDecision]
        assert len(values) == len(set(values))


# ═══════════════════════════════════════════════════════════════════════════
# Core gate decision tests — every RouterStatus → correct GateDecision
# ═══════════════════════════════════════════════════════════════════════════


class TestGateDecisions:
    """Test the core decision logic: RouterStatus → FallbackActivationResult."""

    # ── SUCCESS → USE_PRIMARY ──────────────────────────────────────────

    def test_success_uses_primary(self):
        result = evaluate_fallback_activation(RouterStatus.SUCCESS)
        assert result.decision == GateDecision.USE_PRIMARY
        assert result.should_use_primary is True
        assert result.should_use_fallback is False
        assert result.should_pause is False
        assert result.routing_reason == ""
        assert result.exit_condition == ""

    # ── TIMEOUT → ACTIVATE_FALLBACK ─────────────────────────────────────

    def test_timeout_activates_fallback(self):
        result = evaluate_fallback_activation(RouterStatus.TIMEOUT)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.should_use_fallback is True
        assert result.should_use_primary is False
        assert result.should_pause is False
        assert result.routing_reason == "qwen_timeout"
        assert result.exit_condition == ""

    # ── ERROR → ACTIVATE_FALLBACK ──────────────────────────────────────

    def test_error_activates_fallback(self):
        result = evaluate_fallback_activation(RouterStatus.ERROR)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_error"
        assert result.should_use_fallback is True

    # ── UNAVAILABLE → ACTIVATE_FALLBACK ─────────────────────────────────

    def test_unavailable_activates_fallback(self):
        result = evaluate_fallback_activation(RouterStatus.UNAVAILABLE)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "opencode_go_unavailable"
        assert result.should_use_fallback is True

    # ── PARSE_FAILURE → ACTIVATE_FALLBACK ──────────────────────────────

    def test_parse_failure_activates_fallback(self):
        result = evaluate_fallback_activation(RouterStatus.PARSE_FAILURE)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_parse_failure"
        assert result.should_use_fallback is True

    # ── EMPTY_RESPONSE → ACTIVATE_FALLBACK ─────────────────────────────

    def test_empty_response_activates_fallback(self):
        result = evaluate_fallback_activation(RouterStatus.EMPTY_RESPONSE)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_empty_response"
        assert result.should_use_fallback is True

    # ── RATE_LIMITED → PAUSE_RATE_LIMIT ────────────────────────────────

    def test_rate_limited_pauses(self):
        result = evaluate_fallback_activation(RouterStatus.RATE_LIMITED)
        assert result.decision == GateDecision.PAUSE_RATE_LIMIT
        assert result.should_pause is True
        assert result.should_use_primary is False
        assert result.should_use_fallback is False
        assert result.exit_condition == "rate_limit_paused"
        assert result.routing_reason == ""
        assert "pausing execution" in result.reason.lower()

    # ── QUOTA_EXHAUSTED → PAUSE_RATE_LIMIT ─────────────────────────────

    def test_quota_exhausted_pauses(self):
        result = evaluate_fallback_activation(RouterStatus.QUOTA_EXHAUSTED)
        assert result.decision == GateDecision.PAUSE_RATE_LIMIT
        assert result.should_pause is True
        assert result.exit_condition == "rate_limit_paused"


# ═══════════════════════════════════════════════════════════════════════════
# Full decision matrix test (table-driven)
# ═══════════════════════════════════════════════════════════════════════════


class TestDecisionMatrix:
    """Table-driven test of the complete RouterStatus → decision matrix."""

    DECISION_MATRIX = [
        # (status, expected_decision, expected_routing_reason, expected_exit_condition)
        (RouterStatus.SUCCESS, GateDecision.USE_PRIMARY, "", ""),
        (RouterStatus.TIMEOUT, GateDecision.ACTIVATE_FALLBACK, "qwen_timeout", ""),
        (RouterStatus.ERROR, GateDecision.ACTIVATE_FALLBACK, "qwen_error", ""),
        (
            RouterStatus.UNAVAILABLE,
            GateDecision.ACTIVATE_FALLBACK,
            "opencode_go_unavailable",
            "",
        ),
        (
            RouterStatus.PARSE_FAILURE,
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_parse_failure",
            "",
        ),
        (
            RouterStatus.EMPTY_RESPONSE,
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_empty_response",
            "",
        ),
        (
            RouterStatus.RATE_LIMITED,
            GateDecision.PAUSE_RATE_LIMIT,
            "",
            "rate_limit_paused",
        ),
        (
            RouterStatus.QUOTA_EXHAUSTED,
            GateDecision.PAUSE_RATE_LIMIT,
            "",
            "rate_limit_paused",
        ),
    ]

    @pytest.mark.parametrize(
        "status,expected_decision,expected_routing_reason,expected_exit_condition",
        DECISION_MATRIX,
    )
    def test_decision_matrix(
        self,
        status,
        expected_decision,
        expected_routing_reason,
        expected_exit_condition,
    ):
        result = evaluate_fallback_activation(status)
        assert result.decision == expected_decision, (
            f"{status} → expected {expected_decision}, got {result.decision}"
        )
        assert result.routing_reason == expected_routing_reason, (
            f"{status} → expected routing_reason '{expected_routing_reason}', "
            f"got '{result.routing_reason}'"
        )
        assert result.exit_condition == expected_exit_condition, (
            f"{status} → expected exit_condition '{expected_exit_condition}', "
            f"got '{result.exit_condition}'"
        )
        assert result.router_status == status


# ═══════════════════════════════════════════════════════════════════════════
# Error handling
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """Test error handling and defensive behavior."""

    def test_type_error_on_invalid_status(self):
        """Passing a non-RouterStatus should raise TypeError."""
        with pytest.raises(TypeError, match="RouterStatus"):
            evaluate_fallback_activation("success")  # type: ignore[arg-type]

    def test_type_error_on_none(self):
        with pytest.raises(TypeError):
            evaluate_fallback_activation(None)  # type: ignore[arg-type]

    def test_type_error_on_int(self):
        with pytest.raises(TypeError):
            evaluate_fallback_activation(1)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """The gate must be deterministic: same input → same output."""

    def test_repeated_calls_same_result(self):
        """Calling evaluate_fallback_activation twice with same status
        produces identical results."""
        for status in RouterStatus:
            result1 = evaluate_fallback_activation(status)
            result2 = evaluate_fallback_activation(status)
            assert result1 == result2, (
                f"{status} produced different results across calls"
            )

    def test_result_fields_consistent(self):
        """Every status must produce a result with all non-None fields."""
        for status in RouterStatus:
            result = evaluate_fallback_activation(status)
            assert result.decision is not None
            assert result.reason is not None
            assert result.router_status is not None
            assert result.routing_reason is not None
            assert result.exit_condition is not None


# ═══════════════════════════════════════════════════════════════════════════
# Result reason strings
# ═══════════════════════════════════════════════════════════════════════════


class TestReasonStrings:
    """Validate that each decision produces a meaningful reason string."""

    def test_use_primary_reason_is_descriptive(self):
        result = evaluate_fallback_activation(RouterStatus.SUCCESS)
        assert len(result.reason) > 10
        assert "primary" in result.reason.lower()

    def test_fallback_reasons_mention_fallback(self):
        for status in (
            RouterStatus.TIMEOUT,
            RouterStatus.ERROR,
            RouterStatus.UNAVAILABLE,
            RouterStatus.PARSE_FAILURE,
            RouterStatus.EMPTY_RESPONSE,
        ):
            result = evaluate_fallback_activation(status)
            assert "fallback" in result.reason.lower(), (
                f"{status} reason should mention fallback: {result.reason}"
            )

    def test_pause_reasons_mention_pausing(self):
        for status in (RouterStatus.RATE_LIMITED, RouterStatus.QUOTA_EXHAUSTED):
            result = evaluate_fallback_activation(status)
            assert "pausing" in result.reason.lower(), (
                f"{status} reason should mention pausing: {result.reason}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# FallbackActivationResult convenience properties
# ═══════════════════════════════════════════════════════════════════════════


class TestResultProperties:
    """Convenience properties should be consistent with decision."""

    def test_mutual_exclusivity(self):
        """Exactly one convenience property should be True."""
        for status in RouterStatus:
            result = evaluate_fallback_activation(status)
            truths = sum(
                [
                    result.should_use_primary,
                    result.should_use_fallback,
                    result.should_pause,
                ]
            )
            assert truths == 1, (
                f"{status}: expected exactly 1 True, got {truths}"
            )

    def test_should_use_primary_only_for_success(self):
        for status in RouterStatus:
            result = evaluate_fallback_activation(status)
            expected = status == RouterStatus.SUCCESS
            assert result.should_use_primary == expected, (
                f"{status}: should_use_primary should be {expected}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# derive_router_status() tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveRouterStatus:
    """Test the helper that derives RouterStatus from pipeline diagnostics."""

    # ── SUCCESS scenarios ──────────────────────────────────────────────

    def test_derive_success_clean(self):
        """All good → SUCCESS."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="pass",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_success_conditional_pass(self):
        """Conditional pass is still a success."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="conditional_pass",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_success_revision_required(self):
        """Revision_required is a partial success — still usable."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="revision_required",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_success_all_defaults(self):
        """No arguments → SUCCESS (default state)."""
        status = derive_router_status()
        assert status == RouterStatus.SUCCESS

    # ── QUOTA_EXHAUSTED (highest priority) ─────────────────────────────

    def test_derive_quota_exhausted_wins_over_all(self):
        """Quota exhausted takes priority over everything else."""
        status = derive_router_status(
            quota_exhausted=True,
            cli_success=True,
            validation_verdict="pass",
            is_rate_limited=True,  # quota beats even rate-limit
        )
        assert status == RouterStatus.QUOTA_EXHAUSTED

    def test_derive_quota_exhausted_alone(self):
        status = derive_router_status(quota_exhausted=True)
        assert status == RouterStatus.QUOTA_EXHAUSTED

    # ── RATE_LIMITED ───────────────────────────────────────────────────

    def test_derive_rate_limited_flag(self):
        status = derive_router_status(is_rate_limited=True)
        assert status == RouterStatus.RATE_LIMITED

    def test_derive_rate_limited_from_exit_condition(self):
        """exit_condition='rate_limit_paused' should derive RATE_LIMITED."""
        status = derive_router_status(
            cli_success=True,
            exit_condition="rate_limit_paused",
        )
        assert status == RouterStatus.RATE_LIMITED

    def test_derive_rate_limited_beats_timeout(self):
        """Rate-limited beats timeout in priority order."""
        status = derive_router_status(
            is_rate_limited=True,
            cli_timeout=True,
        )
        assert status == RouterStatus.RATE_LIMITED

    # ── UNAVAILABLE ────────────────────────────────────────────────────

    def test_derive_unavailable(self):
        status = derive_router_status(cli_unavailable=True)
        assert status == RouterStatus.UNAVAILABLE

    def test_derive_unavailable_beats_timeout(self):
        status = derive_router_status(
            cli_unavailable=True,
            cli_timeout=True,
        )
        assert status == RouterStatus.UNAVAILABLE

    # ── TIMEOUT ────────────────────────────────────────────────────────

    def test_derive_timeout(self):
        status = derive_router_status(cli_timeout=True)
        assert status == RouterStatus.TIMEOUT

    def test_derive_timeout_beats_error(self):
        """Timeout takes priority over general error."""
        status = derive_router_status(
            cli_timeout=True,
            cli_success=False,
        )
        assert status == RouterStatus.TIMEOUT

    # ── ERROR ──────────────────────────────────────────────────────────

    def test_derive_error_non_zero_exit(self):
        """cli_success=False → ERROR."""
        status = derive_router_status(cli_success=False)
        assert status == RouterStatus.ERROR

    def test_derive_error_beats_empty_stdout(self):
        """CLI error beats empty stdout."""
        status = derive_router_status(
            cli_success=False,
            stdout_empty=True,
        )
        assert status == RouterStatus.ERROR

    # ── EMPTY_RESPONSE ─────────────────────────────────────────────────

    def test_derive_empty_response(self):
        status = derive_router_status(
            cli_success=True,
            stdout_empty=True,
        )
        assert status == RouterStatus.EMPTY_RESPONSE

    def test_derive_empty_response_beats_parse_failure(self):
        """Empty response beats parse failure."""
        status = derive_router_status(
            cli_success=True,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.EMPTY_RESPONSE

    # ── PARSE_FAILURE ──────────────────────────────────────────────────

    def test_derive_parse_failure_verdict_fail(self):
        status = derive_router_status(
            cli_success=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.PARSE_FAILURE

    def test_derive_parse_failure_verdict_escalate(self):
        status = derive_router_status(
            cli_success=True,
            validation_verdict="escalate",
        )
        assert status == RouterStatus.PARSE_FAILURE

    def test_derive_parse_failure_requires_cli_success(self):
        """If CLI failed, we get ERROR not PARSE_FAILURE (priority order)."""
        status = derive_router_status(
            cli_success=False,
            validation_verdict="fail",
        )
        assert status == RouterStatus.ERROR

    # ── Priority order comprehensive tests ─────────────────────────────

    def test_priority_order_quota_first(self):
        """Quota check is #1 — everything else ignored."""
        status = derive_router_status(
            quota_exhausted=True,
            is_rate_limited=True,
            cli_unavailable=True,
            cli_timeout=True,
            cli_success=False,
            stdout_empty=True,
            validation_verdict="fail",
            exit_condition="rate_limit_paused",
        )
        assert status == RouterStatus.QUOTA_EXHAUSTED

    def test_priority_order_rate_second(self):
        """Rate limit is #2 — beats everything except quota."""
        status = derive_router_status(
            is_rate_limited=True,
            cli_unavailable=True,
            cli_timeout=True,
            cli_success=False,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.RATE_LIMITED

    def test_priority_order_unavailable_third(self):
        """Unavailable is #3."""
        status = derive_router_status(
            cli_unavailable=True,
            cli_timeout=True,
            cli_success=False,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.UNAVAILABLE

    def test_priority_order_timeout_fourth(self):
        """Timeout is #4."""
        status = derive_router_status(
            cli_timeout=True,
            cli_success=False,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.TIMEOUT

    def test_priority_order_error_fifth(self):
        """Error is #5."""
        status = derive_router_status(
            cli_success=False,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.ERROR

    def test_priority_order_empty_response_sixth(self):
        """Empty response is #6."""
        status = derive_router_status(
            cli_success=True,
            stdout_empty=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.EMPTY_RESPONSE

    def test_priority_order_parse_failure_seventh(self):
        """Parse failure is #7 — lowest non-success."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="fail",
        )
        assert status == RouterStatus.PARSE_FAILURE

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_derive_cli_exit_code_alone_does_not_affect(self):
        """cli_exit_code is informational — cli_success drives ERROR."""
        status = derive_router_status(
            cli_exit_code=1,
            cli_success=True,  # explicit success overrides
            validation_verdict="pass",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_cli_success_none_treated_as_not_false(self):
        """None cli_success is not treated as failure."""
        status = derive_router_status(
            cli_success=None,
            validation_verdict="pass",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_validation_verdict_none_is_not_fail(self):
        status = derive_router_status(
            cli_success=True,
            validation_verdict=None,
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_validation_verdict_empty_string_not_fail(self):
        status = derive_router_status(
            cli_success=True,
            validation_verdict="",
        )
        assert status == RouterStatus.SUCCESS

    def test_derive_validation_verdict_pass_explicit(self):
        status = derive_router_status(
            cli_success=True,
            validation_verdict="pass",
        )
        assert status == RouterStatus.SUCCESS


# ═══════════════════════════════════════════════════════════════════════════
# Integration-style: derive → evaluate pipeline
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveThenEvaluatePipeline:
    """Test the full derive_router_status → evaluate_fallback_activation flow."""

    PIPELINE_CASES = [
        # (derive_kwargs, expected_decision, expected_routing_reason)
        (
            {"cli_success": True, "validation_verdict": "pass"},
            GateDecision.USE_PRIMARY,
            "",
        ),
        (
            {"cli_timeout": True},
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_timeout",
        ),
        (
            {"cli_success": False},
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_error",
        ),
        (
            {"cli_unavailable": True},
            GateDecision.ACTIVATE_FALLBACK,
            "opencode_go_unavailable",
        ),
        (
            {"cli_success": True, "validation_verdict": "fail"},
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_parse_failure",
        ),
        (
            {"cli_success": True, "stdout_empty": True},
            GateDecision.ACTIVATE_FALLBACK,
            "qwen_empty_response",
        ),
        (
            {"is_rate_limited": True},
            GateDecision.PAUSE_RATE_LIMIT,
            "",
        ),
        (
            {"quota_exhausted": True},
            GateDecision.PAUSE_RATE_LIMIT,
            "",
        ),
    ]

    @pytest.mark.parametrize(
        "derive_kwargs,expected_decision,expected_routing_reason",
        PIPELINE_CASES,
    )
    def test_pipeline(
        self, derive_kwargs, expected_decision, expected_routing_reason
    ):
        status = derive_router_status(**derive_kwargs)
        result = evaluate_fallback_activation(status)
        assert result.decision == expected_decision
        assert result.routing_reason == expected_routing_reason


# ═══════════════════════════════════════════════════════════════════════════
# Integration: simulation of classify() failure modes
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifyFailureModeSimulation:
    """Simulate each classify() failure path to ensure correct routing."""

    def test_simulate_qwen_success(self):
        """classify() returned valid ClassificationResult."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="pass",
        )
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.USE_PRIMARY

    def test_simulate_opencode_timeout(self):
        """classify() -> OpencodeCallResult with timeout_occurred=True."""
        status = derive_router_status(cli_timeout=True)
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_timeout"

    def test_simulate_opencode_error(self):
        """classify() -> OpencodeCallResult with success=False, exit_code=1."""
        status = derive_router_status(cli_success=False)
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_error"

    def test_simulate_opencode_not_installed(self):
        """classify() -> FileNotFoundError for opencode-go."""
        status = derive_router_status(cli_unavailable=True)
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "opencode_go_unavailable"

    def test_simulate_qwen_empty_response(self):
        """classify() -> CLI succeeded but stdout is empty string."""
        status = derive_router_status(
            cli_success=True,
            stdout_empty=True,
        )
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_empty_response"

    def test_simulate_qwen_malformed_json(self):
        """classify() -> CLI succeeded but parse_response returned fail."""
        status = derive_router_status(
            cli_success=True,
            validation_verdict="fail",
        )
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.ACTIVATE_FALLBACK
        assert result.routing_reason == "qwen_parse_failure"

    def test_simulate_quota_exhausted_pre_call(self):
        """classify() -> guard_llm_call returned can_proceed=False."""
        status = derive_router_status(quota_exhausted=True)
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.PAUSE_RATE_LIMIT
        assert result.exit_condition == "rate_limit_paused"

    def test_simulate_rate_limit_429_after_retry(self):
        """classify() -> 429 error, backoff retry also failed."""
        status = derive_router_status(is_rate_limited=True)
        result = evaluate_fallback_activation(status)
        assert result.decision == GateDecision.PAUSE_RATE_LIMIT
        assert result.exit_condition == "rate_limit_paused"


# ═══════════════════════════════════════════════════════════════════════════
# FallbackActivationResult immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestResultImmutability:
    """FallbackActivationResult must be frozen (immutable)."""

    def test_result_is_frozen(self):
        result = evaluate_fallback_activation(RouterStatus.SUCCESS)
        with pytest.raises(Exception):
            result.decision = GateDecision.ACTIVATE_FALLBACK  # type: ignore[misc]

    def test_result_hashable(self):
        """Frozen dataclasses should be hashable."""
        result = evaluate_fallback_activation(RouterStatus.TIMEOUT)
        # If it's hashable, this won't raise
        _ = {result: "test"}
