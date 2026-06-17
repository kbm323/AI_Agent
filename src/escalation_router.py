"""Escalation path routing — Sub-AC 6.3.3.

Accepts a threshold-exceeded signal (``GateResult`` from Sub-AC 6.3.2),
the divergent output pair (``DivergenceReport`` from Sub-AC 6.3.1) and
context metadata, then dispatches to the configured escalation handler
(re-route, flag, human-in-the-loop).

Architecture
------------
The escalation router sits at the end of the divergence-detection
pipeline::

    DivergenceMetric (6.3.1) → ThresholdGate (6.3.2)
                                         │
                                         ▼ exceeded
                              EscalationRouter (6.3.3)
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                          re_route     flag     human_in_the_loop

Each handler is a **callable** injected via ``EscalationRouteConfig``,
making the router fully testable without real side effects.  The router
itself is a pure decision function — no filesystem I/O, no network calls.

Routing rules
-------------
The route selection follows a priority-ordered decision tree driven by
the divergence margin, risk tags present in the context, and the
dimensions that contributed most to divergence:

* **human_in_the_loop** — margin ≥ 0.70 (very high divergence) OR
  risk_tags include ``security``/``legal``/``data_loss`` regardless
  of margin.
* **re_route** — margin ≥ 0.30 but < 0.70 AND no high-risk tags,
  OR specific dimensions (stance/conclusion) dominate and margin ≥ 0.15.
* **flag** — margin ≥ 0.05 but < 0.30 (low divergence that still
  exceeds threshold), OR margin < 0.05 with ``was_clamped`` signal.
  Also serves as the **default fallback** when no rule matches.

Usage::

    from src.escalation_router import (
        EscalationRouteConfig,
        EscalationRoute,
        EscalationContext,
        route_escalation,
        EscalationHandler,
    )
    from src.threshold_gate import evaluate_threshold
    from src.divergence_metric import compute_divergence

    gate = evaluate_threshold(report.overall_divergence, threshold=0.30)
    if gate.exceeded:
        ctx = EscalationContext(
            meeting_id="meeting-001",
            round_number=2,
            risk_tags=("brand",),
        )
        config = EscalationRouteConfig()
        route = route_escalation(
            gate_result=gate,
            divergence_report=report,
            context=ctx,
            config=config,
        )
        print(f"Escalation dispatched to: {route.handler}")
        print(f"Action taken: {route.action_description}")

Related modules
---------------
* ``divergence_metric`` — Sub-AC 6.3.1 (produces the divergence score)
* ``threshold_gate`` — Sub-AC 6.3.2 (produces the exceeded signal)
* ``trigger_detector`` — Sub-AC 7.2.1 (Codex trigger detection)
* ``resolution_decision`` — Sub-AC 5b-3 (post-rebuttal resolution)

Testable with
-------------
* Mock divergence signal (exceeded threshold) → handler invoked
* Mock divergence signal (within threshold) → no escalation
* Each handler type (re_route, flag, human_in_the_loop) selectable
* Risk-tag-driven escalation (security → human, brand → re_route)
* Margin-based routing thresholds
* Handler invocation verification via mock callables
* Context metadata pass-through to handlers
* Multiple dimension contributions affect routing
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.divergence_metric import DivergenceReport
from src.threshold_gate import GateResult

# ═════════════════════════════════════════════════════════════════════════
# Handler types
# ═════════════════════════════════════════════════════════════════════════

EscalationHandler = Callable[
    [
        "EscalationRoute",       # The route that triggered this handler
        "EscalationContext",     # Context metadata for the escalation
    ],
    None,
]
"""Signature for an escalation handler callable.

Receives the ``EscalationRoute`` (containing the decision) and the
``EscalationContext`` (containing meeting/round metadata).  Returns
``None``; side effects (logging, API calls, notifications) are the
handler's responsibility.

