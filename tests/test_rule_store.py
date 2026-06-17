"""Tests for the rule store/loader module (Sub-AC 3.3.1).

Comprehensive test coverage for:
- StaticConstraintRule data model validation
- OverrideRule data model validation
- RuleStore.from_dict() with mock config
- RuleStore.from_yaml() with real routing_rules.yaml
- RuleSet collection methods
- Category inference for all guardrail patterns
- Edge cases: empty config, missing fields, type errors
- Schema verification: parsed rule objects match expected shape
- Serialization (to_dict) round-tripping
- Integration with existing routing_rules_loader pipeline
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.rule_store import (
    OverrideRule,
    RuleSet,
    RuleStore,
    StaticConstraintRule,
    _infer_category,
    load_rules_from_config,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def minimal_mock_config() -> dict:
    """Smallest valid config — no guardrails or overrides."""
    return {"version": "1.0.0"}


@pytest.fixture
def mock_config_with_constraints() -> dict:
    """Config with 6 static constraint rules (all categories)."""
    return {
        "version": "1.0.0",
        "guardrails": [
            {
                "id": "max_roles_per_meeting",
                "description": "Maximum 7 agents per meeting",
                "rule": "len(required_roles) + len(optional_roles) <= 7",
                "enforcement": "truncate_optional_roles_by_expertise_match",
            },
            {
                "id": "max_required_roles",
                "description": "Maximum 6 required roles per meeting",
                "rule": "len(required_roles) <= 6",
                "enforcement": "demote_lowest_priority_required_to_optional",
            },
            {
                "id": "validator_always_required",
                "description": "Validator role always required",
                "rule": "'validator' in required_roles",
                "enforcement": "force_add_validator",
            },
            {
                "id": "security_always_codex",
                "description": "Security risk => codex required",
                "rule": "if 'security_risk' in risk_tags: codex_required = true",
                "enforcement": "force_codex_true",
            },
            {
                "id": "max_rounds_absolute",
                "description": "Maximum 3+1 rounds",
                "rule": "round_count <= 4",
                "enforcement": "force_escalation_after_4_rounds",
            },
            {
                "id": "no_silent_fail",
                "description": "All failures must be logged",
                "rule": "error_log must contain all failure events",
                "enforcement": "reject_manifest_without_error_log",
            },
        ],
    }


@pytest.fixture
def mock_config_with_overrides() -> dict:
    """Config with override rules."""
    return {
        "version": "1.0.0",
        "guardrails": [
            {
                "id": "max_roles_per_meeting",
                "description": "Max 7 agents",
                "rule": "...",
                "enforcement": "truncate_optional_roles",
            },
        ],
        "override_rules": [
            {
                "id": "require_codex_on_budget",
                "description": "Budget-related => Codex required",
                "condition": "if 'budget_financial' in risk_tags",
                "action": "force_codex_true",
                "priority": 5,
                "target_field": "codex_required",
                "target_value": "true",
            },
            {
                "id": "force_p0_on_security",
                "description": "Security risk => P0",
                "condition": "if 'security_risk' in risk_tags",
                "action": "force_priority_p0",
                "priority": 10,
                "target_field": "priority",
                "target_value": "P0",
            },
            {
                "id": "always_codex",
                "description": "Always require Codex",
                "condition": "true",
                "action": "force_codex_true",
                "priority": 1,
                "target_field": "codex_required",
                "target_value": "true",
            },
        ],
    }


@pytest.fixture(scope="module")
def real_store() -> RuleStore:
    """RuleStore loaded from the real routing_rules.yaml."""
    p = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    return load_rules_from_config(p)


# ═══════════════════════════════════════════════════════════════════════════
# Category inference tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCategoryInference:
    """Test the _infer_category helper directly."""

    def test_role_limits_from_enforcement(self):
        assert _infer_category("any_id", "truncate_optional_roles") == "role_limits"
        assert _infer_category("any_id", "demote_lowest_priority") == "role_limits"
        assert _infer_category("any_id", "force_add_validator") == "role_limits"
        assert _infer_category("any_id", "promote_team_leader") == "role_limits"

    def test_codex_escalation_from_enforcement(self):
        assert _infer_category("any_id", "force_codex_true") == "codex_escalation"

    def test_model_constraints_from_enforcement(self):
        assert _infer_category("any_id", "force_codex_on_second_validation") == "model_constraints"
        assert _infer_category("any_id", "force_glm_for_validator") == "model_constraints"

    def test_meeting_constraints_from_enforcement(self):
        assert _infer_category("any_id", "force_escalation_after_4_rounds") == "meeting_constraints"
        assert _infer_category("any_id", "queue_or_reject") == "meeting_constraints"
        assert _infer_category("any_id", "queue_or_degrade") == "meeting_constraints"

    def test_silent_fail_from_enforcement(self):
        assert _infer_category("any_id", "reject_manifest_without_error_log") == "silent_fail_prevention"
        assert _infer_category("any_id", "validate_manifest_completeness") == "silent_fail_prevention"

    def test_isolation_from_enforcement(self):
        assert _infer_category("any_id", "create_or_verify_directory") == "isolation"

    def test_fallback_to_rule_id_prefix(self):
        # If enforcement doesn't match any prefix, fall back to rule_id
        assert _infer_category("max_roles_any", "unknown_enforcement") == "role_limits"
        assert _infer_category("security_any", "unknown_enforcement") == "codex_escalation"
        assert _infer_category("data_loss_any", "unknown_enforcement") == "codex_escalation"
        assert _infer_category("max_rounds_any", "unknown_enforcement") == "meeting_constraints"
        assert _infer_category("no_silent_any", "unknown_enforcement") == "silent_fail_prevention"
        assert _infer_category("meeting_directory_any", "unknown_enforcement") == "isolation"
        assert _infer_category("validator_is_any", "unknown_enforcement") == "model_constraints"
        assert _infer_category("no_same_model_any", "unknown_enforcement") == "model_constraints"

    def test_unknown_falls_back_to_role_limits(self):
        assert _infer_category("completely_unknown_id", "unknown_enforcement") == "role_limits"


# ═══════════════════════════════════════════════════════════════════════════
# StaticConstraintRule data model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStaticConstraintRule:
    """Test StaticConstraintRule construction and validation."""

    def test_valid_construction(self):
        rule = StaticConstraintRule(
            rule_id="test_rule",
            description="A test rule",
            rule_expr="x == y",
            enforcement="force_thing",
            category="role_limits",
            severity="hard",
        )
        assert rule.rule_id == "test_rule"
        assert rule.description == "A test rule"
        assert rule.rule_expr == "x == y"
        assert rule.enforcement == "force_thing"
        assert rule.category == "role_limits"
        assert rule.severity == "hard"

    def test_default_category(self):
        rule = StaticConstraintRule(
            rule_id="test",
            description="desc",
            rule_expr="x",
            enforcement="y",
        )
        assert rule.category == "role_limits"
        assert rule.severity == "hard"

    def test_empty_rule_id_raises(self):
        with pytest.raises(ValueError, match="rule_id must be non-empty"):
            StaticConstraintRule(
                rule_id="",
                description="desc",
                rule_expr="x",
                enforcement="y",
            )

    def test_whitespace_rule_id_raises(self):
        with pytest.raises(ValueError, match="rule_id must be non-empty"):
            StaticConstraintRule(
                rule_id="   ",
                description="desc",
                rule_expr="x",
                enforcement="y",
            )

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="description must be non-empty"):
            StaticConstraintRule(
                rule_id="test",
                description="",
                rule_expr="x",
                enforcement="y",
            )

    def test_empty_rule_expr_raises(self):
        with pytest.raises(ValueError, match="rule_expr must be non-empty"):
            StaticConstraintRule(
                rule_id="test",
                description="desc",
                rule_expr="",
                enforcement="y",
            )

    def test_empty_enforcement_raises(self):
        with pytest.raises(ValueError, match="enforcement must be non-empty"):
            StaticConstraintRule(
                rule_id="test",
                description="desc",
                rule_expr="x",
                enforcement="",
            )

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="category = .* is not valid"):
            StaticConstraintRule(
                rule_id="test",
                description="desc",
                rule_expr="x",
                enforcement="y",
                category="not_a_real_category",
            )

    def test_non_hard_severity_raises(self):
        with pytest.raises(ValueError, match="severity must be 'hard'"):
            StaticConstraintRule(
                rule_id="test",
                description="desc",
                rule_expr="x",
                enforcement="y",
                severity="soft",
            )

    def test_frozen_immutable(self):
        rule = StaticConstraintRule(
            rule_id="test",
            description="desc",
            rule_expr="x",
            enforcement="y",
        )
        with pytest.raises(Exception):  # dataclass FrozenInstanceError
            rule.rule_id = "modified"  # type: ignore[misc]

    def test_equality(self):
        r1 = StaticConstraintRule(
            rule_id="test", description="d", rule_expr="x", enforcement="y"
        )
        r2 = StaticConstraintRule(
            rule_id="test", description="d", rule_expr="x", enforcement="y"
        )
        assert r1 == r2
        assert hash(r1) == hash(r2)

    def test_to_dict(self):
        rule = StaticConstraintRule(
            rule_id="test_rule",
            description="A test rule",
            rule_expr="x == y",
            enforcement="force_thing",
            category="role_limits",
            severity="hard",
        )
        d = rule.to_dict()
        assert d["rule_id"] == "test_rule"
        assert d["description"] == "A test rule"
        assert d["rule_expr"] == "x == y"
        assert d["enforcement"] == "force_thing"
        assert d["category"] == "role_limits"
        assert d["severity"] == "hard"
        assert d["rule_type"] == "static_constraint"


# ═══════════════════════════════════════════════════════════════════════════
# OverrideRule data model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestOverrideRule:
    """Test OverrideRule construction and validation."""

    def test_valid_construction(self):
        rule = OverrideRule(
            rule_id="test_override",
            description="A test override",
            condition="if x > 0",
            action="force_y",
            priority=5,
            target_field="codex_required",
            target_value="true",
        )
        assert rule.rule_id == "test_override"
        assert rule.condition == "if x > 0"
        assert rule.action == "force_y"
        assert rule.priority == 5
        assert rule.target_field == "codex_required"
        assert rule.target_value == "true"

    def test_zero_priority_is_valid(self):
        rule = OverrideRule(
            rule_id="zero_pri",
            description="desc",
            condition="x",
            action="y",
            priority=0,
            target_field="z",
            target_value="w",
        )
        assert rule.priority == 0

    def test_high_priority_is_valid(self):
        rule = OverrideRule(
            rule_id="high_pri",
            description="desc",
            condition="x",
            action="y",
            priority=100,
            target_field="z",
            target_value="w",
        )
        assert rule.priority == 100

    def test_empty_rule_id_raises(self):
        with pytest.raises(ValueError, match="rule_id must be non-empty"):
            OverrideRule(
                rule_id="",
                description="desc",
                condition="x",
                action="y",
                priority=0,
                target_field="z",
                target_value="w",
            )

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="description must be non-empty"):
            OverrideRule(
                rule_id="test",
                description="",
                condition="x",
                action="y",
                priority=0,
                target_field="z",
                target_value="w",
            )

    def test_empty_condition_raises(self):
        with pytest.raises(ValueError, match="condition must be non-empty"):
            OverrideRule(
                rule_id="test",
                description="desc",
                condition="",
                action="y",
                priority=0,
                target_field="z",
                target_value="w",
            )

    def test_empty_action_raises(self):
        with pytest.raises(ValueError, match="action must be non-empty"):
            OverrideRule(
                rule_id="test",
                description="desc",
                condition="x",
                action="",
                priority=0,
                target_field="z",
                target_value="w",
            )

    def test_empty_target_field_raises(self):
        with pytest.raises(ValueError, match="target_field must be non-empty"):
            OverrideRule(
                rule_id="test",
                description="desc",
                condition="x",
                action="y",
                priority=0,
                target_field="",
                target_value="w",
            )

    def test_negative_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be a non-negative"):
            OverrideRule(
                rule_id="test",
                description="desc",
                condition="x",
                action="y",
                priority=-1,
                target_field="z",
                target_value="w",
            )

    def test_float_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be a non-negative"):
            OverrideRule(
                rule_id="test",
                description="desc",
                condition="x",
                action="y",
                priority=1.5,
                target_field="z",
                target_value="w",
            )

    def test_frozen_immutable(self):
        rule = OverrideRule(
            rule_id="test", description="d", condition="c",
            action="a", priority=0, target_field="t", target_value="v",
        )
        with pytest.raises(Exception):
            rule.priority = 10  # type: ignore[misc]

    def test_equality(self):
        r1 = OverrideRule(
            rule_id="t", description="d", condition="c",
            action="a", priority=0, target_field="t", target_value="v",
        )
        r2 = OverrideRule(
            rule_id="t", description="d", condition="c",
            action="a", priority=0, target_field="t", target_value="v",
        )
        assert r1 == r2
        assert hash(r1) == hash(r2)

    def test_to_dict(self):
        rule = OverrideRule(
            rule_id="test_ov",
            description="desc",
            condition="cond",
            action="act",
            priority=5,
            target_field="field",
            target_value="val",
        )
        d = rule.to_dict()
        assert d["rule_id"] == "test_ov"
        assert d["condition"] == "cond"
        assert d["rule_type"] == "override"
        assert d["priority"] == 5
        assert d["target_value"] == "val"


# ═══════════════════════════════════════════════════════════════════════════
# RuleSet tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRuleSet:
    """Test the RuleSet collection dataclass."""

    def test_empty_ruleset(self):
        rs = RuleSet(version="1.0.0")
        assert rs.constraint_count == 0
        assert rs.override_count == 0
        assert rs.total_rules == 0
        assert rs.constraints_by_category() == {}
        assert rs.overrides_by_target() == {}

    def test_ruleset_with_rules(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        rs = store.ruleset
        assert rs.constraint_count == 1
        assert rs.override_count == 3
        assert rs.total_rules == 4

    def test_get_constraint_found(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        rs = store.ruleset
        rule = rs.get_constraint("max_roles_per_meeting")
        assert rule is not None
        assert rule.rule_id == "max_roles_per_meeting"
        assert rule.enforcement == "truncate_optional_roles_by_expertise_match"

    def test_get_constraint_not_found(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        rs = store.ruleset
        assert rs.get_constraint("nonexistent") is None

    def test_get_override_found(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        rs = store.ruleset
        rule = rs.get_override("force_p0_on_security")
        assert rule is not None
        assert rule.priority == 10

    def test_get_override_not_found(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        rs = store.ruleset
        assert rs.get_override("nonexistent") is None

    def test_constraints_by_category(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        rs = store.ruleset
        by_cat = rs.constraints_by_category()
        assert "role_limits" in by_cat
        assert len(by_cat["role_limits"]) == 3
        role_ids = {r.rule_id for r in by_cat["role_limits"]}
        assert role_ids == {"max_roles_per_meeting", "max_required_roles", "validator_always_required"}

    def test_overrides_by_target(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        rs = store.ruleset
        by_target = rs.overrides_by_target()
        assert "codex_required" in by_target
        assert "priority" in by_target
        assert len(by_target["codex_required"]) == 2  # require_codex_on_budget + always_codex
        assert len(by_target["priority"]) == 1

    def test_to_dict(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        rs = store.ruleset
        d = rs.to_dict()
        assert d["version"] == "1.0.0"
        assert d["stats"]["constraint_count"] == 6
        assert d["stats"]["total_rules"] == 6
        assert len(d["static_constraints"]) == 6
        assert isinstance(d["static_constraints"][0], dict)
        assert d["static_constraints"][0]["rule_type"] == "static_constraint"


# ═══════════════════════════════════════════════════════════════════════════
# RuleStore.from_dict() tests (mock config path)
# ═══════════════════════════════════════════════════════════════════════════


class TestRuleStoreFromDict:
    """Test RuleStore.from_dict() — the mockable entry point."""

    def test_empty_config(self, minimal_mock_config):
        store = RuleStore.from_dict(minimal_mock_config)
        assert store.version == "1.0.0"
        assert store.constraint_count == 0
        assert store.override_count == 0
        assert len(store) == 0

    def test_constraints_parsed(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        assert store.constraint_count == 6
        assert store.override_count == 0
        assert len(store) == 6

    def test_constraints_are_typed(self, mock_config_with_constraints):
        """Verify all parsed constraints are StaticConstraintRule objects."""
        store = RuleStore.from_dict(mock_config_with_constraints)
        for rule in store.constraints:
            assert isinstance(rule, StaticConstraintRule)
            assert rule.severity == "hard"

    def test_constraints_match_expected_schema(self, mock_config_with_constraints):
        """Verify parsed constraint objects match expected schema — core AC requirement."""
        store = RuleStore.from_dict(mock_config_with_constraints)

        # First constraint: max_roles_per_meeting
        c = store.get_constraint("max_roles_per_meeting")
        assert c is not None
        assert c.rule_id == "max_roles_per_meeting"
        assert isinstance(c.description, str) and len(c.description) > 0
        assert isinstance(c.rule_expr, str) and len(c.rule_expr) > 0
        assert isinstance(c.enforcement, str) and len(c.enforcement) > 0
        assert c.category == "role_limits"
        assert c.severity == "hard"
        # Schema: all fields are non-empty strings, category is valid, severity is 'hard'
        d = c.to_dict()
        for key in ("rule_id", "description", "rule_expr", "enforcement", "category", "severity", "rule_type"):
            assert key in d, f"Missing key {key!r} in to_dict() output"
            assert isinstance(d[key], str), f"Key {key!r} must be str, got {type(d[key]).__name__}"

        # Second constraint: max_required_roles
        c2 = store.get_constraint("max_required_roles")
        assert c2 is not None
        assert c2.enforcement == "demote_lowest_priority_required_to_optional"
        assert c2.category == "role_limits"

        # Third: security_always_codex (codex escalation category)
        c3 = store.get_constraint("security_always_codex")
        assert c3 is not None
        assert c3.category == "codex_escalation"

        # Fourth: max_rounds_absolute (meeting_constraints)
        c4 = store.get_constraint("max_rounds_absolute")
        assert c4 is not None
        assert c4.category == "meeting_constraints"

        # Fifth: no_silent_fail (silent_fail_prevention)
        c5 = store.get_constraint("no_silent_fail")
        assert c5 is not None
        assert c5.category == "silent_fail_prevention"

    def test_overrides_parsed(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        assert store.constraint_count == 1
        assert store.override_count == 3

    def test_overrides_are_typed(self, mock_config_with_overrides):
        """Verify all parsed overrides are OverrideRule objects."""
        store = RuleStore.from_dict(mock_config_with_overrides)
        for rule in store.overrides:
            assert isinstance(rule, OverrideRule)

    def test_overrides_match_expected_schema(self, mock_config_with_overrides):
        """Verify parsed override objects match expected schema."""
        store = RuleStore.from_dict(mock_config_with_overrides)

        ov = store.get_override("require_codex_on_budget")
        assert ov is not None
        assert ov.rule_id == "require_codex_on_budget"
        assert isinstance(ov.description, str) and len(ov.description) > 0
        assert isinstance(ov.condition, str) and len(ov.condition) > 0
        assert isinstance(ov.action, str) and len(ov.action) > 0
        assert isinstance(ov.priority, int) and ov.priority >= 0
        assert isinstance(ov.target_field, str) and len(ov.target_field) > 0
        # target_value can be empty string
        assert isinstance(ov.target_value, str)

        d = ov.to_dict()
        expected_keys = {"rule_id", "description", "condition", "action",
                         "priority", "target_field", "target_value", "rule_type"}
        assert set(d.keys()) == expected_keys

    def test_overrides_sorted_by_priority(self, mock_config_with_overrides):
        """Overrides must be sorted by priority (ascending)."""
        store = RuleStore.from_dict(mock_config_with_overrides)
        overrides = store.overrides
        priorities = [r.priority for r in overrides]
        assert priorities == sorted(priorities), f"Not sorted: {priorities}"

    def test_find_overrides_for(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        codex_ovs = store.find_overrides_for("codex_required")
        assert len(codex_ovs) == 2
        # Sorted by priority
        assert codex_ovs[0].priority <= codex_ovs[1].priority

    def test_version_from_config(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        assert store.version == "1.0.0"

    def test_version_default_when_missing(self):
        store = RuleStore.from_dict({})
        assert store.version == "0.0.0"

    def test_rule_lookup_by_id(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        # Direct lookup via 'rules' dict
        assert "max_roles_per_meeting" in store.rules
        assert "nonexistent" not in store.rules

    def test_contains(self, mock_config_with_overrides):
        store = RuleStore.from_dict(mock_config_with_overrides)
        assert "max_roles_per_meeting" in store  # constraint
        assert "force_p0_on_security" in store    # override
        assert "nonexistent" not in store

    def test_source_path_empty_for_dict(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        assert store.ruleset.source_path == ""

    def test_repr(self, mock_config_with_constraints):
        store = RuleStore.from_dict(mock_config_with_constraints)
        r = repr(store)
        assert "RuleStore" in r
        assert "constraints=6" in r

    # ── Error handling ─────────────────────────────────────────────────

    def test_guardrails_not_a_list_raises(self):
        with pytest.raises(TypeError, match="guardrails must be a list"):
            RuleStore.from_dict({"version": "1.0.0", "guardrails": "not_a_list"})

    def test_guardrail_entry_not_dict_raises(self):
        with pytest.raises(TypeError, match="must be a dict"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": ["not_a_dict"],
            })

    def test_guardrail_missing_id_raises(self):
        with pytest.raises(ValueError, match="id.* is required"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [{"description": "no id", "rule": "x", "enforcement": "y"}],
            })

    def test_guardrail_empty_id_raises(self):
        with pytest.raises(ValueError, match="id.* is required"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [{"id": "", "description": "d", "rule": "x", "enforcement": "y"}],
            })

    def test_guardrail_missing_description_raises(self):
        with pytest.raises(ValueError, match="description must be non-empty"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [{"id": "test", "rule": "x", "enforcement": "y"}],
            })

    def test_guardrail_missing_rule_raises(self):
        with pytest.raises(ValueError, match="rule must be non-empty"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [{"id": "test", "description": "d", "enforcement": "y"}],
            })

    def test_guardrail_missing_enforcement_raises(self):
        with pytest.raises(ValueError, match="enforcement must be non-empty"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [{"id": "test", "description": "d", "rule": "x"}],
            })

    def test_duplicate_guardrail_id_raises(self):
        with pytest.raises(ValueError, match="duplicate"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "guardrails": [
                    {"id": "dup", "description": "d1", "rule": "x", "enforcement": "y"},
                    {"id": "dup", "description": "d2", "rule": "x", "enforcement": "y"},
                ],
            })

    def test_override_rules_not_a_list_raises(self):
        with pytest.raises(TypeError, match="override_rules must be a list"):
            RuleStore.from_dict({"version": "1.0.0", "override_rules": "not_a_list"})

    def test_override_entry_not_dict_raises(self):
        with pytest.raises(TypeError, match="must be a dict"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "override_rules": ["not_a_dict"],
            })

    def test_override_missing_id_raises(self):
        with pytest.raises(ValueError, match="id.* is required"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "override_rules": [{
                    "description": "d", "condition": "c", "action": "a",
                    "priority": 0, "target_field": "f", "target_value": "v",
                }],
            })

    def test_override_invalid_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be a number"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "override_rules": [{
                    "id": "test", "description": "d", "condition": "c",
                    "action": "a", "priority": "not_a_number",
                    "target_field": "f", "target_value": "v",
                }],
            })

    def test_override_bool_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be a number"):
            RuleStore.from_dict({
                "version": "1.0.0",
                "override_rules": [{
                    "id": "test", "description": "d", "condition": "c",
                    "action": "a", "priority": False,
                    "target_field": "f", "target_value": "v",
                }],
            })


# ═══════════════════════════════════════════════════════════════════════════
# RuleStore.from_yaml() tests (integration with real config)
# ═══════════════════════════════════════════════════════════════════════════


class TestRuleStoreFromYaml:
    """Integration tests loading from the real routing_rules.yaml."""

    def test_loads_successfully(self, real_store):
        assert real_store is not None
        assert real_store.version == "1.0.0"

    def test_all_16_guardrail_constraints_loaded(self, real_store):
        """The real routing_rules.yaml has exactly 16 guardrail entries."""
        assert real_store.constraint_count == 16

    def test_all_constraints_are_hard(self, real_store):
        """All static constraint rules must have severity='hard'."""
        for rule in real_store.constraints:
            assert rule.severity == "hard", f"{rule.rule_id}: severity={rule.severity}"

    def test_all_6_categories_present(self, real_store):
        """All 6 defined categories must have at least one rule."""
        by_cat = real_store.ruleset.constraints_by_category()
        expected = {
            "role_limits",
            "codex_escalation",
            "meeting_constraints",
            "silent_fail_prevention",
            "isolation",
            "model_constraints",
        }
        actual = set(by_cat.keys())
        assert actual == expected, f"Missing categories: {expected - actual}"

    def test_category_counts_match_yaml_structure(self, real_store):
        """Verify counts match the YAML section structure."""
        by_cat = real_store.ruleset.constraints_by_category()
        assert len(by_cat["role_limits"]) == 4
        assert len(by_cat["codex_escalation"]) == 4
        assert len(by_cat["meeting_constraints"]) == 3
        assert len(by_cat["silent_fail_prevention"]) == 2
        assert len(by_cat["isolation"]) == 1
        assert len(by_cat["model_constraints"]) == 2

    def test_specific_rules_loaded(self, real_store):
        """Verify specific known rules are present and correct."""
        # Role limits
        r = real_store.get_constraint("max_roles_per_meeting")
        assert r is not None
        assert r.enforcement == "truncate_optional_roles_by_expertise_match"
        assert r.category == "role_limits"

        # Codex escalation
        r = real_store.get_constraint("security_always_codex")
        assert r is not None
        assert r.category == "codex_escalation"

        r = real_store.get_constraint("data_loss_always_codex")
        assert r is not None
        assert r.category == "codex_escalation"

        r = real_store.get_constraint("legal_always_codex")
        assert r is not None
        assert r.category == "codex_escalation"

        r = real_store.get_constraint("external_publication_codex")
        assert r is not None
        assert r.category == "codex_escalation"

        # Meeting constraints
        r = real_store.get_constraint("max_rounds_absolute")
        assert r is not None
        assert r.category == "meeting_constraints"

        # Silent fail prevention
        r = real_store.get_constraint("no_silent_fail")
        assert r is not None
        assert r.category == "silent_fail_prevention"

        r = real_store.get_constraint("required_role_no_silent_skip")
        assert r is not None
        assert r.category == "silent_fail_prevention"

        # Isolation
        r = real_store.get_constraint("meeting_directory_isolation")
        assert r is not None
        assert r.category == "isolation"

        # Model constraints
        r = real_store.get_constraint("validator_is_glm")
        assert r is not None
        assert r.category == "model_constraints"

        r = real_store.get_constraint("no_same_model_revalidation")
        assert r is not None
        assert r.category == "model_constraints"
        assert r.enforcement == "force_codex_on_second_validation"

    def test_source_path_set(self, real_store):
        """from_yaml() sets the source_path."""
        assert real_store.ruleset.source_path != ""
        assert "routing_rules.yaml" in real_store.ruleset.source_path

    def test_rules_indexed_by_id(self, real_store):
        """The 'rules' dict provides O(1) lookup."""
        assert isinstance(real_store.rules, dict)
        assert len(real_store.rules) == 16
        # All constraint rule_ids are keys
        for rule in real_store.constraints:
            assert rule.rule_id in real_store.rules

    def test_to_dict_roundtrip(self, real_store):
        """Verify to_dict() produces serializable output."""
        d = real_store.ruleset.to_dict()
        assert isinstance(d, dict)
        assert d["version"] == "1.0.0"
        assert d["stats"]["constraint_count"] == 16
        assert d["stats"]["override_count"] == 0
        assert d["stats"]["total_rules"] == 16
        # Verify each constraint dict has the correct structure
        for entry in d["static_constraints"]:
            assert entry["rule_type"] == "static_constraint"
            assert "rule_id" in entry
            assert "category" in entry
            assert "severity" in entry
            assert entry["severity"] == "hard"


# ═══════════════════════════════════════════════════════════════════════════
# load_rules_from_config convenience function tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadRulesFromConfig:
    """Test the convenience function load_rules_from_config."""

    def test_returns_rule_store(self):
        p = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
        store = load_rules_from_config(p)
        assert isinstance(store, RuleStore)

    def test_file_not_found_raises(self, tmp_path):
        """Should raise FileNotFoundError for a nonexistent file."""
        nonexistent = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            load_rules_from_config(nonexistent)

    def test_invalid_yaml_raises(self, tmp_path):
        """Should raise yaml.YAMLError for invalid YAML."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : : invalid yaml : : :\n{[[")
        import yaml
        with pytest.raises(yaml.YAMLError):
            load_rules_from_config(bad)


