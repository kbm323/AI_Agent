"""Comprehensive tests for the override applicator module (Sub-AC 3.3.3).

Covers:
- apply_overrides: basic set-field override
- apply_overrides: force_add (append to list)
- apply_overrides: truncate_list
- apply_overrides: condition evaluation (if/then, any(), membership)
- apply_overrides: priority ordering (higher wins on same field)
- apply_overrides: idempotency (applying twice = same result)
- apply_overrides: non-matching rules → skipped
- apply_overrides: condition eval errors → error_rules
- apply_overrides: empty overrides → no changes
- apply_overrides: TypeError on non-dict route
- apply_overrides_for_violations: scoped application
- apply_overrides_for_violations: None violation_fields → all
- OverrideChange / OverrideApplicationResult: to_dict, properties
- _coerce_value: string → typed value
- _evaluate_condition: if/then expressions
- Integration: real routing_rules.yaml override rules
- Integration: violating route + overrides → compliant route
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.rule_store import (
    OverrideRule,
    RuleStore,
    StaticConstraintRule,
    load_rules_from_config,
)
from src.constraint_validator import validate_route
from src.override_applicator import (
    OverrideApplicationResult,
    OverrideChange,
    _coerce_value,
    _evaluate_condition,
    apply_overrides,
    apply_overrides_for_violations,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def simple_overrides() -> tuple[OverrideRule, ...]:
    """A minimal set of 4 override rules for isolated testing."""
    return (
        OverrideRule(
            rule_id="force_codex_on_security",
            description="Security risk requires Codex dual validation",
            condition="'security_risk' in risk_tags",
            action="force_codex_true",
            priority=10,
            target_field="codex_required",
            target_value="true",
        ),
        OverrideRule(
            rule_id="force_validator",
            description="Always force-add validator to required_roles",
            condition="'validator' not in required_roles",
            action="force_add",
            priority=20,
            target_field="required_roles",
            target_value="validator",
        ),
        OverrideRule(
            rule_id="truncate_optional_roles",
            description="Truncate optional_roles to max 3",
            condition="len(optional_roles) > 3",
            action="truncate",
            priority=30,
            target_field="optional_roles",
            target_value="3",
        ),
        OverrideRule(
            rule_id="set_priority_p0_on_data_loss",
            description="Data loss risk raises priority to P0",
            condition="'data_loss_risk' in risk_tags",
            action="set",
            priority=15,
            target_field="priority",
            target_value="P0",
        ),
    )


@pytest.fixture
def safe_route() -> dict:
    """A route that should not trigger any overrides."""
    return {
        "required_roles": ["content-director", "validator", "art-director"],
        "optional_roles": ["script-writer"],
        "risk_tags": [],
        "codex_required": False,
        "priority": "P2",
        "round_count": 1,
    }


@pytest.fixture
def violating_route() -> dict:
    """A route that triggers multiple overrides."""
    return {
        "required_roles": ["content-director", "art-director"],
        "optional_roles": ["script-writer", "vfx-artist", "sound-engineer", "character-designer"],
        "risk_tags": ["security_risk"],
        "codex_required": False,
        "priority": "P2",
        "round_count": 1,
    }


@pytest.fixture
def real_store() -> RuleStore:
    """The real routing_rules.yaml loaded once per module."""
    return load_rules_from_config(
        Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. _coerce_value
# ═══════════════════════════════════════════════════════════════════════════


class TestCoerceValue:
    """Test string→typed value coercion."""

    def test_true(self):
        assert _coerce_value("true") is True

    def test_false(self):
        assert _coerce_value("false") is False

    def test_true_whitespace(self):
        assert _coerce_value("  true  ") is True

    def test_none(self):
        assert _coerce_value("none") is None
        assert _coerce_value("null") is None

    def test_empty_string(self):
        assert _coerce_value("") is None

    def test_integer(self):
        assert _coerce_value("42") == 42
        assert _coerce_value("0") == 0
        assert _coerce_value("-1") == -1

    def test_float(self):
        assert _coerce_value("3.14") == 3.14

    def test_string_passthrough(self):
        assert _coerce_value("P0") == "P0"
        assert _coerce_value("validator") == "validator"
        assert _coerce_value("some-text") == "some-text"


# ═══════════════════════════════════════════════════════════════════════════
# 2. _evaluate_condition
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluateCondition:
    """Test override rule condition evaluation."""

    def test_if_then_true(self):
        rule = OverrideRule(
            rule_id="test",
            description="Security risk triggers codex",
            condition="'security_risk' in risk_tags",
            action="force_codex_true",
            priority=0,
            target_field="codex_required",
            target_value="true",
        )
        route = {"risk_tags": ["security_risk"], "codex_required": False}
        assert _evaluate_condition(rule, route) is True

    def test_if_then_false(self):
        rule = OverrideRule(
            rule_id="test",
            description="No security risk, no trigger",
            condition="'security_risk' in risk_tags",
            action="force_codex_true",
            priority=0,
            target_field="codex_required",
            target_value="true",
        )
        route = {"risk_tags": [], "codex_required": False}
        assert _evaluate_condition(rule, route) is False

    def test_simple_comparison(self):
        rule = OverrideRule(
            rule_id="test",
            description="Fewer than 3 required roles",
            condition="len(required_roles) < 3",
            action="force_add",
            priority=0,
            target_field="required_roles",
            target_value="validator",
        )
        route = {"required_roles": ["a", "b"]}
        assert _evaluate_condition(rule, route) is True

        route2 = {"required_roles": ["a", "b", "c"]}
        assert _evaluate_condition(rule, route2) is False

    def test_membership(self):
        rule = OverrideRule(
            rule_id="test",
            description="Validator missing from required_roles",
            condition="'validator' not in required_roles",
            action="force_add",
            priority=0,
            target_field="required_roles",
            target_value="validator",
        )
        assert _evaluate_condition(rule, {"required_roles": ["a"]}) is True
        assert _evaluate_condition(rule, {"required_roles": ["validator"]}) is False

    def test_any_function(self):
        rule = OverrideRule(
            rule_id="test",
            description="Legal or copyright risk present",
            condition="any(tag in risk_tags for tag in ['legal_exposure', 'copyright_risk'])",
            action="set",
            priority=0,
            target_field="codex_required",
            target_value="true",
        )
        assert _evaluate_condition(rule, {"risk_tags": ["legal_exposure"]}) is True
        assert _evaluate_condition(rule, {"risk_tags": ["budget"]}) is False

    def test_error_undefined_name(self):
        rule = OverrideRule(
            rule_id="test",
            description="References nonexistent field",
            condition="len(nonexistent_field) > 0",
            action="set",
            priority=0,
            target_field="x",
            target_value="1",
        )
        with pytest.raises(ValueError, match="nonexistent_field"):
            _evaluate_condition(rule, {"a": 1})


# ═══════════════════════════════════════════════════════════════════════════
# 3. apply_overrides — basic operations
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOverridesBasic:
    """Test basic override application operations."""

    def test_set_field_override(self, simple_overrides):
        """A rule matching security_risk should set codex_required to True."""
        route = {
            "required_roles": ["content-director", "validator", "art-director"],
            "optional_roles": ["script-writer"],
            "risk_tags": ["security_risk"],
            "codex_required": False,
            "priority": "P2",
        }
        result = apply_overrides(route, simple_overrides)
        assert result.transformed_route["codex_required"] is True
        assert "force_codex_on_security" in result.applied_rules

    def test_no_change_when_condition_mismatches(self, simple_overrides, safe_route):
        """A safe route should trigger no overrides."""
        result = apply_overrides(safe_route, simple_overrides)
        assert result.has_changes is False
        assert len(result.applied_rules) == 0
        # transformed should equal original in content
        assert result.transformed_route == safe_route

    def test_force_add_validator(self, simple_overrides):
        """Validator not in required → force_add adds it."""
        route = {
            "required_roles": ["content-director", "art-director"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
        }
        result = apply_overrides(route, simple_overrides)
        assert "validator" in result.transformed_route["required_roles"]
        assert "force_validator" in result.applied_rules

    def test_force_add_no_dup(self, simple_overrides):
        """force_add should not duplicate when already present."""
        route = {
            "required_roles": ["validator", "content-director"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
        }
        result = apply_overrides(route, simple_overrides)
        # force_validator condition is "validator not in required_roles"
        # which is False here, so it should be skipped
        assert "force_validator" in result.skipped_rules
        assert result.transformed_route["required_roles"] == route["required_roles"]

    def test_truncate_list(self, simple_overrides):
        """optional_roles > 3 should be truncated to 3."""
        route = {
            "required_roles": ["validator"],
            "optional_roles": ["a", "b", "c", "d", "e"],
            "risk_tags": [],
            "codex_required": False,
        }
        result = apply_overrides(route, simple_overrides)
        assert len(result.transformed_route["optional_roles"]) == 3
        assert result.transformed_route["optional_roles"] == ["a", "b", "c"]
        assert "truncate_optional_roles" in result.applied_rules

    def test_truncate_no_change_when_below_limit(self, simple_overrides):
        """Truncate should not modify when already within limit."""
        route = {
            "required_roles": ["validator"],
            "optional_roles": ["a", "b"],
            "risk_tags": [],
            "codex_required": False,
        }
        result = apply_overrides(route, simple_overrides)
        assert "truncate_optional_roles" in result.skipped_rules
        assert result.transformed_route["optional_roles"] == ["a", "b"]

    def test_multiple_overrides_in_order(self, simple_overrides, violating_route):
        """A violating route should trigger multiple overrides."""
        result = apply_overrides(violating_route, simple_overrides)

        # Should have applied: force_codex (10), force_validator (20),
        # truncate_optional_roles (30) — 3 rules total.
        # set_priority_p0_on_data_loss won't match (risk_tags has security_risk, not data_loss_risk)
        assert result.appled_count == 3
        assert result.transformed_route["codex_required"] is True
        assert "validator" in result.transformed_route["required_roles"]
        assert len(result.transformed_route["optional_roles"]) == 3

    def test_original_route_unchanged(self, simple_overrides, violating_route):
        """The input route must never be mutated."""
        original = copy.deepcopy(violating_route)
        result = apply_overrides(violating_route, simple_overrides)
        assert violating_route == original
        # Original still has codex_required=False
        assert violating_route["codex_required"] is False
        # Transformed has True
        assert result.original_route is violating_route


# ═══════════════════════════════════════════════════════════════════════════
# 4. apply_overrides — priority ordering
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOverridesPriority:
    """Test that higher-priority rules win on the same field."""

    @pytest.fixture
    def conflicting_overrides(self) -> tuple[OverrideRule, ...]:
        """Two rules targeting the same field with different priorities."""
        return (
            OverrideRule(
                rule_id="low_priority_set",
                description="Low priority: set priority to P2",
                condition="len(required_roles) > 0",
                action="set",
                priority=5,
                target_field="priority",
                target_value="P2",
            ),
            OverrideRule(
                rule_id="high_priority_set",
                description="High priority: set priority to P0",
                condition="len(required_roles) > 0",
                action="set",
                priority=50,
                target_field="priority",
                target_value="P0",
            ),
        )

    def test_higher_priority_wins(self, conflicting_overrides):
        """Both rules match; higher priority (50) should win."""
        route = {
            "required_roles": ["a"],
            "priority": "P3",
        }
        result = apply_overrides(route, conflicting_overrides)
        # Higher priority rule wins → P0
        assert result.transformed_route["priority"] == "P0"
        assert result.appled_count == 2
        # Both rules applied, but last write wins
        changes = result.changes_by_field().get("priority", ())
        assert len(changes) == 2
        assert changes[0].new_value == "P2"  # first applied (priority 5)
        assert changes[1].new_value == "P0"  # second applied (priority 50)

    def test_lower_priority_noop_when_condition_mismatches(self):
        """Higher priority rule with non-matching condition doesn't block lower."""
        rules = (
            OverrideRule(
                rule_id="low_set",
                description="Lower priority set to P2",
                condition="len(required_roles) > 0",
                action="set",
                priority=5,
                target_field="priority",
                target_value="P2",
            ),
            OverrideRule(
                rule_id="high_no_match",
                description="Higher priority but condition won't match",
                condition="len(required_roles) > 10",
                action="set",
                priority=50,
                target_field="priority",
                target_value="P0",
            ),
        )
        route = {"required_roles": ["a"], "priority": "P3"}
        result = apply_overrides(route, rules)
        # Only low matches → P2
        assert result.transformed_route["priority"] == "P2"
        assert "high_no_match" in result.skipped_rules


