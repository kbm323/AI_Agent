"""Primary router failure classifier — Sub-AC 3.2.2.

Receives the primary router's raw response diagnostics (or timeout/error
signals) and classifies them into discrete failure modes: ``timeout``,
``error``, ``empty_route``, ``no_match``.

Design
------
The classifier is a **pure-in-memory decision function** — no filesystem
I/O, no CLI calls, no LLM invocations.  It accepts keyword-only boolean
flags (for mocking each failure scenario) plus optional structured data
from the Qwen classification pipeline, and returns a
``FailureClassification`` with the resolved ``RouterFailureMode`` and
diagnostic metadata.

Failure mode definitions (priority order, first match wins):
1. **TIMEOUT** — The opencode-go CLI call exceeded its configured timeout.
2. **ERROR** — The CLI returned a non-zero exit code or could not be
   executed (OSError, not-on-PATH).  Covers both ``UNAVAILABLE`` and
   ``ERROR`` from the broader ``RouterStatus`` enum.
3. **EMPTY_ROUTE** — The primary router succeeded BUT the classification
   result has no usable route: missing or empty ``required_roles`` AND
   no ``agenda_type`` or an empty/blank ``agenda_type``.
4. **NO_MATCH** — The classification result has a valid-looking route
   (roles present, agenda_type non-empty) but the ``agenda_type`` does
   not match any of the 6 recognized meeting types
   (``creative_production``, ``technical_development``,
   ``marketing_strategy``, ``risk_assessment``, ``general_planning``,
   ``project_review``).

Integration
-----------
This module sits between the ``classify()`` orchestrator and the
Coordinator's routing fan-out.  When ``classify()`` returns a result
with ``validation_verdict != "pass"``, the Coordinator feeds the raw
pipeline diagnostics into ``classify_router_failure()`` to determine the
exact failure mode before deciding whether to:

- Retry (same model, same parameters)
- Activate static fallback routing (``static_rule_matcher``)
- Pause for rate limits (``rate_limit_paused``)
- Escalate to user

Testability
-----------
Every failure scenario is exercisable with keyword-only boolean flags
— no ``opencode-go`` CLI, no network, no LLM calls.  Example::

    classify_router_failure(timeout_occurred=True) → TIMEOUT
    classify_router_failure(cli_error=True) → ERROR
    classify_router_failure(classification_valid=True,
                            has_roles=False,
                            agenda_type="") → EMPTY_ROUTE
    classify_router_failure(classification_valid=True,
                            has_roles=True,
                            agenda_type="unknown_type") → NO_MATCH
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

# ═══════════════════════════════════════════════════════════════════════════
# Recognised meeting types (mirrors trigger_router, trigger_input_parser,
# meeting_creation_dispatcher, and routing_rules.yaml)
# ═══════════════════════════════════════════════════════════════════════════

_VALID_AGENDA_TYPES: frozenset[str] = frozenset({
    "creative_production",
    "technical_development",
    "marketing_strategy",
    "risk_assessment",
    "general_planning",
    "project_review",
})
"""The six recognised meeting agenda types from the Seed ontology."""

# ═══════════════════════════════════════════════════════════════════════════
# Failure mode enum
# ═══════════════════════════════════════════════════════════════════════════


@unique
class RouterFailureMode(StrEnum):
    """Discrete failure modes for the primary LLM router.

    Values are identical to member names — the enum serves as both
    a symbolic constant set and a string-comparable value.

    Attributes:
        TIMEOUT: The opencode-go CLI call exceeded its configured
            timeout.  Partial output may exist but is unreliable.
        ERROR: The CLI returned a non-zero exit code, could not be
            executed (not installed / not on PATH / OSError),
            or encountered an internal error.
        EMPTY_ROUTE: The router succeeded (exit 0, parseable JSON)
            but the classification result has no usable route — no
            roles assigned and no valid agenda type present.
        NO_MATCH: The router produced a valid-looking route (roles
            present, agenda_type non-empty) but the agenda_type does
            not match any of the six recognised meeting types.
    """

    TIMEOUT = "timeout"
    """Call exceeded the configured timeout."""

    ERROR = "error"
    """CLI returned a non-zero exit code or was unavailable."""

    EMPTY_ROUTE = "empty_route"
    """Router succeeded but returned an empty/invalid route."""

    NO_MATCH = "no_match"
    """Router returned a valid route but no matching meeting type."""

    # ── Group membership helpers ──────────────────────────────────────

    @property
    def is_infrastructure_failure(self) -> bool:
        """True when the failure is infrastructure-related (TIMEOUT or ERROR)."""
        return self in (RouterFailureMode.TIMEOUT, RouterFailureMode.ERROR)

    @property
    def is_semantic_failure(self) -> bool:
        """True when the failure is semantic (EMPTY_ROUTE or NO_MATCH)."""
        return self in (RouterFailureMode.EMPTY_ROUTE, RouterFailureMode.NO_MATCH)

    @property
    def is_retryable(self) -> bool:
        """True when the failure may succeed on retry (TIMEOUT only).

        ERROR, EMPTY_ROUTE, and NO_MATCH are NOT retryable — the same
        input is unlikely to produce a different result.
        """
        return self is RouterFailureMode.TIMEOUT


# ═══════════════════════════════════════════════════════════════════════════
# Classification result
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FailureClassification:
    """Immutable result of classifying a primary router failure.

    Attributes:
        mode: The resolved failure mode.
        reason: Human-readable explanation of the classification.
        raw_timeout: Whether the original call timed out.
        raw_exit_code: Raw exit code from the subprocess (-1 on
            timeout/internal error).
        raw_cli_error: Whether the CLI call returned non-zero or
            was unavailable.
        classification_present: Whether a ClassificationResult was
            available for inspection.
        classification_valid: Whether the ClassificationResult was
            valid (parsed successfully, schema passed).
        agenda_type: The agenda_type from the classification (if any).
        has_roles: Whether the classification had non-empty
            required_roles or optional_roles.
        agenda_type_recognized: Whether the agenda_type was one of
            the 6 valid types.
    """

    mode: RouterFailureMode
    """The resolved failure mode."""

    reason: str
    """Human-readable classification explanation."""

    # Raw pipeline diagnostics (for logging/tracing)
    raw_timeout: bool = False
    raw_exit_code: int = 0
    raw_cli_error: bool = False
    classification_present: bool = False
    classification_valid: bool = False
    agenda_type: str = ""
    has_roles: bool = False
    agenda_type_recognized: bool = False

    @property
    def should_pause(self) -> bool:
        """True when the failure suggests pausing (backward compat)."""
        return self.mode == RouterFailureMode.TIMEOUT

    @property
    def should_fallback(self) -> bool:
        """True when static fallback routing should be activated.

        All failure modes except (hypothetical) transient ones should
        fall back.  Currently: ERROR, EMPTY_ROUTE, NO_MATCH.
        TIMEOUT may be retried first before falling back.
        """
        return self.mode in (
            RouterFailureMode.ERROR,
            RouterFailureMode.EMPTY_ROUTE,
            RouterFailureMode.NO_MATCH,
        )

    @property
    def should_escalate(self) -> bool:
        """True when user escalation is appropriate (NO_MATCH only).

        NO_MATCH means the router output is valid but the meeting
        type is unrecognised — only a human can clarify intent.
        """
        return self.mode is RouterFailureMode.NO_MATCH

    def to_dict(self) -> dict:
        """Serialize to a plain dict for logging/manifest storage."""
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "raw_timeout": self.raw_timeout,
            "raw_exit_code": self.raw_exit_code,
            "raw_cli_error": self.raw_cli_error,
            "classification_present": self.classification_present,
            "classification_valid": self.classification_valid,
            "agenda_type": self.agenda_type,
            "has_roles": self.has_roles,
            "agenda_type_recognized": self.agenda_type_recognized,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def classify_router_failure(
    *,
    timeout_occurred: bool = False,
    cli_error: bool = False,
    cli_exit_code: int = 0,
    cli_unavailable: bool = False,
    classification_valid: bool = False,
    classification_present: bool = False,
    agenda_type: str = "",
    has_roles: bool = False,
    valid_agenda_types: frozenset[str] | None = None,
    stderr_snippet: str = "",
) -> FailureClassification:
    """Classify a primary router failure into a discrete failure mode.

    This is the **single entry point** for Sub-AC 3.2.2.  It accepts
    keyword-only diagnostic flags from the primary LLM router pipeline
    and returns a ``FailureClassification`` with the resolved
    ``RouterFailureMode``.

    Priority order (first match wins) — mirrors the evaluation order
    documented in the module header:

    1. ``timeout_occurred`` → ``RouterFailureMode.TIMEOUT``
    2. ``cli_error`` or ``cli_unavailable`` → ``RouterFailureMode.ERROR``
    3. ``classification_present`` BUT ``has_roles`` is False OR
       ``agenda_type`` is empty → ``RouterFailureMode.EMPTY_ROUTE``
    4. ``classification_valid`` BUT ``agenda_type`` is not in
       ``valid_agenda_types`` → ``RouterFailureMode.NO_MATCH``
    5. If none of the above match, returns ``ERROR`` as a defensive
       catch-all (should not happen in practice).

    Args:
        timeout_occurred: True when the opencode-go CLI call timed out.
        cli_error: True when the CLI returned a non-zero exit code.
        cli_exit_code: Raw exit code from the subprocess (-1 on
            timeout / internal error).  Used only for diagnostic
            metadata; the *boolean* flags drive classification.
        cli_unavailable: True when the CLI could not be executed
            (not installed, not on PATH, OSError).
        classification_valid: True when the ClassificationResult
            parsed successfully and passed schema validation.
        classification_present: True when a ClassificationResult
            was available for inspection (even if invalid).
        agenda_type: The agenda_type string from the classification
            result (may be empty).
        has_roles: True when the classification result has non-empty
            ``required_roles`` or ``optional_roles``.
        valid_agenda_types: The set of recognised meeting types.
            Defaults to the six Seed-defined types when ``None``.
        stderr_snippet: Optional snippet of stderr for logging.

    Returns:
        ``FailureClassification`` — inspect ``.mode`` to branch;
        the ``.reason`` string explains the classification rationale.

    Raises:
        TypeError: If any argument has an unexpected type.

    Examples:
        >>> classify_router_failure(timeout_occurred=True)
        FailureClassification(mode=<RouterFailureMode.TIMEOUT: 'timeout'>, ...)

        >>> classify_router_failure(cli_error=True)
        FailureClassification(mode=<RouterFailureMode.ERROR: 'error'>, ...)

        >>> classify_router_failure(
        ...     classification_present=True,
        ...     classification_valid=True,
        ...     has_roles=False,
        ...     agenda_type="",
        ... )
        FailureClassification(mode=<RouterFailureMode.EMPTY_ROUTE: 'empty_route'>, ...)

        >>> classify_router_failure(
        ...     classification_present=True,
        ...     classification_valid=True,
        ...     has_roles=True,
        ...     agenda_type="unknown_type",
        ... )
        FailureClassification(mode=<RouterFailureMode.NO_MATCH: 'no_match'>, ...)
    """
    # Type guards
    if not isinstance(timeout_occurred, bool):
        raise TypeError(
            f"timeout_occurred must be bool, got {type(timeout_occurred).__name__}"
        )
    if not isinstance(cli_error, bool):
        raise TypeError(
            f"cli_error must be bool, got {type(cli_error).__name__}"
        )

    # Resolve valid agenda types
    types = valid_agenda_types if valid_agenda_types is not None else _VALID_AGENDA_TYPES

    # Pre-compute derived flags
    agenda_type_empty = not agenda_type or not agenda_type.strip()
    agenda_type_recognized = (
        not agenda_type_empty and agenda_type.strip() in types
    )

    # ── 1. Timeout ───────────────────────────────────────────────────
    if timeout_occurred:
        return FailureClassification(
            mode=RouterFailureMode.TIMEOUT,
            reason="Primary router timed out — opencode-go CLI call exceeded configured timeout",
            raw_timeout=True,
            raw_exit_code=cli_exit_code,
            raw_cli_error=False,
            classification_present=classification_present,
            classification_valid=classification_valid,
            agenda_type=agenda_type.strip() if agenda_type else "",
            has_roles=has_roles,
            agenda_type_recognized=agenda_type_recognized,
        )

    # ── 2. Error (CLI non-zero exit or unavailable) ──────────────────
    if cli_error or cli_unavailable:
        detail = (
            "CLI unavailable — opencode-go not installed or not on PATH"
            if cli_unavailable
            else f"CLI exited with code {cli_exit_code}"
            + (f": {stderr_snippet[:120]}" if stderr_snippet else "")
        )
        return FailureClassification(
            mode=RouterFailureMode.ERROR,
            reason=f"Primary router error — {detail}",
            raw_timeout=False,
            raw_exit_code=cli_exit_code,
            raw_cli_error=True,
            classification_present=classification_present,
            classification_valid=classification_valid,
            agenda_type=agenda_type.strip() if agenda_type else "",
            has_roles=has_roles,
            agenda_type_recognized=agenda_type_recognized,
        )

    # ── 3. Empty route (router succeeded but no usable route) ────────
    if classification_present:
        if not has_roles and agenda_type_empty:
            return FailureClassification(
                mode=RouterFailureMode.EMPTY_ROUTE,
                reason=(
                    "Primary router returned empty route — "
                    "no roles assigned and no agenda type present"
                ),
                raw_timeout=False,
                raw_exit_code=cli_exit_code,
                raw_cli_error=False,
                classification_present=True,
                classification_valid=classification_valid,
                agenda_type="",
                has_roles=False,
                agenda_type_recognized=False,
            )

        if has_roles and agenda_type_empty:
            return FailureClassification(
                mode=RouterFailureMode.EMPTY_ROUTE,
                reason=(
                    "Primary router returned incomplete route — "
                    "roles present but agenda_type is empty"
                ),
                raw_timeout=False,
                raw_exit_code=cli_exit_code,
                raw_cli_error=False,
                classification_present=True,
                classification_valid=classification_valid,
                agenda_type="",
                has_roles=True,
                agenda_type_recognized=False,
            )

        # ── 4. No match (valid structure, unrecognised type) ───────
        if classification_valid and not agenda_type_recognized and not agenda_type_empty:
            return FailureClassification(
                mode=RouterFailureMode.NO_MATCH,
                reason=(
                    f"Primary router returned unrecognised agenda_type "
                    f"'{agenda_type.strip()}' — not one of: "
                    f"{', '.join(sorted(types))}"
                ),
                raw_timeout=False,
                raw_exit_code=cli_exit_code,
                raw_cli_error=False,
                classification_present=True,
                classification_valid=True,
                agenda_type=agenda_type.strip(),
                has_roles=has_roles,
                agenda_type_recognized=False,
            )

    # ── 5. Defensive catch-all (should not be reached in normal flow) ─
    # If we get here, the inputs don't clearly indicate any failure mode.
    # This can happen when the caller passes ambiguous or inconsistent
    # diagnostics.  Default to ERROR for safety — the Coordinator should
    # treat unrecognised states as failures.
    return FailureClassification(
        mode=RouterFailureMode.ERROR,
        reason=(
            "Unable to classify router failure — ambiguous or "
            "inconsistent diagnostics (timeout=False, error=False, "
            f"classification_present={classification_present}, "
            f"classification_valid={classification_valid}, "
            f"has_roles={has_roles}, "
            f"agenda_type={agenda_type.strip()!r})"
        ),
        raw_timeout=False,
        raw_exit_code=cli_exit_code,
        raw_cli_error=False,
        classification_present=classification_present,
        classification_valid=classification_valid,
        agenda_type=agenda_type.strip() if agenda_type else "",
        has_roles=has_roles,
        agenda_type_recognized=agenda_type_recognized,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: classify from OpencodeCallResult + ClassificationResult
# ═══════════════════════════════════════════════════════════════════════════


def classify_from_pipeline_results(
    *,
    cli_result=None,
    classification_result=None,
    valid_agenda_types: frozenset[str] | None = None,
) -> FailureClassification:
    """Classify a router failure from concrete pipeline result objects.

    Convenience wrapper around ``classify_router_failure()`` that
    accepts the actual ``OpencodeCallResult`` and
    ``ClassificationResult`` objects produced by ``classify()`` and
    derives the boolean flags automatically.

    This is the integration-facing entry point — use it in the
    Coordinator's routing fan-out when ``classify()`` returns a
    non-success ``ClassificationResult``.

    Args:
        cli_result: An ``OpencodeCallResult`` from the Qwen CLI call
            (optional — if None, all CLI flags default to False).
        classification_result: A ``ClassificationResult`` from the
            response parser (optional — if None, classification is
            treated as absent).
        valid_agenda_types: The set of recognised meeting types.
            Defaults to the six Seed-defined types when ``None``.

    Returns:
        ``FailureClassification`` with the resolved failure mode.

    Examples:
        >>> from src.opencode_qwen_wrapper import OpencodeCallResult
        >>> from src.response_parser import ClassificationResult
        >>>
        >>> # Timeout scenario
        >>> cli = OpencodeCallResult(
        ...     success=False, exit_code=-1, timeout_occurred=True,
        ...     stdout="", stderr="timeout", duration_seconds=120.0,
        ...     model="qwen-max", context_file="/tmp/p.json",
        ...     error_message="timeout",
        ... )
        >>> fc = classify_from_pipeline_results(cli_result=cli)
        >>> fc.mode
        <RouterFailureMode.TIMEOUT: 'timeout'>
    """
    # Derive CLI flags from OpencodeCallResult
    timeout_occurred = False
    cli_error = False
    cli_unavailable = False
    cli_exit_code = 0
    stderr_snippet = ""

    if cli_result is not None:
        timeout_occurred = getattr(cli_result, "timeout_occurred", False)
        cli_exit_code = getattr(cli_result, "exit_code", 0)
        stderr_snippet = getattr(cli_result, "stderr", "") or ""
        if not getattr(cli_result, "success", True):
            if timeout_occurred:
                pass  # handled above
            else:
                # Check if unavailable (CLI could not be executed)
                error_msg = getattr(cli_result, "error_message", "") or ""
                if "not found" in error_msg.lower() or "no such file" in error_msg.lower():
                    cli_unavailable = True
                else:
                    cli_error = True

    # Derive classification flags from ClassificationResult
    classification_present = False
    classification_valid = False
    agenda_type = ""
    has_roles = False

    if classification_result is not None:
        classification_present = True
        verdict = getattr(classification_result, "validation_verdict", "fail")
        classification_valid = verdict not in ("fail", "escalate")
        agenda_type = getattr(classification_result, "agenda_type", "") or ""
        required = getattr(classification_result, "required_roles", ()) or ()
        optional = getattr(classification_result, "optional_roles", ()) or ()
        has_roles = len(required) > 0 or len(optional) > 0

    return classify_router_failure(
        timeout_occurred=timeout_occurred,
        cli_error=cli_error,
        cli_exit_code=cli_exit_code,
        cli_unavailable=cli_unavailable,
        classification_valid=classification_valid,
        classification_present=classification_present,
        agenda_type=agenda_type,
        has_roles=has_roles,
        valid_agenda_types=valid_agenda_types,
        stderr_snippet=stderr_snippet,
    )