# ═══════════════════════════════════════════════════════════════════════════
# Sub-AC 3.3.1 contract verification
# ═══════════════════════════════════════════════════════════════════════════


class TestSubAC331Contract:
    """Verify the module fulfills the Sub-AC 3.3.1 contract."""

    def test_testable_with_mock_config(self):
        """Core requirement: must be testable by providing mock config."""
        # No filesystem access, no network, no side effects
        mock = {
            "version": "1.0.0",
            "guardrails": [
                {"id": "test_rule", "description": "Test", "rule": "x", "enforcement": "y"},
            ],
            "override_rules": [
                {"id": "test_ov", "description": "Test", "condition": "c",
                 "action": "a", "priority": 1, "target_field": "f", "target_value": "v"},
            ],
        }
        store = RuleStore.from_dict(mock)
        assert store.constraint_count == 1
        assert store.override_count == 1

    def test_parsed_rule_objects_match_expected_schema(self):
        """Core requirement: parsed rule objects must match expected schema."""
        mock = {
            "version": "1.0.0",
            "guardrails": [
                {"id": "schema_test", "description": "Schema verification rule",
                 "rule": "x <= 7", "enforcement": "truncate_optional"},
            ],
        }
        store = RuleStore.from_dict(mock)
        rule = store.get_constraint("schema_test")
        assert rule is not None

        # Schema requirements for StaticConstraintRule:
        # - rule_id: non-empty string (kebab-case)
        assert isinstance(rule.rule_id, str) and len(rule.rule_id) > 0
        # - description: non-empty string
        assert isinstance(rule.description, str) and len(rule.description) > 0
        # - rule_expr: non-empty string
        assert isinstance(rule.rule_expr, str) and len(rule.rule_expr) > 0
        # - enforcement: non-empty string (snake_case)
        assert isinstance(rule.enforcement, str) and len(rule.enforcement) > 0
        # - category: one of the valid categories
        assert rule.category in StaticConstraintRule._VALID_CATEGORIES
        # - severity: must be "hard"
        assert rule.severity == "hard"

    def test_deterministic_parsing(self):
        """Same config → same RuleStore (deterministic)."""
        config = {
            "version": "1.0.0",
            "guardrails": [
                {"id": "a", "description": "d", "rule": "r", "enforcement": "e"},
                {"id": "b", "description": "d", "rule": "r", "enforcement": "e"},
            ],
        }
        s1 = RuleStore.from_dict(config)
        s2 = RuleStore.from_dict(config)
        assert s1.constraint_count == s2.constraint_count
        assert [r.rule_id for r in s1.constraints] == [r.rule_id for r in s2.constraints]

    def test_serialization_roundtrip(self):
        """to_dict() output must be JSON-serializable and contain all fields."""
        import json
        mock = {
            "version": "1.0.0",
            "guardrails": [
                {"id": "rt", "description": "d", "rule": "r", "enforcement": "e"},
            ],
            "override_rules": [
                {"id": "ov_rt", "description": "d", "condition": "c",
                 "action": "a", "priority": 0, "target_field": "f", "target_value": "v"},
            ],
        }
        store = RuleStore.from_dict(mock)
        d = store.ruleset.to_dict()
        # Must be JSON-serializable
        json_str = json.dumps(d)
        reloaded = json.loads(json_str)
        assert reloaded["version"] == "1.0.0"
        assert reloaded["stats"]["total_rules"] == 2