For testing, inject a mock that records the call arguments.
"""

# ═════════════════════════════════════════════════════════════════════════
# Handler identifiers
# ═════════════════════════════════════════════════════════════════════════

HANDLER_RE_ROUTE: str = "re_route"
"""Re-route: send the divergent output pair to another validator or
model for a second opinion.  Does NOT require human approval; the
re-routed validator's output feeds back into the pipeline."""

HANDLER_FLAG: str = "flag"
"""Flag: mark the divergence for attention in the final meeting summary
and Coordinator dashboard.  The meeting continues without blocking;
flagged items appear in post-meeting review."""

HANDLER_HUMAN_IN_THE_LOOP: str = "human_in_the_loop"
"""Human-in-the-loop: pause the pipeline and request human approval
before proceeding.  The Coordinator sends a Discord message with the
divergent outputs and awaits a decision."""

ALL_HANDLER_IDS: tuple[str, ...] = (
    HANDLER_RE_ROUTE,
    HANDLER_FLAG,
    HANDLER_HUMAN_IN_THE_LOOP,
)
"""Canonical ordered list of all handler identifiers."""

# ═════════════════════════════════════════════════════════════════════════
# High-risk tags that force human-in-the-loop
# ═════════════════════════════════════════════════════════════════════════

HUMAN_ESCALATION_RISK_TAGS: frozenset[str] = frozenset({
    "security",
    "legal",
    "data_loss",
    "financial",
    "irreversible",
    "production",
})
"""Risk tags that unconditionally route to human_in_the_loop regardless
of divergence margin.  These represent decision types where an automated
escalation is insufficient and a human must review."""

# ═════════════════════════════════════════════════════════════════════════
# Routing thresholds
# ═════════════════════════════════════════════════════════════════════════

MARGIN_HUMAN_THRESHOLD: float = 0.70
"""Margin at or above this value routes to human_in_the_loop (unless
overridden by risk tags which trigger human at any margin)."""

MARGIN_RE_ROUTE_THRESHOLD: float = 0.30
"""Margin at or above this value but below ``MARGIN_HUMAN_THRESHOLD``
routes to re_route (send to secondary validator)."""

MARGIN_FLAG_THRESHOLD: float = 0.05
"""Margin at or above this value but below ``MARGIN_RE_ROUTE_THRESHOLD``
routes to flag (mark for attention without blocking)."""

# ═════════════════════════════════════════════════════════════════════════
# Stance/conclusion dimension names that drive re-route
# ═════════════════════════════════════════════════════════════════════════

STANCE_DIMENSION_NAMES: frozenset[str] = frozenset({
    "stance_similarity",
    "conclusion_alignment",
})
"""Dimension names whose high divergence can trigger re-route even at
lower margins, because stance/conclusion disagreement is more likely
to be resolvable through a second validator's fresh perspective."""


# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EscalationContext:
    """Metadata about the meeting context in which divergence was detected.

    Passed through to the escalation handler so it knows which meeting,
    round, and topic are being escalated.  All fields are optional except
    ``meeting_id`` — the router does minimal validation and passes
    everything through to the handler.

    Attributes:
        meeting_id: Unique meeting identifier (required).
        round_number: Which meeting round the divergence was detected in.
        risk_tags: Risk-indicating tags from the meeting manifest.
        topic_id: The specific topic or agenda item being validated.
        validator_primary: The primary validator model (e.g. ``glm-5.1``).
        validator_secondary: The secondary validator model
            (e.g. ``codex-gpt-5.5``).
        extra: Arbitrary additional metadata for handler-specific use.
    """

    meeting_id: str
    """Unique meeting identifier — always required."""

    round_number: int = 1
    """Which meeting round the divergence was detected in (1-3)."""

    risk_tags: tuple[str, ...] = ()
    """Risk tags from the meeting manifest."""

    topic_id: str = ""
    """The specific topic being validated."""

    validator_primary: str = "glm-5.1"
    """Primary validator model identifier."""

    validator_secondary: str = "codex-gpt-5.5"
    """Secondary validator model identifier."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Arbitrary extra metadata for handler-specific use."""

    def __post_init__(self) -> None:
        if not self.meeting_id or not self.meeting_id.strip():
            raise ValueError("meeting_id must be a non-empty string")

    def has_risk_tag(self, tag: str) -> bool:
        """Check whether a specific risk tag is present (case-insensitive).

        Args:
            tag: The risk tag to check for.

        Returns:
            True if the tag is present in ``risk_tags``.
        """
        tag_lower = tag.lower()
        return any(t.lower() == tag_lower for t in self.risk_tags)

    def has_any_risk_tag(self, tags: frozenset[str]) -> bool:
        """Check whether any of the given risk tags are present.

        Args:
            tags: A set of risk tag strings to check.

        Returns:
            True if at least one matching tag is present.
        """
        for t in tags:
            if self.has_risk_tag(t):
                return True
        return False


