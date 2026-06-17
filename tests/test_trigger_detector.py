"""Tests for the score-based Codex trigger detection module.

Sub-AC 7.2.1: Score-based trigger detection — evaluates GLM-5.1
confidence scores and multi-model disagreement heuristic against
configurable thresholds; independently testable with mock score
inputs and expected trigger signals.

Test coverage:
- All 7 triggers: individual fire/not-fire behaviour
- Configurable thresholds: custom values change trigger behaviour
- Enable/disable toggles per trigger
- Boundary conditions: exact threshold values
- Edge cases: empty inputs, None disagreement, extreme scores
- TriggerDetectionConfig validation: invalid thresholds
- AreaScore validation: score range, empty name
- TriggerDetectionResult properties: fired(), signal_by_id(), trigger_count
- to_dict() serialization
- Multiple simultaneous triggers
- Immutability of all dataclasses
- Default config vs custom config
- Verdict parsing: pass, conditional_pass, revision_required, escalate, fail
- Risk tag matching: exact, partial, empty
- GLM escalation triggers parsing
- ALL_TRIGGER_IDS completeness (7 triggers)
- HIGH_RISK_TAGS content validation
"""

from __future__ import annotations

import dataclasses

import pytest

from src.trigger_detector import (
    ALL_TRIGGER_IDS,
    HIGH_RISK_TAGS,
    TRIGGER_CRITICAL_AREA,
    TRIGGER_GLM_ESCALATION_FLAGS,
    TRIGGER_HIGH_RISK_TAGS,
    TRIGGER_LOW_OVERALL_CONFIDENCE,
    TRIGGER_MULTI_MODEL_DISAGREEMENT,
    TRIGGER_MULTIPLE_AREAS_BELOW_PAR,
    TRIGGER_VERDICT_ESCALATE_FAIL,
    AreaScore,
    TriggerDetectionConfig,
    TriggerDetectionResult,
    TriggerSignal,
    detect_codex_trigger,
)


# ═════════════════════════════════════════════════════════════════════════
# Trigger 1: Low overall confidence
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerLowOverallConfidence:
    """Trigger 1 fires when overall_score < overall_confidence_threshold (0.75)."""

    def test_fires_when_below_threshold(self) -> None:
        result = detect_codex_trigger(overall_score=0.60)
        assert result.codex_triggered is True
        assert TRIGGER_LOW_OVERALL_CONFIDENCE in result.fired_triggers
        sig = result.signal_by_id(TRIGGER_LOW_OVERALL_CONFIDENCE)
        assert sig is not None
        assert sig.fired is True
        assert "0.60" in sig.description
        assert sig.score_context == 0.60

    def test_does_not_fire_when_above_threshold(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE not in result.fired_triggers

    def test_does_not_fire_at_exact_threshold(self) -> None:
        """Strict less-than: exactly 0.75 does NOT fire."""
        result = detect_codex_trigger(overall_score=0.75)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE not in result.fired_triggers

    def test_fires_at_threshold_minus_epsilon(self) -> None:
        result = detect_codex_trigger(overall_score=0.749)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE in result.fired_triggers

    def test_with_custom_threshold(self) -> None:
        cfg = TriggerDetectionConfig(overall_confidence_threshold=0.85)
        result = detect_codex_trigger(overall_score=0.80, config=cfg)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE in result.fired_triggers

    def test_with_custom_threshold_not_fired(self) -> None:
        cfg = TriggerDetectionConfig(overall_confidence_threshold=0.50)
        result = detect_codex_trigger(overall_score=0.60, config=cfg)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE not in result.fired_triggers

    def test_disabled_toggle_prevents_fire(self) -> None:
        cfg = TriggerDetectionConfig(enable_low_overall_confidence=False)
        result = detect_codex_trigger(overall_score=0.30, config=cfg)
        assert TRIGGER_LOW_OVERALL_CONFIDENCE not in result.fired_triggers
        assert result.codex_triggered is False


# ═════════════════════════════════════════════════════════════════════════
# Trigger 2: Critical area
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerCriticalArea:
    """Trigger 2 fires when any single area score < critical_area_threshold (0.50)."""

    def test_fires_when_area_below_critical(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.80,
            area_scores=[
                AreaScore("requirements_fit", 0.85),
                AreaScore("risk_policy", 0.35),
            ],
        )
        assert TRIGGER_CRITICAL_AREA in result.fired_triggers

    def test_does_not_fire_when_all_areas_above_critical(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.80,
            area_scores=[
                AreaScore("requirements_fit", 0.85),
                AreaScore("risk_policy", 0.75),
            ],
        )
        assert TRIGGER_CRITICAL_AREA not in result.fired_triggers

    def test_no_area_scores_does_not_fire(self) -> None:
        result = detect_codex_trigger(overall_score=0.80)
        assert TRIGGER_CRITICAL_AREA not in result.fired_triggers

    def test_reports_which_areas_are_critical(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.80,
            area_scores=[
                AreaScore("requirements_fit", 0.45),
                AreaScore("factual_grounding", 0.30),
            ],
        )
        sig = result.signal_by_id(TRIGGER_CRITICAL_AREA)
        assert sig is not None
        assert sig.fired is True
        assert "requirements_fit=0.45" in sig.description
        assert "factual_grounding=0.30" in sig.description
        assert sig.score_context == 2.0  # two critical areas

    def test_with_custom_critical_threshold(self) -> None:
        cfg = TriggerDetectionConfig(critical_area_threshold=0.30)
        result = detect_codex_trigger(
            overall_score=0.80,
            area_scores=[AreaScore("risk_policy", 0.35)],
            config=cfg,
        )
        assert TRIGGER_CRITICAL_AREA not in result.fired_triggers

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_critical_area=False)
        result = detect_codex_trigger(
            overall_score=0.80,
            area_scores=[AreaScore("risk_policy", 0.20)],
            config=cfg,
        )
        assert TRIGGER_CRITICAL_AREA not in result.fired_triggers


