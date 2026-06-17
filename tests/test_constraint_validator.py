"""Comprehensive tests for the constraint validator module (Sub-AC 3.3.2).

Covers:
- SafeEvaluator: basic expressions, membership, comparisons, boolean logic
- SafeEvaluator: generator expressions, any()/all(), list comprehensions
- SafeEvaluator: conditional (if-else) expressions
- _transform_if_then_expr: if/then guardrail pattern conversion
- _normalize_assignment_to_comparison: = true → == True
- _is_evaluable: evaluable vs non-evaluable rule detection
- validate_route: happy path (known-good routes pass)
- validate_route: violation detection (known-bad routes fail)
- validate_route: multiple simultaneous violations
- validate_route: empty rules, empty route
- validate_route: TypeError on non-dict route
- validate_route_from_store: integration with RuleStore
- ConstraintViolation / ConstraintValidationResult: to_dict, properties
- Real routing_rules.yaml guardrails: 16 rules load and classify
- Real rules: known-good route passes all
- Real rules: known-bad routes detect specific violations
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.rule_store import (
    RuleStore,
    StaticConstraintRule,
    load_rules_from_config,
)
from src.constraint_validator import (
    ConstraintValidationResult,
    ConstraintViolation,
    _SafeEvaluator,
    _is_evaluable,
    _normalize_assignment_to_comparison,
    _transform_if_then_expr,
    validate_route,
    validate_route_from_store,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def simple_rules() -> tuple[StaticConstraintRule, ...]:
    """A minimal set of 3 constraint rules for isolated testing."""
    return (
        StaticConstraintRule(
            rule_id="max_roles",
            description="Maximum 7 roles total",
            rule_expr="len(required_roles) + len(optional_roles) <= 7",
            enforcement="truncate_optional_roles_by_expertise_match",
            category="role_limits",
        ),
        StaticConstraintRule(
            rule_id="validator_always",
            description="Validator must be in required_roles",
            rule_expr="'validator' in required_roles",
            enforcement="force_add_validator",
            category="role_limits",
        ),
        StaticConstraintRule(
            rule_id="max_required",
            description="Max 6 required roles",
            rule_expr="len(required_roles) <= 6",
            enforcement="demote_lowest_priority_required_to_optional",
            category="role_limits",
        ),
    )


@pytest.fixture
def good_route() -> dict:
    """A known-good route that passes all simple_rules."""
    return {
        "required_roles": ["content-director", "validator", "art-director"],
        "optional_roles": ["script-writer", "character-designer"],
               "risk_tags": [],
        "codex_required": False,
        "round_count": 2,
    }


@pytest.fixture
def real_store() -> RuleStore:
    """The real routing_rules.yaml loaded once per module."""
    return load_rules_from_config(
        Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. SafeEvaluator — basic expressions
# ═══════════════════════════════════════════════════════════════════════════


class TestSafeEvaluatorBasic:
    """Core expression evaluation: literals, arithmetic, comparisons."""

    def test_literal_constants(self) -> None:
        e = _SafeEvaluator({})
        assert e.evaluate("42") == 42
        assert e.evaluate("3.14") == 3.14
        assert e.evaluate("'hello'") == "hello"
        assert e.evaluate("True") is True
        assert e.evaluate("False") is False
        assert e.evaluate("None") is None

    def test_name_lookup(self) -> None:
        e = _SafeEvaluator({"x": 5, "name": "test"})
        assert e.evaluate("x") == 5
        assert e.evaluate("name") == "test"

    def test_name_not_found_raises(self) -> None:
        e = _SafeEvaluator({})
        with pytest.raises(ValueError, match="not defined"):
            e.evaluate("nonexistent_variable")

    def test_arithmetic(self) -> None:
        e = _SafeEvaluator({"a": 10, "b": 3})
        assert e.evaluate("a + b") == 13
        assert e.evaluate("a - b") == 7
        assert e.evaluate("a * b") == 30
        assert e.evaluate("a / b") == pytest.approx(3.333, rel=1e-3)
        assert e.evaluate("a // b") == 3
        assert e.evaluate("a % b") == 1
        assert e.evaluate("a ** b") == 1000

    def test_comparisons(self) -> None:
        e = _SafeEvaluator({"x": 5, "y": 10})
        assert e.evaluate("x < y") is True
        assert e.evaluate("x <= 5") is True
        assert e.evaluate("x > y") is False
        assert e.evaluate("x >= 5") is True
        assert e.evaluate("x == 5") is True
        assert e.evaluate("x != y") is True

    def test_chained_comparisons(self) -> None:
        e = _SafeEvaluator({"x": 5})
        assert e.evaluate("1 < x < 10") is True
        assert e.evaluate("1 < x < 4") is False

    def test_boolean_operators(self) -> None:
        e = _SafeEvaluator({"a": True, "b": False})
        assert e.evaluate("a and b") is False
        assert e.evaluate("a or b") is True
        assert e.evaluate("not a") is False
        assert e.evaluate("not b") is True

    def test_unary_operators(self) -> None:
        e = _SafeEvaluator({"x": 5})
        assert e.evaluate("-x") == -5
        assert e.evaluate("+x") == 5

    def test_membership(self) -> None:
        e = _SafeEvaluator({"items": ["a", "b", "c"]})
        assert e.evaluate("'a' in items") is True
        assert e.evaluate("'z' in items") is False
        assert e.evaluate("'z' not in items") is True

    def test_len_builtin(self) -> None:
        e = _SafeEvaluator({"items": ["a", "b", "c"]})
        assert e.evaluate("len(items)") == 3
        assert e.evaluate("len(items) == 3") is True
        assert e.evaluate("len(items) > 2") is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. SafeEvaluator — advanced expressions
# ═══════════════════════════════════════════════════════════════════════════


class TestSafeEvaluatorAdvanced:
    """Generator expressions, any/all, list comprehensions, conditionals."""

    def test_any_function(self) -> None:
        e = _SafeEvaluator({"risk_tags": ["security_risk", "data_loss"]})
        assert e.evaluate(
            "any(tag in risk_tags for tag in ['security_risk'])"
        ) is True
        assert e.evaluate(
            "any(tag in risk_tags for tag in ['legal_exposure'])"
        ) is False

    def test_all_function(self) -> None:
        e = _SafeEvaluator({"roles": ["a", "b"]})
        assert e.evaluate("all(r in roles for r in ['a', 'b'])") is True
        assert e.evaluate("all(r in roles for r in ['a', 'c'])") is False

    def test_list_comprehension(self) -> None:
        e = _SafeEvaluator({"items": [1, 2, 3]})
        result = e.evaluate("[x * 2 for x in items]")
        assert result == [2, 4, 6]

    def test_list_comprehension_with_filter(self) -> None:
        e = _SafeEvaluator({"items": [1, 2, 3, 4, 5]})
        result = e.evaluate("[x for x in items if x > 2]")
        assert result == [3, 4, 5]

    def test_conditional_expression(self) -> None:
        e = _SafeEvaluator({"x": 5})
        assert e.evaluate("'big' if x > 3 else 'small'") == "big"
        assert e.evaluate("'big' if x > 10 else 'small'") == "small"

    def test_complex_guardrail_expression(self) -> None:
        e = _SafeEvaluator({
            "required_roles": ["content-director", "validator", "art-director"],
            "optional_roles": ["script-writer", "character-designer"],
            "risk_tags": [],
            "codex_required": False,
        })
        assert e.evaluate(
            "len(required_roles) + len(optional_roles) <= 7"
        ) is True
        assert e.evaluate(
            "len(required_roles) <= 6"
        ) is True
        assert e.evaluate(
            "'validator' in required_roles"
        ) is True
        assert e.evaluate(
            "not ('security_risk' in risk_tags) or codex_required == True"
        ) is True

    def test_disallowed_constructs(self) -> None:
        """Expressions that use disallowed constructs should raise."""
        e = _SafeEvaluator({"x": 5})

        # Assignment (=) is not allowed
        with pytest.raises(ValueError):
            e.evaluate("x = 3")

        # Import is not allowed
        with pytest.raises(ValueError):
            e.evaluate("__import__('os')")

    def test_invalid_syntax(self) -> None:
        e = _SafeEvaluator({})
        with pytest.raises(ValueError, match="Invalid expression syntax"):
            e.evaluate("x +")


# ═══════════════════════════════════════════════════════════════════════════
# 3. _transform_if_then_expr
# ═══════════════════════════════════════════════════════════════════════════


class TestTransformIfThen:
    """if/then guardrail expression conversion."""

    def test_simple_if_then(self) -> None:
        result = _transform_if_then_expr(
            "if 'security_risk' in risk_tags: codex_required = true"
        )
        assert result == "not ('security_risk' in risk_tags) or codex_required == True"

    def test_if_then_false(self) -> None:
        result = _transform_if_then_expr(
            "if some_condition: field = false"
        )
        assert result == "not (some_condition) or field == False"

    def test_no_colon_raises(self) -> None:
        with pytest.raises(ValueError, match="no colon"):
            _transform_if_then_expr("if condition without colon")

    def test_empty_body_raises(self) -> None:
        with pytest.raises(ValueError, match="empty condition or body"):
            _transform_if_then_expr("if condition:  ")

    def test_empty_condition_raises(self) -> None:
        with pytest.raises(ValueError, match="empty condition or body"):
            _transform_if_then_expr("if : body")

    def test_non_if_expression_passthrough(self) -> None:
        result = _transform_if_then_expr("len(x) <= 5")
        assert result == "len(x) <= 5"

    def test_non_if_with_assignment_normalization(self) -> None:
        result = _transform_if_then_expr("field = true")
        assert result == "field == True"

    def test_complex_if_condition(self) -> None:
        result = _transform_if_then_expr(
            "if any(tag in risk_tags for tag in ['legal', 'copyright']): codex_required = true"
        )
        expected = (
            "not (any(tag in risk_tags for tag in ['legal', 'copyright'])) "
            "or codex_required == True"
        )
        assert result == expected

    def test_assignment_to_comparison_standalone(self) -> None:
        assert _normalize_assignment_to_comparison("a = true") == "a == True"
        assert _normalize_assignment_to_comparison("a = false") == "a == False"
        assert _normalize_assignment_to_comparison("a = TRUE") == "a == True"
        assert _normalize_assignment_to_comparison("a = False") == "a == False"


# ═══════════════════════════════════════════════════════════════════════════
# 4. _is_evaluable
# ═══════════════════════════════════════════════════════════════════════════


class TestIsEvaluable:
    """Rule expression evaluability classification."""

    def test_simple_comparison_is_evaluable(self) -> None:
        rule = StaticConstraintRule(
            rule_id="test",
            description="test",
            rule_expr="len(x) <= 5",
            enforcement="truncate",
            category="role_limits",
        )
        assert _is_evaluable(rule) is True

    def test_if_then_is_evaluable(self) -> None:
        rule = StaticConstraintRule(
            rule_id="test",
            description="test",
            rule_expr="if 'security_risk' in risk_tags: codex_required = true",
            enforcement="force_codex_true",
            category="codex_escalation",
        )
        assert _is_evaluable(rule) is True

    def test_natural_language_is_not_evaluable(self) -> None:
        rule = StaticConstraintRule(
            rule_id="test",
            description="test",
            rule_expr="error_log must contain all failure events",
            enforcement="reject_manifest",
            category="silent_fail_prevention",
        )
        assert _is_evaluable(rule) is False

    def test_must_record_pattern(self) -> None:
        rule = StaticConstraintRule(
            rule_id="test",
            description="test",
            rule_expr="manifest must record retry_count, fallback_used",
            enforcement="validate_manifest",
            category="silent_fail_prevention",
        )
        assert _is_evaluable(rule) is False

    def test_maps_pattern(self) -> None:
        rule = StaticConstraintRule(
            rule_id="test",
            description="test",
            rule_expr="meeting_id maps 1:1 to directory",
            enforcement="create_or_verify",
            category="isolation",
        )
        assert _is_evaluable(rule) is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. validate_route — happy path
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateRouteHappyPath:
    """Known-good routes pass all constraints."""

    def test_good_route_passes(self, simple_rules, good_route) -> None:
        result = validate_route(good_route, simple_rules)
        assert result.passed is True
        assert result.violation_count == 0
        assert result.rules_checked == 3
        assert result.rules_evaluable == 3

    def test_good_route_properties(self, simple_rules, good_route) -> None:
        result = validate_route(good_route, simple_rules)
        assert result.rules_checked == 3
        assert result.rules_evaluable == 3
        assert result.rules_skipped == 0
        assert result.skipped_rule_ids == ()

    def test_good_route_to_dict(self, simple_rules, good_route) -> None:
        result = validate_route(good_route, simple_rules)
        d = result.to_dict()
        assert d["passed"] is True
        assert d["violation_count"] == 0
        assert d["violations"] == []
        assert d["rules_checked"] == 3
        assert d["rules_evaluable"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 6. validate_route — violation detection
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateRouteViolations:
    """Known-bad routes are detected."""

    def test_too_many_roles_violated(self, simple_rules) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d"],
            "optional_roles": ["e", "f", "g", "h"],
        }
        result = validate_route(route, simple_rules)
        assert result.passed is False
        assert any(v.rule_id == "max_roles" for v in result.violations)

    def test_missing_validator_violated(self, simple_rules) -> None:
        route = {
            "required_roles": ["content-director", "art-director"],
            "optional_roles": [],
        }
        result = validate_route(route, simple_rules)
        assert any(v.rule_id == "validator_always" for v in result.violations)

    def test_too_many_required_roles(self, simple_rules) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d", "e", "f", "g"],
            "optional_roles": [],
        }
        result = validate_route(route, simple_rules)
        assert any(v.rule_id == "max_required" for v in result.violations)

    def test_violation_has_correct_fields(self, simple_rules) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d"],
            "optional_roles": ["e", "f", "g", "h"],
        }
        result = validate_route(route, simple_rules)
        v = result.violations[0]
        assert v.rule_id == "max_roles"
        assert v.rule_description == "Maximum 7 roles total"
        assert v.enforcement == "truncate_optional_roles_by_expertise_match"
        assert v.category == "role_limits"
        assert "violated" in v.violation_detail.lower()

    def test_multiple_simultaneous_violations(self, simple_rules) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d", "e", "f", "g"],
            "optional_roles": ["h", "i"],
        }
        result = validate_route(route, simple_rules)
        # Should have max_roles and max_required violations, maybe validator
        violated_ids = {v.rule_id for v in result.violations}
        assert "max_roles" in violated_ids
        assert "max_required" in violated_ids

    def test_violations_by_category(self, simple_rules) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d", "e", "f", "g"],
            "optional_roles": ["h", "i"],
        }
        result = validate_route(route, simple_rules)
        grouped = result.violations_by_category()
        assert "role_limits" in grouped
        assert len(grouped["role_limits"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. validate_route — edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateRouteEdgeCases:
    """Boundary and error conditions."""

    def test_none_route_raises_type_error(self, simple_rules) -> None:
        with pytest.raises(TypeError, match="route must be a dict"):
            validate_route(None, simple_rules)

    def test_list_route_raises_type_error(self, simple_rules) -> None:
        with pytest.raises(TypeError, match="route must be a dict"):
            validate_route(["not", "a", "dict"], simple_rules)

    def test_empty_rules_returns_not_passed(self) -> None:
        result = validate_route({"x": 1}, ())
        assert result.passed is False
        assert result.rules_evaluable == 0
        assert result.rules_checked == 0

    def test_empty_route_empty_rules(self) -> None:
        result = validate_route({}, ())
        assert result.passed is False
        assert result.rules_evaluable == 0

    def test_route_with_unused_fields_passes(self, simple_rules, good_route) -> None:
        # Extra fields that no rule references should be fine
        route = dict(good_route)
        route["extra_field"] = "unused"
        result = validate_route(route, simple_rules)
        assert result.passed is True

    def test_missing_field_causes_value_error(self, simple_rules) -> None:
        # Rule references 'required_roles' but route has none
        route = {"optional_roles": ["a"]}  # no required_roles
        # The rule "len(required_roles) <= 6" will fail because
        # 'required_roles' is not in namespace. This should be
        # caught and the rule skipped with an error note.
        result = validate_route(route, simple_rules)
        # Should have skipped rules due to eval error
        assert result.rules_skipped > 0

    def test_skip_rules_with_nlp_expressions(self) -> None:
        rules = (
            StaticConstraintRule(
                rule_id="nlp_rule",
                description="Cannot be evaluated",
                rule_expr="error_log must contain all failure events",
                enforcement="reject",
                category="silent_fail_prevention",
            ),
            StaticConstraintRule(
                rule_id="eval_rule",
                description="Can be evaluated",
                rule_expr="x > 0",
                enforcement="truncate",
                category="role_limits",
            ),
        )
        result = validate_route({"x": 5}, rules)
        assert result.passed is True
        assert result.rules_skipped == 1
        assert "nlp_rule" in result.skipped_rule_ids
        assert result.rules_evaluable == 1
        assert result.rules_checked == 1


# ═══════════════════════════════════════════════════════════════════════════
# 8. validate_route_from_store — integration
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateRouteFromStore:
    """Integration with RuleStore."""

    def test_from_mock_store(self) -> None:
        config = {
            "version": "1.0.0",
            "guardrails": [
                {
                    "id": "test_rule",
                    "description": "Test rule",
                    "rule": "len(x) <= 5",
                    "enforcement": "truncate_optional",
                },
            ],
        }
        store = RuleStore.from_dict(config)
        result = validate_route_from_store({"x": [1, 2, 3]}, store)
        assert result.passed is True

        result = validate_route_from_store({"x": [1, 2, 3, 4, 5, 6]}, store)
        assert result.passed is False
        assert result.violations[0].rule_id == "test_rule"

    def test_from_real_store_good_route(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator", "art-director"],
            "optional_roles": ["script-writer", "character-designer"],
            "risk_tags": [],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route_from_store(route, real_store)
        # Should pass all evaluable constraints
        assert result.passed is True
        assert result.violation_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# 9. Real routing_rules.yaml — rule classification
# ═══════════════════════════════════════════════════════════════════════════


class TestRealRulesClassification:
    """Verify 16 real guardrails load and classify correctly."""

    def test_loads_16_rules(self, real_store) -> None:
        assert real_store.constraint_count == 16

    def test_majority_evaluable(self, real_store) -> None:
        evaluable = sum(1 for r in real_store.constraints if _is_evaluable(r))
        # At least 13 of 16 should be evaluable
        assert evaluable >= 13, f"Only {evaluable}/16 evaluable"

    def test_all_role_limits_evaluable(self, real_store) -> None:
        for r in real_store.constraints:
            if r.category in ("role_limits",):
                assert _is_evaluable(r), f"{r.rule_id} should be evaluable"

    def test_codex_escalation_rules_evaluable(self, real_store) -> None:
        for r in real_store.constraints:
            if r.category == "codex_escalation":
                assert _is_evaluable(r), f"{r.rule_id} should be evaluable"

    def test_non_evaluable_rule_ids(self, real_store) -> None:
        non_eval = {r.rule_id for r in real_store.constraints if not _is_evaluable(r)}
        # These are expected to be non-evaluable because they use natural language
        expected = {"no_silent_fail", "required_role_no_silent_skip", "meeting_directory_isolation"}
        assert non_eval == expected, f"Unexpected non-evaluable rules: {non_eval - expected}"


# ═══════════════════════════════════════════════════════════════════════════
# 10. Real rules — known-bad routes detect violations
# ═══════════════════════════════════════════════════════════════════════════


class TestRealRulesViolations:
    """Known-bad routes detect specific violations against real guardrails."""

    def test_security_risk_without_codex(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator", "art-director"],
            "optional_roles": ["script-writer"],
            "risk_tags": ["security_risk"],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert not result.passed
        assert any(v.rule_id == "security_always_codex" for v in result.violations)

    def test_data_loss_without_codex(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": ["data_loss_risk"],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert not result.passed
        assert any(v.rule_id == "data_loss_always_codex" for v in result.violations)

    def test_legal_exposure_without_codex(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": ["legal_exposure"],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert not result.passed
        assert any(v.rule_id == "legal_always_codex" for v in result.violations)

    def test_external_publication_without_codex(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": ["external_publication"],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert not result.passed
        assert any(v.rule_id == "external_publication_codex" for v in result.violations)

    def test_risk_tags_with_codex_passes(self, real_store) -> None:
        """All risk tags pass when codex_required is True."""
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": ["security_risk", "data_loss_risk", "legal_exposure", "external_publication"],
            "codex_required": True,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        # Should not have any codex-related violations
        codex_violations = [
            v for v in result.violations
            if v.rule_id in ("security_always_codex", "data_loss_always_codex",
                             "legal_always_codex", "external_publication_codex")
        ]
        assert len(codex_violations) == 0

    def test_too_many_roles_against_real_rules(self, real_store) -> None:
        route = {
            "required_roles": ["a", "b", "c", "d", "e", "f", "g"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert any(v.rule_id == "max_required_roles" for v in result.violations)

    def test_round_count_exceeded(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
            "round_count": 5,  # > 4
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert any(v.rule_id == "max_rounds_absolute" for v in result.violations)

    def test_max_concurrent_meetings_exceeded(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 5,  # > 2
            "active_opencode_calls": 2,
        }
        result = validate_route(route, real_store.constraints)
        assert any(v.rule_id == "max_concurrent_meetings" for v in result.violations)

    def test_max_opencode_calls_exceeded(self, real_store) -> None:
        route = {
            "required_roles": ["content-director", "validator"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
            "round_count": 2,
            "active_meetings": 1,
            "active_opencode_calls": 10,  # > 4
        }
        result = validate_route(route, real_store.constraints)
        assert any(v.rule_id == "max_concurrent_opencode_calls" for v in result.violations)

    def test_combined_violations(self, real_store) -> None:
        """A route that violates everything possible."""
        route = {
            "required_roles": ["a", "b", "c", "d", "e", "f", "g"],
            "optional_roles": ["h"],
            "risk_tags": ["security_risk", "data_loss_risk", "legal_exposure", "external_publication"],
            "codex_required": False,
            "round_count": 5,
            "active_meetings": 5,
            "active_opencode_calls": 10,
        }
        result = validate_route(route, real_store.constraints)
        violated_ids = {v.rule_id for v in result.violations}
        expected_violations = {
            "max_roles_per_meeting",
            "max_required_roles",
            "validator_always_required",
            "security_always_codex",
            "data_loss_always_codex",
            "legal_always_codex",
            "external_publication_codex",
            "max_rounds_absolute",
            "max_concurrent_meetings",
            "max_concurrent_opencode_calls",
        }
        missing = expected_violations - violated_ids
        assert not missing, f"Expected violations not detected: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# 11. ConstraintViolation and ConstraintValidationResult serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestSerialization:
    """to_dict round-tripping and property access."""

    def test_violation_to_dict(self) -> None:
        v = ConstraintViolation(
            rule_id="test",
            rule_description="A test rule",
            enforcement="truncate",
            category="role_limits",
            violation_detail="Test violation detail",
        )
        d = v.to_dict()
        assert d["rule_id"] == "test"
        assert d["rule_description"] == "A test rule"
        assert d["enforcement"] == "truncate"
        assert d["category"] == "role_limits"
        assert d["violation_detail"] == "Test violation detail"

    def test_result_to_dict_passed(self) -> None:
        result = ConstraintValidationResult(
            passed=True,
            violations=(),
            rules_checked=3,
            rules_evaluable=3,
            rules_skipped=0,
            skipped_rule_ids=(),
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert d["violation_count"] == 0
        assert d["rules_checked"] == 3

    def test_result_to_dict_failed(self) -> None:
        v = ConstraintViolation("r1", "desc", "enf", "cat", "detail")
        result = ConstraintValidationResult(
            passed=False,
            violations=(v,),
            rules_checked=5,
            rules_evaluable=6,
            rules_skipped=2,
            skipped_rule_ids=("nlp1", "nlp2"),
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert d["violation_count"] == 1
        assert d["violations"][0]["rule_id"] == "r1"
        assert d["rules_checked"] == 5
        assert d["rules_skipped"] == 2
        assert "nlp1" in d["skipped_rule_ids"]

    def test_result_violations_by_category(self) -> None:
        v1 = ConstraintViolation("r1", "d1", "e1", "role_limits", "detail1")
        v2 = ConstraintViolation("r2", "d2", "e2", "codex_escalation", "detail2")
        v3 = ConstraintViolation("r3", "d3", "e3", "role_limits", "detail3")
        result = ConstraintValidationResult(
            passed=False,
            violations=(v1, v2, v3),
        )
        grouped = result.violations_by_category()
        assert "role_limits" in grouped
        assert "codex_escalation" in grouped
        assert len(grouped["role_limits"]) == 2
        assert len(grouped["codex_escalation"]) == 1

    def test_violation_immutability(self) -> None:
        v = ConstraintViolation("test", "desc", "enf", "cat", "detail")
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            v.rule_id = "changed"  # type: ignore[misc]

    def test_result_immutability(self) -> None:
        result = ConstraintValidationResult(passed=True)
        with pytest.raises(Exception):
            result.passed = False  # type: ignore[misc]