@dataclass(frozen=True)
class EscalationRouteConfig:
    """Configuration for the escalation routing decision.

    All thresholds can be adjusted independently.  Each handler can be
    replaced with a custom callable (injected for testing).  The
    ``routing_fn`` can be swapped out entirely for custom routing logic.

    Attributes:
        margin_human_threshold: Margin >= this routes to human_in_the_loop.
        margin_re_route_threshold: Margin >= this but < human routes to
            re_route.
        margin_flag_threshold: Margin >= this but < re_route routes to flag.
        human_escalation_tags: Risk tags that force human_in_the_loop.
        stance_dimension_names: Division dimension names that can trigger
            re-route at lower margins.
        handler_re_route: Callable for the re_route handler.
        handler_flag: Callable for the flag handler.
        handler_human_in_the_loop: Callable for the human_in_the_loop handler.
        routing_fn: Optional custom routing function that overrides the
            default ``_default_routing_fn``.  Receives (gate_result,
            divergence_report, context, config) and must return a
            ``(handler_id, handler, action_description, triggered_by)``
            tuple.
    """

    # ── Thresholds ──

    margin_human_threshold: float = MARGIN_HUMAN_THRESHOLD
    """Margin >= this routes to human_in_the_loop (unless overridden)."""

    margin_re_route_threshold: float = MARGIN_RE_ROUTE_THRESHOLD
    """Margin >= this but < human routes to re_route."""

    margin_flag_threshold: float = MARGIN_FLAG_THRESHOLD
    """Margin >= this but < re_route routes to flag."""

    # ── Risk tag overrides ──

    human_escalation_tags: frozenset[str] = HUMAN_ESCALATION_RISK_TAGS
    """Risk tags that unconditionally route to human_in_the_loop."""

    stance_dimension_names: frozenset[str] = STANCE_DIMENSION_NAMES
    """Dimension names whose high divergence can trigger re-route."""

    # ── Handler callables (injectable for testing) ──

    handler_re_route: EscalationHandler | None = None
    """Callable for the re_route handler (None = no-op handler)."""

    handler_flag: EscalationHandler | None = None
    """Callable for the flag handler (None = no-op handler)."""

    handler_human_in_the_loop: EscalationHandler | None = None
    """Callable for the human_in_the_loop handler (None = no-op handler)."""

    # ── Custom routing ──

    routing_fn: Callable[..., tuple[str, EscalationHandler | None, str, str]] | None = (
        None
    )
    """Inject a custom routing function for testing.

    Signature: ``(gate_result, divergence_report, context, config) ->
    (handler_id, handler, action_description, triggered_by)``.

    When provided, the default routing logic is completely bypassed.
    """

    def __post_init__(self) -> None:
        """Validate threshold ranges."""
        if not 0.0 <= self.margin_human_threshold <= 1.0:
            raise ValueError(
                f"margin_human_threshold must be in [0.0, 1.0], "
                f"got {self.margin_human_threshold}"
            )
        if not 0.0 <= self.margin_re_route_threshold <= 1.0:
            raise ValueError(
                f"margin_re_route_threshold must be in [0.0, 1.0], "
                f"got {self.margin_re_route_threshold}"
            )
        if not 0.0 <= self.margin_flag_threshold <= 1.0:
            raise ValueError(
                f"margin_flag_threshold must be in [0.0, 1.0], "
                f"got {self.margin_flag_threshold}"
            )
        # Threshold ordering validation (flag <= re_route <= human)
        if self.margin_flag_threshold > self.margin_re_route_threshold:
            raise ValueError(
                f"margin_flag_threshold ({self.margin_flag_threshold}) "
                f"must be <= margin_re_route_threshold "
                f"({self.margin_re_route_threshold})"
            )
        if self.margin_re_route_threshold > self.margin_human_threshold:
            raise ValueError(
                f"margin_re_route_threshold ({self.margin_re_route_threshold}) "
                f"must be <= margin_human_threshold "
                f"({self.margin_human_threshold})"
            )

    def get_handler(self, handler_id: str) -> EscalationHandler | None:
        """Retrieve the handler callable for a given handler ID.

        Args:
            handler_id: One of ``HANDLER_RE_ROUTE``, ``HANDLER_FLAG``,
                or ``HANDLER_HUMAN_IN_THE_LOOP``.

        Returns:
            The handler callable, or None if not configured.
        """
        handler_map: dict[str, EscalationHandler | None] = {
            HANDLER_RE_ROUTE: self.handler_re_route,
            HANDLER_FLAG: self.handler_flag,
            HANDLER_HUMAN_IN_THE_LOOP: self.handler_human_in_the_loop,
        }
        return handler_map.get(handler_id)


