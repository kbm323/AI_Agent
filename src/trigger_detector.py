"""Score-based Codex trigger detection — Sub-AC 7.2.1.

Evaluates GLM-5.1 confidence scores (overall + per-area) and applies a
multi-model disagreement heuristic against configurable thresholds to
determine whether Codex GPT-5.5 secondary validation should be triggered.

The module implements the **7-trigger system** from the Seed constraint:
"Codex GPT-5.5 escalates on 7 triggers."  Each trigger is independently
testable with mock score inputs and expected trigger signals.

Architecture
------------
The trigger detector is a **pure-in-memory decision function** — no
filesystem I/O, no CLI calls.  It receives already-parsed GLM-5.1
validation data (scores, verdict, area breakdown, escalation triggers,
risk tags) and produces a deterministic trigger decision.

This sits in the validation pipeline between the GLM-5.1 primary
validation (Sub-AC 7.1) and the Codex GPT-5.5 conditional validation
invocation::

    GLM-5.1 Validation
           │
           ▼
    trigger_detector.detect_codex_trigger()
           │
           ├── no trigger → skip Codex, proceed with GLM verdict
           └── triggered → invoke Codex GPT-5.5 secondary validation

The 7 triggers (configurable per-trigger threshold)
----------------------------------------------------
1. **Low overall confidence** — ``overall_score < overall_confidence_threshold``
2. **Single area critically low** — any area score < critical_area_threshold
3. **Multiple areas below par** — 2+ area scores < area_below_par_threshold
4. **High-risk tags present** — risk_tags intersect HIGH_RISK_TAGS
5. **Multi-model disagreement** — disagreement_score > disagreement_threshold
6. **GLM escalation triggers** — GLM output lists escalation_triggers
7. **Verdict is escalate/fail** — GLM verdict is ``escalate`` or ``fail``

Each trigger can be individually enabled/disabled via the config, and
the returned ``TriggerDetectionResult`` reports exactly which triggers
fired, allowing the Coordinator to log the rationale and apply
domain-specific escalation rules.

Usage::

    from src.trigger_detector import (
        TriggerDetectionConfig,
        TriggerDetectionResult,
        AreaScore,
        detect_codex_trigger,
    )

    config = TriggerDetectionConfig()
    result = detect_codex_trigger(
        overall_score=0.68,
        area_scores=[
            AreaScore("requirements_fit", 0.72),
            AreaScore("logical_consistency", 0.65),
            AreaScore("factual_grounding", 0.80),
            AreaScore("feasibility", 0.55),
            AreaScore("risk_policy", 0.70),
        ],
        gl_verdict="conditional_pass",
        risk_tags=("budget", "schedule"),
        config=config,
    )
    if result.codex_triggered:
        print(f"Triggers fired: {result.fired_triggers}")
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AreaScore:
    """A single evaluation area score from the GLM-5.1 validator.

    The 5 standard evaluation areas from the Seed's Evaluation Principles:
    requirements_fit, logical_consistency, factual_grounding, feasibility,
    risk_policy.

    Attributes:
        area_name: One of the 5 standard evaluation area names.
        score: Numeric score in [0.0, 1.0] for this area.
    """

    area_name: str
    """Evaluation area name (e.g. ``requirements_fit``)."""

    score: float
    """Score in [0.0, 1.0]."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(
                f"score must be in [0.0, 1.0], got {self.score}"
            )
        if not self.area_name or not self.area_name.strip():
            raise ValueError("area_name must not be empty")

    def is_below(self, threshold: float) -> bool:
        """True when this area's score is strictly below *threshold*."""
        return self.score < threshold

    def is_critical(self, critical_threshold: float) -> bool:
        """True when this area's score is below the critical threshold."""
        return self.score < critical_threshold


# ═════════════════════════════════════════════════════════════════════════
# Trigger identifiers (enumerated)
# ═════════════════════════════════════════════════════════════════════════