# ═════════════════════════════════════════════════════════════════════════
# Trigger 3: Multiple areas below par
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerMultipleAreasBelowPar:
    """Trigger 3 fires when 2+ areas < area_below_par_threshold (0.70)."""

    def test_fires_when_two_areas_below_par(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            area_scores=[
                AreaScore("requirements_fit", 0.65),
                AreaScore("logical_consistency", 0.60),
                AreaScore("feasibility", 0.90),
            ],
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR in result.fired_triggers

    def test_does_not_fire_with_only_one_below_par(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            area_scores=[
                AreaScore("requirements_fit", 0.65),
                AreaScore("logical_consistency", 0.85),
                AreaScore("feasibility", 0.90),
            ],
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR not in result.fired_triggers

    def test_does_not_fire_when_all_above_par(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            area_scores=[
                AreaScore("requirements_fit", 0.85),
                AreaScore("risk_policy", 0.80),
            ],
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR not in result.fired_triggers

    def test_custom_min_count(self) -> None:
        cfg = TriggerDetectionConfig(
            area_below_par_threshold=0.70,
            area_below_par_min_count=3,
        )
        result = detect_codex_trigger(
            overall_score=0.85,
            area_scores=[
                AreaScore("requirements_fit", 0.65),
                AreaScore("logical_consistency", 0.60),
            ],
            config=cfg,
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR not in result.fired_triggers

    def test_custom_par_threshold(self) -> None:
        cfg = TriggerDetectionConfig(area_below_par_threshold=0.85)
        result = detect_codex_trigger(
            overall_score=0.90,
            area_scores=[
                AreaScore("requirements_fit", 0.80),
                AreaScore("risk_policy", 0.82),
            ],
            config=cfg,
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR in result.fired_triggers

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_multiple_areas_below_par=False)
        result = detect_codex_trigger(
            overall_score=0.85,
            area_scores=[
                AreaScore("a", 0.60),
                AreaScore("b", 0.60),
            ],
            config=cfg,
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR not in result.fired_triggers

    def test_reports_count_and_areas(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            area_scores=[
                AreaScore("requirements_fit", 0.65),
                AreaScore("risk_policy", 0.55),
                AreaScore("feasibility", 0.68),
            ],
        )
        sig = result.signal_by_id(TRIGGER_MULTIPLE_AREAS_BELOW_PAR)
        assert sig is not None
        assert sig.fired is True
        assert sig.score_context == 3.0


# ═════════════════════════════════════════════════════════════════════════
# Trigger 4: High-risk tags
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerHighRiskTags:
    """Trigger 4 fires when risk_tags intersect HIGH_RISK_TAGS."""

    def test_fires_with_security_tag(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("security",),
        )
        assert TRIGGER_HIGH_RISK_TAGS in result.fired_triggers

    def test_fires_with_multiple_high_risk_tags(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("security", "legal", "budget"),
        )
        assert TRIGGER_HIGH_RISK_TAGS in result.fired_triggers
        sig = result.signal_by_id(TRIGGER_HIGH_RISK_TAGS)
        assert sig is not None
        assert sig.score_context == 3.0

    def test_does_not_fire_with_non_high_risk_tags(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("schedule", "technical"),
        )
        assert TRIGGER_HIGH_RISK_TAGS not in result.fired_triggers

    def test_does_not_fire_with_empty_risk_tags(self) -> None:
        result = detect_codex_trigger(overall_score=0.90, risk_tags=())
        assert TRIGGER_HIGH_RISK_TAGS not in result.fired_triggers

    def test_does_not_fire_with_no_risk_tags(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        assert TRIGGER_HIGH_RISK_TAGS not in result.fired_triggers

    def test_all_high_risk_tags_trigger(self) -> None:
        """Each individual HIGH_RISK_TAG should trigger when present."""
        for tag in HIGH_RISK_TAGS:
            result = detect_codex_trigger(
                overall_score=0.90,
                risk_tags=(tag,),
            )
            assert TRIGGER_HIGH_RISK_TAGS in result.fired_triggers, (
                f"Tag '{tag}' did not trigger high_risk_tags"
            )

    def test_case_insensitive_matching(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("SECURITY", "Legal"),
        )
        assert TRIGGER_HIGH_RISK_TAGS in result.fired_triggers

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_high_risk_tags=False)
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("security", "data_loss"),
            config=cfg,
        )
        assert TRIGGER_HIGH_RISK_TAGS not in result.fired_triggers
        assert result.codex_triggered is False


# ═════════════════════════════════════════════════════════════════════════
# Trigger 5: Multi-model disagreement
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerMultiModelDisagreement:
    """Trigger 5 fires when disagreement_score > disagreement_threshold (0.30)."""

    def test_fires_when_above_threshold(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=0.45,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT in result.fired_triggers

    def test_does_not_fire_when_below_threshold(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=0.15,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers

    def test_does_not_fire_at_exact_threshold(self) -> None:
        """Strict greater-than: exactly 0.30 does NOT fire."""
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=0.30,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers

    def test_does_not_fire_when_none(self) -> None:
        """None disagreement means no assessment possible — do not fire."""
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=None,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers

    def test_with_custom_threshold(self) -> None:
        cfg = TriggerDetectionConfig(disagreement_threshold=0.50)
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=0.45,
            config=cfg,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_multi_model_disagreement=False)
        result = detect_codex_trigger(
            overall_score=0.85,
            disagreement_score=0.80,
            config=cfg,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers
        assert result.codex_triggered is False


# ═════════════════════════════════════════════════════════════════════════
# Trigger 6: GLM escalation flags
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerGlmEscalationFlags:
    """Trigger 6 fires when GLM output contains escalation triggers."""

    def test_fires_with_escalation_triggers(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            gl_escalation_triggers=("high_risk", "irrecoverable"),
        )
        assert TRIGGER_GLM_ESCALATION_FLAGS in result.fired_triggers

    def test_does_not_fire_with_empty_triggers(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            gl_escalation_triggers=(),
        )
        assert TRIGGER_GLM_ESCALATION_FLAGS not in result.fired_triggers

    def test_does_not_fire_when_none_provided(self) -> None:
        result = detect_codex_trigger(overall_score=0.85)
        assert TRIGGER_GLM_ESCALATION_FLAGS not in result.fired_triggers

    def test_reports_escalation_list(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            gl_escalation_triggers=("legal_concern", "budget_risk"),
        )
        sig = result.signal_by_id(TRIGGER_GLM_ESCALATION_FLAGS)
        assert sig is not None
        assert sig.fired is True
        assert "legal_concern" in sig.description
        assert "budget_risk" in sig.description
        assert sig.score_context == 2.0

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_glm_escalation_flags=False)
        result = detect_codex_trigger(
            overall_score=0.85,
            gl_escalation_triggers=("critical_risk",),
            config=cfg,
        )
        assert TRIGGER_GLM_ESCALATION_FLAGS not in result.fired_triggers
        assert result.codex_triggered is False


# ═════════════════════════════════════════════════════════════════════════
# Trigger 7: Verdict escalate/fail
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerVerdictEscalateFail:
    """Trigger 7 fires when GLM-5.1 verdict is 'escalate' or 'fail'."""

    def test_fires_with_escalate(self) -> None:
        result = detect_codex_trigger(overall_score=0.70, gl_verdict="escalate")
        assert TRIGGER_VERDICT_ESCALATE_FAIL in result.fired_triggers

    def test_fires_with_fail(self) -> None:
        result = detect_codex_trigger(overall_score=0.70, gl_verdict="fail")
        assert TRIGGER_VERDICT_ESCALATE_FAIL in result.fired_triggers

    def test_fires_case_insensitive(self) -> None:
        for v in ("ESCALATE", "Fail", "EsCaLaTe"):
            result = detect_codex_trigger(overall_score=0.70, gl_verdict=v)
            assert TRIGGER_VERDICT_ESCALATE_FAIL in result.fired_triggers, (
                f"Verdict '{v}' should fire"
            )

    def test_does_not_fire_with_pass(self) -> None:
        result = detect_codex_trigger(overall_score=0.90, gl_verdict="pass")
        assert TRIGGER_VERDICT_ESCALATE_FAIL not in result.fired_triggers

    def test_does_not_fire_with_conditional_pass(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.82, gl_verdict="conditional_pass"
        )
        assert TRIGGER_VERDICT_ESCALATE_FAIL not in result.fired_triggers

    def test_does_not_fire_with_revision_required(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.65, gl_verdict="revision_required"
        )
        assert TRIGGER_VERDICT_ESCALATE_FAIL not in result.fired_triggers

    def test_disabled_toggle(self) -> None:
        cfg = TriggerDetectionConfig(enable_verdict_escalate_fail=False)
        result = detect_codex_trigger(
            overall_score=0.70, gl_verdict="fail", config=cfg
        )
        assert TRIGGER_VERDICT_ESCALATE_FAIL not in result.fired_triggers
        assert result.codex_triggered is False


# ═════════════════════════════════════════════════════════════════════════
# Multiple simultaneous triggers
# ═════════════════════════════════════════════════════════════════════════


class TestMultipleSimultaneousTriggers:
    """Verify that multiple triggers can fire simultaneously."""

    def test_all_triggers_fire_together(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.55,  # trigger 1
            area_scores=[
                AreaScore("requirements_fit", 0.40),  # trigger 2
                AreaScore("logical_consistency", 0.55),  # trigger 3
                AreaScore("risk_policy", 0.60),  # trigger 3
            ],
            risk_tags=("security",),  # trigger 4
            disagreement_score=0.50,  # trigger 5
            gl_verdict="fail",  # trigger 7
            gl_escalation_triggers=("irrecoverable",),  # trigger 6
        )
        assert result.codex_triggered is True
        assert result.trigger_count == 7
        assert set(result.fired_triggers) == set(ALL_TRIGGER_IDS)

    def test_no_triggers_fire_with_clean_scores(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.92,
            area_scores=[
                AreaScore("requirements_fit", 0.90),
                AreaScore("logical_consistency", 0.88),
                AreaScore("factual_grounding", 0.93),
                AreaScore("feasibility", 0.85),
                AreaScore("risk_policy", 0.90),
            ],
            risk_tags=(),
            gl_verdict="pass",
            gl_escalation_triggers=(),
        )
        assert result.codex_triggered is False
        assert result.trigger_count == 0
        assert result.fired_triggers == ()

    def test_two_triggers_fire(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.60,  # trigger 1
            risk_tags=("budget",),  # trigger 4
            gl_verdict="pass",
        )
        assert result.codex_triggered is True
        assert result.trigger_count == 2
        assert TRIGGER_LOW_OVERALL_CONFIDENCE in result.fired_triggers
        assert TRIGGER_HIGH_RISK_TAGS in result.fired_triggers


# ═════════════════════════════════════════════════════════════════════════
# TriggerDetectionConfig validation
# ═════════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    """TriggerDetectionConfig validates threshold ranges."""

    def test_default_config_is_valid(self) -> None:
        cfg = TriggerDetectionConfig()
        assert cfg.overall_confidence_threshold == 0.75
        assert cfg.critical_area_threshold == 0.50
        assert cfg.area_below_par_threshold == 0.70

    def test_overall_confidence_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="overall_confidence_threshold"):
            TriggerDetectionConfig(overall_confidence_threshold=1.5)

    def test_overall_confidence_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="overall_confidence_threshold"):
            TriggerDetectionConfig(overall_confidence_threshold=-0.1)

    def test_critical_area_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="critical_area_threshold"):
            TriggerDetectionConfig(critical_area_threshold=2.0)

    def test_area_below_par_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="area_below_par_threshold"):
            TriggerDetectionConfig(area_below_par_threshold=-0.5)

    def test_disagreement_threshold_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="disagreement_threshold"):
            TriggerDetectionConfig(disagreement_threshold=1.5)

    def test_area_below_par_min_count_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="area_below_par_min_count"):
            TriggerDetectionConfig(area_below_par_min_count=0)

    def test_area_below_par_min_count_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="area_below_par_min_count"):
            TriggerDetectionConfig(area_below_par_min_count=-1)

    def test_boundary_values_are_valid(self) -> None:
        """0.0 and 1.0 are valid threshold values."""
        cfg = TriggerDetectionConfig(
            overall_confidence_threshold=0.0,
            critical_area_threshold=0.0,
            area_below_par_threshold=1.0,
            disagreement_threshold=1.0,
        )
        assert cfg.overall_confidence_threshold == 0.0
        assert cfg.disagreement_threshold == 1.0

    def test_is_trigger_enabled_returns_correctly(self) -> None:
        cfg = TriggerDetectionConfig(
            enable_low_overall_confidence=True,
            enable_critical_area=False,
        )
        assert cfg.is_trigger_enabled(TRIGGER_LOW_OVERALL_CONFIDENCE) is True
        assert cfg.is_trigger_enabled(TRIGGER_CRITICAL_AREA) is False

    def test_is_trigger_enabled_unknown_id_returns_false(self) -> None:
        cfg = TriggerDetectionConfig()
        assert cfg.is_trigger_enabled("nonexistent_trigger") is False

    def test_all_enabled_by_default(self) -> None:
        cfg = TriggerDetectionConfig()
        for tid in ALL_TRIGGER_IDS:
            assert cfg.is_trigger_enabled(tid) is True, (
                f"Trigger '{tid}' should be enabled by default"
            )