@dataclass(frozen=True)
class EscalationRoute:
    """The result of an escalation routing decision.

    Records which handler was selected, the dispatch decision context,
    and whether the handler was actually invoked.

    Attributes:
        handler_id: The selected handler identifier
            (``re_route``, ``flag``, ``human_in_the_loop``).
        handler: The handler callable that was (or would be) invoked.
            ``None`` if no handler was configured for this type.
        action_description: Human-readable description of the action
            taken and why this route was selected.
        triggered_by: What caused this route to be selected —
            ``margin``, ``risk_tags``, ``stance_dimension``, or
            ``default``.
        gate_result: The threshold gate evaluation that triggered
            escalation (passed through for handler context).
        divergence_report: The divergence report that was evaluated
            (passed through for handler context).
        handler_invoked: True when the handler callable was actually
            called (non-None handler).
        handler_error: Error message if handler invocation failed
            (empty string on success or when no handler).
    """

    handler_id: str
    """The selected handler identifier."""

    handler: EscalationHandler | None
    """The handler callable (None if not configured)."""

    action_description: str
    """Human-readable description of the action taken."""

    triggered_by: str
    """What caused this route: ``margin``, ``risk_tags``,
    ``stance_dimension``, or ``default``."""

    gate_result: GateResult
    """The threshold gate evaluation result."""

    divergence_report: DivergenceReport
    """The divergence report that triggered evaluation."""

    handler_invoked: bool = False
    """True when the handler was actually called."""

    handler_error: str = ""
    """Error message from handler invocation (empty on success)."""

    @property
    def is_re_route(self) -> bool:
        """True when the route is re_route."""
        return self.handler_id == HANDLER_RE_ROUTE

    @property
    def is_flag(self) -> bool:
        """True when the route is flag."""
        return self.handler_id == HANDLER_FLAG

    @property
    def is_human_in_the_loop(self) -> bool:
        """True when the route is human_in_the_loop."""
        return self.handler_id == HANDLER_HUMAN_IN_THE_LOOP

    @property
    def blocks_pipeline(self) -> bool:
        """True when this escalation route blocks further processing.

        Only ``human_in_the_loop`` blocks the pipeline; ``re_route``
        and ``flag`` are non-blocking.
        """
        return self.handler_id == HANDLER_HUMAN_IN_THE_LOOP

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "handler_id": self.handler_id,
            "action_description": self.action_description,
            "triggered_by": self.triggered_by,
            "handler_invoked": self.handler_invoked,
            "handler_error": self.handler_error,
            "gate_result": self.gate_result.to_dict(),
            "divergence_report": self.divergence_report.to_dict(),
        }


