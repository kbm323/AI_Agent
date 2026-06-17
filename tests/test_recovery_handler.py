"""Tests for the recovery handler module (Sub-AC 4.3.4).

Covers:
- ExhaustedRetryState creation, validation, and computed properties
- RecoveryAction enum values
- RecoveryResult computed properties and as_log_dict()
- classify_for_recovery decision matrix (all error categories × fallback states)
- Default requeue/escalate/dead_letter handler behaviour
- execute_recovery with mock handlers for each action
- execute_recovery exception handling when a handler raises
- exhausted_state_from_result convenience constructor
- Integration: end-to-end retry-exhausted → classify → recovery action
- Edge cases: unknown error category, missing handler, empty metadata
"""

from __future__ import annotations

import pytest

from src.recovery_handler import (
    ExhaustedRetryState,
    RecoveryAction,
    RecoveryActionHandler,
    RecoveryResult,
    classify_for_recovery,
    default_dead_letter_handler,
    default_escalate_handler,
    default_requeue_handler,
    execute_recovery,
    exhausted_state_from_result,
)
from src.retry_executor import (
    ERROR_CATEGORY_MODEL,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_PERMANENT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_TRANSIENT,
    ERROR_CATEGORY_UNKNOWN,
    ERROR_CATEGORY_VALIDATION,
    RetryAttempt,
    RetryConfig,
    RetryResult,
    classify_error,
)


# ── Helper: build exhausted RetryResult quickly ─────────────────────────


def _make_result(
    error: Exception,
    max_retries: int = 3,
) -> RetryResult[None]:
    """Build a RetryResult that represents an exhausted retry sequence."""
    cfg = RetryConfig(max_retries=max_retries, retry_delay_seconds=0.0)
    attempts = tuple(
        RetryAttempt(
            attempt_number=i,
            error=error,
            start_time=float(i),
            end_time=float(i) + 0.01,
        )
        for i in range(1, max_retries + 1)
    )
    return RetryResult(
        success=False,
        attempts=attempts,
        max_retries_exceeded=True,
        final_error=error,
        result=None,
        config=cfg,
    )


def _make_state(
    error: Exception,
    *,
    operation_name: str = "test-op",
    meeting_id: str | None = None,
    max_retries: int = 3,
    fallback_attempted: bool = False,
    quorum_reassessed: bool = False,
    metadata: dict[str, str] | None = None,
) -> ExhaustedRetryState:
    """Build an ExhaustedRetryState for testing."""
    rr = _make_result(error, max_retries)
    return ExhaustedRetryState(
        retry_result=rr,
        operation_name=operation_name,
        error_category=classify_error(error),
        max_retries=max_retries,
        meeting_id=meeting_id,
        fallback_attempted=fallback_attempted,
        quorum_reassessed=quorum_reassessed,
        metadata=metadata or {},
    )


# ── RecoveryAction tests ────────────────────────────────────────────────


class TestRecoveryAction:
    """Verify RecoveryAction enum values and behaviour."""

    def test_enum_values(self):
        assert RecoveryAction.REQUEUE == "requeue"
        assert RecoveryAction.ESCALATE == "escalate"
        assert RecoveryAction.DEAD_LETTER == "dead_letter"

    def test_is_string_subclass(self):
        assert isinstance(RecoveryAction.REQUEUE, str)
        assert RecoveryAction.ESCALATE.upper() == "ESCALATE"

    def test_three_values(self):
        values = list(RecoveryAction)
        assert len(values) == 3
        assert RecoveryAction.REQUEUE in values
        assert RecoveryAction.ESCALATE in values
        assert RecoveryAction.DEAD_LETTER in values


# ── ExhaustedRetryState tests ───────────────────────────────────────────