# ═════════════════════════════════════════════════════════════════════════
# AreaScore validation
# ═════════════════════════════════════════════════════════════════════════


class TestAreaScore:
    """AreaScore validates its fields."""

    def test_valid_score(self) -> None:
        a = AreaScore("requirements_fit", 0.85)
        assert a.area_name == "requirements_fit"
        assert a.score == 0.85

    def test_score_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="0.0, 1.0"):
            AreaScore("req", 1.5)

    def test_negative_score_raises(self) -> None:
        with pytest.raises(ValueError, match="0.0, 1.0"):
            AreaScore("req", -0.1)

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="area_name must not be empty"):
            AreaScore("", 0.5)

    def test_whitespace_name_raises(self) -> None:
        with pytest.raises(ValueError, match="area_name must not be empty"):
            AreaScore("   ", 0.5)

    def test_boundary_scores(self) -> None:
        """0.0 and 1.0 are valid."""
        assert AreaScore("a", 0.0).score == 0.0
        assert AreaScore("b", 1.0).score == 1.0

    def test_is_below_helper(self) -> None:
        a = AreaScore("req", 0.60)
        assert a.is_below(0.70) is True
        assert a.is_below(0.60) is False  # strict less-than
        assert a.is_below(0.50) is False

    def test_is_critical_helper(self) -> None:
        a = AreaScore("req", 0.40)
        assert a.is_critical(0.50) is True
        assert a.is_critical(0.40) is False  # strict less-than
        assert a.is_critical(0.30) is False