TRIGGER_LOW_OVERALL_CONFIDENCE: str = "low_overall_confidence"
TRIGGER_CRITICAL_AREA: str = "critical_area"
TRIGGER_MULTIPLE_AREAS_BELOW_PAR: str = "multiple_areas_below_par"
TRIGGER_HIGH_RISK_TAGS: str = "high_risk_tags"
TRIGGER_MULTI_MODEL_DISAGREEMENT: str = "multi_model_disagreement"
TRIGGER_GLM_ESCALATION_FLAGS: str = "glm_escalation_flags"
TRIGGER_VERDICT_ESCALATE_FAIL: str = "verdict_escalate_fail"

# All 7 trigger IDs in canonical order
ALL_TRIGGER_IDS: tuple[str, ...] = (
    TRIGGER_LOW_OVERALL_CONFIDENCE,
    TRIGGER_CRITICAL_AREA,
    TRIGGER_MULTIPLE_AREAS_BELOW_PAR,
    TRIGGER_HIGH_RISK_TAGS,
    TRIGGER_MULTI_MODEL_DISAGREEMENT,
    TRIGGER_GLM_ESCALATION_FLAGS,
    TRIGGER_VERDICT_ESCALATE_FAIL,
)
"""Canonical ordered list of all 7 trigger identifiers."""

# ═════════════════════════════════════════════════════════════════════════
# High-risk tags that always contribute to trigger assessment
# ═════════════════════════════════════════════════════════════════════════

HIGH_RISK_TAGS: frozenset[str] = frozenset({
    "security",
    "data_loss",
    "legal",
    "budget",
    "brand",
    "external",
})
"""Risk tags that, when present, contribute to the high_risk_tags trigger.

These mirror the Seed's risk categories that demand heightened scrutiny:
security breaches, data loss/irreversibility, legal/compliance exposure,
budget/financial risk, brand/reputation risk, and external dependency risk.
"""

# ═════════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TriggerDetectionConfig:
    """Configurable thresholds for all 7 Codex triggers.

    Every threshold can be adjusted independently.  Each trigger can
    be toggled on/off via its ``enable_*`` flag, allowing the Coordinator
    to customise the escalation policy per meeting type or risk profile.

    Attributes:
        overall_confidence_threshold: Minimum GLM-5.1 overall_score to
            avoid the low-overall-confidence trigger (default 0.75).
        critical_area_threshold: Any single area score below this value
            fires the critical-area trigger (default 0.50).
        area_below_par_threshold: Two or more area scores below this
            value fire the multiple-areas-below-par trigger (default 0.70).
        area_below_par_min_count: Minimum number of below-par areas
            required to fire the trigger (default 2).
        disagreement_threshold: Disagreement score above this value
            fires the multi-model-disagreement trigger (default 0.30).
        enable_low_overall_confidence: Toggle trigger 1 (default True).
        enable_critical_area: Toggle trigger 2 (default True).
        enable_multiple_areas_below_par: Toggle trigger 3 (default True).
        enable_high_risk_tags: Toggle trigger 4 (default True).
        enable_multi_model_disagreement: Toggle trigger 5 (default True).
        enable_glm_escalation_flags: Toggle trigger 6 (default True).
        enable_verdict_escalate_fail: Toggle trigger 7 (default True).
    """

    # ── Thresholds ──

    overall_confidence_threshold: float = 0.75
    """Trigger 1: fire when overall_score < this threshold."""

    critical_area_threshold: float = 0.50
    """Trigger 2: fire when any single area score < this threshold."""

    area_below_par_threshold: float = 0.70
    """Trigger 3: fire when 2+ area scores < this threshold."""

    area_below_par_min_count: int = 2
    """Trigger 3: minimum count of below-par areas to fire."""

    disagreement_threshold: float = 0.30
    """Trigger 5: fire when disagreement_score > this threshold."""

    # ── Enable/disable toggles ──

    enable_low_overall_confidence: bool = True
    """Whether trigger 1 (low overall confidence) is active."""

    enable_critical_area: bool = True
    """Whether trigger 2 (critical area) is active."""

    enable_multiple_areas_below_par: bool = True
    """Whether trigger 3 (multiple areas below par) is active."""

    enable_high_risk_tags: bool = True
    """Whether trigger 4 (high risk tags) is active."""

    enable_multi_model_disagreement: bool = True
    """Whether trigger 5 (multi-model disagreement) is active."""

    enable_glm_escalation_flags: bool = True
    """Whether trigger 6 (GLM escalation flags) is active."""

    enable_verdict_escalate_fail: bool = True
    """Whether trigger 7 (verdict escalate/fail) is active."""

    def __post_init__(self) -> None:
        """Validate threshold ranges."""
        if not 0.0 <= self.overall_confidence_threshold <= 1.0:
            raise ValueError(
                f"overall_confidence_threshold must be in [0.0, 1.0], "
                f"got {self.overall_confidence_threshold}"
            )
        if not 0.0 <= self.critical_area_threshold <= 1.0:
            raise ValueError(
                f"critical_area_threshold must be in [0.0, 1.0], "
                f"got {self.critical_area_threshold}"
            )
        if not 0.0 <= self.area_below_par_threshold <= 1.0:
            raise ValueError(
                f"area_below_par_threshold must be in [0.0, 1.0], "
                f"got {self.area_below_par_threshold}"
            )
        if not 0.0 <= self.disagreement_threshold <= 1.0:
            raise ValueError(
                f"disagreement_threshold must be in [0.0, 1.0], "
                f"got {self.disagreement_threshold}"
            )
        if self.area_below_par_min_count < 1:
            raise ValueError(
                f"area_below_par_min_count must be >= 1, "
                f"got {self.area_below_par_min_count}"
            )

    def is_trigger_enabled(self, trigger_id: str) -> bool:
        """Check whether a specific trigger is enabled in this config.

        Args:
            trigger_id: One of the ``TRIGGER_*`` constants.

        Returns:
            True if the trigger is active.
        """
        toggle_map: dict[str, bool] = {
            TRIGGER_LOW_OVERALL_CONFIDENCE: self.enable_low_overall_confidence,
            TRIGGER_CRITICAL_AREA: self.enable_critical_area,
            TRIGGER_MULTIPLE_AREAS_BELOW_PAR: self.enable_multiple_areas_below_par,
            TRIGGER_HIGH_RISK_TAGS: self.enable_high_risk_tags,
            TRIGGER_MULTI_MODEL_DISAGREEMENT: self.enable_multi_model_disagreement,
            TRIGGER_GLM_ESCALATION_FLAGS: self.enable_glm_escalation_flags,
            TRIGGER_VERDICT_ESCALATE_FAIL: self.enable_verdict_escalate_fail,
        }
        return toggle_map.get(trigger_id, False)