# ═════════════════════════════════════════════════════════════════════════
# Default routing logic
# ═════════════════════════════════════════════════════════════════════════


def _default_routing_fn(
    gate_result: GateResult,
    divergence_report: DivergenceReport,
    context: EscalationContext,
    config: EscalationRouteConfig,
) -> tuple[str, EscalationHandler | None, str, str]:
    """Default priority-ordered escalation routing decision.

    Decision tree (evaluated in priority order):

    1. **Risk tags** — if context has any tag in
       ``human_escalation_tags`` → human_in_the_loop (regardless of
       margin).
    2. **High divergence** — margin >= ``margin_human_threshold``
       → human_in_the_loop.
    3. **Moderate divergence** — margin >= ``margin_re_route_threshold``
       → re_route (send to secondary validator).
    4. **Stance/conclusion divergence** — the highest-divergence
       dimension is in ``stance_dimension_names`` and its divergence
       >= 0.15 → re_route (stance disagreement benefits from a
       second opinion even at moderate margins).
    5. **Low divergence** — margin >= ``margin_flag_threshold``
       → flag (mark for attention, non-blocking).
    6. **Default fallback** — flag with ``was_clamped`` rationale.

    Args:
        gate_result: The threshold gate evaluation (must have
            ``exceeded=True``).
        divergence_report: The divergence report from Sub-AC 6.3.1.
        context: Meeting context metadata.
        config: Router configuration.

    Returns:
        Tuple of ``(handler_id, handler, action_description,
        triggered_by)``.
    """
    margin = gate_result.margin

    # ── Priority 1: Risk tags force human-in-the-loop ─────────────────
    if context.has_any_risk_tag(config.human_escalation_tags):
        matching_tags = [
            t
            for t in context.risk_tags
            if t.lower()
            in {ht.lower() for ht in config.human_escalation_tags}
        ]
        desc = (
            f"Risk tags {matching_tags} present in meeting context. "
            f"Divergence margin {margin:.4f}. "
            f"Routing to human_in_the_loop for mandatory review."
        )
        return (
            HANDLER_HUMAN_IN_THE_LOOP,
            config.handler_human_in_the_loop,
            desc,
            "risk_tags",
        )

    # ── Priority 2: Margin-based human escalation ─────────────────────
    if margin >= config.margin_human_threshold:
        desc = (
            f"Divergence margin {margin:.4f} >= human threshold "
            f"{config.margin_human_threshold}. "
            f"Primary {divergence_report.primary_source} vs "
            f"secondary {divergence_report.secondary_source} are "
            f"substantially divergent. Routing to human_in_the_loop."
        )
        return (
            HANDLER_HUMAN_IN_THE_LOOP,
            config.handler_human_in_the_loop,
            desc,
            "margin",
        )

    # ── Priority 3: Re-route for moderate divergence ──────────────────
    if margin >= config.margin_re_route_threshold:
        desc = (
            f"Divergence margin {margin:.4f} >= re-route threshold "
            f"{config.margin_re_route_threshold}. "
            f"Routing to re_route for secondary validator review."
        )
        return (
            HANDLER_RE_ROUTE,
            config.handler_re_route,
            desc,
            "margin",
        )

    # ── Priority 4: Stance/conclusion dimension triggers re-route ─────
    highest_dim = divergence_report.highest_divergence_dimension
    if (
        highest_dim is not None
        and highest_dim.name in config.stance_dimension_names
        and highest_dim.divergence >= 0.15
    ):
        desc = (
            f"Stance/conclusion dimension '{highest_dim.name}' shows "
            f"divergence {highest_dim.divergence:.4f} (weighted "
            f"{highest_dim.weighted_divergence:.4f}). "
            f"Routing to re_route for stance resolution."
        )
        return (
            HANDLER_RE_ROUTE,
            config.handler_re_route,
            desc,
            "stance_dimension",
        )

    # ── Priority 5: Flag for low-but-exceeded divergence ──────────────
    if margin >= config.margin_flag_threshold:
        desc = (
            f"Divergence margin {margin:.4f} >= flag threshold "
            f"{config.margin_flag_threshold}. "
            f"Divergence is present but low — flagging for attention "
            f"without blocking the pipeline."
        )
        return (
            HANDLER_FLAG,
            config.handler_flag,
            desc,
            "margin",
        )

    # ── Priority 6: Default fallback (flag with clamp rationale) ──────
    trigger_reason = "margin" if not gate_result.was_clamped else "default"
    desc = (
        f"Divergence threshold exceeded but margin {margin:.4f} is "
        f"below all routing thresholds. "
        f"Defaulting to flag for manual review."
    )
    if gate_result.was_clamped:
        desc += (
            f" (Note: score was clamped from {gate_result.score} "
            f"to {gate_result.effective_score})"
        )
    return (
        HANDLER_FLAG,
        config.handler_flag,
        desc,
        trigger_reason,
    )


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