# ═════════════════════════════════════════════════════════════════════════
# TriggerDetectionResult properties
# ═════════════════════════════════════════════════════════════════════════


class TestTriggerDetectionResult:
    """TriggerDetectionResult properties and methods."""

    def test_signal_by_id_returns_signal(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.60,
            risk_tags=("security",),
        )
        sig = result.signal_by_id(TRIGGER_LOW_OVERALL_CONFIDENCE)
        assert sig is not None
        assert sig.trigger_id == TRIGGER_LOW_OVERALL_CONFIDENCE

    def test_signal_by_id_returns_none_for_disabled(self) -> None:
        cfg = TriggerDetectionConfig(enable_low_overall_confidence=False)
        result = detect_codex_trigger(overall_score=0.60, config=cfg)
        sig = result.signal_by_id(TRIGGER_LOW_OVERALL_CONFIDENCE)
        assert sig is None

    def test_fired_method(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.60,
            risk_tags=("security",),
        )
        assert result.fired(TRIGGER_LOW_OVERALL_CONFIDENCE) is True
        assert result.fired(TRIGGER_HIGH_RISK_TAGS) is True
        assert result.fired(TRIGGER_VERDICT_ESCALATE_FAIL) is False

    def test_fired_returns_false_for_disabled(self) -> None:
        cfg = TriggerDetectionConfig(enable_high_risk_tags=False)
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("security",),
            config=cfg,
        )
        assert result.fired(TRIGGER_HIGH_RISK_TAGS) is False

    def test_fired_returns_false_for_unknown_id(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        assert result.fired("nonexistent") is False

    def test_trigger_count(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.55,
            risk_tags=("legal",),
            gl_verdict="fail",
        )
        assert result.trigger_count == 3

    def test_has_any_trigger_alias(self) -> None:
        result = detect_codex_trigger(overall_score=0.60)
        assert result.has_any_trigger is True
        assert result.has_any_trigger == result.codex_triggered

    def test_to_dict_serialization(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.60,
            area_scores=[AreaScore("requirements_fit", 0.55)],
            risk_tags=("security",),
            disagreement_score=0.40,
            gl_verdict="conditional_pass",
            gl_escalation_triggers=("high_risk",),
        )
        d = result.to_dict()
        assert d["codex_triggered"] is True
        assert isinstance(d["fired_triggers"], list)
        assert len(d["fired_triggers"]) > 0
        assert d["overall_score"] == 0.60
        assert d["gl_verdict"] == "conditional_pass"
        assert d["disagreement_score"] == 0.40
        assert d["risk_tags"] == ["security"]
        assert d["gl_escalation_triggers"] == ["high_risk"]
        assert len(d["area_scores"]) == 1
        assert len(d["signals"]) > 0
        # Verify signal structure
        for s in d["signals"]:
            assert "trigger_id" in s
            assert "fired" in s
            assert "description" in s

    def test_to_dict_with_none_disagreement(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        d = result.to_dict()
        assert d["disagreement_score"] is None

    def test_to_dict_with_empty_inputs(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        d = result.to_dict()
        assert d["area_scores"] == []
        assert d["risk_tags"] == []
        assert d["gl_escalation_triggers"] == []
        assert d["gl_verdict"] == "pass"


# ═════════════════════════════════════════════════════════════════════════
# Immutability
# ═════════════════════════════════════════════════════════════════════════


class TestImmutability:
    """All dataclasses are frozen (immutable)."""

    def test_area_score_is_frozen(self) -> None:
        a = AreaScore("req", 0.90)
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.score = 0.50  # type: ignore[misc]

    def test_config_is_frozen(self) -> None:
        cfg = TriggerDetectionConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.overall_confidence_threshold = 0.50  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.codex_triggered = True  # type: ignore[misc]

    def test_signal_is_frozen(self) -> None:
        sig = TriggerSignal(
            trigger_id="test", fired=True,
            description="test", score_context=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.fired = False  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Constants verification
# ═════════════════════════════════════════════════════════════════════════


class TestConstants:
    """Verify module-level constants."""

    def test_all_trigger_ids_has_seven_triggers(self) -> None:
        assert len(ALL_TRIGGER_IDS) == 7

    def test_all_trigger_ids_are_unique(self) -> None:
        assert len(set(ALL_TRIGGER_IDS)) == 7

    def test_high_risk_tags_content(self) -> None:
        assert "security" in HIGH_RISK_TAGS
        assert "data_loss" in HIGH_RISK_TAGS
        assert "legal" in HIGH_RISK_TAGS
        assert "budget" in HIGH_RISK_TAGS
        assert "brand" in HIGH_RISK_TAGS
        assert "external" in HIGH_RISK_TAGS

    def test_low_risk_tags_not_in_high_risk(self) -> None:
        assert "schedule" not in HIGH_RISK_TAGS
        assert "technical" not in HIGH_RISK_TAGS


# ═════════════════════════════════════════════════════════════════════════
# Input validation
# ═════════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Input validation for detect_codex_trigger."""

    def test_overall_score_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="0.0, 1.0"):
            detect_codex_trigger(overall_score=1.5)

    def test_overall_score_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="0.0, 1.0"):
            detect_codex_trigger(overall_score=-0.1)

    def test_overall_score_boundary_zero(self) -> None:
        result = detect_codex_trigger(overall_score=0.0)
        assert result.overall_score == 0.0
        assert result.codex_triggered is True

    def test_overall_score_boundary_one(self) -> None:
        result = detect_codex_trigger(overall_score=1.0)
        assert result.overall_score == 1.0
        assert result.codex_triggered is False

    def test_risk_tags_normalized_to_lowercase(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("SECURITY", "Budget"),
        )
        assert "security" in result.risk_tags
        assert "budget" in result.risk_tags

    def test_whitespace_risk_tags_are_filtered(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            risk_tags=("  security  ", "", "   "),
        )
        assert result.risk_tags == ("security",)

    def test_gl_escalation_triggers_normalized(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.85,
            gl_escalation_triggers=("  HIGH_RISK  ", ""),
        )
        assert result.gl_escalation_triggers == ("high_risk",)

    def test_gl_verdict_normalized(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.70, gl_verdict="  ESCALATE  "
        )
        assert result.gl_verdict == "escalate"


# ═════════════════════════════════════════════════════════════════════════
# Disagreement score boundary tests
# ═════════════════════════════════════════════════════════════════════════


class TestDisagreementBoundaries:
    """Test disagreement score boundaries."""

    def test_disagreement_zero(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            disagreement_score=0.0,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT not in result.fired_triggers

    def test_disagreement_one(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            disagreement_score=1.0,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT in result.fired_triggers

    def test_disagreement_just_above_threshold(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            disagreement_score=0.31,
        )
        assert TRIGGER_MULTI_MODEL_DISAGREEMENT in result.fired_triggers


# ═════════════════════════════════════════════════════════════════════════
# Partial area scores
# ═════════════════════════════════════════════════════════════════════════


class TestPartialAreaScores:
    """Behaviour when fewer than 5 standard areas are provided."""

    def test_single_area_suffices_for_critical_check(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            area_scores=[AreaScore("risk_policy", 0.30)],
        )
        assert TRIGGER_CRITICAL_AREA in result.fired_triggers

    def test_two_areas_below_par_with_only_two_areas(self) -> None:
        result = detect_codex_trigger(
            overall_score=0.90,
            area_scores=[
                AreaScore("a", 0.60),
                AreaScore("b", 0.65),
            ],
        )
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR in result.fired_triggers

    def test_no_areas_triggers_nothing_area_based(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        assert TRIGGER_CRITICAL_AREA not in result.fired_triggers
        assert TRIGGER_MULTIPLE_AREAS_BELOW_PAR not in result.fired_triggers


# ═════════════════════════════════════════════════════════════════════════
# Config pass-through to result
# ═════════════════════════════════════════════════════════════════════════


class TestConfigPassthrough:
    """The config used is reflected in the result."""

    def test_default_config_reflected(self) -> None:
        result = detect_codex_trigger(overall_score=0.90)
        assert result.config.overall_confidence_threshold == 0.75

    def test_custom_config_reflected(self) -> None:
        cfg = TriggerDetectionConfig(overall_confidence_threshold=0.90)
        result = detect_codex_trigger(overall_score=0.90, config=cfg)
        assert result.config.overall_confidence_threshold == 0.90
