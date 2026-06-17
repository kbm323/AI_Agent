"""Comprehensive tests for the routing rules schema validator (Sub-AC 3.1b).

Covers:
- Happy path: real routing_rules.yaml passes all checks
- Null / non-dict input
- Missing top-level sections
- Wrong types on top-level sections
- Version: missing, wrong type, invalid SemVer
- Metadata: missing sub-fields, wrong types
- Defaults: field-level type checks
- Teams: wrong count, invalid team_id, missing team fields
- Roles: wrong count, missing/invalid fields, duplicate role_id, invalid team/role_type
- Agenda types: missing/duplicate id, invalid keyword groups, missing lists
- Risk detection: duplicate risk_tag, invalid severity, missing keywords
- Escalation rules: missing codex_triggers, invalid trigger fields
- Priority rules: invalid priority value, missing default
- Guardrails: duplicate id, missing fields
- Unknown top-level keys
- Multiple simultaneous violations (no early-exit)
- Error report properties and immutability
"""

from __future__ import annotations

import copy
from textwrap import dedent

import pytest

from src.routing_rules_loader import load_routing_rules
from src.routing_rules_validator import (
    RoutingRulesValidationError,
    SchemaViolation,
    ValidationReport,
    validate_routing_rules,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def real_rules() -> dict:
    """Load the actual routing_rules.yaml once for the test module."""
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    return load_routing_rules(p)


@pytest.fixture
def valid_minimal() -> dict:
    """Return a deeply-copied dict of the real rules (guaranteed valid)."""
    # We use a module-scoped fixture + deepcopy per test
    # so individual tests can mutate safely.
    return load_routing_rules(
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "config"
        / "routing_rules.yaml"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path
# ═══════════════════════════════════════════════════════════════════════════


class TestValidRoutingRules:
    """Verify that the real routing_rules.yaml passes full validation."""

    def test_real_rules_pass(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert validated is valid_minimal  # returns same object
        assert validated["version"] == "1.0.0"

    def test_version_field(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert validated["version"] == "1.0.0"

    def test_teams_count(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert len(validated["teams"]) == 6

    def test_roles_count(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert len(validated["roles"]) == 29

    def test_agenda_types_present(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert len(validated["agenda_types"]) >= 10

    def test_risk_detection_present(self, valid_minimal: dict) -> None:
        validated = validate_routing_rules(valid_minimal)
        assert len(validated["risk_detection"]["patterns"]) >= 10


# ═══════════════════════════════════════════════════════════════════════════
# 2. Null / non-dict input
# ═══════════════════════════════════════════════════════════════════════════


class TestNullAndNonDictInput:
    def test_none_input_raises(self) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(None)
        report = exc_info.value.report
        assert not report.passed
        assert report.error_count == 1
        assert report.violations[0].path == "<root>"
        assert "None" in report.violations[0].message

    def test_list_input_raises(self) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(["not", "a", "dict"])
        report = exc_info.value.report
        assert not report.passed
        assert report.violations[0].path == "<root>"
        assert "list" in report.violations[0].message.lower()

    def test_string_input_raises(self) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules("just a string")
        report = exc_info.value.report
        assert not report.passed
        assert report.violations[0].path == "<root>"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Missing / wrong-type top-level sections
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingSections:
    def test_empty_dict_fails_all_sections(self) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules({})
        report = exc_info.value.report
        assert not report.passed
        # All 12 sections should be flagged as missing
        missing_count = sum(1 for v in report.violations if v.error_type == "missing_section")
        assert missing_count == 12

    def test_missing_version_only(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        del data["version"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(v.path == "version" for v in exc_info.value.report.violations)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Version validation
# ═══════════════════════════════════════════════════════════════════════════


class TestVersionValidation:
    def test_valid_semver_accepted(self, valid_minimal: dict) -> None:
        for ver in ("1.0.0", "0.1.0", "99.99.99", "2.3.4"):
            data = copy.deepcopy(valid_minimal)
            data["version"] = ver
            validate_routing_rules(data)  # should not raise

    def test_invalid_semver_rejected(self, valid_minimal: dict) -> None:
        for ver in ("1.0", "v1.0.0", "1.0.0.0", "one.two.three", "", "1.0.0-beta"):
            data = copy.deepcopy(valid_minimal)
            data["version"] = ver
            with pytest.raises(RoutingRulesValidationError) as exc_info:
                validate_routing_rules(data)
            assert any(v.path == "version" for v in exc_info.value.report.violations)

    def test_version_not_string(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["version"] = 1.0  # type: ignore[assignment]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        violations = exc_info.value.report.violations
        assert any(v.path == "version" and v.error_type == "wrong_type" for v in violations)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Metadata validation
# ═══════════════════════════════════════════════════════════════════════════


class TestMetadataValidation:
    def test_missing_description(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        del data["metadata"]["description"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(v.path == "metadata.description" for v in exc_info.value.report.violations)

    def test_activated_when_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["metadata"]["activated_when"] = "not_a_list"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(v.path == "metadata.activated_when" for v in exc_info.value.report.violations)

    def test_metadata_not_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["metadata"] = ["list", "instead"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        violations = exc_info.value.report.violations
        assert any(v.path == "metadata" and v.error_type == "wrong_type" for v in violations)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Defaults validation
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultsValidation:
    def test_invalid_max_roles_per_meeting(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["defaults"]["max_roles_per_meeting"] = "seven"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "defaults.max_roles_per_meeting" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_quorum_ratio_out_of_range(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["defaults"]["quorum_minimum_ratio"] = 1.5
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "defaults.quorum_minimum_ratio" and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. Teams validation
# ═══════════════════════════════════════════════════════════════════════════


class TestTeamsValidation:
    def test_wrong_team_count(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["teams"] = {"only-one": {"name": "Test", "display_emoji": "T", "description": "desc"}}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "teams" and v.error_type == "wrong_length"
            for v in exc_info.value.report.violations
        )

    def test_unknown_team_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["teams"] = {
            "content-production": data["teams"]["content-production"],
            "art-design": data["teams"]["art-design"],
            "tech-engineering": data["teams"]["tech-engineering"],
            "marketing": data["teams"]["marketing"],
            "validation": data["teams"]["validation"],
            "bogus-team": {"name": "Bogus", "display_emoji": "X", "description": "nope"},
        }
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "bogus-team" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_team_not_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["teams"] = "not_a_dict"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(v.path == "teams" and v.error_type == "wrong_type" for v in exc_info.value.report.violations)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Roles validation
# ═══════════════════════════════════════════════════════════════════════════


class TestRolesValidation:
    def test_wrong_role_count(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = data["roles"][:5]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "roles" and v.error_type == "wrong_length"
            for v in exc_info.value.report.violations
        )

    def test_missing_role_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])  # shallow copy list
        del data["roles"][0]["role_id"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "roles[0].role_id" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_duplicate_role_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        # Clone first role and change only role_id to match second
        clone = copy.deepcopy(data["roles"][1])
        data["roles"].append(clone)
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        # Should have a duplicate violation AND wrong role count
        assert any(
            v.error_type == "invalid_value" and "duplicate" in v.message.lower()
            for v in exc_info.value.report.violations
        )

    def test_invalid_team(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        data["roles"][0]["team"] = "nonexistent-team"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path.endswith(".team") and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_invalid_role_type(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        data["roles"][0]["role_type"] = "supervisor"  # not in valid set
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path.endswith(".role_type") and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_missing_model(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        del data["roles"][0]["model"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "roles[0].model" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_model_not_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        data["roles"][0]["model"] = "qwen-max"  # should be a dict
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "roles[0].model" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_expertise_tags_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        data["roles"][0]["expertise_tags"] = "tag1, tag2"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "expertise_tags" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_persistent_bot_not_bool(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["roles"] = list(data["roles"])
        data["roles"][0] = dict(data["roles"][0])
        data["roles"][0]["persistent_bot"] = 1  # truthy but not strict bool
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "persistent_bot" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 9. Agenda types validation
# ═══════════════════════════════════════════════════════════════════════════


class TestAgendaTypesValidation:
    def test_missing_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        del data["agenda_types"][0]["id"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "agenda_types[0].id" in v.path and v.error_type == "missing_field"
            for v in exc_info.value.report.violations
        )

    def test_duplicate_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["agenda_types"][1] = dict(data["agenda_types"][1])
        data["agenda_types"][1]["id"] = data["agenda_types"][0]["id"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "invalid_value" == v.error_type and "duplicate" in v.message.lower()
            for v in exc_info.value.report.violations
        )

    def test_keywords_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["agenda_types"][0] = dict(data["agenda_types"][0])
        data["agenda_types"][0]["keywords"] = "keyword1, keyword2"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "keywords" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_keyword_group_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["agenda_types"][0] = dict(data["agenda_types"][0])
        data["agenda_types"][0]["keywords"] = [["valid"], "not_a_list"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "keywords[1]" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_tags_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["agenda_types"][0] = dict(data["agenda_types"][0])
        data["agenda_types"][0]["tags"] = None
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path.endswith(".tags") and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_validator_required_not_bool(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["agenda_types"][0] = dict(data["agenda_types"][0])
        data["agenda_types"][0]["validator_required"] = "yes"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "validator_required" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 10. Risk detection validation
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskDetectionValidation:
    def test_patterns_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["risk_detection"] = {"patterns": "not_a_list"}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "risk_detection.patterns" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_duplicate_risk_tag(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["risk_detection"]["patterns"][1] = dict(data["risk_detection"]["patterns"][1])
        data["risk_detection"]["patterns"][1]["risk_tag"] = data["risk_detection"]["patterns"][0]["risk_tag"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "duplicate" in v.message.lower() for v in exc_info.value.report.violations
        )

    def test_invalid_severity(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["risk_detection"]["patterns"][0] = dict(data["risk_detection"]["patterns"][0])
        data["risk_detection"]["patterns"][0]["severity"] = "extreme"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "severity" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_keywords_not_list_in_pattern(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["risk_detection"]["patterns"][0] = dict(data["risk_detection"]["patterns"][0])
        data["risk_detection"]["patterns"][0]["keywords"] = "security"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "keywords" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 11. Escalation rules validation
# ═══════════════════════════════════════════════════════════════════════════


class TestEscalationRulesValidation:
    def test_codex_triggers_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["escalation_rules"]["codex_triggers"] = "not_a_list"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "codex_triggers" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_conflict_resolution_not_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["escalation_rules"]["conflict_resolution"] = ["list", "instead"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "conflict_resolution" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 12. Priority rules validation
# ═══════════════════════════════════════════════════════════════════════════


class TestPriorityRulesValidation:
    def test_invalid_priority_value(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["priority_rules"]["inference"][0] = dict(data["priority_rules"]["inference"][0])
        data["priority_rules"]["inference"][0]["priority"] = "P5"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "priority" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_invalid_default_priority(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["priority_rules"] = dict(data["priority_rules"])
        data["priority_rules"]["default"] = "high"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "priority_rules.default" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 13. Guardrails validation
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardrailsValidation:
    def test_duplicate_guardrail_id(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["guardrails"][1] = dict(data["guardrails"][1])
        data["guardrails"][1]["id"] = data["guardrails"][0]["id"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "duplicate" in v.message.lower() for v in exc_info.value.report.violations
        )

    def test_missing_guardrail_fields(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["guardrails"] = [{"id": "only-id"}]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        # Should have violations for missing description, rule, enforcement
        assert exc_info.value.report.error_count >= 3


# ═══════════════════════════════════════════════════════════════════════════
# 14. Unknown keys
# ═══════════════════════════════════════════════════════════════════════════


class TestUnknownKeys:
    def test_unknown_top_level_key(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["extra_section"] = {"foo": "bar"}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "extra_section" and v.error_type == "unknown_key"
            for v in exc_info.value.report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 15. Multiple simultaneous violations (no early-exit)
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleViolations:
    def test_all_sections_broken(self) -> None:
        """A heavily corrupted dict should produce many violations, not just one."""
        broken = {
            "version": 123,
            "metadata": "not_dict",
            "defaults": ["list", "not", "dict"],
            "teams": None,
            "roles": "not_list",
            "agenda_types": 0,
            "risk_detection": {"patterns": "nope"},
            "escalation_rules": False,
            "priority_rules": 42,
            "guardrails": "not_list",
            "matching_algorithm": True,
            "output_schema": 3.14,
        }
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(broken)
        report = exc_info.value.report
        assert not report.passed
        # Should have many violations — at least 12 (one per section)
        assert report.error_count >= 12

    def test_two_sections_broken_reports_both(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        del data["version"]
        data["defaults"]["max_roles_per_meeting"] = "not_an_int"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        violations = exc_info.value.report.violations
        paths = {v.path for v in violations}
        assert "version" in paths or any("version" in p for p in paths)
        assert "defaults.max_roles_per_meeting" in paths or any(
            "max_roles_per_meeting" in p for p in paths
        )


# ═══════════════════════════════════════════════════════════════════════════
# 16. ValidationReport properties
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationReportProperties:
    def test_report_passed_property(self, valid_minimal: dict) -> None:
        """When valid, report is not visible (no exception raised)."""
        validate_routing_rules(valid_minimal)  # no exception = pass

    def test_error_count_matches_violations_length(self) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules({})
        report = exc_info.value.report
        assert report.error_count == len(report.violations)

    def test_violations_by_section(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["version"] = 99  # not a string
        del data["metadata"]["primary_router"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        grouped = exc_info.value.report.violations_by_section()
        assert "version" in grouped
        assert "metadata" in grouped

    def test_exception_is_value_error_subclass(self) -> None:
        with pytest.raises(ValueError):
            validate_routing_rules({})

    def test_exception_message_contains_section_names(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["version"] = "bad"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert "version" in str(exc_info.value).lower()

    def test_schema_violation_is_frozen(self) -> None:
        sv = SchemaViolation(path="test", error_type="test", message="test")
        with pytest.raises(Exception):  # dataclass frozen
            sv.path = "other"  # type: ignore[misc]

    def test_callable_accepts_validation_report(self) -> None:
        """RoutingRulesValidationError can be constructed from a report."""
        report = ValidationReport(passed=False, violations=(), sections_checked=0)
        # A report with no violations but passed=False is a weird edge case
        # but the exception should still construct fine.
        exc = RoutingRulesValidationError(report)
        assert exc.report is report


# ═══════════════════════════════════════════════════════════════════════════
# 17. Output Schema fields validation (Sub-AC 3.1.3 — target format contract)
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputSchemaFieldsValidation:
    """Validate the output_schema.fields target format definition."""

    def test_valid_fields_pass(self, valid_minimal: dict) -> None:
        """The real routing_rules.yaml output_schema.fields should pass."""
        data = copy.deepcopy(valid_minimal)
        validate_routing_rules(data)  # should not raise

    def test_missing_fields_section(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        del data["output_schema"]["fields"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "output_schema.fields" and v.error_type == "missing_field"
            for v in exc_info.value.report.violations
        )

    def test_fields_not_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = ["a", "list", "not", "dict"]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "output_schema.fields" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_empty_fields_dict(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "output_schema.fields" and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_unknown_field_name(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = dict(data["output_schema"]["fields"])
        data["output_schema"]["fields"]["bogus_field"] = "string"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "output_schema.fields.bogus_field" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_non_string_field_type(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {"agenda_type": 42}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "output_schema.fields.agenda_type" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_empty_string_field_type(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {"agenda_type": "   "}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "output_schema.fields.agenda_type" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_literal_value_types_accepted(self, valid_minimal: dict) -> None:
        """Literal values like 'static_fallback' in type position are tolerated."""
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {
            "agenda_type": "string",
            "routing_source": "static_fallback",  # literal value, not a type name
            "version": "1.0.0",  # literal value
        }
        # Should pass — unrecognised type strings are tolerated
        validate_routing_rules(data)  # no exception

    def test_valid_type_specifiers(self, valid_minimal: dict) -> None:
        """All recognised type specifiers should pass validation."""
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {
            "agenda_type": "string",
            "agenda_label": "string",
            "tags": "array<string>",
            "risk_tags": "array<string>",
            "required_roles": "array<string>",
            "optional_roles": "array<string>",
            "validator_required": "boolean",
            "codex_required": "boolean",
            "priority": "string",
            "confidence": "number",
            "generated_at": "string",
        }
        validate_routing_rules(data)  # no exception

    def test_multiple_field_violations_reported(self, valid_minimal: dict) -> None:
        """Multiple field issues in output_schema.fields are all reported."""
        data = copy.deepcopy(valid_minimal)
        data["output_schema"] = dict(data["output_schema"])
        data["output_schema"]["fields"] = {
            "bogus1": 123,  # non-string type + unknown field
            "bogus2": "",  # empty type + unknown field
        }
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        violations = exc_info.value.report.violations
        # At minimum: 2 unknown field + 1 wrong_type + 1 empty type
        assert len([v for v in violations if "output_schema.fields" in v.path]) >= 4


# ═══════════════════════════════════════════════════════════════════════════
# 18. Matching algorithm deep validation (Sub-AC 3.1.3)
# ═══════════════════════════════════════════════════════════════════════════


class TestMatchingAlgorithmValidation:
    """Validate the matching_algorithm sub-structure."""

    def test_valid_matching_algorithm_passes(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        validate_routing_rules(data)  # no exception

    def test_steps_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = dict(data["matching_algorithm"])
        data["matching_algorithm"]["steps"] = "not_a_list"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "matching_algorithm.steps" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_steps_empty_list_accepted(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = dict(data["matching_algorithm"])
        data["matching_algorithm"]["steps"] = []
        # Empty steps list is tolerated (list[str] allows empty)
        validate_routing_rules(data)  # no exception

    def test_steps_with_non_string_items(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = dict(data["matching_algorithm"])
        data["matching_algorithm"]["steps"] = ["valid step", 42, None]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            "matching_algorithm.steps" in v.path and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_language_support_not_list(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = dict(data["matching_algorithm"])
        data["matching_algorithm"]["language_support"] = "korean"
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            v.path == "matching_algorithm.language_support" and v.error_type == "wrong_type"
            for v in exc_info.value.report.violations
        )

    def test_missing_steps_and_language_support(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = {"description": "minimal"}
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        violations = exc_info.value.report.violations
        assert any("matching_algorithm.steps" in v.path for v in violations)
        assert any("matching_algorithm.language_support" in v.path for v in violations)

    def test_note_field_accepted(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["matching_algorithm"] = dict(data["matching_algorithm"])
        data["matching_algorithm"]["note"] = "Some note"
        validate_routing_rules(data)  # no exception


# ═══════════════════════════════════════════════════════════════════════════
# 19. Escalation action validation (Sub-AC 3.1.3)
# ═══════════════════════════════════════════════════════════════════════════


class TestEscalationActionValidation:
    """Validate escalation_rules.codex_triggers action values."""

    def test_valid_actions_pass(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        validate_routing_rules(data)  # no exception

    def test_invalid_action_value(self, valid_minimal: dict) -> None:
        data = copy.deepcopy(valid_minimal)
        data["escalation_rules"] = dict(data["escalation_rules"])
        data["escalation_rules"]["codex_triggers"] = [
            {
                "id": "test-trigger",
                "trigger_number": 1,
                "description": "A test trigger",
                "action": "do_something_unknown",
            }
        ]
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        assert any(
            ".action" in v.path and v.error_type == "invalid_value"
            for v in exc_info.value.report.violations
        )

    def test_all_valid_action_values_accepted(self, valid_minimal: dict) -> None:
        """Verify all defined valid escalation actions are accepted."""
        valid_actions = [
            "escalate_to_codex",
            "escalate_to_human",
            "force_codex_required",
            "force_re_validate",
            "pause_and_notify",
            "set_codex_required_true",
        ]
        data = copy.deepcopy(valid_minimal)
        data["escalation_rules"] = dict(data["escalation_rules"])
        data["escalation_rules"]["codex_triggers"] = [
            {
                "id": f"test-{action}",
                "trigger_number": i + 1,
                "description": f"Trigger with action {action}",
                "action": action,
            }
            for i, action in enumerate(valid_actions)
        ]
        validate_routing_rules(data)  # no exception