def route_escalation(
    gate_result: GateResult,
    divergence_report: DivergenceReport,
    context: EscalationContext,
    config: EscalationRouteConfig | None = None,
    *,
    _invoke_handler: bool = True,
) -> EscalationRoute:
    """Route an exceeded-divergence signal to the appropriate escalation handler.

    This is the main entry point for **Sub-AC 6.3.3**.  It:

    1. Validates that the gate signal indicates threshold exceeded.
    2. Applies routing rules (risk-tag priority → margin → stance →
       default fallback).
    3. Dispatches to the configured handler callable.
    4. Returns an ``EscalationRoute`` with the full decision record.

    If the gate signal is *not* exceeded, the function returns a
    ``no-op`` route that records the non-escalation decision.  This
    is intentional — the Coordinator does not need to branch on
    ``gate_result.exceeded`` before calling this function; it can call
    unconditionally and inspect ``route.handler_invoked`` to determine
    whether any action was taken.

    Args:
        gate_result: The threshold gate evaluation from Sub-AC 6.3.2.
            Must be a ``GateResult`` instance.
        divergence_report: The divergence report from Sub-AC 6.3.1.
            Must be a ``DivergenceReport`` instance.
        context: Meeting context metadata.
        config: ``EscalationRouteConfig`` with handler callables and
            routing thresholds.  Uses defaults when ``None``.
        _invoke_handler: When ``False``, skip handler invocation
            (useful for testing routing logic in isolation).
            ``True`` by default.

    Returns:
        ``EscalationRoute`` with the routing decision and invocation
        result.  Check ``route.handler_invoked`` to determine whether
        a handler was actually called.

    Raises:
        TypeError: If gate_result, divergence_report, or context are
            not of the expected types.
        ValueError: If config validation fails.

    Examples:
        >>> from src.threshold_gate import evaluate_threshold
        >>> from src.divergence_metric import DivergenceReport
        >>>
        >>> gate = evaluate_threshold(0.45, 0.30)
        >>> ctx = EscalationContext(meeting_id="meeting-001")
        >>> calls: list[EscalationRoute] = []
        >>> def mock_handler(route, ctx):
        ...     calls.append(route)
        ...     print(f"Handler: {route.handler_id}")
        >>>
        >>> cfg = EscalationRouteConfig(handler_re_route=mock_handler)
        >>> route = route_escalation(gate, report, ctx, cfg)
        >>> route.handler_invoked
        True
        >>> route.handler_id
        're_route'
        >>> len(calls)
        1

        Non-exceeded gate (no escalation):

        >>> gate = evaluate_threshold(0.15, 0.30)
        >>> route = route_escalation(gate, report, ctx)
        >>> route.handler_invoked
        False
        >>> route.handler_id
        'none'
    """
    # ── Type validation ────────────────────────────────────────────
    if not isinstance(gate_result, GateResult):
        raise TypeError(
            f"gate_result must be GateResult, got "
            f"{type(gate_result).__name__}"
        )
    if not isinstance(divergence_report, DivergenceReport):
        raise TypeError(
            f"divergence_report must be DivergenceReport, got "
            f"{type(divergence_report).__name__}"
        )
    if not isinstance(context, EscalationContext):
        raise TypeError(
            f"context must be EscalationContext, got "
            f"{type(context).__name__}"
        )

    # ── Default config ─────────────────────────────────────────────
    if config is None:
        config = EscalationRouteConfig()

    # ── No-exceeded gate: return no-op route ───────────────────────
    if not gate_result.exceeded:
        return EscalationRoute(
            handler_id="none",
            handler=None,
            action_description=(
                f"Divergence score {gate_result.effective_score:.4f} "
                f"is within threshold {gate_result.effective_threshold:.4f}. "
                f"No escalation needed."
            ),
            triggered_by="none",
            gate_result=gate_result,
            divergence_report=divergence_report,
            handler_invoked=False,
            handler_error="",
        )

    # ── Apply routing logic ────────────────────────────────────────
    if config.routing_fn is not None:
        handler_id, handler, description, triggered_by = config.routing_fn(
            gate_result, divergence_report, context, config
        )
    else:
        handler_id, handler, description, triggered_by = _default_routing_fn(
            gate_result, divergence_report, context, config
        )

    # ── Build route (before invocation) ────────────────────────────
    route = EscalationRoute(
        handler_id=handler_id,
        handler=handler,
        action_description=description,
        triggered_by=triggered_by,
        gate_result=gate_result,
        divergence_report=divergence_report,
        handler_invoked=False,
        handler_error="",
    )

    # ── Invoke handler (unless suppressed) ─────────────────────────
    if _invoke_handler and handler is not None:
        try:
            handler(route, context)
            # Rebuild with invocation flag set (dataclass is frozen)
            object.__setattr__(route, "handler_invoked", True)
        except Exception as exc:
            object.__setattr__(route, "handler_invoked", False)
            object.__setattr__(
                route,
                "handler_error",
                f"{type(exc).__name__}: {exc}",
            )

    return route


def route_escalation_unless_within(
    gate_result: GateResult,
    divergence_report: DivergenceReport,
    context: EscalationContext,
    config: EscalationRouteConfig | None = None,
    *,
    _invoke_handler: bool = True,
) -> EscalationRoute | None:
    """Route escalation only when the gate is exceeded; return ``None`` otherwise.

    Convenience wrapper for callers that want to branch on whether
    escalation occurred rather than inspecting the route.

    Args:
        gate_result: The threshold gate evaluation.
        divergence_report: The divergence report.
        context: Meeting context metadata.
        config: Router configuration (optional).
        _invoke_handler: Whether to invoke the handler callable.

    Returns:
        ``EscalationRoute`` when escalation occurred, ``None`` when
        divergence was within threshold.

    Examples:
        >>> route = route_escalation_unless_within(gate, report, ctx)
        >>> if route is not None:
        ...     print(f"Escalated to: {route.handler_id}")
        ... else:
        ...     print("No escalation needed")
    """
    route = route_escalation(
        gate_result=gate_result,
        divergence_report=divergence_report,
        context=context,
        config=config,
        _invoke_handler=_invoke_handler,
    )
    if route.handler_id == "none":
        return None
    return route