class TestExhaustedRetryState:
    """Verify ExhaustedRetryState creation, validation, and properties."""

    def test_creation_with_transient_error(self):
        state = _make_state(ConnectionError("refused"))
        assert state.operation_name == "test-op"
        assert state.error_category == ERROR_CATEGORY_NETWORK
        assert state.max_retries == 3
        assert state.fallback_attempted is False
        assert state.quorum_reassessed is False
        assert state.meeting_id is None

    def test_creation_with_meeting_id(self):
        state = _make_state(TimeoutError(), meeting_id="m-abc-123")
        assert state.meeting_id == "m-abc-123"

    def test_creation_with_metadata(self):
        state = _make_state(
            ValueError("bad"),
            metadata={"role_id": "strategist", "round": "2"},
        )
        assert state.metadata == {"role_id": "strategist", "round": "2"}

    def test_is_frozen(self):
        state = _make_state(ConnectionError())
        with pytest.raises(Exception):
            state.error_category = "changed"  # type: ignore[misc]

    def test_final_error_message_with_error(self):
        state = _make_state(ValueError("specific failure"))
        assert state.final_error_message == "specific failure"

    def test_final_error_message_without_error(self):
        """Edge case: final_error is None but max_retries_exceeded=True."""
        cfg = RetryConfig(max_retries=3)
        rr = RetryResult(
            success=False,
            attempts=(
                RetryAttempt(
                    attempt_number=1, error=Exception("only"),
                    start_time=1.0, end_time=1.01,
                ),
            ),
            max_retries_exceeded=True,
            final_error=None,
            result=None,
            config=cfg,
        )
        state = ExhaustedRetryState(
            retry_result=rr,
            operation_name="edge-case",
            error_category=ERROR_CATEGORY_UNKNOWN,
            max_retries=3,
        )
        assert state.final_error_message == "unknown error"

    def test_total_attempts(self):
        state = _make_state(TimeoutError(), max_retries=5)
        assert state.total_attempts == 5

    def test_unknown_category_logs_warning(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        cfg = RetryConfig(max_retries=3)
        rr = _make_result(ValueError("test"))
        ExhaustedRetryState(
            retry_result=rr,
            operation_name="test",
            error_category="invalid-category",
            max_retries=3,
        )
        assert "Unknown error_category" in caplog.text


# ── RecoveryResult tests ────────────────────────────────────────────────


class TestRecoveryResult:
    """Verify RecoveryResult creation, properties, and serialization."""

    def test_successful_requeue(self):
        result = RecoveryResult(
            action=RecoveryAction.REQUEUE,
            success=True,
            message="Requeued successfully",
        )
        assert result.action == RecoveryAction.REQUEUE
        assert result.success is True
        assert result.is_requeue is True
        assert result.is_escalate is False
        assert result.is_dead_letter is False

    def test_successful_escalate(self):
        result = RecoveryResult(
            action=RecoveryAction.ESCALATE,
            success=True,
            message="Escalated to human",
        )
        assert result.is_requeue is False
        assert result.is_escalate is True
        assert result.is_dead_letter is False

    def test_successful_dead_letter(self):
        result = RecoveryResult(
            action=RecoveryAction.DEAD_LETTER,
            success=True,
            message="Moved to dead-letter",
        )
        assert result.is_requeue is False
        assert result.is_escalate is False
        assert result.is_dead_letter is True

    def test_failed_with_error(self):
        exc = RuntimeError("handler crashed")
        result = RecoveryResult(
            action=RecoveryAction.ESCALATE,
            success=False,
            message="Handler failed",
            error=exc,
        )
        assert result.success is False
        assert result.error is exc
        assert str(result.error) == "handler crashed"

    def test_as_log_dict_success(self):
        result = RecoveryResult(
            action=RecoveryAction.REQUEUE,
            success=True,
            message="OK",
            details={"op": "worker-1"},
        )
        d = result.as_log_dict()
        assert d["recovery_action"] == "requeue"
        assert d["recovery_success"] is True
        assert d["recovery_message"] == "OK"
        assert d["recovery_details"] == {"op": "worker-1"}

    def test_as_log_dict_with_error(self):
        result = RecoveryResult(
            action=RecoveryAction.ESCALATE,
            success=False,
            message="fail",
            error=ValueError("bad"),
        )
        d = result.as_log_dict()
        assert d["recovery_success"] is False
        assert d["recovery_error"] == "bad"

    def test_empty_details_not_in_log_dict(self):
        result = RecoveryResult(
            action=RecoveryAction.DEAD_LETTER,
            success=True,
            message="done",
        )
        d = result.as_log_dict()
        assert "recovery_details" not in d

    def test_empty_error_not_in_log_dict(self):
        result = RecoveryResult(
            action=RecoveryAction.REQUEUE,
            success=True,
            message="ok",
        )
        d = result.as_log_dict()
        assert "recovery_error" not in d


# ── classify_for_recovery tests ─────────────────────────────────────────


class TestClassifyForRecovery:
    """Verify the decision matrix for classify_for_recovery."""

    # ── Permanent errors → always DEAD_LETTER ────────────────────────

    def test_permanent_no_fallback(self):
        state = _make_state(FileNotFoundError("missing"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.DEAD_LETTER

    def test_permanent_with_fallback(self):
        state = _make_state(FileNotFoundError("missing"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.DEAD_LETTER

    def test_permanent_with_quorum(self):
        state = _make_state(
            FileNotFoundError("missing"),
            fallback_attempted=True,
            quorum_reassessed=True,
        )
        assert classify_for_recovery(state) == RecoveryAction.DEAD_LETTER

    def test_permanent_permission_error(self):
        state = _make_state(PermissionError("denied"))
        assert classify_for_recovery(state) == RecoveryAction.DEAD_LETTER

    # ── Validation errors → always ESCALATE ──────────────────────────

    def test_validation_no_fallback(self):
        state = _make_state(ValueError("JSON schema mismatch"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    def test_validation_with_fallback(self):
        state = _make_state(ValueError("JSON schema mismatch"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Transient errors ─────────────────────────────────────────────

    def test_transient_no_fallback(self):
        state = _make_state(OSError("I/O error"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_transient_with_fallback(self):
        state = _make_state(OSError("I/O error"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Network errors ───────────────────────────────────────────────

    def test_network_no_fallback(self):
        state = _make_state(ConnectionError("refused"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_network_with_fallback(self):
        state = _make_state(ConnectionError("refused"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    def test_connection_reset_no_fallback(self):
        state = _make_state(ConnectionResetError("reset"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_connection_reset_with_fallback(self):
        state = _make_state(ConnectionResetError("reset"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Timeout errors ───────────────────────────────────────────────

    def test_timeout_no_fallback(self):
        state = _make_state(TimeoutError("timed out"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_timeout_with_fallback(self):
        state = _make_state(TimeoutError("timed out"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Model errors ─────────────────────────────────────────────────

    def test_model_no_fallback(self):
        state = _make_state(Exception("rate limit exceeded"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_model_with_fallback(self):
        state = _make_state(Exception("rate limit exceeded"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    def test_model_api_key_no_fallback(self):
        state = _make_state(Exception("API key invalid"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_model_api_key_with_fallback(self):
        state = _make_state(Exception("API key invalid"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Unknown errors ───────────────────────────────────────────────

    def test_unknown_no_fallback(self):
        state = _make_state(RuntimeError("unexpected"), fallback_attempted=False)
        assert classify_for_recovery(state) == RecoveryAction.REQUEUE

    def test_unknown_with_fallback(self):
        state = _make_state(RuntimeError("unexpected"), fallback_attempted=True)
        assert classify_for_recovery(state) == RecoveryAction.ESCALATE

    # ── Full decision matrix parametrized ────────────────────────────

    @pytest.mark.parametrize(
        "error, cat, fallback, expected_action",
        [
            (FileNotFoundError("x"), ERROR_CATEGORY_PERMANENT, False, RecoveryAction.DEAD_LETTER),
            (FileNotFoundError("x"), ERROR_CATEGORY_PERMANENT, True, RecoveryAction.DEAD_LETTER),
            (ValueError("json parse"), ERROR_CATEGORY_VALIDATION, False, RecoveryAction.ESCALATE),
            (ValueError("json parse"), ERROR_CATEGORY_VALIDATION, True, RecoveryAction.ESCALATE),
            (OSError("io"), ERROR_CATEGORY_TRANSIENT, False, RecoveryAction.REQUEUE),
            (OSError("io"), ERROR_CATEGORY_TRANSIENT, True, RecoveryAction.ESCALATE),
            (ConnectionError("x"), ERROR_CATEGORY_NETWORK, False, RecoveryAction.REQUEUE),
            (ConnectionError("x"), ERROR_CATEGORY_NETWORK, True, RecoveryAction.ESCALATE),
            (TimeoutError(), ERROR_CATEGORY_TIMEOUT, False, RecoveryAction.REQUEUE),
            (TimeoutError(), ERROR_CATEGORY_TIMEOUT, True, RecoveryAction.ESCALATE),
            (Exception("rate limit"), ERROR_CATEGORY_MODEL, False, RecoveryAction.REQUEUE),
            (Exception("rate limit"), ERROR_CATEGORY_MODEL, True, RecoveryAction.ESCALATE),
            (RuntimeError("?"), ERROR_CATEGORY_UNKNOWN, False, RecoveryAction.REQUEUE),
            (RuntimeError("?"), ERROR_CATEGORY_UNKNOWN, True, RecoveryAction.ESCALATE),
        ],
    )
    def test_decision_matrix(self, error, cat, fallback, expected_action):
        state = _make_state(error, fallback_attempted=fallback)
        # Override category for tests where classify_error gives different result
        object.__setattr__(state, "error_category", cat)
        assert classify_for_recovery(state) == expected_action


# ── Default handler tests ────────────────────────────────────────────────


class TestDefaultHandlers:
    """Verify the three default recovery action handlers."""

    def test_default_requeue_handler(self):
        state = _make_state(ConnectionError("refused"), fallback_attempted=False)
        result = default_requeue_handler(state)
        assert result.action == RecoveryAction.REQUEUE
        assert result.success is True
        assert "REQUEUE" in result.message
        assert result.details["operation_name"] == "test-op"
        assert result.details["suggested_new_max_retries"] == 6  # 3 * 2
        assert result.details["fallback_attempted"] is False

    def test_default_requeue_handler_with_meeting_id(self):
        state = _make_state(TimeoutError(), meeting_id="m-xyz")
        result = default_requeue_handler(state)
        assert "m-xyz" in result.message

    def test_default_escalate_handler_model(self):
        state = _make_state(Exception("rate limit"))
        # Override to model category since classify_error gives model
        object.__setattr__(state, "error_category", ERROR_CATEGORY_MODEL)
        result = default_escalate_handler(state)
        assert result.action == RecoveryAction.ESCALATE
        assert result.success is True
        assert "codex-gpt-5.5" in result.details["escalation_target"]

    def test_default_escalate_handler_validation(self):
        state = _make_state(ValueError("schema error"))
        object.__setattr__(state, "error_category", ERROR_CATEGORY_VALIDATION)
        result = default_escalate_handler(state)
        assert "codex-gpt-5.5" in result.details["escalation_target"]

    def test_default_escalate_handler_transient_after_fallback(self):
        state = _make_state(OSError("io"), fallback_attempted=True)
        object.__setattr__(state, "error_category", ERROR_CATEGORY_TRANSIENT)
        result = default_escalate_handler(state)
        assert result.details["escalation_target"] == "supervisor-agent"

    def test_default_escalate_handler_unknown_after_fallback(self):
        state = _make_state(RuntimeError("?"), fallback_attempted=True)
        object.__setattr__(state, "error_category", ERROR_CATEGORY_UNKNOWN)
        result = default_escalate_handler(state)
        assert result.details["escalation_target"] == "human-in-the-loop"

    def test_default_dead_letter_handler(self):
        state = _make_state(FileNotFoundError("config.json"))
        result = default_dead_letter_handler(state)
        assert result.action == RecoveryAction.DEAD_LETTER
        assert result.success is True
        assert "DEAD_LETTER" in result.message
        assert result.details["immutable"] is True
        assert result.details["total_attempts"] == 3

    def test_default_dead_letter_handler_truncates_long_error(self):
        """Error message > 120 chars should be truncated in the message."""
        state = _make_state(FileNotFoundError("x" * 200))
        result = default_dead_letter_handler(state)
        assert len(result.details["final_error"]) == 200  # full preserved in details
        # Message contains truncated version
        assert "..." not in result.message  # slicing just cuts, no ellipsis


# ── execute_recovery tests with mock handlers ────────────────────────────


class TestExecuteRecoveryWithMocks:
    """Verify execute_recovery dispatches to the correct mock handler."""

    def test_dispatches_to_requeue(self):
        state = _make_state(ConnectionError("refused"), fallback_attempted=False)
        mock_called = []

        def mock_requeue(s: ExhaustedRetryState) -> RecoveryResult:
            mock_called.append(s.operation_name)
            return RecoveryResult(
                action=RecoveryAction.REQUEUE,
                success=True,
                message="Mock requeue executed",
            )

        result = execute_recovery(state, on_requeue=mock_requeue)
        assert result.success is True
        assert result.action == RecoveryAction.REQUEUE
        assert result.message == "Mock requeue executed"
        assert mock_called == ["test-op"]

    def test_dispatches_to_escalate(self):
        state = _make_state(ValueError("schema error"))
        mock_called = []

        def mock_escalate(s: ExhaustedRetryState) -> RecoveryResult:
            mock_called.append(s.error_category)
            return RecoveryResult(
                action=RecoveryAction.ESCALATE,
                success=True,
                message="Mock escalate",
                details={"target": "codex"},
            )

        result = execute_recovery(state, on_escalate=mock_escalate)
        assert result.success is True
        assert result.action == RecoveryAction.ESCALATE
        assert result.details == {"target": "codex"}
        assert mock_called == [ERROR_CATEGORY_VALIDATION]

    def test_dispatches_to_dead_letter(self):
        state = _make_state(FileNotFoundError("missing"))
        mock_called = []

        def mock_dead_letter(s: ExhaustedRetryState) -> RecoveryResult:
            mock_called.append(s.operation_name)
            return RecoveryResult(
                action=RecoveryAction.DEAD_LETTER,
                success=True,
                message="Mock dead letter",
            )

        result = execute_recovery(state, on_dead_letter=mock_dead_letter)
        assert result.success is True
        assert result.action == RecoveryAction.DEAD_LETTER
        assert mock_called == ["test-op"]

    def test_mock_handler_receives_full_state(self):
        """The mock handler should receive all state fields correctly."""
        state = _make_state(
            TimeoutError(),
            operation_name="worker-call",
            meeting_id="m-456",
            max_retries=5,
            fallback_attempted=True,
            quorum_reassessed=True,
            metadata={"role": "analyst"},
        )

        received: list[ExhaustedRetryState] = []

        def mock(s: ExhaustedRetryState) -> RecoveryResult:
            received.append(s)
            return RecoveryResult(RecoveryAction.ESCALATE, True)

        execute_recovery(state, on_escalate=mock)
        assert len(received) == 1
        s = received[0]
        assert s.operation_name == "worker-call"
        assert s.meeting_id == "m-456"
        assert s.max_retries == 5
        assert s.fallback_attempted is True
        assert s.quorum_reassessed is True
        assert s.metadata == {"role": "analyst"}
        assert s.error_category == ERROR_CATEGORY_TIMEOUT

    def test_all_three_mocks_provided(self):
        """All three mock handlers should only the correct one fire."""
        requeue_calls: list[str] = []
        escalate_calls: list[str] = []
        dead_letter_calls: list[str] = []

        def mock_req(s): requeue_calls.append("req"); return RecoveryResult(RecoveryAction.REQUEUE, True)
        def mock_esc(s): escalate_calls.append("esc"); return RecoveryResult(RecoveryAction.ESCALATE, True)
        def mock_dl(s): dead_letter_calls.append("dl"); return RecoveryResult(RecoveryAction.DEAD_LETTER, True)

        # Permanent error → dead-letter
        state_dl = _make_state(FileNotFoundError("missing"))
        result = execute_recovery(state_dl, on_requeue=mock_req, on_escalate=mock_esc, on_dead_letter=mock_dl)
        assert result.action == RecoveryAction.DEAD_LETTER
        assert requeue_calls == []
        assert escalate_calls == []
        assert dead_letter_calls == ["dl"]

        # Transient, no fallback → requeue
        state_rq = _make_state(OSError("io"), fallback_attempted=False)
        result2 = execute_recovery(state_rq, on_requeue=mock_req, on_escalate=mock_esc, on_dead_letter=mock_dl)
        assert result2.action == RecoveryAction.REQUEUE
        assert requeue_calls == ["req"]
        assert escalate_calls == []
        assert dead_letter_calls == ["dl"]

    def test_requeue_after_fallback_and_quorum_escalates(self):
        """Transient + fallback=True → escalate even if quorum=True."""
        state = _make_state(
            OSError("io"),
            fallback_attempted=True,
            quorum_reassessed=True,
        )
        # Full fallback+quorum done → must escalate
        mock_called = []

        def mock_esc(s): mock_called.append("escalated"); return RecoveryResult(RecoveryAction.ESCALATE, True)

        result = execute_recovery(state, on_escalate=mock_esc)
        assert result.action == RecoveryAction.ESCALATE
        assert mock_called == ["escalated"]


# ── execute_recovery exception handling ──────────────────────────────────


class TestExecuteRecoveryExceptionHandling:
    """Verify execute_recovery gracefully handles handler exceptions."""

    def test_handler_raises_exception(self):
        state = _make_state(ConnectionError("refused"), fallback_attempted=False)

        def crashing_handler(s: ExhaustedRetryState) -> RecoveryResult:
            raise RuntimeError("handler disaster")

        result = execute_recovery(state, on_requeue=crashing_handler)
        assert result.success is False
        assert result.action == RecoveryAction.REQUEUE
        assert result.error is not None
        assert isinstance(result.error, RuntimeError)
        assert "handler disaster" in str(result.error)
        assert "RuntimeError" in result.message

    def test_handler_raises_value_error(self):
        state = _make_state(FileNotFoundError("missing"))

        def bad_handler(s: ExhaustedRetryState) -> RecoveryResult:
            raise ValueError("bad input to handler")

        result = execute_recovery(state, on_dead_letter=bad_handler)
        assert result.success is False
        assert isinstance(result.error, ValueError)

    def test_handler_returns_none_returns_failed_result(self):
        """If a handler mistakenly returns None, a failed RecoveryResult is returned."""
        state = _make_state(ConnectionError("refused"), fallback_attempted=False)

        def none_handler(s: ExhaustedRetryState) -> RecoveryResult:
            return None  # type: ignore[return-value]

        result = execute_recovery(state, on_requeue=none_handler)
        assert result is not None
        assert result.success is False
        assert result.action == RecoveryAction.REQUEUE
        assert "returned None" in result.message
        assert isinstance(result.error, ValueError)


# ── exhausted_state_from_result tests ────────────────────────────────────


class TestExhaustedStateFromResult:
    """Verify the exhausted_state_from_result convenience constructor."""

    def test_builds_state_from_result(self):
        rr = _make_result(TimeoutError("call timed out"), max_retries=3)
        state = exhausted_state_from_result(
            rr, "worker-alpha", meeting_id="m-001"
        )
        assert state.retry_result is rr
        assert state.operation_name == "worker-alpha"
        assert state.meeting_id == "m-001"
        assert state.error_category == ERROR_CATEGORY_TIMEOUT
        assert state.max_retries == 3
        assert state.fallback_attempted is False

    def test_with_fallback_and_quorum(self):
        rr = _make_result(OSError("disk full"), max_retries=5)
        state = exhausted_state_from_result(
            rr,
            "disk-write",
            fallback_attempted=True,
            quorum_reassessed=True,
        )
        assert state.fallback_attempted is True
        assert state.quorum_reassessed is True
        assert state.error_category == ERROR_CATEGORY_TRANSIENT
        assert state.max_retries == 5

    def test_with_metadata(self):
        rr = _make_result(ValueError("schema"), max_retries=2)
        state = exhausted_state_from_result(
            rr,
            "validate",
            metadata={"round": "3", "validator": "glm-5.1"},
        )
        assert state.metadata == {"round": "3", "validator": "glm-5.1"}

    def test_raises_on_non_exhausted_result(self):
        """max_retries_exceeded=False should raise ValueError."""
        cfg = RetryConfig(max_retries=3)
        rr = RetryResult(
            success=True,
            attempts=(
                RetryAttempt(1, None, 1.0, 1.01),
            ),
            max_retries_exceeded=False,
            final_error=None,
            result="ok",
            config=cfg,
        )
        with pytest.raises(ValueError, match="max_retries_exceeded=False"):
            exhausted_state_from_result(rr, "op")

    def test_preserves_model_error_category(self):
        rr = _make_result(Exception("API key invalid"), max_retries=3)
        state = exhausted_state_from_result(rr, "model-call")
        assert state.error_category == ERROR_CATEGORY_MODEL

    def test_preserves_permanent_error_category(self):
        rr = _make_result(FileNotFoundError("no file"), max_retries=2)
        state = exhausted_state_from_result(rr, "read-config")
        assert state.error_category == ERROR_CATEGORY_PERMANENT


# ── Integration tests ───────────────────────────────────────────────────


class TestIntegration:
    """End-to-end scenarios matching the Seed's failure-handling sequence."""

    def test_retry_exhausted_requeue_path(self):
        """Simulate: retry exhausted (transient, no fallback) → REQUEUE."""
        # Build an exhausted retry from a transient failure
        state = _make_state(OSError("transient I/O error"), fallback_attempted=False)

        # Classify
        action = classify_for_recovery(state)
        assert action == RecoveryAction.REQUEUE

        # Execute with default handler
        result = execute_recovery(state)
        assert result.success is True
        assert result.action == RecoveryAction.REQUEUE
        assert "REQUEUE" in result.message

    def test_retry_exhausted_escalate_path(self):
        """Simulate: retry+fallback exhausted → ESCALATE."""
        state = _make_state(
            ConnectionError("primary+fallback both failed"),
            fallback_attempted=True,
            quorum_reassessed=True,
        )

        action = classify_for_recovery(state)
        assert action == RecoveryAction.ESCALATE

        result = execute_recovery(state)
        assert result.success is True
        assert result.action == RecoveryAction.ESCALATE
        assert "ESCALATE" in result.message

    def test_retry_exhausted_dead_letter_path(self):
        """Simulate: permanent failure → DEAD_LETTER."""
        state = _make_state(FileNotFoundError("critical config missing"))

        action = classify_for_recovery(state)
        assert action == RecoveryAction.DEAD_LETTER

        result = execute_recovery(state)
        assert result.success is True
        assert result.action == RecoveryAction.DEAD_LETTER
        assert result.details["immutable"] is True

    def test_full_worker_failure_sequence(self):
        """End-to-end: worker fails → retry exhausted → recovery executed.

        This matches the Seed's failure sequence:
        retry(1×) → fallback(1×) → quorum reassessment → escalation
        """
        # Simulate a worker that fails with timeout
        cfg = RetryConfig(max_retries=3, retry_delay_seconds=0.0)
        attempts = tuple(
            RetryAttempt(
                attempt_number=i,
                error=TimeoutError(f"timeout #{i}"),
                start_time=float(i),
                end_time=float(i) + 0.01,
            )
            for i in range(1, 4)
        )
        rr = RetryResult(
            success=False,
            attempts=attempts,
            max_retries_exceeded=True,
            final_error=TimeoutError("timeout #3"),
            result=None,
            config=cfg,
        )

        # Build exhausted state — fallback also attempted and failed
        state = ExhaustedRetryState(
            retry_result=rr,
            operation_name="worker-strategist",
            meeting_id="m-full-001",
            error_category=ERROR_CATEGORY_TIMEOUT,
            max_retries=3,
            fallback_attempted=True,
            quorum_reassessed=True,
            metadata={"role_id": "strategist", "round": "2"},
        )

        # Classify → ESCALATE (timeout + fallback already attempted)
        action = classify_for_recovery(state)
        assert action == RecoveryAction.ESCALATE

        # Execute with mock escalation that simulates Codex handoff
        escalation_log: list[dict] = []

        def mock_codex_escalation(s: ExhaustedRetryState) -> RecoveryResult:
            escalation_log.append({
                "op": s.operation_name,
                "meeting": s.meeting_id,
                "category": s.error_category,
                "target": "codex-gpt-5.5",
            })
            return RecoveryResult(
                action=RecoveryAction.ESCALATE,
                success=True,
                message="Escalated to Codex GPT-5.5 for review",
                details={"escalation_id": "esc-001", "target": "codex-gpt-5.5"},
            )

        result = execute_recovery(state, on_escalate=mock_codex_escalation)
        assert result.success is True
        assert len(escalation_log) == 1
        assert escalation_log[0]["op"] == "worker-strategist"
        assert escalation_log[0]["target"] == "codex-gpt-5.5"

    def test_permanent_error_no_retry_no_fallback(self):
        """Permanent error should go straight to DEAD_LETTER."""
        state = _make_state(
            FileExistsError("file already exists"),
            fallback_attempted=False,
            quorum_reassessed=False,
        )
        # Permanent → dead_letter regardless
        assert classify_for_recovery(state) == RecoveryAction.DEAD_LETTER

    def test_recovery_result_as_log_dict_integration(self):
        """Verify RecoveryResult.as_log_dict() works for manifest integration."""
        state = _make_state(OSError("io"), fallback_attempted=True)
        result = execute_recovery(state)
        log_entry = result.as_log_dict()
        assert log_entry["recovery_action"] == "escalate"
        assert log_entry["recovery_success"] is True
        assert "recovery_message" in log_entry
        assert "recovery_details" in log_entry