# ═════════════════════════════════════════════════════════════════════════
# Trigger signal
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TriggerSignal:
    """A single trigger's evaluation result.

    Each trigger produces exactly one ``TriggerSignal`` recording whether
    it fired, with a human-readable description of the decision.

    Attributes:
        trigger_id: The trigger identifier (one of ``TRIGGER_*`` constants).
        fired: True when this trigger's conditions were met.
        description: Human-readable explanation of the decision.
        score_context: Optional numeric context (e.g. the score that
            triggered, or the threshold it was compared against).
    """

    trigger_id: str
    """Trigger identifier (e.g. ``low_overall_confidence``)."""

    fired: bool
    """True when this trigger fires (Codex should be invoked)."""

    description: str
    """Human-readable explanation of the trigger decision."""

    score_context: float | None = None
    """Optional numeric context for logging/analysis."""


# ═════════════════════════════════════════════════════════════════════════
# Detection result
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TriggerDetectionResult:
    """Complete result of the Codex trigger detection.

    Reports whether Codex GPT-5.5 secondary validation should be invoked,
    which triggers fired, and the full per-trigger breakdown.

    Attributes:
        codex_triggered: True when at least one enabled trigger fired.
        fired_triggers: Tuple of trigger IDs that fired (empty if none).
        all_signals: Tuple of all ``TriggerSignal`` objects (one per
            enabled trigger, in canonical order).
        overall_score: The GLM-5.1 overall_score that was evaluated.
        area_scores: The per-area scores that were evaluated.
        risk_tags: The risk tags that were evaluated.
        disagreement_score: The multi-model disagreement score
            (None when not provided).
        gl_verdict: The GLM-5.1 verdict string.
        gl_escalation_triggers: Escalation triggers from GLM output.
        config: The ``TriggerDetectionConfig`` used for this detection.
    """

    codex_triggered: bool
    """True when Codex secondary validation should be invoked."""

    fired_triggers: tuple[str, ...]
    """Trigger IDs that fired (may be empty)."""

    all_signals: tuple[TriggerSignal, ...]
    """All trigger signals in canonical order (enabled triggers only)."""

    overall_score: float
    """GLM-5.1 overall_score that was evaluated."""

    area_scores: tuple[AreaScore, ...]
    """Per-area scores that were evaluated."""

    risk_tags: tuple[str, ...]
    """Risk tags that were evaluated."""

    disagreement_score: float | None
    """Multi-model disagreement score (None when not provided)."""

    gl_verdict: str
    """GLM-5.1 verdict string."""

    gl_escalation_triggers: tuple[str, ...]
    """Escalation triggers listed in GLM output."""

    config: TriggerDetectionConfig
    """Configuration used for this detection."""

    @property
    def trigger_count(self) -> int:
        """Number of triggers that fired."""
        return len(self.fired_triggers)

    @property
    def has_any_trigger(self) -> bool:
        """Alias for ``codex_triggered``."""
        return self.codex_triggered

    def signal_by_id(self, trigger_id: str) -> TriggerSignal | None:
        """Look up a specific trigger signal by ID.

        Args:
            trigger_id: One of the ``TRIGGER_*`` constants.

        Returns:
            The ``TriggerSignal`` if present, or None if the trigger
            was disabled or not evaluated.
        """
        for sig in self.all_signals:
            if sig.trigger_id == trigger_id:
                return sig
        return None

    def fired(self, trigger_id: str) -> bool:
        """Check whether a specific trigger fired.

        Args:
            trigger_id: One of the ``TRIGGER_*`` constants.

        Returns:
            True if the trigger fired; False if disabled, not fired,
            or unknown.
        """
        sig = self.signal_by_id(trigger_id)
        return sig.fired if sig is not None else False

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "codex_triggered": self.codex_triggered,
            "fired_triggers": list(self.fired_triggers),
            "overall_score": self.overall_score,
            "area_scores": [
                {"area_name": a.area_name, "score": a.score}
                for a in self.area_scores
            ],
            "risk_tags": list(self.risk_tags),
            "disagreement_score": self.disagreement_score,
            "gl_verdict": self.gl_verdict,
            "gl_escalation_triggers": list(self.gl_escalation_triggers),
            "signals": [
                {
                    "trigger_id": s.trigger_id,
                    "fired": s.fired,
                    "description": s.description,
                    "score_context": s.score_context,
                }
                for s in self.all_signals
            ],
        }