# ═══════════════════════════════════════════════════════════════════════════
# 5. apply_overrides — idempotency
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOverridesIdempotency:
    """Test that applying overrides twice produces the same result."""

    def test_idempotent(self, simple_overrides, violating_route):
        result1 = apply_overrides(violating_route, simple_overrides)
        result2 = apply_overrides(result1.transformed_route, simple_overrides)
        assert result2.transformed_route == result1.transformed_route
        # Second application should produce zero changes
        assert result2.has_changes is False

    def test_idempotent_on_safe_route(self, simple_overrides, safe_route):
        result1 = apply_overrides(safe_route, simple_overrides)
        result2 = apply_overrides(result1.transformed_route, simple_overrides)
        assert result2.transformed_route == result1.transformed_route
        assert result2.has_changes is False


# ═══════════════════════════════════════════════════════════════════════════
# 6. apply_overrides — edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOverridesEdgeCases:
    """Edge cases for the override applicator."""

    def test_empty_overrides(self, violating_route):
        result = apply_overrides(violating_route, ())
        assert result.rules_total == 0
        assert result.has_changes is False
        assert result.transformed_route == violating_route

    def test_non_dict_route_raises_typeerror(self):
        with pytest.raises(TypeError, match="route must be a dict"):
            apply_overrides([], ())

        with pytest.raises(TypeError, match="route must be a dict"):
            apply_overrides("not a route", ())

    def test_condition_eval_error_tracked(self):
        """Rules with eval errors go to error_rules, not applied or skipped."""
        rules = (
            OverrideRule(
                rule_id="bad_condition",
                description="This condition references undefined fields",
                condition="undefined_field > 5",
                action="set",
                priority=0,
                target_field="x",
                target_value="1",
            ),
        )
        result = apply_overrides({"a": 1}, rules)
        assert "bad_condition" not in result.applied_rules
        assert "bad_condition" not in result.skipped_rules
        assert any("bad_condition" in e for e in result.error_rules)

    def test_non_bool_condition_tracked(self):
        """Conditions evaluating to non-bool are treated as errors."""
        rules = (
            OverrideRule(
                rule_id="returns_int",
                description="Condition returns int, not bool",
                condition="len(required_roles)",
                action="set",
                priority=0,
                target_field="x",
                target_value="1",
            ),
        )
        route = {"required_roles": ["a", "b"]}
        result = apply_overrides(route, rules)
        assert any("returns_int" in e for e in result.error_rules)

    def test_deep_copy_isolation(self, simple_overrides, violating_route):
        """Deeply nested structures in original must be isolated."""
        result = apply_overrides(violating_route, simple_overrides)
        # Modify transformed; original must stay unchanged
        result.transformed_route["required_roles"].append("EXTRA")
        assert "EXTRA" not in violating_route["required_roles"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. apply_overrides_for_violations
# ═══════════════════════════════════════════════════════════════════════════


class TestApplyOverridesForViolations:
    """Test scoped override application."""

    def test_only_targeted_fields(self, simple_overrides, violating_route):
        """When violation_fields is set, only those field's rules are evaluated."""
        # Only codex_required is violated → only codex rule should apply
        result = apply_overrides_for_violations(
            violating_route,
            simple_overrides,
            violation_fields=frozenset({"codex_required"}),
        )
        assert result.appled_count == 1
        assert "force_codex_on_security" in result.applied_rules
        # Validator and truncate should NOT be applied
        assert "force_validator" not in result.applied_rules
        assert "truncate_optional_roles" not in result.applied_rules

    def test_none_violation_fields_applies_all(self, simple_overrides, violating_route):
        """None violation_fields → all overrides evaluated."""
        scoped = apply_overrides_for_violations(violating_route, simple_overrides, violation_fields=None)
        full = apply_overrides(violating_route, simple_overrides)
        assert scoped.transformed_route == full.transformed_route

    def test_empty_violation_fields(self, simple_overrides, violating_route):
        """Empty frozenset → no overrides applied."""
        result = apply_overrides_for_violations(
            violating_route,
            simple_overrides,
            violation_fields=frozenset(),
        )
        assert result.appled_count == 0
        assert result.has_changes is False


# ═══════════════════════════════════════════════════════════════════════════
# 8. OverrideChange / OverrideApplicationResult — properties
# ═══════════════════════════════════════════════════════════════════════════


class TestResultProperties:
    """Test result dataclass properties and serialization."""

    def test_has_changes_true(self, simple_overrides, violating_route):
        result = apply_overrides(violating_route, simple_overrides)
        assert result.has_changes is True

    def test_has_changes_false(self, simple_overrides, safe_route):
        result = apply_overrides(safe_route, simple_overrides)
        assert result.has_changes is False

    def test_changes_by_field(self, simple_overrides, violating_route):
        result = apply_overrides(violating_route, simple_overrides)
        by_field = result.changes_by_field()
        assert "codex_required" in by_field
        assert "required_roles" in by_field
        assert "optional_roles" in by_field

    def test_to_dict(self, simple_overrides, violating_route):
        result = apply_overrides(violating_route, simple_overrides)
        d = result.to_dict()
        assert d["rules_total"] == 4
        assert d["applied_count"] == 3
        assert "has_changes" in d
        assert "changes" in d
        assert len(d["changes"]) == 3

    def test_override_change_to_dict(self):
        change = OverrideChange(
            rule_id="test_rule",
            field="codex_required",
            old_value=False,
            new_value=True,
            action="force_codex_true",
        )
        d = change.to_dict()
        assert d["rule_id"] == "test_rule"
        assert d["field"] == "codex_required"
        assert d["old_value"] is False
        assert d["new_value"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 9. Integration: real routing_rules.yaml override rules
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegrationRealRules:
    """Integration tests using the real routing_rules.yaml."""

    def test_real_override_rules_exist(self, real_store):
        """The real config may or may not have override rules. This documents
        the expected behavior."""
        # Override rules are separate from guardrails
        count = real_store.override_count
        # Just verify the property works
        assert isinstance(count, int)
        assert count >= 0

    def test_override_rules_typed(self, real_store):
        """All override rules should be OverrideRule instances."""
        for rule in real_store.overrides:
            assert isinstance(rule, OverrideRule)
            assert rule.rule_id
            assert rule.condition
            assert rule.target_field

    def test_violating_route_with_overrides(self, real_store):
        """A route violating constraints + override rules → transformed route."""
        # Build a route that has security_risk but codex_required=False
        route = {
            "required_roles": ["content-director", "validator", "art-director"],
            "optional_roles": ["script-writer", "character-designer", "vfx-artist", "sound-engineer", "ui-designer"],
            "risk_tags": ["security_risk"],
            "codex_required": False,
            "priority": "P2",
            "round_count": 1,
            "active_meetings": 1,
            "active_opencode_calls": 2,
        }
        result = apply_overrides(route, real_store.overrides)
        # The transformed route must be a dict
        assert isinstance(result.transformed_route, dict)
        # Original route must be unchanged
        assert route["codex_required"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 10. Contract: violating route + overrides → compliant route
# ═══════════════════════════════════════════════════════════════════════════


class TestContractViolatingToCompliant:
    """Verify the core contract: violating route + overrides → compliant."""

    def test_violating_route_becomes_compliant(self):
        """A route violating max_roles constraint becomes compliant after override."""
        # Constraint: len(required_roles) + len(optional_roles) <= 7
        constraint = StaticConstraintRule(
            rule_id="max_roles_per_meeting",
            description="Maximum 7 agents per meeting",
            rule_expr="len(required_roles) + len(optional_roles) <= 7",
            enforcement="truncate_optional_roles_by_expertise_match",
            category="role_limits",
        )

        # Override that truncates optional_roles to max 3
        override = OverrideRule(
            rule_id="limit_optional_roles",
            description="Truncate optional_roles to stay within role limit",
            condition="len(required_roles) + len(optional_roles) > 7",
            action="truncate",
            priority=10,
            target_field="optional_roles",
            target_value="3",
        )

        # Violating route: 4 required + 5 optional = 9 > 7
        route = {
            "required_roles": ["role-1", "role-2", "role-3", "role-4"],
            "optional_roles": ["opt-1", "opt-2", "opt-3", "opt-4", "opt-5"],
            "risk_tags": [],
            "codex_required": False,
        }

        # Before: violation detected
        before = validate_route(route, (constraint,))
        assert before.passed is False
        assert before.violation_count == 1

        # Apply override
        result = apply_overrides(route, (override,))
        assert result.appled_count == 1
        assert len(result.transformed_route["optional_roles"]) == 3

        # After: compliant
        after = validate_route(result.transformed_route, (constraint,))
        assert after.passed is True
        assert after.violation_count == 0

    def test_codex_required_enforced(self):
        """A route with security_risk but no codex → override sets codex_required."""
        constraint = StaticConstraintRule(
            rule_id="security_always_codex",
            description="Security risk requires Codex",
            rule_expr="if 'security_risk' in risk_tags: codex_required = True",
            enforcement="force_codex_true",
            category="codex_escalation",
        )

        override = OverrideRule(
            rule_id="enforce_codex_on_security",
            description="Force codex_required when security_risk present",
            condition="'security_risk' in risk_tags",
            action="force_codex_true",
            priority=10,
            target_field="codex_required",
            target_value="true",
        )

        route = {
            "required_roles": ["validator"],
            "optional_roles": [],
            "risk_tags": ["security_risk"],
            "codex_required": False,
        }

        # Before: violation
        before = validate_route(route, (constraint,))
        assert before.passed is False

        # Apply override
        result = apply_overrides(route, (override,))
        assert result.transformed_route["codex_required"] is True

        # After: compliant
        after = validate_route(result.transformed_route, (constraint,))
        assert after.passed is True

    def test_force_add_validator_ensures_compliance(self):
        """Validator missing from required_roles → force_add fixes it."""
        constraint = StaticConstraintRule(
            rule_id="validator_always_required",
            description="Validator role is always required",
            rule_expr="'validator' in required_roles",
            enforcement="force_add_validator",
            category="role_limits",
        )

        override = OverrideRule(
            rule_id="add_validator",
            description="Add validator to required_roles",
            condition="'validator' not in required_roles",
            action="force_add",
            priority=10,
            target_field="required_roles",
            target_value="validator",
        )

        route = {
            "required_roles": ["content-director", "art-director"],
            "optional_roles": [],
            "risk_tags": [],
            "codex_required": False,
        }

        # Before: violation
        before = validate_route(route, (constraint,))
        assert before.passed is False

        # Apply override
        result = apply_overrides(route, (override,))
        assert "validator" in result.transformed_route["required_roles"]

        # After: compliant
        after = validate_route(result.transformed_route, (constraint,))
        assert after.passed is True

    def test_transformed_output_matches_expected(self):
        """The transformed output must exactly match expectations.

        This is the explicit testability requirement from Sub-AC 3.3.3:
        "testable by providing a violating route plus override rules
        and asserting the transformed output matches expectations"
        """
        overrides = (
            OverrideRule(
                rule_id="set_codex",
                description="Set codex_required to True",
                condition="'security_risk' in risk_tags",
                action="force_codex_true",
                priority=10,
                target_field="codex_required",
                target_value="true",
            ),
            OverrideRule(
                rule_id="set_priority",
                description="Set priority to P0",
                condition="len(risk_tags) >= 1",
                action="set",
                priority=20,
                target_field="priority",
                target_value="P0",
            ),
        )

        violating = {
            "required_roles": ["a", "b"],
            "optional_roles": ["c"],
            "risk_tags": ["security_risk"],
            "codex_required": False,
            "priority": "P2",
        }

        expected = {
            "required_roles": ["a", "b"],
            "optional_roles": ["c"],
            "risk_tags": ["security_risk"],
            "codex_required": True,
            "priority": "P0",
        }

        result = apply_overrides(violating, overrides)
        assert result.transformed_route == expected


# ═══════════════════════════════════════════════════════════════════════════
# 11. Sub-AC 3.3.3 contract verification
# ═══════════════════════════════════════════════════════════════════════════


class TestSubACContract:
    """Verify the module fulfills the Sub-AC 3.3.3 contract."""

    def test_public_api_exists(self):
        """The module must expose apply_overrides as the entry point."""
        from src import override_applicator
        assert hasattr(override_applicator, "apply_overrides")
        assert callable(override_applicator.apply_overrides)

    def test_accepts_violating_route_and_override_rules(self):
        """Must accept a route dict and OverrideRule tuple."""
        route = {"required_roles": ["a"], "risk_tags": ["test"]}
        rule = OverrideRule(
            rule_id="test",
            description="Test",
            condition="'test' in risk_tags",
            action="set",
            priority=0,
            target_field="codex_required",
            target_value="true",
        )
        result = apply_overrides(route, (rule,))
        assert isinstance(result, OverrideApplicationResult)

    def test_returns_transformed_route(self):
        """The result must contain the transformed route."""
        route = {"x": 1, "risk_tags": []}
        result = apply_overrides(route, ())
        assert "x" in result.transformed_route
        assert result.transformed_route["x"] == 1

    def test_no_mutation_of_input(self):
        """The original route dict must never be mutated."""
        route = {"a": [1, 2, 3]}
        rule = OverrideRule(
            rule_id="trunc",
            description="Truncate list to max 2",
            condition="len(a) > 2",
            action="truncate",
            priority=0,
            target_field="a",
            target_value="2",
        )
        result = apply_overrides(route, (rule,))
        # Original unchanged
        assert route["a"] == [1, 2, 3]
        # Transformed is truncated
        assert result.transformed_route["a"] == [1, 2]
