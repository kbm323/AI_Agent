"""Fallback activation gate — Sub-AC 3.2b.

Decides whether static fallback routing should be activated based on
the primary LLM router (Qwen) response status.  This is the single
decision point between the Qwen classification pipeline and the
static rule matching engine.

Design principles:
- **Deterministic**: same status + context → identical decision
- **Independently testable**: all failure-mode scenarios exercisable
  with pure data — no LLM calls, no network, no filesystem
- **Output-compatible**: produces decisions the Coordinator can
  branch on without parsing internal diagnostics
- **No side effects**: pure function over (status, context) → decision

Decision matrix (from routing_rules.yaml metadata.activated_when)::

    SUCCESS         → USE_PRIMARY        (Qwen result is usable)
    TIMEOUT         → ACTIVATE_FALLBACK   (qwen_timeout)
    ERROR           → ACTIVATE_FALLBACK   (qwen_error)
    UNAVAILABLE     → ACTIVATE_FALLBACK   (opencode_go_unavailable)
    PARSE_FAILURE   → ACTIVATE_FALLBACK   (qwen_parse_failure)
    EMPTY_RESPONSE  → ACTIVATE_FALLBACK   (qwen_empty_response)
    RATE_LIMITED    → PAUSE_RATE_LIMIT    (rate_limit_paused)
    QUOTA_EXHAUSTED → PAUSE_RATE_LIMIT    (rate_limit_paused)

Usage::

    from src.fallback_activation_gate import (
        RouterStatus,
        GateDecision,
        evaluate_fallback_activation,
    )

    result = evaluate_fallback_activation(
        status=RouterStatus.TIMEOUT,
        classification_result=qwen_result,
        meeting_topic="뮤직비디오 제작 회의",
    )

    if result.decision == GateDecision.USE_PRIMARY:
        route = qwen_result  # use Qwen classification
    elif result.decision == GateDecision.ACTIVATE_FALLBACK:
        route = static_matcher.match(topic, reason=result.routing_reason)
    elif result.decision == GateDecision.PAUSE_RATE_LIMIT:
        save_state_and_exit(exit_condition="rate_limit_paused")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════


class RouterStatus(str, Enum):
    """Primary LLM router response status — maps to routing_rules.yaml
    ``metadata.activated_when`` entries.

    Attributes:
        SUCCESS: Qwen classified the topic successfully.  The
            ``ClassificationResult`` is valid and usable.
        TIMEOUT: Qwen API call exceeded the configured timeout.
            Partial output may exist but is unreliable.
        ERROR: Qwen returned a non-zero exit code or internal error.
            The ``OpencodeCallResult.success`` is False.
        UNAVAILABLE: The ``opencode-go`` CLI could not be executed
            (not installed, not on PATH, or OSError).
        PARSE_FAILURE: Qwen returned output but JSON extraction or
            schema validation failed.  The raw text may contain
            useful content but could not be parsed.
        EMPTY_RESPONSE: Qwen returned zero-length output or only
            whitespace.  No content to parse.
        RATE_LIMITED: A 429 or quota-exceeded error was received and
            the backoff retry also failed.
        QUOTA_EXHAUSTED: Pre-call quota check found remaining quota
            below the 10% threshold.
    """

    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    PARSE_FAILURE = "parse_failure"
    EMPTY_RESPONSE = "empty_response"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"

    # ── Group membership helpers ──────────────────────────────────────

    @property
    def is_failure(self) -> bool:
        """True for any non-success status."""
        return self is not RouterStatus.SUCCESS

    @property
    def is_rate_limit_related(self) -> bool:
        """True when the status is rate-limit or quota related."""
        return self in (RouterStatus.RATE_LIMITED, RouterStatus.QUOTA_EXHAUSTED)

    @property
    def triggers_fallback(self) -> bool:
        """True when this status should trigger static fallback routing."""
        return self in _FALLBACK_TRIGGERING_STATUSES

    @property
    def triggers_pause(self) -> bool:
        """True when this status should trigger a rate-limit pause."""
        return self in _PAUSE_TRIGGERING_STATUSES


class GateDecision(str, Enum):
    """Decision produced by the fallback activation gate.

    Attributes:
        USE_PRIMARY: Proceed with the Qwen classification result.
            The Coordinator should extract agenda_type, roles, tags,
            and priority from the ``ClassificationResult``.
        ACTIVATE_FALLBACK: Activate the static rule matching engine
            (``static_rule_matcher``).  The Coordinator should load
            ``routing_rules.yaml`` and call ``match_meeting_route()``
            with the meeting topic.
        PAUSE_RATE_LIMIT: Save meeting state to manifest and exit with
            ``exit_condition = "rate_limit_paused"``.  The meeting is
            resumable from manifest after quota reset.
    """

    USE_PRIMARY = "use_primary"
    ACTIVATE_FALLBACK = "activate_fallback"
    PAUSE_RATE_LIMIT = "pause_rate_limit"


# ═══════════════════════════════════════════════════════════════════════════
# Status → decision mapping tables
# ═══════════════════════════════════════════════════════════════════════════

#: Statuses that trigger static fallback routing.
_FALLBACK_TRIGGERING_STATUSES: frozenset[RouterStatus] = frozenset({
    RouterStatus.TIMEOUT,
    RouterStatus.ERROR,
    RouterStatus.UNAVAILABLE,
    RouterStatus.PARSE_FAILURE,
    RouterStatus.EMPTY_RESPONSE,
})

#: Statuses that trigger a rate-limit pause.
_PAUSE_TRIGGERING_STATUSES: frozenset[RouterStatus] = frozenset({
    RouterStatus.RATE_LIMITED,
    RouterStatus.QUOTA_EXHAUSTED,
})

#: Mapping from RouterStatus to the routing_reason string
#: used in ``MatchResult.routing_reason`` and meeting logs.
_ROUTING_REASON_MAP: dict[RouterStatus, str] = {
    RouterStatus.TIMEOUT: "qwen_timeout",
    RouterStatus.ERROR: "qwen_error",
    RouterStatus.UNAVAILABLE: "opencode_go_unavailable",
    RouterStatus.PARSE_FAILURE: "qwen_parse_failure",
    RouterStatus.EMPTY_RESPONSE: "qwen_empty_response",
    RouterStatus.RATE_LIMITED: "qwen_rate_limited",
    RouterStatus.QUOTA_EXHAUSTED: "quota_exhausted",
}

#: Mapping from RouterStatus to the exit_condition for PAUSE_RATE_LIMIT.
_PAUSE_EXIT_CONDITION_MAP: dict[RouterStatus, str] = {
    RouterStatus.RATE_LIMITED: "rate_limit_paused",
    RouterStatus.QUOTA_EXHAUSTED: "rate_limit_paused",
}


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FallbackActivationResult:
    """Immutable result of the fallback activation gate evaluation.

    This is the contract between the gate and the Coordinator.  The
    Coordinator should branch on ``decision``:

    * ``USE_PRIMARY`` → extract route from ``classification_result``
    * ``ACTIVATE_FALLBACK`` → call static matcher, use ``routing_reason``
    * ``PAUSE_RATE_LIMIT`` → save state and exit with ``exit_condition``

    Attributes:
        decision: The gate's decision (USE_PRIMARY, ACTIVATE_FALLBACK,
            or PAUSE_RATE_LIMIT).
        reason: Human-readable explanation of the decision.
        router_status: The original RouterStatus that produced this
            decision (for logging/tracing).
        routing_reason: When decision is ACTIVATE_FALLBACK, the
            ``routing_reason`` string to pass to the static matcher
            (e.g. ``"qwen_timeout"``).  Empty string otherwise.
        exit_condition: When decision is PAUSE_RATE_LIMIT, the
            ``exit_condition`` to surface to the Coordinator
            (e.g. ``"rate_limit_paused"``).  Empty string otherwise.
        classification_result: The original ClassificationResult from
            Qwen, if available.  Always None when decision is
            ACTIVATE_FALLBACK or PAUSE_RATE_LIMIT (the gate has
            determined the Qwen result is unusable).
    """

    decision: GateDecision
    """What the Coordinator should do next."""

    reason: str
    """Human-readable decision rationale."""

    router_status: RouterStatus
    """The original router status (for logging/tracing)."""

    routing_reason: str = ""
    """When ACTIVATE_FALLBACK: the reason string for the static matcher."""

    exit_condition: str = ""
    """When PAUSE_RATE_LIMIT: the exit_condition for state saving."""

    @property
    def should_use_primary(self) -> bool:
        """Convenience: True when the Qwen result should be used."""
        return self.decision == GateDecision.USE_PRIMARY

    @property
    def should_use_fallback(self) -> bool:
        """Convenience: True when static fallback should be activated."""
        return self.decision == GateDecision.ACTIVATE_FALLBACK

    @property
    def should_pause(self) -> bool:
        """Convenience: True when execution should pause for rate limits."""
        return self.decision == GateDecision.PAUSE_RATE_LIMIT


# ═══════════════════════════════════════════════════════════════════════════
# Decision engine
# ═══════════════════════════════════════════════════════════════════════════


def _build_use_primary_result(status: RouterStatus) -> FallbackActivationResult:
    """Build a USE_PRIMARY gate result."""
    return FallbackActivationResult(
        decision=GateDecision.USE_PRIMARY,
        reason="Qwen classification succeeded — using primary router result",
        router_status=status,
        routing_reason="",
        exit_condition="",
    )


def _build_activate_fallback_result(status: RouterStatus) -> FallbackActivationResult:
    """Build an ACTIVATE_FALLBACK gate result with the correct routing_reason."""
    routing_reason = _ROUTING_REASON_MAP.get(status, "static_fallback")
    status_descriptions = {
        RouterStatus.TIMEOUT: "Qwen API call timed out",
        RouterStatus.ERROR: "Qwen returned an error",
        RouterStatus.UNAVAILABLE: "opencode-go CLI unavailable",
        RouterStatus.PARSE_FAILURE: "Qwen response could not be parsed",
        RouterStatus.EMPTY_RESPONSE: "Qwen returned an empty response",
    }
    description = status_descriptions.get(status, f"Router status: {status.value}")

    return FallbackActivationResult(
        decision=GateDecision.ACTIVATE_FALLBACK,
        reason=f"{description} — activating static fallback routing",
        router_status=status,
        routing_reason=routing_reason,
        exit_condition="",
    )


def _build_pause_result(status: RouterStatus) -> FallbackActivationResult:
    """Build a PAUSE_RATE_LIMIT gate result with the correct exit_condition."""
    exit_condition = _PAUSE_EXIT_CONDITION_MAP.get(status, "rate_limit_paused")
    status_descriptions = {
        RouterStatus.RATE_LIMITED: (
            "Rate limit hit and backoff retry failed"
        ),
        RouterStatus.QUOTA_EXHAUSTED: (
            "Quota below 10% threshold — cannot proceed"
        ),
    }
    description = status_descriptions.get(status, f"Rate-limit related: {status.value}")

    return FallbackActivationResult(
        decision=GateDecision.PAUSE_RATE_LIMIT,
        reason=f"{description} — pausing execution, state saved to manifest",
        router_status=status,
        routing_reason="",
        exit_condition=exit_condition,
    )


#: Core decision dispatch table — maps RouterStatus to result builder.
_DECISION_TABLE: dict[RouterStatus, Callable[[RouterStatus], FallbackActivationResult]] = {
    RouterStatus.SUCCESS: _build_use_primary_result,
    RouterStatus.TIMEOUT: _build_activate_fallback_result,
    RouterStatus.ERROR: _build_activate_fallback_result,
    RouterStatus.UNAVAILABLE: _build_activate_fallback_result,
    RouterStatus.PARSE_FAILURE: _build_activate_fallback_result,
    RouterStatus.EMPTY_RESPONSE: _build_activate_fallback_result,
    RouterStatus.RATE_LIMITED: _build_pause_result,
    RouterStatus.QUOTA_EXHAUSTED: _build_pause_result,
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def evaluate_fallback_activation(
    status: RouterStatus,
) -> FallbackActivationResult:
    """Evaluate the primary router status and return a go/no-go decision.

    This is the **single entry point** for Sub-AC 3.2b.  It accepts
    a ``RouterStatus`` enum value and returns a ``FallbackActivationResult``
    with the decision, rationale, and any routing metadata.

    The function is a pure dispatch: same *status* always produces the
    same result.  No side effects, no network calls, no filesystem access.

    Args:
        status: The primary LLM router outcome (one of ``RouterStatus``
            enum values).

    Returns:
        ``FallbackActivationResult`` — inspect ``.decision`` to branch.

    Raises:
        TypeError: If *status* is not a ``RouterStatus`` member.

    Examples:
        >>> result = evaluate_fallback_activation(RouterStatus.SUCCESS)
        >>> result.decision
        <GateDecision.USE_PRIMARY: 'use_primary'>
        >>> result.should_use_primary
        True

        >>> result = evaluate_fallback_activation(RouterStatus.TIMEOUT)
        >>> result.decision
        <GateDecision.ACTIVATE_FALLBACK: 'activate_fallback'>
        >>> result.routing_reason
        'qwen_timeout'

        >>> result = evaluate_fallback_activation(RouterStatus.RATE_LIMITED)
        >>> result.decision
        <GateDecision.PAUSE_RATE_LIMIT: 'pause_rate_limit'>
        >>> result.exit_condition
        'rate_limit_paused'
    """
    if not isinstance(status, RouterStatus):
        raise TypeError(
            f"status must be a RouterStatus, got {type(status).__name__}"
        )

    builder = _DECISION_TABLE.get(status)
    if builder is None:
        # Defensive: should never happen with complete table, but
        # treat unknown status as fallback activation for safety.
        return FallbackActivationResult(
            decision=GateDecision.ACTIVATE_FALLBACK,
            reason=f"Unknown router status '{status}' — activating fallback",
            router_status=status,
            routing_reason="static_fallback",
            exit_condition="",
        )

    return builder(status)


# ═══════════════════════════════════════════════════════════════════════════
# RouterStatus derivation helper
# ═══════════════════════════════════════════════════════════════════════════


def derive_router_status(
    *,
    cli_success: bool | None = None,
    cli_exit_code: int | None = None,
    cli_timeout: bool = False,
    cli_unavailable: bool = False,
    stdout_empty: bool = False,
    validation_verdict: str | None = None,
    exit_condition: str = "",
    is_rate_limited: bool = False,
    quota_exhausted: bool = False,
) -> RouterStatus:
    """Derive a ``RouterStatus`` from raw pipeline diagnostics.

    This helper is used by the Coordinator (or a wrapper around
    ``classify()``) to translate concrete pipeline outcomes into the
    ``RouterStatus`` enum consumed by ``evaluate_fallback_activation()``.

    Priority order (first match wins):
    1. **quota_exhausted** → QUOTA_EXHAUSTED
    2. **is_rate_limited** or exit_condition == "rate_limit_paused" →
       RATE_LIMITED
    3. **cli_unavailable** → UNAVAILABLE
    4. **cli_timeout** → TIMEOUT
    5. **cli_success is False** (non-zero exit) → ERROR
    6. **stdout_empty** → EMPTY_RESPONSE
    7. **validation_verdict** is "fail" or "escalate" → PARSE_FAILURE
    8. Otherwise → SUCCESS

    Args:
        cli_success: Whether the opencode-go CLI call succeeded.
        cli_exit_code: Raw exit code from the subprocess.
        cli_timeout: Whether the call timed out.
        cli_unavailable: Whether the CLI could not be executed.
        stdout_empty: Whether stdout was empty/whitespace-only.
        validation_verdict: The ``validation_verdict`` from
            ``ClassificationResult`` (pass, conditional_pass,
            revision_required, escalate, fail).
        exit_condition: The ``exit_condition`` from
            ``ClassificationResult``.
        is_rate_limited: Whether a rate-limit error was detected.
        quota_exhausted: Whether pre-call quota check failed.

    Returns:
        The derived ``RouterStatus``.

    Examples:
        >>> derive_router_status(cli_success=True, validation_verdict="pass")
        <RouterStatus.SUCCESS: 'success'>

        >>> derive_router_status(cli_timeout=True)
        <RouterStatus.TIMEOUT: 'timeout'>

        >>> derive_router_status(cli_unavailable=True)
        <RouterStatus.UNAVAILABLE: 'unavailable'>

        >>> derive_router_status(is_rate_limited=True)
        <RouterStatus.RATE_LIMITED: 'rate_limited'>

        >>> derive_router_status(cli_success=True, stdout_empty=True)
        <RouterStatus.EMPTY_RESPONSE: 'empty_response'>

        >>> derive_router_status(cli_success=True, validation_verdict="fail")
        <RouterStatus.PARSE_FAILURE: 'parse_failure'>
    """
    # 1. Quota exhausted
    if quota_exhausted:
        return RouterStatus.QUOTA_EXHAUSTED

    # 2. Rate limited
    if is_rate_limited or exit_condition == "rate_limit_paused":
        return RouterStatus.RATE_LIMITED

    # 3. CLI unavailable
    if cli_unavailable:
        return RouterStatus.UNAVAILABLE

    # 4. Timeout
    if cli_timeout:
        return RouterStatus.TIMEOUT

    # 5. CLI error (non-zero exit, not success)
    if cli_success is False:
        return RouterStatus.ERROR

    # 6. Empty stdout
    if stdout_empty:
        return RouterStatus.EMPTY_RESPONSE

    # 7. Parse/validation failure
    if validation_verdict in ("fail", "escalate"):
        return RouterStatus.PARSE_FAILURE

    # 8. Success
    return RouterStatus.SUCCESS