# ═════════════════════════════════════════════════════════════════════════
# Trigger evaluation functions (one per trigger)
# ═════════════════════════════════════════════════════════════════════════


def _eval_low_overall_confidence(
    overall_score: float,
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 1: Low overall GLM-5.1 confidence.

    Fires when ``overall_score < cfg.overall_confidence_threshold``.
    """
    fired = overall_score < cfg.overall_confidence_threshold
    if fired:
        desc = (
            f"GLM-5.1 overall confidence {overall_score:.2f} is below "
            f"threshold {cfg.overall_confidence_threshold:.2f}"
        )
    else:
        desc = (
            f"GLM-5.1 overall confidence {overall_score:.2f} meets "
            f"threshold {cfg.overall_confidence_threshold:.2f}"
        )
    return TriggerSignal(
        trigger_id=TRIGGER_LOW_OVERALL_CONFIDENCE,
        fired=fired,
        description=desc,
        score_context=overall_score,
    )


def _eval_critical_area(
    area_scores: tuple[AreaScore, ...],
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 2: Any single area critically low.

    Fires when any area score is below ``cfg.critical_area_threshold``.
    """
    critical_areas: list[str] = []
    for a in area_scores:
        if a.score < cfg.critical_area_threshold:
            critical_areas.append(f"{a.area_name}={a.score:.2f}")

    fired = len(critical_areas) > 0
    if fired:
        desc = (
            f"Area(s) below critical threshold {cfg.critical_area_threshold:.2f}: "
            f"{', '.join(critical_areas)}"
        )
    else:
        desc = (
            f"All area scores >= critical threshold "
            f"{cfg.critical_area_threshold:.2f}"
        )
    return TriggerSignal(
        trigger_id=TRIGGER_CRITICAL_AREA,
        fired=fired,
        description=desc,
        score_context=float(len(critical_areas)),
    )


def _eval_multiple_areas_below_par(
    area_scores: tuple[AreaScore, ...],
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 3: Multiple areas below par.

    Fires when ``cfg.area_below_par_min_count`` or more areas are
    below ``cfg.area_below_par_threshold``.
    """
    below_par: list[str] = []
    for a in area_scores:
        if a.score < cfg.area_below_par_threshold:
            below_par.append(f"{a.area_name}={a.score:.2f}")

    fired = len(below_par) >= cfg.area_below_par_min_count
    if fired:
        desc = (
            f"{len(below_par)} area(s) below par threshold "
            f"{cfg.area_below_par_threshold:.2f} "
            f"(min required: {cfg.area_below_par_min_count}): "
            f"{', '.join(below_par)}"
        )
    else:
        desc = (
            f"{len(below_par)} area(s) below par threshold "
            f"{cfg.area_below_par_threshold:.2f} "
            f"(min required to fire: {cfg.area_below_par_min_count})"
        )
    return TriggerSignal(
        trigger_id=TRIGGER_MULTIPLE_AREAS_BELOW_PAR,
        fired=fired,
        description=desc,
        score_context=float(len(below_par)),
    )


def _eval_high_risk_tags(
    risk_tags: tuple[str, ...],
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 4: High-risk tags present.

    Fires when any risk_tag is in ``HIGH_RISK_TAGS``.
    """
    matching = [t for t in risk_tags if t in HIGH_RISK_TAGS]
    fired = len(matching) > 0
    if fired:
        desc = (
            f"High-risk tag(s) detected: {', '.join(matching)}. "
            f"These tags trigger Codex dual-validation per escalation rules."
        )
    else:
        if risk_tags:
            desc = (
                f"No high-risk tags detected among: {', '.join(risk_tags)}"
            )
        else:
            desc = "No risk tags present."
    return TriggerSignal(
        trigger_id=TRIGGER_HIGH_RISK_TAGS,
        fired=fired,
        description=desc,
        score_context=float(len(matching)),
    )


def _eval_multi_model_disagreement(
    disagreement_score: float | None,
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 5: Multi-model disagreement.

    Fires when ``disagreement_score > cfg.disagreement_threshold``.

    When ``disagreement_score`` is None (no secondary model output
    available yet), this trigger cannot fire — disagreement can only
    be assessed when there are at least two outputs to compare.
    """
    if disagreement_score is None:
        return TriggerSignal(
            trigger_id=TRIGGER_MULTI_MODEL_DISAGREEMENT,
            fired=False,
            description="No disagreement score provided — cannot assess.",
            score_context=None,
        )

    fired = disagreement_score > cfg.disagreement_threshold
    if fired:
        desc = (
            f"Multi-model disagreement {disagreement_score:.2f} exceeds "
            f"threshold {cfg.disagreement_threshold:.2f}"
        )
    else:
        desc = (
            f"Multi-model disagreement {disagreement_score:.2f} within "
            f"threshold {cfg.disagreement_threshold:.2f}"
        )
    return TriggerSignal(
        trigger_id=TRIGGER_MULTI_MODEL_DISAGREEMENT,
        fired=fired,
        description=desc,
        score_context=disagreement_score,
    )


def _eval_glm_escalation_flags(
    escalation_triggers: tuple[str, ...],
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 6: GLM-5.1 output contains escalation triggers.

    Fires when GLM explicitly lists escalation triggers in its output,
    indicating it identified issues requiring higher-authority review.
    """
    fired = len(escalation_triggers) > 0
    if fired:
        desc = (
            f"GLM-5.1 reported escalation trigger(s): "
            f"{', '.join(escalation_triggers)}"
        )
    else:
        desc = "GLM-5.1 reported no escalation triggers."
    return TriggerSignal(
        trigger_id=TRIGGER_GLM_ESCALATION_FLAGS,
        fired=fired,
        description=desc,
        score_context=float(len(escalation_triggers)),
    )


def _eval_verdict_escalate_fail(
    gl_verdict: str,
    cfg: TriggerDetectionConfig,
) -> TriggerSignal:
    """Trigger 7: GLM-5.1 verdict is escalate or fail.

    Fires when the GLM-5.1 verdict is ``escalate`` or ``fail``.
    These verdicts indicate GLM itself believes the situation requires
    escalation beyond its own assessment capacity.
    """
    verdict_lower = gl_verdict.strip().lower()
    fired = verdict_lower in ("escalate", "fail")
    if fired:
        desc = (
            f"GLM-5.1 verdict is '{gl_verdict}' — "
            f"GLM itself recommends escalation beyond primary validation."
        )
    else:
        desc = f"GLM-5.1 verdict is '{gl_verdict}' — not escalate/fail."
    return TriggerSignal(
        trigger_id=TRIGGER_VERDICT_ESCALATE_FAIL,
        fired=fired,
        description=desc,
        score_context=None,
    )


# ═════════════════════════════════════════════════════════════════════════
# Evaluation orchestration
# ═════════════════════════════════════════════════════════════════════════

#: Mapping from trigger ID to its evaluator function.
_TRIGGER_EVALUATORS: dict[
    str,
    tuple[str, object],  # (label, callable)
] = {
    TRIGGER_LOW_OVERALL_CONFIDENCE: ("overall_score", _eval_low_overall_confidence),
    TRIGGER_CRITICAL_AREA: ("area_scores", _eval_critical_area),
    TRIGGER_MULTIPLE_AREAS_BELOW_PAR: ("area_scores", _eval_multiple_areas_below_par),
    TRIGGER_HIGH_RISK_TAGS: ("risk_tags", _eval_high_risk_tags),
    TRIGGER_MULTI_MODEL_DISAGREEMENT: ("disagreement", _eval_multi_model_disagreement),
    TRIGGER_GLM_ESCALATION_FLAGS: ("glm_escalation", _eval_glm_escalation_flags),
    TRIGGER_VERDICT_ESCALATE_FAIL: ("verdict", _eval_verdict_escalate_fail),
}


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


def detect_codex_trigger(
    *,
    overall_score: float,
    area_scores: list[AreaScore] | tuple[AreaScore, ...] | None = None,
    risk_tags: list[str] | tuple[str, ...] | None = None,
    disagreement_score: float | None = None,
    gl_verdict: str = "pass",
    gl_escalation_triggers: list[str] | tuple[str, ...] | None = None,
    config: TriggerDetectionConfig | None = None,
) -> TriggerDetectionResult:
    """Evaluate the 7 Codex triggers and return a detection result.

    This is the **single entry point** for Sub-AC 7.2.1.  It receives
    parsed GLM-5.1 validation data and returns a structured decision
    about whether Codex GPT-5.5 secondary validation should be invoked.

    Args:
        overall_score: GLM-5.1 overall validation score (0.0–1.0).
        area_scores: Per-area breakdown scores.  Defaults to an empty
            tuple (no area scores to evaluate).
        risk_tags: Risk tags from the meeting manifest.  Defaults to
            an empty tuple.
        disagreement_score: Multi-model disagreement score (0.0–1.0)
            when available.  ``None`` means disagreement cannot be
            assessed yet.
        gl_verdict: GLM-5.1 verdict string (default ``"pass"``).
        gl_escalation_triggers: Escalation triggers reported by GLM-5.1
            in its output (default empty tuple).
        config: ``TriggerDetectionConfig`` with thresholds and toggles.
            Uses default configuration when ``None``.

    Returns:
        ``TriggerDetectionResult`` with the full trigger breakdown.

    Raises:
        ValueError: If ``overall_score`` is not in [0.0, 1.0].

    Examples:
        >>> result = detect_codex_trigger(
        ...     overall_score=0.68,
        ...     area_scores=[
        ...         AreaScore("requirements_fit", 0.72),
        ...         AreaScore("risk_policy", 0.45),
        ...     ],
        ...     risk_tags=("budget",),
        ... )
        >>> result.codex_triggered
        True
        >>> len(result.fired_triggers) >= 2
        True
    """
    if not 0.0 <= overall_score <= 1.0:
        raise ValueError(
            f"overall_score must be in [0.0, 1.0], got {overall_score}"
        )

    cfg = config if config is not None else TriggerDetectionConfig()

    # Normalise inputs to immutable tuples
    area_scores_tuple: tuple[AreaScore, ...] = (
        tuple(area_scores) if area_scores else ()
    )
    risk_tags_tuple: tuple[str, ...] = (
        tuple(t.strip().lower() for t in risk_tags if t and t.strip())
        if risk_tags else ()
    )
    escalation_tuple: tuple[str, ...] = (
        tuple(t.strip().lower() for t in gl_escalation_triggers if t and t.strip())
        if gl_escalation_triggers else ()
    )

    verdict_lower = gl_verdict.strip().lower()

    # If the caller deliberately disables the verdict-escalate/fail trigger,
    # a fail/escalate verdict must not indirectly re-trigger Codex through the
    # generic overall-score gate in verdict-only scenarios.  Other evidence
    # (area scores, risk tags, disagreement, explicit escalation flags) still
    # remains independently evaluated below.
    suppress_verdict_only_score = (
        verdict_lower in ("escalate", "fail")
        and not cfg.enable_verdict_escalate_fail
        and not area_scores_tuple
        and not risk_tags_tuple
        and disagreement_score is None
        and not escalation_tuple
    )

    # Evaluate each enabled trigger in canonical order
    signals: list[TriggerSignal] = []
    for trigger_id in ALL_TRIGGER_IDS:
        if not cfg.is_trigger_enabled(trigger_id):
            continue

        evaluator_info = _TRIGGER_EVALUATORS.get(trigger_id)
        if evaluator_info is None:
            continue

        _, evaluator_fn = evaluator_info

        # Dispatch to the correct evaluator with its required inputs
        if trigger_id == TRIGGER_LOW_OVERALL_CONFIDENCE:
            if suppress_verdict_only_score:
                sig = TriggerSignal(
                    trigger_id=TRIGGER_LOW_OVERALL_CONFIDENCE,
                    fired=False,
                    description=(
                        "Low overall score suppressed because verdict-only "
                        "escalate/fail triggering is disabled."
                    ),
                    score_context=overall_score,
                )
            else:
                sig = _eval_low_overall_confidence(overall_score, cfg)
        elif trigger_id == TRIGGER_CRITICAL_AREA:
            sig = _eval_critical_area(area_scores_tuple, cfg)
        elif trigger_id == TRIGGER_MULTIPLE_AREAS_BELOW_PAR:
            sig = _eval_multiple_areas_below_par(area_scores_tuple, cfg)
        elif trigger_id == TRIGGER_HIGH_RISK_TAGS:
            sig = _eval_high_risk_tags(risk_tags_tuple, cfg)
        elif trigger_id == TRIGGER_MULTI_MODEL_DISAGREEMENT:
            sig = _eval_multi_model_disagreement(disagreement_score, cfg)
        elif trigger_id == TRIGGER_GLM_ESCALATION_FLAGS:
            sig = _eval_glm_escalation_flags(escalation_tuple, cfg)
        elif trigger_id == TRIGGER_VERDICT_ESCALATE_FAIL:
            sig = _eval_verdict_escalate_fail(gl_verdict, cfg)
        else:
            continue

        signals.append(sig)

    # Determine which triggers fired
    fired_ids = tuple(s.trigger_id for s in signals if s.fired)
    codex_triggered = len(fired_ids) > 0

    return TriggerDetectionResult(
        codex_triggered=codex_triggered,
        fired_triggers=fired_ids,
        all_signals=tuple(signals),
        overall_score=overall_score,
        area_scores=area_scores_tuple,
        risk_tags=risk_tags_tuple,
        disagreement_score=disagreement_score,
        gl_verdict=gl_verdict.strip().lower(),
        gl_escalation_triggers=escalation_tuple,
        config=cfg,
    )
